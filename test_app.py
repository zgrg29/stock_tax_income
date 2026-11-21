"""Streamlit flow tests; AppTest needs local loopback sockets on Windows."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_importers import nab_row, transfer_rows


class PageTests(unittest.TestCase):
    def app(self):
        return AppTest.from_file(str(Path(__file__).with_name('app.py')))

    def report(self, quantity=50, malformed=False):
        report = BytesIO()
        frame = pd.DataFrame([
            {'Trade Date': '2025-07-01', 'Symbol': 'ABC', 'Side': 'BUY',
             'Units': 100, 'Avg. Price': 10, 'Fees': 10, 'GST': 0},
            {'Trade Date': '2025-08-01', 'Symbol': 'ABC', 'Side': 'SELL',
             'Units': quantity, 'Avg. Price': 12, 'Fees': 0, 'GST': 0},
            {'Trade Date': '2025-09-01', 'Symbol': 'ABC', 'Side': 'SELL',
             'Units': 50, 'Avg. Price': 12, 'Fees': 0, 'GST': 0},
        ])
        if malformed:
            frame = frame.drop(columns='Fees')
        with pd.ExcelWriter(report, engine='openpyxl') as writer:
            frame.to_excel(writer, sheet_name='Aus Equities', index=False)
        report.name = 'stake.xlsx'
        return report

    def run_report(self, report):
        return self.run_uploads(stake=[report])

    def run_uploads(self, *, nab=None, stake=None, accounts=None, reviews=None):
        def upload(label, **kwargs):
            if label.startswith('Nabtrade'):
                return nab
            if label.startswith('Stake'):
                return stake or []
            if label.startswith('账户受益权'):
                if accounts is not None:
                    accounts.seek(0)
                return accounts
            if label.startswith('逐行转仓'):
                if reviews is not None:
                    reviews.seek(0)
                return reviews
            return None
        with patch.object(st.sidebar, 'file_uploader', side_effect=upload):
            at = self.app().run(timeout=20)
            at.sidebar.selectbox[0].set_value(2025)
            at.sidebar.selectbox[1].select('符合条件的澳洲税务居民个人：50%')
            at.sidebar.checkbox[0].check()
            at.sidebar.button[0].click().run(timeout=20)
        self.assertFalse(at.exception)
        return at

    def test_initial_page_and_no_file_validation(self):
        at = self.app().run(timeout=20)
        self.assertFalse(at.exception)
        at.sidebar.button[0].click().run(timeout=20)
        self.assertFalse(at.exception)
        self.assertTrue(at.error)
        self.assertFalse(at.metric)

    def test_successful_excel_to_displayed_result(self):
        at = self.run_report(self.report())
        self.assertFalse(at.error)
        self.assertEqual(at.metric[0].value, '$190.00')
        self.assertEqual(at.metric[1].value, '$0.00')
        details = next(d.value for d in at.dataframe if 'gain' in d.value.columns)
        self.assertEqual(len(details), 2)

    def test_missing_history_never_displays_final_amount(self):
        at = self.run_report(self.report(quantity=200))
        self.assertTrue(at.error)
        self.assertFalse(at.metric)

    def test_parse_error_never_displays_partial_amount(self):
        at = self.run_report(self.report(malformed=True))
        self.assertTrue(at.error)
        self.assertFalse(at.metric)

    def transfer_reports(self, pairs=40, pending=6, sell=False, owners_match=True):
        buys, outs, ins = [], [], []
        for i in range(pairs + pending):
            ticker = f'ETF{i}'
            buys.append(nab_row(Code=ticker + '.AX'))
            out, inc = transfer_rows(identifier=f'T{i}', ticker=ticker)
            if i < pairs:
                outs.append(out)
                ins.append(inc)
            else:
                outs.append(dict(out, **{'Movement Type': 'TRANSFER', 'Transfer Identifier': ''}))
        if sell:
            ins.append({'Trade Date': '2025-08-01', 'Symbol': 'ETF0', 'Side': 'SELL',
                        'Units': 100, 'Avg. Price': 30, 'Fees': 10, 'GST': 0, 'Account': 'S1'})
        nab, stake = BytesIO(), BytesIO()
        with pd.ExcelWriter(nab, engine='openpyxl') as writer:
            pd.DataFrame(buys + outs).to_excel(writer, sheet_name='Domestic Portfolio Transactions', startrow=1, index=False)
        with pd.ExcelWriter(stake, engine='openpyxl') as writer:
            pd.DataFrame(ins).to_excel(writer, sheet_name='Aus Equities', index=False)
        nab.name, stake.name = 'Transactions_report.xlsx', 'stake.xlsx'
        owner = 'person-1' if owners_match else 'person-2'
        accounts = BytesIO(f'Broker,Account,Owner ID,Evidence\nNabtrade,N1,person-1,original statement\nStake,S1,{owner},original statement\n'.encode())
        return dict(nab=nab, stake=[stake], accounts=accounts)

    def test_86_rows_have_one_summary_and_all_details(self):
        at = self.run_uploads(**self.transfer_reports())
        self.assertEqual(len(at.warning), 1)
        self.assertIn('86 条转仓记录', at.warning[0].value)
        self.assertIn('80 条已确认', at.warning[0].value)
        self.assertIn('6 条需要核对', at.warning[0].value)
        self.assertTrue(at.error)
        self.assertFalse(at.metric)
        details = next(d.value for d in at.dataframe if 'Excel 行号' in d.value.columns)
        self.assertEqual(len(details), 86)
        labels = [b.label for b in at.get('download_button')]
        self.assertIn('下载完整转仓审计 CSV', labels)
        self.assertIn('下载全部导入行审计 CSV', labels)

    def test_confirmed_transfers_have_no_yellow_banner_and_correct_sale(self):
        at = self.run_uploads(**self.transfer_reports(pairs=1, pending=0, sell=True))
        self.assertFalse(at.warning)
        self.assertFalse(at.error)
        self.assertEqual(at.metric[0].value, '$990.00')
        self.assertTrue(any('2 条已确认' in x.value for x in at.info))

    def test_ownership_change_blocks_tax_amount_on_page(self):
        at = self.run_uploads(**self.transfer_reports(pairs=1, pending=0, sell=True, owners_match=False))
        self.assertTrue(at.error)
        self.assertFalse(at.metric)
        details = next(d.value for d in at.dataframe if 'Excel 行号' in d.value.columns)
        self.assertTrue(all(details['状态'] == '计算受阻'))

    def test_86_confirmed_rows_do_not_create_any_yellow_warning(self):
        at = self.run_uploads(**self.transfer_reports(pairs=43, pending=0))
        self.assertFalse(at.warning)
        self.assertFalse(at.error)
        self.assertEqual(at.metric[0].value, '$0.00')
        self.assertTrue(any('86 条已确认' in x.value for x in at.info))


if __name__ == '__main__':
    unittest.main()
