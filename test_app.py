"""End-to-end tests of the restored original app, using synthetic Excel reports."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent


def nab_row(day='2025-07-01', side='BUY', qty=100, price=10, fee=10, code='ABC.AX', **extra):
    return dict({'Date': day, 'Code': code, 'Movement Type': side, 'Quantity': qty,
                 'Transaction Price': price, 'Brokerage': fee}, **extra)


def stake_row(day='2025-08-01', side='SELL', qty=100, price=12, fee=0, code='ABC', **extra):
    return dict({'Trade Date': day, 'Symbol': code, 'Side': side, 'Units': qty,
                 'Avg. Price': price, 'Fees': fee}, **extra)


def workbook(rows, broker='Stake', name='report.xlsx', sheet=None):
    result = BytesIO()
    sheet = sheet or ('Domestic Portfolio Transactions' if broker == 'Nabtrade' else 'Aus Equities')
    with pd.ExcelWriter(result, engine='openpyxl') as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet, startrow=1 if broker == 'Nabtrade' else 0, index=False)
    result.name = name
    return result


def run_reports(nab=None, stake=None, same_owner=False, opening_loss=0):
    def upload(label, **kwargs):
        if 'Nabtrade' in label:
            return nab
        if 'Stake' in label:
            return stake or []
        raise AssertionError('The original workflow must not require extra uploads: ' + label)
    with patch.object(st.sidebar, 'file_uploader', side_effect=upload):
        at = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
        at.sidebar.selectbox[0].set_value('2025-2026')
        at.sidebar.checkbox[0].set_value(same_owner)
        at.sidebar.number_input[0].set_value(float(opening_loss))
        at.sidebar.button[0].click().run(timeout=30)
    if at.exception:
        raise AssertionError([error.message for error in at.exception])
    return at


def gains(at):
    return next(metric.value for metric in at.metric if metric.label == '本财年纯盈利项加总')


class PageTests(unittest.TestCase):
    def test_original_homepage_has_only_two_broker_uploads(self):
        at = AppTest.from_file(str(ROOT / 'app.py')).run(timeout=30)
        self.assertFalse(at.exception)
        self.assertEqual(len(at.sidebar.selectbox), 1)
        self.assertEqual(len(at.get('file_uploader')), 2)
        self.assertEqual(at.sidebar.button[0].label, '🚀 开始融合账目并计算')

    def test_original_minimal_columns_and_partial_sales_work(self):
        at = run_reports(workbook([nab_row()], 'Nabtrade'),
                         [workbook([stake_row(qty=50), stake_row(day='2025-09-01', qty=50)])])
        self.assertFalse(at.error)
        self.assertEqual(gains(at), '$190.00')
        self.assertTrue(any('**$190.00**' in x.value for x in at.success))

    def test_same_day_multiple_costs_no_longer_block_whole_report(self):
        at = run_reports(workbook([nab_row(), nab_row(price=20)], 'Nabtrade'),
                         [workbook([stake_row(qty=200, price=30)])])
        self.assertFalse(at.error)
        self.assertEqual(gains(at), '$2,980.00')

    def test_blank_cells_and_report_footer_match_original_import_behavior(self):
        rows = [nab_row(fee=None), {'Transaction Price': 9999, 'Movement Type': None, 'Date': None, 'Code': None}]
        at = run_reports(workbook(rows, 'Nabtrade'), [workbook([stake_row(fee=None)])])
        self.assertFalse(at.error)
        self.assertFalse(at.warning)
        self.assertEqual(gains(at), '$200.00')

    def test_one_unresolved_stock_does_not_hide_other_known_stock(self):
        at = run_reports(workbook([nab_row()], 'Nabtrade'),
                         [workbook([stake_row(), stake_row(code='UNKNOWN')])])
        self.assertEqual(gains(at), '$190.00')
        self.assertTrue(any('非最终报税金额' in x.value for x in at.subheader))
        self.assertTrue(any('原始买入成本' in x.value for x in at.markdown))
        self.assertFalse(any('数据校验未通过' in x.value for x in at.error))

    def test_ordinary_transfer_keeps_original_lot_without_account_csv(self):
        rows = [nab_row(day='2024-01-01'), nab_row(day='2025-07-01', side='TRANSFER OUT', fee=0, price=9999)]
        at = run_reports(workbook(rows, 'Nabtrade'), [workbook([stake_row(price=30, fee=10)])], same_owner=True)
        self.assertFalse(at.error)
        self.assertEqual(gains(at), '$1,980.00')
        self.assertTrue(any('**$990.00**' in x.value for x in at.success))
        self.assertFalse(at.warning)

    def test_administrative_zero_quantity_transfer_does_not_erase_known_history(self):
        rows = [nab_row(), nab_row(side='TRANSFER', day='2025-07-10', qty=0, fee=0)]
        at = run_reports(workbook(rows, 'Nabtrade'), [workbook([stake_row()])], same_owner=True)
        self.assertEqual(gains(at), '$190.00')
        self.assertFalse(at.error)

    def test_large_transfer_batch_is_one_summary_without_stopping_calculation(self):
        buys = [nab_row(code=f'ETF{i}.AX', day='2024-01-01') for i in range(86)]
        moves = [nab_row(code=f'ETF{i}.AX', day='2025-07-01', side='TRANSFER OUT', fee=0) for i in range(86)]
        at = run_reports(workbook(buys + moves, 'Nabtrade'),
                         [workbook([stake_row(code='ETF0', price=30, fee=10)])])
        self.assertEqual(len(at.warning), 1)
        self.assertIn('86 条普通转仓记录', at.warning[0].value)
        self.assertEqual(gains(at), '$1,980.00')
        self.assertTrue(any('非最终报税金额' in x.value for x in at.subheader))

    def test_missing_history_never_becomes_zero_profit(self):
        at = run_reports(stake=[workbook([stake_row()])])
        self.assertTrue(any('未按零盈利处理' in x.value for x in at.markdown))
        self.assertFalse(any('净资本利得' in x.value for x in at.success))
        self.assertFalse(at.metric)

    def test_ownership_change_cannot_be_overridden_by_global_confirmation(self):
        rows = [nab_row(), nab_row(side='TRANSFER OUT', day='2025-07-20', fee=0,
                                  **{'Beneficial Ownership Changed': 'YES'})]
        at = run_reports(workbook(rows, 'Nabtrade'), [workbook([stake_row()])], same_owner=True)
        self.assertFalse(any('净资本利得' in x.value for x in at.success))
        self.assertTrue(any('受益所有权变化' in x.value for x in at.markdown))

    def test_prior_loss_offsets_gain_without_new_files(self):
        at = run_reports(workbook([nab_row()], 'Nabtrade'), [workbook([stake_row()])], opening_loss=50)
        self.assertTrue(any('**$140.00**' in x.value for x in at.success))


if __name__ == '__main__':
    unittest.main()
