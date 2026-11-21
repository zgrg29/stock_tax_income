"""FIFO capital gains, with all amounts denominated in AUD."""
from collections import defaultdict, deque
from dataclasses import dataclass
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


def calculate_events(transactions, start_year):
    start, end = date(start_year, 7, 1), date(start_year + 1, 7, 1)
    portfolio = defaultdict(deque)
    events = []
    for tx in sorted(transactions, key=lambda t: t.date):
        if tx.date >= end:
            continue
        key = tx.market, tx.ticker
        lots = portfolio[key]
        context = f'{tx.source} {tx.reference} {tx.date} {tx.market}:{tx.ticker}'
        if tx.date < date(1999, 9, 21):
            raise CalculationError(f'{context}: 1999-09-21 以前的资产需单独处理指数化/豁免规则')
        if tx.action == 'BUY':
            lots.append(Lot(tx.date, tx.quantity, tx.price + tx.fees / tx.quantity, tx.source))
            continue
        holding = sum((lot.quantity for lot in lots), ZERO)
        if holding < tx.quantity:
            raise CalculationError(f'{context}: 缺失历史买入成本，需 {tx.quantity} 股，库存仅 {holding} 股')
        if tx.action == 'RETURN_OF_CAPITAL':
            if holding != tx.quantity:
                raise CalculationError(f'{context}: 资本返还数量须与当日全部持仓 {holding} 一致')
            for lot in lots:
                excess = max(ZERO, tx.price - lot.unit_cost)
                lot.unit_cost = max(ZERO, lot.unit_cost - tx.price)
                if excess and start <= tx.date < end:
                    events.append(Event(*key, lot.date, tx.date, lot.quantity,
                                        excess * lot.quantity, discount_eligible(lot.date, tx.date),
                                        lot.source, tx.source, 'RETURN_OF_CAPITAL'))
            continue
        remaining = tx.quantity
        unit_proceeds = tx.price - tx.fees / tx.quantity
        while remaining:
            lot = lots[0]
            matched = min(remaining, lot.quantity)
            if start <= tx.date < end:
                events.append(Event(*key, lot.date, tx.date, matched,
                                    (unit_proceeds - lot.unit_cost) * matched,
                                    discount_eligible(lot.date, tx.date), lot.source, tx.source))
            remaining -= matched
            lot.quantity -= matched
            if not lot.quantity:
                lots.popleft()
    return events


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
