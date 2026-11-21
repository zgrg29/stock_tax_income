import unittest
from datetime import date
from decimal import Decimal as D
from io import BytesIO, StringIO

import pandas as pd

from cgt import CalculationError, calculate_events, summarize
from importers import BrokerImporter, load_rates


def stake_row(**changes):
    row = {'Trade Date': '2025-07-01', 'Symbol': 'ABC', 'Side': 'BUY',
           'Units': 100, 'Avg. Price': 10, 'Fees': 10, 'GST': 0,
           'Trade Identifier': float('nan')}
    row.update(changes)
    return row


class ImportTests(unittest.TestCase):
    def test_blank_ids_preserve_identical_real_executions(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(), stake_row()]), 'Stake', 'AU', 'one')
        self.assertFalse(importer.errors)
        self.assertEqual(len(importer.transactions), 2)

    def test_blank_fees_are_zero(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(Fees=float('nan'), GST=None)]), 'Stake', 'AU', 'one')
        self.assertFalse(importer.errors)
        self.assertEqual(importer.transactions[0].fees, 0)

    def test_missing_fee_column_is_not_silently_zero(self):
        frame = pd.DataFrame([stake_row()]).drop(columns='Fees')
        importer = BrokerImporter()
        importer.add_frame(frame, 'Stake', 'AU', 'one')
        self.assertEqual(len(importer.errors), 1)

    def test_bad_row_reports_error_but_inspects_later_rows(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(Units='bad'), stake_row()]), 'Stake', 'AU', 'one')
        self.assertEqual(len(importer.errors), 1)
        self.assertEqual(len(importer.transactions), 1)
        self.assertIn('第 2 行', importer.errors[0])

    def test_identified_overlap_is_removed(self):
        importer = BrokerImporter()
        frame = pd.DataFrame([stake_row(**{'Trade Identifier': 'ID-1'})])
        importer.add_frame(frame, 'Stake', 'AU', 'one')
        importer.add_frame(frame, 'Stake', 'AU', 'two')
        self.assertFalse(importer.errors)
        self.assertEqual(len(importer.transactions), 1)

    def test_conflicting_identifier_is_error(self):
        importer = BrokerImporter()
        for source, price in [('one', 10), ('two', 11)]:
            importer.add_frame(pd.DataFrame([stake_row(**{'Trade Identifier': 'ID-1', 'Avg. Price': price})]), 'Stake', 'AU', source)
        self.assertEqual(len(importer.errors), 1)

    def test_unknown_overlap_is_blocked_instead_of_guessing(self):
        importer = BrokerImporter()
        for source in ('one', 'two'):
            importer.add_frame(pd.DataFrame([stake_row()]), 'Stake', 'AU', source)
        self.assertEqual(len(importer.errors), 1)

    def test_market_scopes_identifiers(self):
        importer = BrokerImporter({(date(2025, 7, 1), 'USD'): D('1.5')})
        for market in ('AU', 'US'):
            importer.add_frame(pd.DataFrame([stake_row(**{'Trade Identifier': '1'})]), 'Stake', market, market)
        self.assertFalse(importer.errors)
        self.assertEqual(len(importer.transactions), 2)

    def test_fx_applied_to_buy_and_sell_separately(self):
        rates = load_rates(StringIO('Date,Currency,AUD per unit\n2025-07-01,USD,1.5\n2025-08-01,USD,1.6\n'))
        importer = BrokerImporter(rates)
        importer.add_frame(pd.DataFrame([stake_row(Fees=0), stake_row(**{'Trade Date': '2025-08-01', 'Side': 'SELL', 'Avg. Price': 10, 'Fees': 0})]), 'Stake', 'US', 'one')
        self.assertFalse(importer.errors)
        self.assertEqual(summarize(calculate_events(importer.transactions, 2025))['taxable_gain'], D(100))

    def test_missing_fx_is_error(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row()]), 'Stake', 'US', 'one')
        self.assertEqual(len(importer.errors), 1)
        self.assertFalse(importer.transactions)

    def test_rate_conflict_is_error(self):
        with self.assertRaises(CalculationError):
            load_rates(StringIO('Date,Currency,AUD per unit\n2025-07-01,USD,1.5\n2025-07-01,USD,1.6\n'))

    def test_negative_disposal_quantity(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(Side='SELL', Units=-10)]), 'Stake', 'AU', 'one')
        self.assertFalse(importer.errors)
        self.assertEqual(importer.transactions[0].quantity, D(10))

    def test_unsupported_action_and_ambiguous_roc_are_errors(self):
        for action in ('SPLIT', 'ROC'):
            importer = BrokerImporter()
            importer.add_frame(pd.DataFrame([stake_row(Side=action)]), 'Stake', 'AU', 'one')
            self.assertEqual(len(importer.errors), 1)

    def test_same_day_mixed_sides_block(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(), stake_row(Side='SELL')]), 'Stake', 'AU', 'one')
        self.assertTrue(importer.ordering_errors())

    def test_same_day_different_buy_costs_need_sequence(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(), stake_row(**{'Avg. Price': 11})]), 'Stake', 'AU', 'one')
        self.assertTrue(importer.ordering_errors())

    def test_nab_sheet_whitespace_and_duplicate_file(self):
        data = BytesIO()
        frame = pd.DataFrame([{'Date': '2024-01-01', 'Code': 'AAPL', 'Movement Type': 'BUY',
                               'Quantity': 1, 'Average Price (AUD) *1': 100,
                               'Brokerage (AUD)': None, 'Other Fees (AUD)': None}])
        with pd.ExcelWriter(data, engine='openpyxl') as writer:
            frame.to_excel(writer, sheet_name='Int.  Portfolio Transactions', startrow=1, index=False)
        data.name = 'nab.xlsx'
        importer = BrokerImporter()
        importer.add_file(data, 'Nabtrade')
        importer.add_file(data, 'Nabtrade')
        self.assertFalse(importer.errors)
        self.assertEqual(len(importer.transactions), 1)

    def test_nab_does_not_fallback_to_unconverted_foreign_price(self):
        importer = BrokerImporter()
        frame = pd.DataFrame([{'Date': '2024-01-01', 'Code': 'AAPL', 'Movement Type': 'BUY',
                               'Quantity': 1, 'Transaction Price (Exch CCY)': 100,
                               'Brokerage (AUD)': 0, 'Other Fees (AUD)': 0}])
        importer.add_frame(frame, 'Nabtrade', 'US', 'one')
        self.assertTrue(importer.errors)

    def test_non_iso_strings_use_australian_day_first(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([stake_row(**{'Trade Date': '01/08/2025'})]), 'Stake', 'AU', 'one')
        self.assertFalse(importer.errors)
        self.assertEqual(importer.transactions[0].date, date(2025, 8, 1))


if __name__ == '__main__':
    unittest.main()
