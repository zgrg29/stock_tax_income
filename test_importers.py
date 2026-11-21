import unittest
from datetime import date
from decimal import Decimal as D
from io import BytesIO, StringIO

import pandas as pd

from cgt import CalculationError, calculate_events, summarize
from importers import (BrokerImporter, AccountRegistry, load_rates, load_accounts,
                       load_transfer_reviews, CONFIRMED, REVIEW, BLOCKED, INVALID)


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


def nab_row(**changes):
    row = {'Date': '2024-01-01', 'Code': 'ABC.AX', 'Movement Type': 'BUY',
           'Quantity': 100, 'Transaction Price': 10, 'Brokerage': 10, 'Other Fees': 0,
           'Account': 'N1'}
    row.update(changes)
    return row


def transfer_rows(quantity=100, identifier='T1', out_date='2025-07-01', in_date='2025-07-02', ticker='ABC'):
    outgoing = nab_row(**{'Date': out_date, 'Code': ticker + '.AX', 'Movement Type': 'TRANSFER OUT',
                          'Quantity': -quantity, 'Brokerage': 0, 'Transfer Identifier': identifier,
                          'Transaction Price': 9999})
    incoming = stake_row(**{'Trade Date': in_date, 'Symbol': ticker, 'Side': 'TRANSFER IN',
                            'Units': quantity, 'Fees': 0, 'Avg. Price': None,
                            'Transfer Identifier': identifier, 'Account': 'S1'})
    return outgoing, incoming


def registry(other_owner=False):
    return AccountRegistry([{'Broker': b, 'Account': a, 'Owner ID': o, 'Evidence': 'verified account statement'}
                            for b, a, o in [('Nabtrade', 'N1', 'person-1'),
                                            ('Stake', 'S1', 'person-2' if other_owner else 'person-1'),
                                            ('Stake', 'S2', 'person-1')]])


class TransferImportTests(unittest.TestCase):
    def load(self, buys=None, quantity=100, sell=None, accounts=None, outgoing=None, incoming=None):
        out, inc = transfer_rows(quantity)
        importer = BrokerImporter(accounts=accounts or registry())
        importer.add_frame(pd.DataFrame((buys if buys is not None else [nab_row()]) +
                                        [outgoing or out]), 'Nabtrade', 'AU', 'nab')
        importer.add_frame(pd.DataFrame([incoming or inc] + (sell or [])), 'Stake', 'AU', 'stake')
        ledger = importer.reconcile(2025)
        return importer, ledger

    def assert_clean(self, importer, ledger):
        self.assertFalse(importer.errors)
        self.assertFalse(ledger.errors)
        self.assertTrue(all(not r['Blocks CGT'] for r in importer.transfer_audit))

    def test_complete_transfer_is_neutral_and_preserves_original_lot(self):
        importer, ledger = self.load()
        self.assert_clean(importer, ledger)
        self.assertEqual([r['Status'] for r in importer.transfer_audit], [CONFIRMED, CONFIRMED])
        self.assertEqual(ledger.events, [])
        self.assertEqual(len(ledger.lots), 1)
        lot = ledger.lots[0]
        self.assertEqual((lot['buy_date'], lot['quantity'], lot['unit_cost'], lot['account']),
                         ('2024-01-01', '100', '10.1', 'STAKE:S1'))
        # Transfer-day price 9999 and missing inbound price never become cost.
        self.assertFalse(importer.warnings)
        check = next(iter(ledger.transfer_checks.values()))
        for phase in ('out', 'in'):
            before, after = check[phase]['before'], check[phase]['after']
            self.assertEqual(sum(D(r['quantity']) for r in before), sum(D(r['quantity']) for r in after))
            self.assertEqual(sum(D(r['quantity']) * D(r['unit_cost']) for r in before),
                             sum(D(r['quantity']) * D(r['unit_cost']) for r in after))

    def test_multiple_tax_lots_and_partial_transfer_keep_dates_and_costs(self):
        buys = [nab_row(), nab_row(**{'Date': '2024-06-01', 'Quantity': 50, 'Transaction Price': 20, 'Brokerage': 5})]
        for quantity in (150, 120):
            importer, ledger = self.load(buys=buys, quantity=quantity)
            self.assert_clean(importer, ledger)
            destination = [l for l in ledger.lots if l['account'] == 'STAKE:S1']
            self.assertEqual([(r['buy_date'], D(r['quantity']), D(r['unit_cost'])) for r in destination],
                             [('2024-01-01', D(100), D('10.1')),
                              ('2024-06-01', D(quantity - 100), D('20.1'))])
            self.assertEqual(sum(D(l['quantity']) for l in ledger.lots), D(150))
            self.assertEqual(sum(D(l['quantity']) * D(l['unit_cost']) for l in ledger.lots), D(2015))

    def test_sale_after_transfer_matches_original_gain_and_discount(self):
        sale = stake_row(**{'Trade Date': '2025-08-01', 'Side': 'SELL', 'Units': 100,
                             'Avg. Price': 30, 'Fees': 10, 'Account': 'S1'})
        importer, ledger = self.load(sell=[sale])
        self.assert_clean(importer, ledger)
        baseline = BrokerImporter()
        baseline.add_frame(pd.DataFrame([nab_row()]), 'Nabtrade', 'AU', 'nab')
        baseline.add_frame(pd.DataFrame([sale]), 'Stake', 'AU', 'stake')
        original = calculate_events(baseline.transactions, 2025)
        self.assertEqual([(e.buy_date, e.quantity, e.gain, e.discountable) for e in ledger.events],
                         [(e.buy_date, e.quantity, e.gain, e.discountable) for e in original])
        self.assertEqual(summarize(ledger.events)['taxable_gain'], D(990))
        self.assertEqual(ledger.lots, [])
        self.assertTrue(ledger.events[0].buy_record_id)
        self.assertTrue(ledger.events[0].sell_record_id)
        self.assertEqual(len(ledger.events[0].transfer_chain), 1)

    def test_missing_original_buy_blocks_even_without_sale(self):
        importer, ledger = self.load(buys=[])
        self.assertTrue(ledger.errors)
        self.assertTrue(all(r['Status'] == BLOCKED for r in importer.transfer_audit))
        self.assertTrue(all(r['Cost Complete'] == '否' for r in importer.transfer_audit))
        self.assertFalse(ledger.lots)

    def test_multiple_cross_broker_transfers_keep_acquisition(self):
        out, inc = transfer_rows()
        second_out = dict(inc, **{'Trade Date': '2025-07-10', 'Side': 'TRANSFER OUT', 'Transfer Identifier': 'T2'})
        second_in = dict(inc, **{'Trade Date': '2025-07-11', 'Account': 'S2', 'Transfer Identifier': 'T2'})
        sale = stake_row(**{'Trade Date': '2025-08-01', 'Side': 'SELL', 'Account': 'S2', 'Avg. Price': 30, 'Fees': 10})
        importer = BrokerImporter(accounts=registry())
        importer.add_frame(pd.DataFrame([nab_row(), out]), 'Nabtrade', 'AU', 'nab')
        importer.add_frame(pd.DataFrame([inc, second_out, second_in, sale]), 'Stake', 'AU', 'stake')
        ledger = importer.reconcile(2025)
        self.assert_clean(importer, ledger)
        self.assertEqual(len(ledger.events[0].transfer_chain), 2)
        self.assertEqual(ledger.events[0].buy_date, date(2024, 1, 1))
        self.assertEqual(ledger.events[0].gain, D(1980))

    def test_repeated_transfer_leg_not_double_counted_and_audited(self):
        out, inc = transfer_rows()
        importer = BrokerImporter(accounts=registry())
        importer.add_frame(pd.DataFrame([nab_row(), out, out]), 'Nabtrade', 'AU', 'nab')
        importer.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake')
        ledger = importer.reconcile(2025)
        self.assert_clean(importer, ledger)
        self.assertEqual(len(importer.transfer_audit), 3)
        self.assertEqual(sum('Duplicate Of' in r for r in importer.transfer_audit), 1)
        self.assertEqual(sum(D(l['quantity']) for l in ledger.lots), D(100))

    def test_beneficial_owner_change_is_not_tax_exempt_transfer(self):
        importer, ledger = self.load(accounts=registry(other_owner=True))
        self.assertTrue(all(r['Status'] == BLOCKED for r in importer.transfer_audit))
        self.assertTrue(all(r['Blocks CGT'] for r in importer.transfer_audit))
        self.assertEqual(ledger.lots[0]['account'], 'NABTRADE:N1')
        self.assertFalse(ledger.events)  # Never invent a sale price or rollover.

    def test_explicit_owner_change_without_pair_is_blocked(self):
        out, _ = transfer_rows()
        out['Beneficial Ownership Changed'] = 'YES'
        out['Transfer Identifier'] = ''
        importer = BrokerImporter(accounts=registry())
        importer.add_frame(pd.DataFrame([out]), 'Nabtrade', 'AU', 'nab')
        importer.reconcile(2025)
        self.assertEqual(importer.transfer_audit[0]['Status'], BLOCKED)

    def test_unknown_ownership_never_assumed_from_transfer_name(self):
        importer, _ = self.load(accounts=AccountRegistry())
        self.assertTrue(all(r['Status'] == REVIEW for r in importer.transfer_audit))
        self.assertTrue(all(r['Blocks CGT'] for r in importer.transfer_audit))

    def test_mismatched_pair_quantities_are_data_error(self):
        out, inc = transfer_rows()
        inc['Units'] = 99
        importer, _ = self.load(outgoing=out, incoming=inc)
        self.assertTrue(all(r['Status'] == INVALID for r in importer.transfer_audit))

    def test_source_account_quantity_shortage_is_blocked(self):
        importer, ledger = self.load(buys=[nab_row(Quantity=90)])
        self.assertTrue(ledger.errors)
        self.assertEqual(importer.transfer_audit[0]['Status'], BLOCKED)
        self.assertEqual(sum(D(l['quantity']) for l in ledger.lots), D(90))

    def test_large_consecutive_records_keep_actual_excel_rows_and_no_warning_flood(self):
        rows = [{} for _ in range(1594)]
        for index in range(86):
            out, _ = transfer_rows(identifier=f'T{index}', ticker=f'ETF{index}')
            rows.append(out)
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame(rows), 'Nabtrade', 'AU', 'nab')
        importer.reconcile(2025)
        self.assertFalse(importer.warnings)
        self.assertEqual(len(importer.transfer_audit), 86)
        self.assertEqual([r['Excel Row'] for r in importer.transfer_audit[:4]], [1597, 1598, 1599, 1600])
        self.assertTrue(all(r['Status'] == BLOCKED for r in importer.transfer_audit))

    def test_transfer_like_sale_label_is_not_silently_ignored(self):
        importer = BrokerImporter()
        importer.add_frame(pd.DataFrame([nab_row(**{'Movement Type': 'TRANSFER SALE'})]), 'Nabtrade', 'AU', 'nab')
        importer.reconcile(2025)
        self.assertTrue(importer.errors)
        self.assertEqual(importer.transfer_audit[0]['Status'], REVIEW)

    def test_nonzero_transfer_charges_require_review(self):
        out, inc = transfer_rows()
        out['Brokerage'] = 1
        importer, _ = self.load(outgoing=out, incoming=inc)
        self.assertTrue(importer.errors)
        self.assertEqual(importer.transfer_audit[0]['Status'], BLOCKED)

    def test_report_account_mapping_does_not_guess_from_broker(self):
        accounts = load_accounts(StringIO('Broker,Account,Owner ID,Evidence,File,Sheet\nNabtrade,N1,p1,statement,nab.xlsx,Domestic Portfolio Transactions\nStake,S1,p1,statement,stake.xlsx,Aus Equities\n'))
        out, inc = transfer_rows()
        buy = nab_row()
        for row in (out, inc, buy):
            row.pop('Account')
        importer = BrokerImporter(accounts=accounts)
        importer.add_frame(pd.DataFrame([buy, out]), 'Nabtrade', 'AU', 'nab.xlsx', filename='nab.xlsx', sheet='Domestic Portfolio Transactions')
        importer.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake.xlsx', filename='stake.xlsx', sheet='Aus Equities')
        ledger = importer.reconcile(2025)
        self.assert_clean(importer, ledger)
        self.assertEqual(ledger.lots[0]['account'], 'STAKE:S1')

    def test_same_day_trade_and_transfer_cannot_be_ordered_by_guessing(self):
        sale = stake_row(**{'Trade Date': '2025-07-02', 'Side': 'SELL', 'Account': 'S1'})
        importer, ledger = self.load(sell=[sale])
        self.assertTrue(ledger.errors)
        self.assertTrue(any(r['Status'] == BLOCKED for r in importer.transfer_audit))

    def test_unresolved_earlier_transfer_prevents_false_later_confirmation(self):
        out, inc = transfer_rows()
        unknown = nab_row(**{'Date': '2025-06-01', 'Movement Type': 'TRANSFER', 'Brokerage': 0})
        importer = BrokerImporter(accounts=registry())
        importer.add_frame(pd.DataFrame([nab_row(), unknown, out]), 'Nabtrade', 'AU', 'nab')
        importer.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake')
        importer.reconcile(2025)
        self.assertTrue(all(r['Status'] != CONFIRMED for r in importer.transfer_audit))

    def test_review_is_bound_to_exact_record_and_does_not_override_known_direction(self):
        out, _ = transfer_rows()
        first = BrokerImporter()
        first.add_frame(pd.DataFrame([out]), 'Nabtrade', 'AU', 'nab')
        record_id = first.transfer_audit[0]['Record ID']
        reviews = load_transfer_reviews(StringIO(f'Record ID,Direction,Evidence\n{record_id},IN,statement\n'))
        second = BrokerImporter(transfer_reviews=reviews)
        second.add_frame(pd.DataFrame([out]), 'Nabtrade', 'AU', 'nab')
        self.assertTrue(second.errors)
        self.assertEqual(second.transfer_audit[0]['Status'], INVALID)

    def test_original_cost_adjustments_survive_transfer(self):
        adjustment = nab_row(**{'Date': '2025-01-01', 'Movement Type': 'ROC', 'Brokerage': 0,
                                'Capital Return Per Share (AUD)': 1})
        sale = stake_row(**{'Trade Date': '2025-08-01', 'Side': 'SELL', 'Units': 100,
                             'Avg. Price': 30, 'Fees': 10, 'Account': 'S1'})
        importer, ledger = self.load(buys=[nab_row(), adjustment], sell=[sale])
        self.assert_clean(importer, ledger)
        self.assertEqual(ledger.events[0].gain, D(2080))
        self.assertEqual(len(ledger.events[0].cost_adjustments), 1)
        check = next(iter(ledger.transfer_checks.values()))
        self.assertEqual(D(check['out']['allocations'][0]['unit_cost']), D('9.1'))

    def test_in_transit_inventory_cannot_be_sold(self):
        out, inc = transfer_rows(in_date='2025-07-10')
        sale = stake_row(**{'Trade Date': '2025-07-05', 'Side': 'SELL', 'Account': 'S1'})
        _, ledger = self.load(outgoing=out, incoming=inc, sell=[sale])
        self.assertTrue(ledger.errors)
        self.assertFalse(ledger.events)
        self.assertEqual(sum(D(l['quantity']) for l in ledger.lots), D(100))
        self.assertTrue(ledger.lots[0]['account'].startswith('IN_TRANSIT:'))

    def test_year_end_transit_preserves_owned_total_and_cost(self):
        out, inc = transfer_rows(out_date='2026-06-29', in_date='2026-07-02')
        importer, ledger = self.load(outgoing=out, incoming=inc)
        self.assert_clean(importer, ledger)
        self.assertTrue(ledger.lots[0]['account'].startswith('IN_TRANSIT:'))
        self.assertEqual(D(ledger.lots[0]['quantity']), D(100))
        self.assertEqual(D(ledger.lots[0]['unit_cost']), D('10.1'))

    def test_partial_transfer_does_not_allow_selling_other_account_shares(self):
        sale = stake_row(**{'Trade Date': '2025-08-01', 'Side': 'SELL', 'Account': 'S1', 'Units': 60})
        _, ledger = self.load(quantity=50, sell=[sale])
        self.assertTrue(ledger.errors)
        self.assertFalse(ledger.events)
        self.assertEqual(sum(D(l['quantity']) for l in ledger.lots), D(100))

    def test_reconciliation_id_links_different_broker_references_without_overwriting(self):
        out, inc = transfer_rows()
        inc['Transfer Identifier'] = 'OTHER-REF'
        original = BrokerImporter(accounts=registry())
        original.add_frame(pd.DataFrame([nab_row(), out]), 'Nabtrade', 'AU', 'nab')
        original.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake')
        reviews = {r['Record ID']: {'Record ID': r['Record ID'], 'Reconciliation ID': 'MATCH-1', 'Evidence': 'both broker statements'}
                   for r in original.transfer_audit}
        importer = BrokerImporter(accounts=registry(), transfer_reviews=reviews)
        importer.add_frame(pd.DataFrame([nab_row(), out]), 'Nabtrade', 'AU', 'nab')
        importer.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake')
        ledger = importer.reconcile(2025)
        self.assert_clean(importer, ledger)
        self.assertEqual([r['Transfer Identifier'] for r in importer.transfer_audit], ['T1', 'OTHER-REF'])

    def test_duplicate_excel_file_with_transfers_is_applied_once(self):
        out, inc = transfer_rows()
        data = BytesIO()
        with pd.ExcelWriter(data, engine='openpyxl') as writer:
            pd.DataFrame([nab_row(), out]).to_excel(writer, sheet_name='Domestic Portfolio Transactions', startrow=1, index=False)
        data.name = 'nab.xlsx'
        importer = BrokerImporter(accounts=registry())
        importer.add_file(data, 'Nabtrade')
        importer.add_file(data, 'Nabtrade')
        importer.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake')
        ledger = importer.reconcile(2025)
        self.assert_clean(importer, ledger)
        self.assertEqual(len(importer.import_attempts), 2)
        self.assertTrue(importer.import_attempts[1]['Duplicate'])
        self.assertEqual(D(ledger.lots[0]['quantity']), D(100))

    def test_conflicting_duplicate_transfer_cannot_move_twice(self):
        out, inc = transfer_rows()
        conflict = dict(out, Quantity=-99)
        importer = BrokerImporter(accounts=registry())
        importer.add_frame(pd.DataFrame([nab_row(), out, conflict]), 'Nabtrade', 'AU', 'nab')
        importer.add_frame(pd.DataFrame([inc]), 'Stake', 'AU', 'stake')
        importer.reconcile(2025)
        self.assertTrue(importer.errors)
        self.assertEqual(importer.transfer_audit[1]['Status'], INVALID)

    def test_account_mapping_conflict_is_not_silently_overridden(self):
        accounts = AccountRegistry([{'Broker': 'Nabtrade', 'Account': 'N2', 'Owner ID': 'person-1',
                                    'Evidence': 'statement', 'File': 'nab.xlsx', 'Sheet': 'Domestic Portfolio Transactions'}])
        importer = BrokerImporter(accounts=accounts)
        importer.add_frame(pd.DataFrame([nab_row()]), 'Nabtrade', 'AU', 'nab', filename='nab.xlsx', sheet='Domestic Portfolio Transactions')
        self.assertTrue(importer.errors)

    def test_stale_reviews_do_not_confirm_different_records(self):
        importer = BrokerImporter(transfer_reviews={'stale': {'Evidence': 'old report'}})
        importer.add_frame(pd.DataFrame([nab_row()]), 'Nabtrade', 'AU', 'nab')
        importer.reconcile(2025)
        self.assertTrue(importer.errors)

    def test_ambiguous_original_lot_order_prevents_transfer_confirmation(self):
        importer, ledger = self.load(buys=[nab_row(Quantity=50),
                                          nab_row(Quantity=50, **{'Transaction Price': 20})])
        self.assertTrue(ledger.errors)
        self.assertTrue(all(r['Status'] == BLOCKED for r in importer.transfer_audit))

    def test_malformed_earlier_purchase_never_certifies_later_transfer(self):
        bad_buy = nab_row(Date='invalid date', Quantity=10)
        importer, _ = self.load(buys=[nab_row(), bad_buy])
        self.assertTrue(importer.errors)
        self.assertTrue(all(r['Status'] == BLOCKED for r in importer.transfer_audit))


if __name__ == '__main__':
    unittest.main()
