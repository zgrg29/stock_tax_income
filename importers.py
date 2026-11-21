"""Strict broker report import; malformed trades never silently disappear."""
import hashlib
import json
from collections import defaultdict
from io import BytesIO
import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from cgt import (CalculationError, Transaction, Transfer, ZERO, number, trade_date,
                 calculate_ledger)


# Exact broker movement labels only; a substring is never a tax classification.
TRANSFER_TYPES = {
    'TRANSFER': '', 'STOCK TRANSFER': '', 'SECURITY TRANSFER': '', 'OFF MARKET TRANSFER': '',
    'TRANSFER IN': 'IN', 'TRANSFER OUT': 'OUT',
    'INWARD TRANSFER': 'IN', 'OUTWARD TRANSFER': 'OUT',
    'STOCK TRANSFER IN': 'IN', 'STOCK TRANSFER OUT': 'OUT',
    'BROKER TRANSFER IN': 'IN', 'BROKER TRANSFER OUT': 'OUT',
    'SECURITY TRANSFER IN': 'IN', 'SECURITY TRANSFER OUT': 'OUT',
}
CONFIRMED, REVIEW, BLOCKED, INVALID = '已确认', '需要核对', '计算受阻', '数据错误'


class TransferReviewRequired(CalculationError):
    def __init__(self, message, status=BLOCKED):
        super().__init__(message)
        self.status = status


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


def security_code(value, market):
    ticker = text(value).upper()
    return ticker.removesuffix('.AX') if market == 'AU' else ticker


class AccountRegistry:
    """Explicit, evidence-backed beneficial-owner/account relationships.

    Owner IDs represent the entire beneficial ownership (including joint shares
    and proportions), not merely matching names or one joint account holder.
    """
    def __init__(self, rows=()):
        self.owners = {}
        self.evidence = {}
        self.reports = {}
        for row in rows:
            broker, account = text(row.get('Broker')).upper(), text(row.get('Account'))
            owner, evidence = text(row.get('Owner ID')), text(row.get('Evidence'))
            if not all((broker, account, owner, evidence)):
                raise CalculationError('账户关系须提供 Broker,Account,Owner ID,Evidence')
            key = f'{broker}:{account}'
            if key in self.owners and self.owners[key] != owner:
                raise CalculationError(f'账户 {key} 对应多个受益所有人关系')
            self.owners[key], self.evidence[key] = owner, evidence
            filename, sheet = text(row.get('File')), normalized(text(row.get('Sheet')))
            if bool(filename) != bool(sheet):
                raise CalculationError('报表账户映射的 File 和 Sheet 必须同时填写')
            if filename:
                report = broker, filename, sheet
                if report in self.reports and self.reports[report] != key:
                    raise CalculationError(f'报表 {filename}/{sheet} 对应多个账户')
                self.reports[report] = key

    def account(self, broker, account, filename, sheet):
        explicit = f'{broker.upper()}:{account}' if account else ''
        mapped = self.reports.get((broker.upper(), filename, normalized(sheet)), '')
        if explicit and mapped and explicit != mapped:
            raise CalculationError('原报表账户与人工提供的账户映射冲突')
        return explicit or mapped


def load_accounts(file):
    if file is None:
        return AccountRegistry()
    frame = pd.read_csv(file, dtype=str, keep_default_na=False)
    if not {'Broker', 'Account', 'Owner ID', 'Evidence'}.issubset(frame.columns):
        raise CalculationError('账户关系 CSV 必须包含 Broker,Account,Owner ID,Evidence')
    return AccountRegistry(frame.to_dict('records'))


def load_transfer_reviews(file):
    if file is None:
        return {}
    frame = pd.read_csv(file, dtype=str, keep_default_na=False)
    if not {'Record ID', 'Evidence'}.issubset(frame.columns):
        raise CalculationError('转仓补充证据 CSV 至少需要 Record ID,Evidence')
    reviews = {}
    for row in frame.to_dict('records'):
        key = text(row['Record ID'])
        if not key or key in reviews or not text(row['Evidence']):
            raise CalculationError('转仓补充证据的 Record ID 必须唯一，Evidence 不能为空')
        reviews[key] = row
    return reviews


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
    def __init__(self, rates=None, accounts=None, transfer_reviews=None):
        self.rates = rates or {}
        self.transactions = []
        self.errors = []
        self.warnings = []
        self._files = set()
        self._ids = {}
        self._fingerprints = defaultdict(list)
        self.accounts = accounts or AccountRegistry()
        self.transfer_reviews = transfer_reviews or {}
        self.audit = []
        self.transfer_audit = []
        self.transfer_legs = []
        self._transfer_seen = {}
        self._used_reviews = set()
        self._reconciled = False
        self.import_attempts = []

    def rate(self, date, currency):
        if currency == 'AUD':
            return number(1, 'rate')
        if (date, currency) not in self.rates:
            raise CalculationError(f'缺少 {date} {currency} 汇率；需提供每 1 {currency} 对应的 AUD 金额')
        return self.rates[date, currency]

    def add_file(self, file, broker):
        data = file.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        self.import_attempts.append({'File': file.name, 'Broker': broker, 'SHA256': digest,
                                     'Duplicate': (broker, digest) in self._files})
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
                    self.add_frame(frame, broker, market, f'{file.name}/{names[expected]}', digest,
                                   filename=file.name, sheet=names[expected])
                if not found:
                    self.errors.append(f'{file.name}: 未找到支持的交易工作表')
        except Exception as exc:
            self.errors.append(f'{file.name}: 无法读取 Excel: {exc}')

    def add_frame(self, frame, broker, market, source, file_key=None, *, filename='', sheet=''):
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
            raw_action = normalized(text(row[side_col])).upper()
            record_id = hashlib.sha256(f'{broker}|{file_key}|{sheet or source}|{position}'.encode()).hexdigest()[:24]
            record = {'Record ID': record_id, 'File': filename or source, 'Sheet': sheet,
                      'Excel Row': position, 'Source': source, 'File SHA256': file_key,
                      'Raw Type': raw_action, 'Ticker': security_code(row[symbol_col], market),
                      'Market': market,
                      'Date': text(row[date_col]), 'Quantity': text(row[qty_col]),
                      'Status': REVIEW, 'Reason': '', 'Blocks CGT': True,
                      'Raw Data': json.dumps({str(k): text(v) for k, v in row.items()}, ensure_ascii=False)}
            self.audit.append(record)
            is_transfer = raw_action in TRANSFER_TYPES or bool(re.search(r'\bTRANSFER\b', raw_action))
            if is_transfer:
                record.update({'Original Purchase': '未验证', 'Cost Complete': '未验证',
                               'CGT Impact': '未验证，暂不输出最终金额', 'Required Action': '',
                               'Direction': '', 'Transfer Identifier': '',
                               'Reconciliation ID': '',
                               'Counterparty Broker': '', 'Counterparty Account': '', 'Evidence': ''})
                self.transfer_audit.append(record)
            try:
                action = raw_action
                action = {'B': 'BUY', 'S': 'SELL', 'ROC': 'RETURN_OF_CAPITAL',
                          'RETURN OF CAPITAL': 'RETURN_OF_CAPITAL'}.get(action, action)
                if not is_transfer and action not in ('BUY', 'SELL', 'RETURN_OF_CAPITAL'):
                    raise CalculationError(f'不支持的交易类型 {action!r}，需单独核对公司行动/收入记录')
                raw_date = row[date_col]
                if blank(raw_date) or isinstance(raw_date, (int, float)):
                    raise CalculationError('成交日期缺失或为未解析的 Excel 序列号')
                if isinstance(raw_date, str) and re.match(r'^\d{4}-\d{2}-\d{2}', raw_date.strip()):
                    date = trade_date(raw_date.strip())
                else:
                    date = trade_date(pd.to_datetime(raw_date, errors='raise', dayfirst=True))
                ticker = security_code(row[symbol_col], market)
                if not ticker:
                    raise CalculationError('股票代码为空')
                record.update({'Date': str(date), 'Ticker': ticker})
                tx_market = text(row.get('Market')).upper() or market
                if tx_market != market:
                    raise CalculationError('Market 与工作表不一致；当前仅支持 AU 澳股 / US 美股')
                account = self.accounts.account(broker, text(row.get('Account Number')) or text(row.get('Account')),
                                                filename, sheet)
                record['Account'] = account
                if is_transfer:
                    self._add_transfer(row, record, broker, market, date, ticker, account, qty_col)
                    continue
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
                tx = Transaction(date, tx_market, ticker, action, quantity, price, fees, source,
                                 str(position), account, record_id)
                trade_id = text(row.get('Trade Identifier')) if broker == 'Stake' else ''
                fingerprint = (tx.date, tx.market, tx.ticker, tx.action, tx.quantity, tx.price, tx.fees, account)
                if trade_id:
                    key = broker, account, tx.market, trade_id
                    if key in self._ids:
                        if self._ids[key] != fingerprint:
                            raise CalculationError(f'交易编号 {trade_id} 对应的记录冲突')
                        self.warnings.append(f'{context}: 重复交易编号 {trade_id} 已跳过')
                        record.update(Status=CONFIRMED, Reason='重复交易编号，未再次计入库存', **{'Blocks CGT': False})
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
                record.update(Status=CONFIRMED, Reason='已导入买卖/成本调整，等待账本验证', **{'Blocks CGT': False})
            except Exception as exc:
                record.update(Status=getattr(exc, 'status', INVALID), Reason=str(exc))
                if is_transfer:
                    record.update({'Required Action': '修正原始数据或补齐对应证据后重新导入',
                                   'CGT Impact': '未完成税务校验，阻止最终金额'})
                self.errors.append(f'{context}: {exc}')

    def _add_transfer(self, row, record, broker, market, date, ticker, account, qty_col):
        if record['Raw Type'] not in TRANSFER_TYPES:
            raise TransferReviewRequired('名称含 Transfer，但不是已支持的纯证券转仓类型；不能自动按非处置处理', REVIEW)
        try:
            quantity = number(abs(Decimal(str(row[qty_col]))), '转仓数量', positive=True)
        except InvalidOperation:
            raise CalculationError('转仓数量无效') from None
        record['Quantity'] = str(quantity)
        review = self.transfer_reviews.get(record['Record ID'], {})
        if review:
            if not text(review.get('Evidence')):
                raise CalculationError('逐行转仓补充信息必须提供 Evidence 凭据引用')
            self._used_reviews.add(record['Record ID'])
        for name in ('Direction', 'Transfer Identifier', 'Reconciliation ID', 'Counterparty Broker', 'Counterparty Account', 'Evidence'):
            original, supplied = text(row.get(name)), text(review.get(name))
            if original and supplied and original.upper() != supplied.upper():
                raise CalculationError(f'补充证据 {name} 与原报表冲突，不允许静默覆盖')
            record[name] = supplied or original
        if record['Reconciliation ID'] and not record['Evidence']:
            raise CalculationError('跨券商共同 Reconciliation ID 必须提供 Evidence 凭据引用')
        implied = TRANSFER_TYPES[record['Raw Type']]
        direction = record['Direction'].upper() or implied
        if direction not in ('IN', 'OUT', '') or (implied and direction != implied):
            raise CalculationError('转仓 Direction 必须为 IN/OUT，且必须与明确的原始类型一致')
        record['Direction'] = direction
        changed = text(row.get('Beneficial Ownership Changed')).upper()
        if changed not in ('', 'YES', 'NO', 'UNKNOWN'):
            raise CalculationError('Beneficial Ownership Changed 必须为 YES/NO/UNKNOWN')
        # Transfer charges can require a separate tax analysis. Do not drop them
        # or silently add them to an old lot's cost without establishing treatment.
        for fee in ('Brokerage', 'Other Fees', 'Fees', 'GST', 'Brokerage (AUD)', 'Other Fees (AUD)'):
            if fee in row and not blank(row[fee]) and number(row[fee], fee):
                raise TransferReviewRequired('转仓含非零费用，需核对可计入成本的处理；未擅自忽略费用')
        leg = dict(record=record, broker=broker, market=market, ticker=ticker,
                   date=date, quantity=quantity, account=account, changed=changed)
        transfer_id = record['Transfer Identifier'] or record['Reconciliation ID']
        if transfer_id and account and direction:
            key = broker, account, market, ticker, transfer_id, direction
            signature = date, quantity, changed, record['Counterparty Broker'], record['Counterparty Account']
            if key in self._transfer_seen:
                previous, previous_signature = self._transfer_seen[key]
                if signature != previous_signature:
                    raise CalculationError('同一转仓编号/方向/账户的日期、数量或关系冲突')
                record.update({'Status': CONFIRMED, 'Reason': '重复转仓腿已去重，不重复移动持仓',
                               'Duplicate Of': previous['Record ID'], 'Blocks CGT': False,
                               'CGT Impact': '重复记录未进入税务账本', 'Required Action': '无'})
                return
            self._transfer_seen[key] = record, signature
        self.transfer_legs.append(leg)

    def reconcile(self, start_year):
        """Reconcile evidence first, then use the existing FIFO engine once.

        A supplied account registry is evidence, not an independent verification
        of a person's identity. Missing facts never become assumed tax exemptions.
        """
        if self._reconciled:
            raise CalculationError('导入器已完成核对；重新导入后再计算')
        self._reconciled = True
        cutoff = trade_date(f'{start_year + 1}-07-01')
        for record in self.transfer_audit:
            try:
                if trade_date(record['Date']) >= cutoff:
                    record['Blocks CGT'] = False
                    record['CGT Impact'] = '目标财年之后；未纳入本期税务账本，仍需另行核对'
            except CalculationError:
                pass  # Invalid dates cannot be assumed outside the target year.
        unused = set(self.transfer_reviews) - self._used_reviews
        if unused:
            self.errors.append('转仓补充证据有未匹配的 Record ID；可能对应旧文件或错误行号')
        grouped = defaultdict(list)
        for leg in self.transfer_legs:
            r = leg['record']
            visible_buys = any(tx.action == 'BUY' and tx.market == leg['market'] and
                               tx.ticker == leg['ticker'] and tx.date <= leg['date']
                               for tx in self.transactions)
            r['Original Purchase'] = '可见买入记录，可用批次待验证' if visible_buys else '未找到原始买入'
            if not visible_buys:
                r['Cost Complete'] = '否'
            if leg['changed'] == 'YES':
                r.update(Status=BLOCKED, Reason='原报表明确受益所有人变化，不能认定普通非处置转仓',
                         **{'Required Action': '核对处置/取得日期、市场价值及适用 rollover；补齐后单独处理'})
                continue
            pairing_id = r['Reconciliation ID'] or r['Transfer Identifier']
            if not pairing_id or not r['Direction'] or not leg['account']:
                r.update(Status=REVIEW if visible_buys else BLOCKED,
                         Reason='转仓编号、方向或报告账户缺失，尚未验证' if visible_buys else '缺失原始买入成本，且转仓编号/方向/账户未核对',
                         **{'Required Action': '补充账户关系/报表账户映射及逐行转仓证据，不要用总确认勾选代替'})
                continue
            grouped[pairing_id, leg['market'], leg['ticker']].append(leg)
        movements, movement_records = [], {}

        def mark(legs, status, reason, action):
            for leg in legs:
                record = leg['record']
                missing_cost = status == REVIEW and record['Original Purchase'] == '未找到原始买入'
                record.update(Status=BLOCKED if missing_cost else status,
                              Reason=reason + ('；缺失原始买入成本' if missing_cost else ''),
                              **{'Required Action': action})

        for (identifier, market, ticker), legs in grouped.items():
            if len(legs) != 2 or {l['record']['Direction'] for l in legs} != {'IN', 'OUT'}:
                status = REVIEW if len(legs) == 1 else INVALID
                mark(legs, status, '转入/转出未配对或存在多笔冲突，不能假定是完整非处置转仓',
                     '提供另一端记录；两端券商编号不同可用补充证据中的共同 Reconciliation ID 关联')
                continue
            outgoing = next(l for l in legs if l['record']['Direction'] == 'OUT')
            incoming = next(l for l in legs if l['record']['Direction'] == 'IN')
            if outgoing['quantity'] != incoming['quantity'] or outgoing['date'] > incoming['date']:
                mark(legs, INVALID, '转出/转入数量不匹配或转入日期早于转出', '核对两端数量、日期及是否存在拆股/分笔转仓')
                continue
            from_account, to_account = outgoing['account'], incoming['account']
            conflict = False
            for leg, other in ((outgoing, incoming), (incoming, outgoing)):
                r = leg['record']
                if ((r['Counterparty Broker'] and r['Counterparty Broker'].upper() != other['broker'].upper()) or
                    (r['Counterparty Account'] and r['Counterparty Account'] != other['account'].split(':', 1)[1])):
                    conflict = True
            if conflict or from_account == to_account:
                mark(legs, INVALID, '转仓对手账户证据冲突或来源/目标账户相同', '修正账户映射与转仓配对')
                continue
            owners = self.accounts.owners.get(from_account), self.accounts.owners.get(to_account)
            if 'YES' in {l['changed'] for l in legs} or (all(owners) and owners[0] != owners[1]):
                mark(legs, BLOCKED, '受益所有人发生变化，不能作为普通券商转仓豁免',
                     '单独核对处置/取得日期、对价/市场价值、成本及是否适用 rollover；本模块不自动估算')
                continue
            if not all(owners):
                mark(legs, REVIEW, '缺少账户受益所有人及其证据，所有权未验证',
                     '上传账户关系 CSV；Owner ID 需代表完整受益权关系（包括共同持有比例）')
                continue
            movement_id = hashlib.sha256(f'{identifier}|{market}|{ticker}|{from_account}|{to_account}'.encode()).hexdigest()[:24]
            movement = Transfer(movement_id, market, ticker, outgoing['quantity'], outgoing['date'],
                                incoming['date'], from_account, to_account,
                                tuple(l['record']['Record ID'] for l in legs))
            movements.append(movement)
            movement_records[movement_id] = legs
            for leg in legs:
                leg['record'].update({'Reason': '账户关系与两端记录已匹配，待核对原始批次库存',
                                      'Movement ID': movement_id,
                                      'Linked Record IDs': json.dumps(list(movement.record_ids)),
                                      'From Account': from_account, 'To Account': to_account,
                                      'From Owner': owners[0], 'To Owner': owners[1],
                                      'Ownership Evidence': self.accounts.evidence[from_account] + ' | ' + self.accounts.evidence[to_account]})

        ledger = calculate_ledger(self.transactions, start_year, movements)
        for record in self.audit:
            check = ledger.row_checks.get(record['Record ID'])
            if check:
                record['Ledger Status'] = check['Status']
                record['Ledger Issue'] = check.get('Issue', '')
        for movement_id, legs in movement_records.items():
            check = ledger.transfer_checks.get(movement_id, {})
            for leg in legs:
                r = leg['record']
                if not r['Blocks CGT'] and leg['date'] >= cutoff:
                    continue
                r['Original Purchase'] = check.get('original_purchase', '未验证')
                r['Cost Complete'] = '是' if check.get('cost_complete') else '未验证'
                if check.get('cost_complete') is False:
                    r['Cost Complete'] = '否'
                r['Lot Audit'] = json.dumps(check, ensure_ascii=False)
                if 'error' in check:
                    r.update(Status=BLOCKED, Reason=check['error'],
                             **{'Cost Complete': '否' if check.get('cost_complete') is False else '未验证',
                                'Required Action': '补齐历史买入、报表账户或日内顺序并核对数量后重试'})
                elif 'out' in check:
                    r.update(Status=CONFIRMED, Reason='按提供的受益所有人证据及两端数量核对通过；原始批次连续',
                             **{'Blocks CGT': False, 'CGT Impact': '普通转仓不产生处置；按 FIFO 移动原始批次，保留日期及调整后成本',
                                'Required Action': '无；请保留证据与批次分配记录'})
        # Never certify a later transfer using a ledger which skipped an earlier
        # unresolved movement or malformed trade for the same security.
        uncertain = defaultdict(list)
        for r in self.audit:
            if r['Blocks CGT'] and r['Status'] != CONFIRMED:
                try:
                    uncertain[r['Market'], r['Ticker']].append(trade_date(r['Date']))
                except CalculationError:
                    uncertain[r['Market'], r['Ticker']].append(trade_date('1900-01-01'))
        for movement in movements:
            earlier = uncertain.get((movement.market, movement.ticker), [])
            if earlier and min(earlier) <= movement.in_date:
                for leg in movement_records[movement.transfer_id]:
                    r = leg['record']
                    if r['Status'] == CONFIRMED and leg['date'] < cutoff:
                        r.update(Status=BLOCKED, Reason='该证券此前存在未核对或错误记录，批次连续性未验证',
                                 **{'Blocks CGT': True, 'Cost Complete': '未验证',
                                    'CGT Impact': '阻止最终金额；不能使用跳过未知转仓后的临时库存作为确认证据',
                                    'Required Action': '先解决该证券较早的未核对记录'})
        # Show the source check on duplicate audit rows, without applying it twice.
        originals = {r['Record ID']: r for r in self.transfer_audit}
        for record in self.transfer_audit:
            if 'Duplicate Of' in record:
                original = originals[record['Duplicate Of']]
                for key in ('Original Purchase', 'Cost Complete', 'Lot Audit', 'Movement ID', 'Linked Record IDs'):
                    record[key] = original.get(key, '未验证')
        return ledger

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
