"""FIFO capital gains, with all amounts denominated in AUD."""
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ZERO = Decimal('0')


class CalculationError(ValueError):
    """Inputs cannot produce a complete calculation."""


def number(value, label, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CalculationError(f'{label} 必须为有效数字') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise CalculationError(f'{label} 必须为有限的{"正数" if positive else "非负数"}')
    return result


def trade_date(value):
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            raise CalculationError('带时区日期需先核对为券商成交日期，不能直接删除时区')
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return trade_date(datetime.fromisoformat(str(value).strip()))
    except (ValueError, TypeError):
        raise CalculationError(f'无效成交日期: {value}；请使用 YYYY-MM-DD') from None


def discount_eligible(bought, sold):
    # Exclude acquisition and disposal days; use calendar years, not 365 days.
    try:
        anniversary = bought.replace(year=bought.year + 1)
    except ValueError:
        anniversary = date(bought.year + 1, 2, 28)
    return sold > anniversary


@dataclass(frozen=True)
class Transaction:
    date: date
    market: str
    ticker: str
    action: str
    quantity: Decimal
    price: Decimal
    fees: Decimal = ZERO
    source: str = ''
    reference: str = ''
    account: str = ''
    record_id: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'date', trade_date(self.date))
        for name in ('market', 'ticker', 'action'):
            value = str(getattr(self, name)).strip().upper()
            if not value:
                raise CalculationError(f'{name} 不能为空')
            object.__setattr__(self, name, value)
        if self.action not in ('BUY', 'SELL', 'RETURN_OF_CAPITAL'):
            raise CalculationError(f'不支持的交易类型: {self.action}')
        object.__setattr__(self, 'quantity', number(self.quantity, '数量', positive=True))
        object.__setattr__(self, 'price', number(self.price, 'AUD 每股金额'))
        object.__setattr__(self, 'fees', number(self.fees, 'AUD 手续费'))
        if self.action == 'RETURN_OF_CAPITAL' and self.fees:
            raise CalculationError('资本返还暂不支持附带手续费')


@dataclass
class Lot:
    date: date
    quantity: Decimal
    unit_cost: Decimal
    source: str
    account: str = ''
    record_id: str = ''
    transfers: tuple = ()
    adjustments: tuple = ()


@dataclass(frozen=True)
class Transfer:
    """A reconciled pair of custody legs, with ownership evidence checked upstream."""
    transfer_id: str
    market: str
    ticker: str
    quantity: Decimal
    out_date: date
    in_date: date
    from_account: str
    to_account: str
    record_ids: tuple

    def __post_init__(self):
        object.__setattr__(self, 'quantity', number(self.quantity, '转仓数量', positive=True))
        object.__setattr__(self, 'out_date', trade_date(self.out_date))
        object.__setattr__(self, 'in_date', trade_date(self.in_date))
        if self.in_date < self.out_date or not self.from_account or not self.to_account:
            raise CalculationError('转仓日期或账户无效')
        if self.from_account == self.to_account:
            raise CalculationError('转仓来源和目标账户相同')


@dataclass
class Ledger:
    events: list = field(default_factory=list)
    lots: list = field(default_factory=list)
    transfer_checks: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    row_checks: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    market: str
    ticker: str
    buy_date: date
    sell_date: date
    quantity: Decimal
    gain: Decimal
    discountable: bool
    buy_platform: str
    sell_platform: str
    kind: str = 'SELL'
    buy_record_id: str = ''
    sell_record_id: str = ''
    transfer_chain: tuple = ()
    cost_adjustments: tuple = ()


def calculate_ledger(transactions, start_year, transfers=()):
    """One FIFO engine for trades and verified custody movements.

    Transfer assets require identified custody accounts. In-transit lots remain
    owned but are unavailable for sale. Any error invalidates the final result.
    """
    start, end = date(start_year, 7, 1), date(start_year + 1, 7, 1)
    transactions = list(transactions)
    transfers = [t for t in transfers if t.out_date < end]
    strict_assets = {(t.market, t.ticker) for t in transfers}
    ledger = Ledger()
    portfolio = defaultdict(list)
    tainted = set()
    timeline = [(t.date, 1, t) for t in transactions if t.date < end]
    days = defaultdict(list)
    for movement in transfers:
        timeline.extend([(movement.out_date, 0, movement), (movement.in_date, 2, movement)])
        for day in {movement.out_date, movement.in_date}:
            days[day, movement.market, movement.ticker].append(movement.transfer_id)
    ambiguous = {key for key, ids in days.items() if len(set(ids)) > 1}
    for tx in transactions:
        key = tx.date, tx.market, tx.ticker
        if key in days:
            ambiguous.add(key)
    trade_days = defaultdict(list)
    for tx in transactions:
        trade_days[tx.date, tx.market, tx.ticker].append(tx)
    for key, rows in trade_days.items():
        costs = {r.price + r.fees / r.quantity for r in rows if r.action == 'BUY'}
        if len({r.action for r in rows}) > 1 or len(costs) > 1:
            ambiguous.add(key)

    def snapshot(lots):
        return [dict(buy_date=str(l.date), quantity=str(l.quantity), unit_cost=str(l.unit_cost),
                     account=l.account, buy_record_id=l.record_id, transfers=list(l.transfers),
                     cost_adjustments=list(l.adjustments))
                for l in lots if l.quantity]

    for day, phase, item in sorted(timeline, key=lambda entry: (entry[0], entry[1])):
        if day >= end:
            continue
        key = item.market, item.ticker
        lots = portfolio[key]
        is_transfer = isinstance(item, Transfer)
        check = ledger.transfer_checks.setdefault(item.transfer_id, {}) if is_transfer else None
        if check is not None:
            check['record_ids'] = list(item.record_ids)
        elif item.record_id:
            ledger.row_checks[item.record_id] = {'Status': '已验证'}
        try:
            if (day, *key) in ambiguous:
                raise CalculationError(f'{day} {key}: 同日存在不同成本/类型或转仓及其他动作，缺少可靠日内顺序')
            if key in tainted:
                raise CalculationError(f'{day} {key}: 此证券此前数据错误，后续库存及成本无法验证')
            if is_transfer:
                transit = f'IN_TRANSIT:{item.transfer_id}'
                account = item.from_account if phase == 0 else transit
                eligible = sorted((l for l in lots if l.account == account and l.quantity), key=lambda l: l.date)
                holding = sum((l.quantity for l in eligible), ZERO)
                if holding < item.quantity:
                    check['original_purchase'] = '缺失或数量不足'
                    check['cost_complete'] = False
                    raise CalculationError(f'{day} {key}: 转仓数量 {item.quantity} 超过来源账户已知持仓 {holding}；补齐原始买入/转仓记录')
                before = snapshot(lots)
                remaining = item.quantity
                allocations = []
                for lot in eligible:
                    if not remaining:
                        break
                    matched = min(remaining, lot.quantity)
                    moved = replace(lot, quantity=matched,
                                    account=transit if phase == 0 else item.to_account,
                                    transfers=lot.transfers + ((item.transfer_id,) if phase == 0 else ()))
                    lots.append(moved)
                    lot.quantity -= matched
                    remaining -= matched
                    allocations.extend(snapshot([moved]))
                lots[:] = [l for l in lots if l.quantity]
                check.update(original_purchase='存在', cost_complete=True)
                check['out' if phase == 0 else 'in'] = dict(before=before, after=snapshot(lots), allocations=allocations)
                continue
            tx = item
            context = f'{tx.source} {tx.reference} {day} {tx.market}:{tx.ticker}'
            if day < date(1999, 9, 21):
                raise CalculationError(f'{context}: 1999-09-21 以前的资产需单独处理指数化/豁免规则')
            if key in strict_assets and not tx.account:
                raise CalculationError(f'{context}: 转仓证券的交易缺少账户标识，不能猜测归属')
            if tx.action == 'BUY':
                lots.append(Lot(day, tx.quantity, tx.price + tx.fees / tx.quantity,
                                tx.source, tx.account, tx.record_id))
                continue
            eligible = sorted((l for l in lots if l.quantity and
                               (key not in strict_assets or l.account == tx.account)), key=lambda l: l.date)
            holding = sum((l.quantity for l in eligible), ZERO)
            if tx.action != 'RETURN_OF_CAPITAL' and holding < tx.quantity:
                raise CalculationError(f'{context}: 缺失历史买入成本或账户库存不足，需 {tx.quantity} 股，库存仅 {holding} 股')
            if tx.action == 'RETURN_OF_CAPITAL':
                # A distribution applies to the owner's entire relevant holding,
                # not just whichever account reported the corporate action.
                eligible = [l for l in lots if l.quantity]
                holding = sum((l.quantity for l in eligible), ZERO)
                if holding != tx.quantity:
                    raise CalculationError(f'{context}: 资本返还数量须与当日全部持仓 {holding} 一致')
                for lot in eligible:
                    excess = max(ZERO, tx.price - lot.unit_cost)
                    lot.unit_cost = max(ZERO, lot.unit_cost - tx.price)
                    lot.adjustments += (tx.record_id or f'{tx.source}:{tx.reference}:{day}',)
                    if excess and start <= day < end:
                        ledger.events.append(Event(*key, lot.date, day, lot.quantity, excess * lot.quantity,
                                                   discount_eligible(lot.date, day), lot.source, tx.source,
                                                   'RETURN_OF_CAPITAL', lot.record_id, tx.record_id, lot.transfers, lot.adjustments))
                continue
            remaining = tx.quantity
            unit_proceeds = tx.price - tx.fees / tx.quantity
            for lot in eligible:
                if not remaining:
                    break
                matched = min(remaining, lot.quantity)
                if start <= day < end:
                    ledger.events.append(Event(*key, lot.date, day, matched,
                                               (unit_proceeds - lot.unit_cost) * matched,
                                               discount_eligible(lot.date, day), lot.source, tx.source,
                                               'SELL', lot.record_id, tx.record_id, lot.transfers, lot.adjustments))
                remaining -= matched
                lot.quantity -= matched
            lots[:] = [l for l in lots if l.quantity]
        except CalculationError as exc:
            ledger.errors.append(str(exc))
            tainted.add(key)
            if check is not None:
                check['error'] = str(exc)
            elif item.record_id:
                ledger.row_checks[item.record_id] = {'Status': '计算受阻', 'Issue': str(exc)}
    ledger.lots = [dict(market=key[0], ticker=key[1], verified=key not in tainted, **row)
                   for key, lots in portfolio.items() for row in snapshot(lots)]
    return ledger


def calculate_events(transactions, start_year):
    ledger = calculate_ledger(transactions, start_year)
    if ledger.errors:
        raise CalculationError('\n'.join(ledger.errors))
    return ledger.events


def summarize(events, opening_loss=ZERO, discount_rate=Decimal('0.5')):
    opening_loss = number(opening_loss, '期初尚未使用资本亏损')
    discount_rate = number(discount_rate, '折扣比例')
    if discount_rate not in (ZERO, Decimal('0.5')):
        raise CalculationError('仅支持不折扣或符合条件的个人 50% 折扣')
    short_gains = sum((e.gain for e in events if e.gain > 0 and not e.discountable), ZERO)
    long_gains = sum((e.gain for e in events if e.gain > 0 and e.discountable), ZERO)
    losses = -sum((e.gain for e in events if e.gain < 0), ZERO)
    remaining_short, remaining_long = short_gains, long_gains
    unused = []
    # Apply current losses before unapplied prior losses; non-discount gains first.
    for available in (losses, opening_loss):
        offset = min(available, remaining_short)
        remaining_short -= offset
        available -= offset
        offset = min(available, remaining_long)
        remaining_long -= offset
        unused.append(available - offset)
    discount = remaining_long * discount_rate
    return dict(gross_gains=short_gains + long_gains, current_losses=losses,
                opening_loss=opening_loss, opening_loss_used=opening_loss - unused[1],
                discount=discount, taxable_gain=remaining_short + remaining_long - discount,
                carried_loss=sum(unused, ZERO))
