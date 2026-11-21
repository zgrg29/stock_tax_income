"""Strict broker report import; malformed trades never silently disappear."""
import hashlib
from collections import defaultdict
from io import BytesIO
import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from cgt import CalculationError, Transaction, ZERO, number, trade_date


def blank(value):
    return value is None or bool(pd.isna(value)) or str(value).strip() == ''


def text(value):
    return '' if blank(value) else str(value).strip()


def amount(row, column, *, optional=False):
    if column not in row:
        raise CalculationError(f'缺少金额列 {column}')
    if blank(row[column]):
        if optional:
            return ZERO
        raise CalculationError(f'{column} 为空')
    return number(row[column], column)


def normalized(value):
    return ' '.join(str(value).split())


def load_rates(file):
    """CSV: Date,Currency,AUD per unit. Rates are AUD per foreign unit."""
    if file is None:
        return {}
    frame = pd.read_csv(file, dtype=str)
    if not {'Date', 'Currency', 'AUD per unit'}.issubset(frame.columns):
        raise CalculationError('汇率 CSV 必须包含 Date,Currency,AUD per unit')
    rates = {}
    for index, row in frame.iterrows():
        try:
            key = trade_date(row['Date']), text(row['Currency']).upper()
            if not re.fullmatch('[A-Z]{3}', key[1]) or key[1] == 'AUD':
                raise CalculationError('Currency 须为非 AUD 的三位币种代码')
            rate = number(row['AUD per unit'], 'AUD per unit', positive=True)
            if key in rates and rates[key] != rate:
                raise CalculationError('同日同币种汇率冲突')
            rates[key] = rate
        except CalculationError as exc:
            raise CalculationError(f'汇率 CSV 第 {index + 2} 行: {exc}') from exc
    return rates


class BrokerImporter:
    def __init__(self, rates=None):
        self.rates = rates or {}
        self.transactions = []
        self.errors = []
        self.warnings = []
        self._files = set()
        self._ids = {}
        self._fingerprints = defaultdict(list)

    def rate(self, date, currency):
        if currency == 'AUD':
            return number(1, 'rate')
        if (date, currency) not in self.rates:
            raise CalculationError(f'缺少 {date} {currency} 汇率；需提供每 1 {currency} 对应的 AUD 金额')
        return self.rates[date, currency]

    def add_file(self, file, broker):
        data = file.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if (broker, digest) in self._files:
            self.warnings.append(f'{file.name}: 重复文件已跳过')
            return
        self._files.add((broker, digest))
        try:
            with pd.ExcelFile(BytesIO(data)) as book:
                names = {normalized(name): name for name in book.sheet_names}
                sheets = (('Domestic Portfolio Transactions', 'AU'), ('Int. Portfolio Transactions', 'US')) if broker == 'Nabtrade' else (('Aus Equities', 'AU'), ('Wall St Equities', 'US'))
                found = False
                for expected, market in sheets:
                    if expected not in names:
                        continue
                    found = True
                    frame = pd.read_excel(book, sheet_name=names[expected], skiprows=1 if broker == 'Nabtrade' else 0)
                    self.add_frame(frame, broker, market, f'{file.name}/{expected}', digest)
                if not found:
                    self.errors.append(f'{file.name}: 未找到支持的交易工作表')
        except Exception as exc:
            self.errors.append(f'{file.name}: 无法读取 Excel: {exc}')

    def add_frame(self, frame, broker, market, source, file_key=None):
        frame = frame.copy()
        frame.columns = [normalized(c) for c in frame.columns]
        if frame.columns.duplicated().any():
            self.errors.append(f'{source}: 列名重复')
            return
        date_col, symbol_col, side_col, qty_col = ('Date', 'Code', 'Movement Type', 'Quantity') if broker == 'Nabtrade' else ('Trade Date', 'Symbol', 'Side', 'Units')
        required = {date_col, symbol_col, side_col, qty_col}
        if not required.issubset(frame.columns):
            self.errors.append(f'{source}: 缺少列 {", ".join(sorted(required - set(frame.columns)))}')
            return
        file_key = file_key or source
        for position, (_, row) in enumerate(frame.iterrows(), start=3 if broker == 'Nabtrade' else 2):
            if all(blank(v) for v in row):
                continue
            context = f'{source} 第 {position} 行'
            try:
                action = text(row[side_col]).upper()
                if 'TRANSFER' in action:
                    self.warnings.append(f'{context}: 转仓未作为买卖处理，请核对原始买入历史及受益所有人未变化')
                    continue
                action = {'B': 'BUY', 'S': 'SELL', 'ROC': 'RETURN_OF_CAPITAL',
                          'RETURN OF CAPITAL': 'RETURN_OF_CAPITAL'}.get(action, action)
                if action not in ('BUY', 'SELL', 'RETURN_OF_CAPITAL'):
                    raise CalculationError(f'不支持的交易类型 {action!r}，需单独核对公司行动/收入记录')
                raw_date = row[date_col]
                if blank(raw_date) or isinstance(raw_date, (int, float)):
                    raise CalculationError('成交日期缺失或为未解析的 Excel 序列号')
                if isinstance(raw_date, str) and re.match(r'^\d{4}-\d{2}-\d{2}', raw_date.strip()):
                    date = trade_date(raw_date.strip())
                else:
                    date = trade_date(pd.to_datetime(raw_date, errors='raise', dayfirst=True))
                ticker = text(row[symbol_col]).upper()
                if market == 'AU' and ticker.endswith('.AX'):
                    ticker = ticker[:-3]
                if not ticker:
                    raise CalculationError('股票代码为空')
                # Nabtrade may encode disposals as negative movements. The
                # explicit SELL side defines direction; never accept a negative BUY.
                if action == 'SELL':
                    try:
                        quantity = number(abs(Decimal(str(row[qty_col]))), qty_col, positive=True)
                    except InvalidOperation:
                        raise CalculationError(f'{qty_col} 必须为有效数字') from None
                else:
                    quantity = amount(row, qty_col)
                if action == 'RETURN_OF_CAPITAL':
                    # Never infer per-share vs total amounts from magnitude.
                    price = amount(row, 'Capital Return Per Share (AUD)')
                    fees = ZERO
                elif broker == 'Nabtrade' and market == 'US':
                    price = amount(row, 'Average Price (AUD) *1')
                    fees = amount(row, 'Brokerage (AUD)', optional=True) + amount(row, 'Other Fees (AUD)', optional=True)
                else:
                    currency = text(row.get('Currency')).upper() or ('AUD' if market == 'AU' else 'USD')
                    if market == 'AU' and currency != 'AUD':
                        raise CalculationError('澳股报表 Currency 与 AUD 不一致')
                    rate = self.rate(date, currency)
                    price_col = 'Transaction Price' if broker == 'Nabtrade' else 'Avg. Price'
                    price = amount(row, price_col) * rate
                    fee_columns = ('Brokerage', 'Other Fees') if broker == 'Nabtrade' else ('Fees', 'GST')
                    fees = sum((amount(row, c, optional=True) for c in fee_columns), ZERO) * rate
                tx_market = text(row.get('Market')).upper() or market
                if tx_market != market:
                    raise CalculationError('Market 与工作表不一致；当前仅支持 AU 澳股 / US 美股')
                tx = Transaction(date, tx_market, ticker, action, quantity, price, fees, source, str(position))
                trade_id = text(row.get('Trade Identifier')) if broker == 'Stake' else ''
                fingerprint = (tx.date, tx.market, tx.ticker, tx.action, tx.quantity, tx.price, tx.fees)
                account = text(row.get('Account Number')) or text(row.get('Account'))
                if trade_id:
                    key = broker, account, tx.market, trade_id
                    if key in self._ids:
                        if self._ids[key] != fingerprint:
                            raise CalculationError(f'交易编号 {trade_id} 对应的记录冲突')
                        self.warnings.append(f'{context}: 重复交易编号 {trade_id} 已跳过')
                        continue
                # Equal rows within one file may be genuine executions. Across
                # files, without IDs, don't guess whether an overlap is real.
                previous = self._fingerprints[fingerprint]
                if any(other_file != file_key and (not trade_id or not other_id)
                       for other_file, other_id in previous):
                    raise CalculationError('与另一份报表存在相同成交但缺少交易编号，无法安全去重；请上传不重叠报表')
                if trade_id:
                    self._ids[key] = fingerprint
                previous.append((file_key, trade_id))
                self.transactions.append(tx)
            except Exception as exc:
                self.errors.append(f'{context}: {exc}')

    def ordering_errors(self):
        groups = defaultdict(list)
        for tx in self.transactions:
            groups[tx.date, tx.market, tx.ticker].append(tx)
        errors = []
        for (day, market, ticker), rows in groups.items():
            buy_costs = {r.price + r.fees / r.quantity for r in rows if r.action == 'BUY'}
            if len({r.action for r in rows}) > 1 or len(buy_costs) > 1:
                errors.append(f'{day} {market}:{ticker}: 同日存在不同交易类型或不同买入成本；需核对日内成交顺序，暂不能自动 FIFO')
        return errors
