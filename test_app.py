"""Streamlit flow tests; AppTest needs local loopback sockets on Windows."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest


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
        def upload(label, **kwargs):
            return [report] if label.startswith('Stake') else None
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
        self.assertEqual(len(at.dataframe[0].value), 2)

    def test_missing_history_never_displays_final_amount(self):
        at = self.run_report(self.report(quantity=200))
        self.assertTrue(at.error)
        self.assertFalse(at.metric)

    def test_parse_error_never_displays_partial_amount(self):
        at = self.run_report(self.report(malformed=True))
        self.assertTrue(at.error)
        self.assertFalse(at.metric)


if __name__ == '__main__':
    unittest.main()
