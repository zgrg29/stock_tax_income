import unittest
from datetime import date, datetime
from decimal import Decimal as D

from cgt import CalculationError, Transaction, calculate_events, discount_eligible, summarize


def tx(day, action, qty, price, fees=0, market='AU', source='broker'):
    return Transaction(day, market, 'TEST', action, qty, price, fees, source)


class CalculationTests(unittest.TestCase):
    def test_partial_sales_do_not_reuse_buy_fees(self):
        events = calculate_events([tx('2025-07-01', 'BUY', 100, 10, 10),
                                   tx('2025-08-01', 'SELL', 50, 12),
                                   tx('2025-09-01', 'SELL', 50, 12)], 2025)
        self.assertEqual([e.gain for e in events], [D(95), D(95)])

    def test_losses_offset_non_discount_gains_first(self):
        events = calculate_events([tx('2023-01-01', 'BUY', 100, 10),
                                   tx('2025-07-01', 'SELL', 100, 20),
                                   tx('2025-08-01', 'BUY', 100, 10),
                                   tx('2025-09-01', 'SELL', 100, 20),
                                   tx('2025-10-01', 'BUY', 100, 20),
                                   tx('2025-11-01', 'SELL', 100, 10)], 2025)
        self.assertEqual(summarize(events)['taxable_gain'], D(500))

    def test_missing_history_including_prior_year_blocks_result(self):
        for day in ('2024-08-01', '2025-08-01'):
            with self.assertRaisesRegex(CalculationError, '历史买入'):
                calculate_events([tx(day, 'SELL', 10, 12)], 2025)

    def test_june_30_included_july_1_excluded(self):
        events = calculate_events([tx('2025-06-01', 'BUY', 3, 10),
                                   tx('2025-06-30', 'SELL', 1, 11),
                                   tx(datetime(2026, 6, 30, 15), 'SELL', 1, 12),
                                   tx('2026-07-01', 'SELL', 1, 13)], 2025)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].gain, D(2))

    def test_discount_anniversary_and_leap_year(self):
        self.assertFalse(discount_eligible(date(2024, 7, 1), date(2025, 7, 1)))
        self.assertTrue(discount_eligible(date(2024, 7, 1), date(2025, 7, 2)))
        self.assertFalse(discount_eligible(date(2023, 7, 1), date(2024, 6, 30)))
        self.assertFalse(discount_eligible(date(2024, 2, 29), date(2025, 2, 28)))
        self.assertTrue(discount_eligible(date(2024, 2, 29), date(2025, 3, 1)))

    def test_market_separation_and_cross_broker_matching(self):
        with self.assertRaises(CalculationError):
            calculate_events([tx('2025-07-01', 'BUY', 1, 10, market='AU'),
                              tx('2025-08-01', 'SELL', 1, 20, market='US')], 2025)
        events = calculate_events([tx('2025-07-01', 'BUY', 1, 10, source='Nabtrade'),
                                   tx('2025-08-01', 'SELL', 1, 20, source='Stake')], 2025)
        self.assertEqual(events[0].gain, D(10))

    def test_prior_sales_consume_inventory(self):
        events = calculate_events([tx('2024-01-01', 'BUY', 10, 10),
                                   tx('2024-08-01', 'SELL', 10, 20),
                                   tx('2025-08-01', 'BUY', 10, 30),
                                   tx('2025-09-01', 'SELL', 10, 40)], 2025)
        self.assertEqual(events[0].gain, D(100))
        self.assertFalse(events[0].discountable)

    def test_sell_fee_distributed_across_lots(self):
        events = calculate_events([tx('2025-07-01', 'BUY', 10, 10),
                                   tx('2025-07-02', 'BUY', 10, 20),
                                   tx('2025-08-01', 'SELL', 20, 30, 10)], 2025)
        self.assertEqual(sum(e.gain for e in events), D(290))

    def test_opening_loss_and_no_events(self):
        self.assertEqual(summarize([], 500)['carried_loss'], D(500))
        events = calculate_events([tx('2025-07-01', 'BUY', 10, 10),
                                   tx('2025-08-01', 'SELL', 10, 30)], 2025)
        result = summarize(events, 250)
        self.assertEqual(result['taxable_gain'], 0)
        self.assertEqual(result['opening_loss_used'], D(200))
        self.assertEqual(result['carried_loss'], D(50))

    def test_current_loss_before_opening_loss(self):
        events = calculate_events([tx('2025-07-01', 'BUY', 10, 10),
                                   tx('2025-08-01', 'SELL', 10, 5)], 2025)
        result = summarize(events, 100)
        self.assertEqual(result['opening_loss_used'], 0)
        self.assertEqual(result['carried_loss'], D(150))

    def test_no_discount_option(self):
        events = calculate_events([tx('2023-01-01', 'BUY', 10, 10),
                                   tx('2025-08-01', 'SELL', 10, 30)], 2025)
        self.assertEqual(summarize(events, discount_rate=0)['taxable_gain'], D(200))

    def test_capital_return_excess_and_remaining_cost(self):
        events = calculate_events([tx('2024-01-01', 'BUY', 10, 1, 10),
                                   tx('2025-08-01', 'RETURN_OF_CAPITAL', 10, 3),
                                   tx('2025-09-01', 'SELL', 10, 5)], 2025)
        self.assertEqual([e.gain for e in events], [D(10), D(50)])
        self.assertEqual(events[0].kind, 'RETURN_OF_CAPITAL')

    def test_capital_return_reduces_cost_without_immediate_gain(self):
        events = calculate_events([tx('2024-01-01', 'BUY', 10, 10),
                                   tx('2025-08-01', 'RETURN_OF_CAPITAL', 10, 2),
                                   tx('2025-09-01', 'SELL', 10, 12)], 2025)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].gain, D(40))

    def test_invalid_numbers_fail(self):
        for qty in (0, -1, 'NaN', 'Infinity'):
            with self.assertRaises(CalculationError):
                tx('2025-07-01', 'BUY', qty, 10)
        with self.assertRaises(CalculationError):
            summarize([], 'NaN')

    def test_fractional_quantities(self):
        events = calculate_events([tx('2025-07-01', 'BUY', '0.3', 10, '0.03'),
                                   tx('2025-08-01', 'SELL', '0.1', 12),
                                   tx('2025-09-01', 'SELL', '0.2', 12)], 2025)
        self.assertEqual(sum(e.gain for e in events), D('0.57'))


if __name__ == '__main__':
    unittest.main()
