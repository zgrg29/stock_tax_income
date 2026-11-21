"""Compatibility tests against the original broker report import path in app.py."""
import unittest
import test_app as fixtures


class ImportTests(unittest.TestCase):
    def test_axw_cxa_suffixes_and_underscore_transfer_match_stake(self):
        for suffix in ('.AXW', '.CXA'):
            rows = [fixtures.nab_row(day='2024-01-01', code='FUND'+suffix),
                    fixtures.nab_row(day='2025-07-01', side='TRANSFER_OUT', code='FUND'+suffix, fee=0)]
            at = fixtures.run_reports(fixtures.workbook(rows, 'Nabtrade'),
                                      [fixtures.workbook([fixtures.stake_row(code='FUND')])], same_owner=True)
            self.assertEqual(fixtures.gains(at), '$190.00')
            self.assertEqual(fixtures.sales_table(at).iloc[0]['处理结果'], '已计算')
            self.assertFalse(at.warning)

    def test_drp_is_not_reported_as_missing_buy(self):
        rows = [fixtures.nab_row(day='2024-01-01', side='DRP - Dividend Plan Allotment',
                                 qty=10, price=7.1234, fee=0, Value=71.23)]
        at = fixtures.run_reports(fixtures.workbook(rows, 'Nabtrade'),
                                  [fixtures.workbook([fixtures.stake_row(qty=10, price=9)])])
        self.assertEqual(fixtures.gains(at), '$18.77')
        self.assertEqual(fixtures.sales_table(at).iloc[0]['处理结果'], '已计算')
        self.assertFalse(at.warning)
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
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertTrue(any('非最终报税金额' in x.value for x in at.subheader))
        self.assertTrue(any('不能确定为普通转仓' in x.value for x in at.markdown))

    def test_bad_row_does_not_drop_later_sales_in_same_report(self):
        rows = [fixtures.stake_row(qty=50), fixtures.stake_row(day='2025-08-15', qty='bad'),
                fixtures.stake_row(day='2025-09-01', qty=50)]
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'), [fixtures.workbook(rows)])
        self.assertEqual(fixtures.gains(at), '$190.00')
        table = fixtures.sales_table(at)
        self.assertEqual(len(table), 3)
        self.assertTrue(any('解析失败' in status for status in table['处理结果']))

    def test_sale_and_purchase_aliases_use_same_fifo(self):
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row(side='Purchase')], 'Nabtrade'),
                                  [fixtures.workbook([fixtures.stake_row(side='Sale')])])
        self.assertEqual(fixtures.gains(at), '$190.00')

    def test_day_first_july_sale_belongs_to_selected_fy(self):
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row(day='2024-01-01')], 'Nabtrade'),
                                  [fixtures.workbook([fixtures.stake_row(day='01/07/2025')])])
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertEqual(fixtures.sales_table(at).iloc[0]['卖出日期'], fixtures.pd.Timestamp('2025-07-01'))

    def test_us_string_date_keeps_original_month_first_meaning(self):
        rows = [fixtures.stake_row(day='01/01/2024', side='BUY', price=10, fee=10, code='AAPL', Currency='AUD'),
                fixtures.stake_row(day='07/01/2025', price=12, fee=0, code='AAPL', Currency='AUD')]
        at = fixtures.run_reports(stake=[fixtures.workbook(rows, sheet='Wall St Equities')])
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertEqual(fixtures.sales_table(at).iloc[0]['卖出日期'], fixtures.pd.Timestamp('2025-07-01'))

    def test_same_id_different_sales_are_not_deleted(self):
        rows = [fixtures.stake_row(qty=50, **{'Trade Identifier': 'order-1'}),
                fixtures.stake_row(day='2025-09-01', qty=50, **{'Trade Identifier': 'order-1'})]
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'), [fixtures.workbook(rows)])
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertTrue(all(fixtures.sales_table(at)['处理结果'] != '重复记录'))

    def test_same_file_identical_id_rows_remain_visible_as_possible_split_fills(self):
        row = fixtures.stake_row(qty=50, **{'Trade Identifier': 'order-1'})
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row()], 'Nabtrade'), [fixtures.workbook([row, row])])
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertTrue(all(fixtures.sales_table(at)['处理结果'] != '重复记录'))

    def test_exchange_suffixes_match_across_brokers(self):
        at = fixtures.run_reports(fixtures.workbook([fixtures.nab_row(code='ASX:ABC')], 'Nabtrade'),
                                  [fixtures.workbook([fixtures.stake_row(code='ABC.ASX')])])
        self.assertEqual(fixtures.gains(at), '$190.00')

    def test_unconverted_foreign_sale_is_visible_even_without_calculable_trades(self):
        us = fixtures.workbook([fixtures.stake_row(code='AAPL')], sheet='Wall St Equities')
        at = fixtures.run_reports(stake=[us])
        table = fixtures.sales_table(at)
        self.assertEqual(table.iloc[0]['代码'], 'AAPL')
        self.assertIn('待换算 AUD', table.iloc[0]['处理结果'])

    def test_future_unconverted_trade_does_not_remove_this_year_gain(self):
        rows = [dict(fixtures.nab_row(day='2024-01-01', code='AAPL'), **{'Average Price (AUD) *1': 10, 'Brokerage (AUD)': 10}),
                dict(fixtures.nab_row(day='2025-08-01', side='SALE', code='AAPL'), **{'Average Price (AUD) *1': 12, 'Brokerage (AUD)': 0}),
                fixtures.nab_row(day='2026-08-01', code='AAPL')]
        at = fixtures.run_reports(fixtures.workbook(rows, 'Nabtrade', sheet='Int. Portfolio Transactions'))
        self.assertEqual(fixtures.gains(at), '$190.00')
        self.assertFalse(at.warning)


if __name__ == '__main__':
    unittest.main()
