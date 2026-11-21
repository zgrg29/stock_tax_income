"""Unit tests execute the actual FIFO and loss-offset nodes from app.py."""
import ast
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

TREE = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8-sig'))


def engine(rows):
    loop = next(node for node in ast.walk(TREE) if isinstance(node, ast.For)
                and isinstance(node.target, ast.Tuple)
                and [getattr(t, 'id', '') for t in node.target.elts] == ['idx', 'row'])
    helper = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'held_over_twelve_months')
    env = {'pd': pd, 'np': np, 'portfolio': {}, 'cgt_events': [], 'uncertain_assets': set(),
           'unresolved': [], 'fy_start': pd.Timestamp('2025-07-01'),
           'fy_exclusive_end': pd.Timestamp('2026-07-01'),
           'df_all': pd.DataFrame(rows).sort_values('Date', kind='stable')}
    exec(compile(ast.Module(body=[helper, loop], type_ignores=[]), 'app.py', 'exec'), env)
    return env


def tx(day, side, qty, price, fees=0, ticker='ABC', market='AU', **extra):
    return dict(Date=pd.Timestamp(day), Type=side, Quantity=qty, Price=price, Fees=fees,
                Ticker=ticker, Market=market, Source='synthetic', **extra)


class CalculationTests(unittest.TestCase):
    def test_partial_sale_buy_fees_sum_to_original_fee(self):
        env = engine([tx('2025-07-01', 'BUY', 100, 10, 10),
                      tx('2025-08-01', 'SELL', 50, 12), tx('2025-09-01', 'SELL', 50, 12)])
        self.assertAlmostEqual(sum(e['Net Gain/Loss'] for e in env['cgt_events']), 190)

    def test_fractional_sale_residue_does_not_erase_valid_stock(self):
        env = engine([tx('2025-07-01', 'BUY', 0.3, 10),
                      tx('2025-08-01', 'SELL', 0.1, 12), tx('2025-09-01', 'SELL', 0.2, 12)])
        self.assertFalse(env['uncertain_assets'])
        self.assertAlmostEqual(sum(e['Net Gain/Loss'] for e in env['cgt_events']), 0.6)

    def test_june_30_time_included_and_next_year_excluded(self):
        env = engine([tx('2025-07-01', 'BUY', 2, 10), tx('2026-06-30 15:00', 'SELL', 1, 12),
                      tx('2026-07-01', 'SELL', 1, 12)])
        self.assertEqual(len(env['cgt_events']), 1)
        self.assertEqual(env['cgt_events'][0]['Sell Date'], '2026-06-30')

    def test_calendar_anniversary_boundary(self):
        for sold, discounted in [('2025-07-01', False), ('2025-07-02', True)]:
            env = engine([tx('2024-07-01', 'BUY', 1, 10), tx(sold, 'SELL', 1, 12)])
            self.assertEqual('Discountable' in env['cgt_events'][0]['Category'], discounted)

    def test_market_codes_never_share_inventory(self):
        env = engine([tx('2025-07-01', 'BUY', 1, 10), tx('2025-08-01', 'SELL', 1, 20, market='US')])
        self.assertIn(('US', 'ABC'), env['uncertain_assets'])
        self.assertFalse(env['cgt_events'])

    def test_transfer_check_never_adds_or_removes_lots(self):
        env = engine([tx('2024-01-01', 'BUY', 100, 10, 10),
                      tx('2025-07-01', 'TRANSFER_CHECK', 50, 0),
                      tx('2025-07-02', 'TRANSFER_CHECK', 50, 0),
                      tx('2025-08-01', 'SELL', 100, 30, 10)])
        self.assertAlmostEqual(env['cgt_events'][0]['Net Gain/Loss'], 1980)
        self.assertEqual(env['cgt_events'][0]['Buy Date'], '2024-01-01')
        self.assertFalse(env['unresolved'])

    def test_explicit_capital_return_reduces_cost_and_excess_creates_gain(self):
        env = engine([tx('2024-01-01', 'BUY', 10, 1, 10),
                      tx('2025-07-01', 'RETURN_OF_CAPITAL', 10, 999, **{'ROC Per Share': 3}),
                      tx('2025-08-01', 'SELL', 10, 5)])
        self.assertEqual([e['Net Gain/Loss'] for e in env['cgt_events']], [10, 50])

    def test_losses_offset_non_discount_gains_first(self):
        start = next(n for n in ast.walk(TREE) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'total_losses_to_offset' for t in n.targets))
        body = next(body for n in ast.walk(TREE) for body in (getattr(n, 'body', None), getattr(n, 'orelse', None))
                    if isinstance(body, list) and start in body)
        nodes = body[body.index(start):]
        end = next(i for i,n in enumerate(nodes) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'carried_loss' for t in n.targets))
        env = dict(all_losses=1000, opening_loss=0, st_gains=1000, lt_gains=1000)
        exec(compile(ast.Module(body=nodes[:end+1], type_ignores=[]), 'app.py', 'exec'), env)
        self.assertEqual(env['taxable_gain'], 500)


if __name__ == '__main__':
    unittest.main()
