"""Compatibility tests against the original broker report import path in app.py."""
import unittest
import test_app as fixtures


class ImportTests(unittest.TestCase):
    def test_blank_identifiers_preserve_distinct_real_rows(self):
        sales = [fixtures.stake_row(qty=50), fixtures.stake_row(qty=50)]
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'),
                                  [fixtures.workbook(sales)])
        self.assertEqual(fixtures.gains(at), '$190.00')

    def test_repeated_stake_file_does_not_double_inventory(self):
        sale = fixtures.workbook([fixtures.stake_row()], name='sales.xlsx')
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'), [sale, sale])
        self.assertFalse(at.warning)
        self.assertEqual(fixtures.gains(at), '$190.00')

    def test_identical_fills_in_overlapping_reports_keep_multiplicity(self):
        rows = [fixtures.stake_row(qty=50), fixtures.stake_row(qty=50)]
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'),
                                  [fixtures.workbook(rows, name='a.xlsx'), fixtures.workbook(rows, name='b.xlsx')])
        self.assertEqual(fixtures.gains(at), '$190.00')

    def test_international_sheet_accepts_both_spacing_variants(self):
        for sheet in ('Int. Portfolio Transactions', 'Int.  Portfolio Transactions'):
            rows = [dict(fixtures.nab_row(code='AAPL'), **{'Average Price (AUD) *1': 10,
                                                          'Brokerage (AUD)': 10}),
                    dict(fixtures.nab_row(day='2025-08-01', side='SELL', code='AAPL'),
                         **{'Average Price (AUD) *1': 12, 'Brokerage (AUD)': 0})]
            at = fixtures.run_reports(fixtures.workbook(rows, 'Nabtrade', sheet=sheet))
            self.assertEqual(fixtures.gains(at), '$190.00')

    def test_missing_foreign_currency_conversion_does_not_hide_domestic_result(self):
        us = fixtures.workbook([fixtures.stake_row(code='AAPL')], sheet='Wall St Equities', name='us.xlsx')
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'),
                                  [fixtures.workbook([fixtures.stake_row()]), us])
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertTrue(any('非最终报税金额' in x.value for x in at.subheader))
        self.assertTrue(any('AUD' in x.value for x in at.markdown))

    def test_unsupported_transfer_sale_is_not_silently_exempt(self):
        rows = [fixtures.nab_row(), fixtures.nab_row(day='2025-07-10', side='TRANSFER SALE', fee=0)]
        at = fixtures.run_reports(fixtures.workbook(rows, 'Nabtrade'), [fixtures.workbook([fixtures.stake_row()])])
        self.assertFalse(any('净资本利得' in x.value for x in at.success))
        self.assertTrue(any('不能确定为普通转仓' in x.value for x in at.markdown))


if __name__ == '__main__':
    unittest.main()
