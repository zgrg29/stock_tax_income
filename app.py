import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import re


def fee_value(row, column):
    """Preserve original optional fee columns, treating blank cells as zero."""
    value = row.get(column, 0)
    return 0.0 if pd.isna(value) else float(value)


def held_over_twelve_months(bought, sold):
    # Exclude acquisition and disposal dates; a year is not always 365 days.
    return sold.normalize() > bought.normalize() + pd.DateOffset(years=1)


def transaction_type(value):
    action = ' '.join(str(value).upper().split())
    if action in ('TRANSFER_IN', 'TRANSFER_OUT', 'STOCK_TRANSFER_IN', 'STOCK_TRANSFER_OUT',
                  'BROKER_TRANSFER_IN', 'BROKER_TRANSFER_OUT', 'SECURITY_TRANSFER_IN', 'SECURITY_TRANSFER_OUT'):
        action = action.replace('_', ' ')
    return {'B': 'BUY', 'PURCHASE': 'BUY', 'BOUGHT': 'BUY',
            'S': 'SELL', 'SALE': 'SELL', 'SOLD': 'SELL'}.get(action, action)


def security_code(value, market):
    code = str(value).strip().upper()
    prefixes = ('ASX:', 'AU:') if market == 'AU' else ('NASDAQ:', 'NYSE:', 'NYSEARCA:', 'US:')
    suffixes = ('.AX', '.ASX', '.AU', '.AXW', '.CXA') if market == 'AU' else ('.US',)
    for prefix in prefixes:
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    for suffix in suffixes:
        if code.endswith(suffix):
            code = code[:-len(suffix)]
            break
    return code


def transaction_date(value, market='AU'):
    # AU report strings use day/month/year; keep US month/day parsing and
    # preserve Excel datetime cells and ISO dates without reinterpretation.
    if pd.isna(value):
        raise ValueError('交易日期为空')
    if isinstance(value, (int, float, np.number)):
        raise ValueError('交易日期是未解析的数字，请核对原表日期格式')
    is_iso = isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}', value.strip())
    return pd.to_datetime(value, dayfirst=market == 'AU' and not bool(is_iso) if isinstance(value, str) else False).tz_localize(None)

# ==========================================
# Streamlit 页面基础配置
# ==========================================
st.set_page_config(
    page_title="澳洲 ATO 跨券商股票 CGT 报税清算系统",
    page_icon="🇦🇺",
    layout="wide"  # 响应式布局：PC端宽屏，移动端自动缩进
)

st.title("🇦🇺 澳洲 ATO 股票 CGT 联合清算系统")
st.markdown("""
本工具支持跨券商、跨历史时期的股票库存撮合。自动适用澳洲满一年的 **50% CGT 税收减免 (Individual Discount)**。
""")

# ==========================================
# 侧边栏：参数配置与文件上传
# ==========================================
st.sidebar.header("⚙️ 第一步：配置报税参数")

# 动态生成财年选项
current_year = datetime.now().year
fy_options = [f"{year}-{year+1}" for year in range(current_year - 5, current_year + 3)]
# 默认选中 2025-2026 财年
default_index = fy_options.index("2025-2026") if "2025-2026" in fy_options else 0

TARGET_FY = st.sidebar.selectbox("选择目标申报财年 (Financial Year)", fy_options, index=default_index)

# 解析目标财年的时间范围
start_year = int(TARGET_FY.split("-")[0])
fy_start = pd.to_datetime(f"{start_year}-07-01")
fy_end = pd.to_datetime(f"{start_year + 1}-06-30")
fy_exclusive_end = pd.to_datetime(f"{start_year + 1}-07-01")

st.sidebar.markdown("---")
st.sidebar.header("📁 第二步：上传券商原始 Excel")

# 1. 上传 Nabtrade 报告
nabtrade_file = st.sidebar.file_uploader(
    "上传原券商报告 (Nabtrade)",
    type=["xlsx", "xls"],
    help="请上传包含 'Domestic Portfolio Transactions' 或 'Int. Portfolio Transactions' 工作表的完整 Excel 报告。"
)

# 2. 上传多个 Stake 报告
stake_files = st.sidebar.file_uploader(
    "上传 Stake 报告 (支持多选)",
    type=["xlsx", "xls"],
    accept_multiple_files=True,
    help="您可以同时选择并上传多个不同财年或不同时段导出的 Stake Excel 报告。"
)

# 用来存放全局流水的列表和安全去重集合
all_transactions = []
seen_stake_identifiers = {}
sale_records = []
transfer_records = []
unresolved = []
uncertain_assets = set()
parse_errors = []
corporate_records = []
transfer_types = {'TRANSFER', 'TRANSFER IN', 'TRANSFER OUT', 'STOCK TRANSFER',
                  'STOCK TRANSFER IN', 'STOCK TRANSFER OUT', 'INWARD TRANSFER',
                  'OUTWARD TRANSFER', 'BROKER TRANSFER IN', 'BROKER TRANSFER OUT',
                  'SECURITY TRANSFER', 'SECURITY TRANSFER IN', 'SECURITY TRANSFER OUT',
                  'OFF MARKET TRANSFER'}
same_owner = st.sidebar.checkbox('如有普通券商转仓，我确认受益所有人未改变', value=False,
                                 help='不需额外 CSV。未确认也可以计算，但含转仓的结果只作待核对小计。所有权变化不能按普通转仓处理。')
opening_loss = st.sidebar.number_input('往年尚未使用的资本亏损（AUD，可选）', min_value=0.0, value=0.0,
                                       help='填写本财年期初可用余额；默认 0，不要求补充文件。')


def record_transfer(row, action, source, row_number, ticker, date_column, quantity_column, market='AU'):
    """Retain source rows; do not create acquisitions or estimate missing cost."""
    transfer_records.append({'来源': source, 'Excel 行号': row_number,
                             '代码': ticker, '日期': str(row.get(date_column, '')),
                             '数量': row.get(quantity_column, ''), '原始类型': row.get('Movement Type', row.get('Side', action)),
                             '受益所有权': '用户确认未改变' if same_owner else '未验证',
                             '处理': '仅在所有权未变的前提下，不改变全局历史持仓'})
    changed = str(row.get('Beneficial Ownership Changed', '')).strip().upper() == 'YES'
    from_owner = str(row.get('From Owner ID', '')).strip()
    to_owner = str(row.get('To Owner ID', '')).strip()
    if pd.isna(row.get('From Owner ID', np.nan)):
        from_owner = ''
    if pd.isna(row.get('To Owner ID', np.nan)):
        to_owner = ''
    if changed or (from_owner and to_owner and from_owner != to_owner):
        uncertain_assets.add((market, ticker))
        unresolved.append(f'{source} 第 {row_number} 行 {ticker}: 明确存在受益所有权变化，不能按普通券商转仓处理')
        transfer_records[-1]['受益所有权'] = '原始记录显示已改变'
        return
    transfer_quantity = pd.to_numeric(row.get(quantity_column), errors='coerce')
    if pd.isna(transfer_quantity) or not np.isfinite(transfer_quantity) or transfer_quantity == 0:
        transfer_records[-1]['处理'] += '；原表未提供有效转仓数量，未据此改变历史库存'
        return
    all_transactions.append({'Date': transaction_date(row[date_column], market),
                             'Ticker': ticker, 'Market': market, 'Type': 'TRANSFER_CHECK',
                             'Quantity': float(abs(transfer_quantity)), 'Price': 0.0,
                             'Fees': 0.0, 'Source': source})


def show_calculation_notes():
    notes = list(dict.fromkeys(parse_errors + unresolved))
    if notes:
        st.warning(f'有 {len(notes)} 项记录需要核对；下面保留已匹配买入批次的试算金额，未知部分不按零盈利处理，总额不是最终报税金额。')
        for note in notes[:3]:
            st.write(note)
        if len(notes) > 3:
            with st.expander('查看其余待核对记录'):
                st.dataframe(pd.DataFrame({'需核对事项': notes[3:]}), hide_index=True, width='stretch')


def corporate_note(tx, status, detail):
    corporate_records.append({'日期': tx['Date'], '代码': tx['Ticker'], '原始类型': tx.get('Raw Type', tx['Type']),
                              '数量': tx.get('Signed Quantity', tx['Quantity']), '来源': tx['Source'],
                              '处理': status, '说明': detail})


def prepare_corporate_actions(transactions):
    """Interpret report movements without replacing the original FIFO engine.

    Paid allotments have independent lots. Paired changes move existing lots;
    report carrying values/fees are never charged a second time. Unknown tax
    treatment is retained on the lot, not invented as a zero cost acquisition.
    """
    result = [dict(tx) for tx in transactions]
    paired = {'TRADABLE_RIGHTS_CONVERSION', 'RHT - RENOUNCEABLE RIGHTS ISSUE ALLOTMENT',
              'CHANGE_SECURITY_CODE', 'RECONSTRUCTION'}
    groups = {}
    for tx in result:
        kind = tx['Type']
        tx['Raw Type'] = kind
        if kind in ('DRP - DIVIDEND PLAN ALLOTMENT', 'SPP - SHARE PURCHASE PLAN'):
            paid = tx.get('Reported Value', np.nan)
            if pd.isna(paid):
                paid = tx['Price'] * tx['Quantity']
            if tx.get('Signed Quantity', tx['Quantity']) > 0 and np.isfinite(paid) and paid > 0:
                tx['Price'], tx['Type'] = float(paid) / tx['Quantity'], 'BUY'
                corporate_note(tx, '已计入独立买入批次', '使用报表实际分配金额及原始发行日期；不按普通现金分红忽略')
            else:
                tx['Type'] = 'ACQUIRE_UNVERIFIED'
        elif kind == 'TRADABLE_RIGHTS_ISSUE' and tx.get('Signed Quantity', 0) > 0:
            if tx['Price'] == 0 and tx.get('Reported Value', np.nan) == 0:
                tx['Type'] = 'BUY'
                corporate_note(tx, '已记录免费获配权利', '报表明确金额为零；行权时承接权利成本并加入实际认购金额')
            else:
                tx['Type'] = 'ACQUIRE_UNVERIFIED'
        elif kind in ('ADJUSTMENT_INCREASE', 'BSP - BONUS PLAN ALLOTMENT'):
            tx['Type'] = 'ACQUIRE_UNVERIFIED' if tx.get('Signed Quantity', tx['Quantity']) > 0 else 'REMOVE_UNVERIFIED'
            tx['Review Existing Lots'] = True
        elif kind in paired:
            key = (tx['Date'], tx['Market'], tx.get('Report', ''), tx.get('Account', ''), kind)
            groups.setdefault(key, []).append(tx)
    for key, legs in groups.items():
        outgoing = [t for t in legs if t.get('Signed Quantity', 0) < 0]
        incoming = [t for t in legs if t.get('Signed Quantity', 0) > 0]
        if (len(legs) == 2 and len(outgoing) == len(incoming) == 1
                and outgoing[0]['Ticker'] != incoming[0]['Ticker']
                and np.isclose(outgoing[0]['Quantity'], incoming[0]['Quantity'], rtol=1e-12, atol=1e-9)):
            old, new = outgoing[0], incoming[0]
            old['Type'] = 'PAIRED_ACTION_LEG'
            new.update({'Type': 'EXERCISE_RIGHTS' if key[-1] == 'TRADABLE_RIGHTS_CONVERSION' else 'MOVE_LOTS',
                        'From Ticker': old['Ticker'], 'Counterpart Source': old['Source'],
                        'Tax Review': key[-1] == 'RECONSTRUCTION'})
        else:
            for tx in legs:
                tx['Type'] = 'ACQUIRE_UNVERIFIED' if tx.get('Signed Quantity', 0) > 0 else 'REMOVE_UNVERIFIED'
    return result


def apply_corporate_action(row, portfolio):
    """Return True when a non-trade corporate action was handled."""
    action, key = row['Type'], (row['Market'], row['Ticker'])
    if action == 'PAIRED_ACTION_LEG':
        return True
    if action == 'ACQUIRE_UNVERIFIED':
        lots = portfolio.setdefault(key, [])
        # A bonus/allocation can change the cost allocation of existing lots.
        if row.get('Review Existing Lots', False) == True:
            for lot in lots:
                lot['cost_known'] = False
        lots.append({'date': row['Date'], 'qty': row['Quantity'], 'price': np.nan,
                     'fees': 0.0, 'source': row['Source'], 'cost_known': False})
        corporate_note(row, '数量已计入，税务成本待核对', '原始增持记录存在；未把报表估值、赠股分红参数或未知调整金额当作已确认税务成本')
        return True
    if action not in ('MOVE_LOTS', 'EXERCISE_RIGHTS', 'REMOVE_UNVERIFIED'):
        return False
    source_key = (row['Market'], row.get('From Ticker')) if action != 'REMOVE_UNVERIFIED' else key
    source_lots = portfolio.setdefault(source_key, [])
    if action == 'REMOVE_UNVERIFIED':
        for lot in source_lots:
            lot['cost_known'] = False
    quantity = row['Quantity']
    available = sum(lot['qty'] for lot in source_lots)
    if available + 1e-9 < quantity:
        corporate_note(row, '来源批次数量未对上', f'来源 {source_key[1]} 已读入 {available:g} 股，需要 {quantity:g} 股')
        unresolved.append(f"{row['Source']}: 公司行动来源数量未对上，需核对原始配对记录")
        return True
    moved, remaining = [], quantity
    while remaining > 1e-9:
        lot = source_lots[0]
        take = min(remaining, lot['qty'])
        unit_fee = lot['fees'] / lot['qty']
        moved.append(dict(lot, qty=take, fees=unit_fee * take))
        lot['fees'] -= unit_fee * take
        lot['qty'] -= take
        remaining -= take
        if lot['qty'] <= 1e-9:
            source_lots.pop(0)
    known = all(lot.get('cost_known', True) and np.isfinite(lot['price']) for lot in moved)
    if action == 'EXERCISE_RIGHTS':
        paid = row.get('Reported Value', np.nan)
        if pd.isna(paid):
            paid = row['Price'] * quantity
        known = known and np.isfinite(paid) and paid >= 0
        cost = sum(lot['price'] * lot['qty'] + lot['fees'] for lot in moved) + paid + row['Fees'] if known else np.nan
        moved = [{'date': row['Date'], 'qty': quantity, 'price': cost / quantity,
                  'fees': 0.0, 'source': row['Source'], 'cost_known': bool(known)}]
    if action != 'REMOVE_UNVERIFIED':
        review = row.get('Tax Review', False) == True
        if review:
            for lot in moved:
                lot['cost_known'] = False
        portfolio.setdefault(key, []).extend(moved)
        portfolio[key].sort(key=lambda lot: lot['date'])
        corporate_note(row, '已承接原始批次' if not review else '已承接数量，重组税务条件待核对',
                       '行权采用行权日及原权利成本加认购款' if action == 'EXERCISE_RIGHTS' else
                       '保留原购入日、原始成本和手续费，不重复计入报表结转费用；一般重组仍须核对 rollover')
    else:
        corporate_note(row, '数量已扣减，税务处理待核对', '未配对的移出不自动当作免税转仓')
    if (action == 'REMOVE_UNVERIFIED' or row.get('Tax Review', False) == True) and fy_start <= row['Date'] < fy_exclusive_end:
        unresolved.append(f"{row['Source']}: 当年公司行动是否触发 CGT 尚待核对，未假定自动 rollover")
    return True


def capture_sales(frame, source, market, date_col, symbol_col, side_col, qty_col, price_col, first_row):
    """Record raw sales BEFORE validation, currency checks, or FIFO matching."""
    frame = frame.copy()
    frame['_Sale ID'] = ''
    if side_col not in frame:
        return frame
    for index, row in frame.iterrows():
        if transaction_type(row[side_col]) != 'SELL':
            continue
        try:
            date = transaction_date(row.get(date_col), market)
        except (ValueError, TypeError):
            date = pd.NaT
        ticker = security_code(row.get(symbol_col, ''), market)
        display_price = row.get(price_col, '')
        display_currency = 'AUD' if market == 'AU' else str(row.get('Currency', '报表币种（未核实）'))
        for aud_column in ('Average Price (AUD) *1', 'Avg. Price (AUD)'):
            if not pd.isna(row.get(aud_column, np.nan)):
                display_price, display_currency = row[aud_column], 'AUD'
                break
        record_id = f'sale-{len(sale_records) + 1}'
        frame.at[index, '_Sale ID'] = record_id
        sale_records.append({'Sale ID': record_id, '来源': source, 'Excel 行号': index + first_row,
                             '券商交易编号': str(row.get('Trade Identifier', '')), '原始类型': str(row[side_col]),
                             '市场': market, '代码': ticker, '原始日期': str(row.get(date_col, '')),
                             '卖出日期': date, '卖出数量': str(row.get(qty_col, '')),
                             '报表单价': str(display_price), '价格币种': display_currency,
                             '处理结果': '未解析，待核对',
                             '说明': '原始卖出行已保留；缺失日期/代码/方向等字段时不会进入FIFO',
                             '已匹配数量': 0.0, '已匹配盈亏 AUD（待核对时非最终金额）': np.nan})
        if (pd.isna(date) or date < fy_exclusive_end) and any(pd.isna(row.get(c)) for c in (date_col, symbol_col)):
            parse_errors.append(f'{source} 第 {index + first_row} 行：卖出缺少日期或代码，原始行已保留待核对')
    return frame


def mark_sale(row, status, reason=''):
    record_id = row.get('_Sale ID', row.get('Sale ID', ''))
    for record in sale_records:
        if record['Sale ID'] == record_id:
            record['处理结果'], record['说明'] = status, reason
            return


def show_sales(events):
    """Calculation eligibility never controls whether the original sale is visible."""
    for record in sale_records:
        date = record['卖出日期']
        if not pd.isna(date) and not fy_start <= date < fy_exclusive_end:
            record['处理结果'] = '财年外'
            continue
        matches = [event for event in events if event.get('Sale ID') == record['Sale ID']]
        if not matches:
            continue
        matched = sum(event['Quantity'] for event in matches)
        record['已匹配数量'] = matched
        record['已匹配盈亏 AUD（待核对时非最终金额）'] = sum(event['Net Gain/Loss'] for event in matches)
        raw_qty = pd.to_numeric(record['卖出数量'], errors='coerce')
        if any(not np.isfinite(event['Net Gain/Loss']) for event in matches):
            record['处理结果'] = '数量已匹配，税务成本待核对'
            record['说明'] = '对应公司行动成本尚未确认；不把未知税务成本当零'
        elif pd.isna(raw_qty) or not np.isclose(matched, abs(raw_qty), rtol=1e-10, atol=1e-12):
            record['处理结果'] = '部分匹配，剩余成本缺失'
        elif (record['市场'], record['代码']) in uncertain_assets:
            record['处理结果'] = '已匹配，相关历史或成本待核对'
        else:
            record['处理结果'], record['说明'] = '已计算', '已按原版 FIFO 匹配历史买入'
    current = [r for r in sale_records if pd.isna(r['卖出日期']) or fy_start <= r['卖出日期'] < fy_exclusive_end]
    if sale_records:
        st.subheader('📋 本财年全部卖出记录（含待核对）')
        if current:
            complete = sum(r['处理结果'] == '已计算' for r in current)
            duplicate = sum(r['处理结果'] == '重复记录' for r in current)
            st.caption(f'读取 {len(current)} 条卖出：已计算 {complete} 条，重复 {duplicate} 条，其余 {len(current) - complete - duplicate} 条待核对；日期缺失的记录也保留在表中。')
            st.dataframe(pd.DataFrame(current), hide_index=True, width='stretch')
        else:
            st.info('已读取的卖出日期均不在所选财年；可展开下面的全部记录核对原始日期。')
        with st.expander('查看全部卖出记录（含财年外）'):
            st.dataframe(pd.DataFrame(sale_records), hide_index=True, width='stretch')
        st.download_button('下载全部卖出记录 CSV（含未计算）', pd.DataFrame(sale_records).to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'all_sales_{TARGET_FY}.csv', mime='text/csv', on_click='ignore')

# 开始处理按钮
run_analysis = st.sidebar.button("🚀 开始融合账目并计算")

# ==========================================
# 核心数据处理逻辑
# ==========================================
if run_analysis:
    if not nabtrade_file and not stake_files:
        st.error("❌ 请至少上传一个 Nabtrade 或 Stake 的 Excel 文件后再点击运行。")
    else:
        # 实时处理日志在手机端单行展示
        st.info("🚀 开始进行跨多券商、多历史期持仓联合 CGT 结算分析...")
        st.caption(f"📅 目标财年: {TARGET_FY} ({fy_start.strftime('%Y-%m-%d')} 至 {fy_end.strftime('%Y-%m-%d')})")

        with st.spinner("正在解析数据并进行跨平台 FIFO 库存撮合，请稍候..."):

            # --- 1. 解析 Nabtrade 文件 ---
            if nabtrade_file is not None:
                # 国内交易
                try:
                    df_dom = pd.read_excel(nabtrade_file, sheet_name='Domestic Portfolio Transactions', skiprows=1)
                    df_dom = capture_sales(df_dom, f'{nabtrade_file.name}/Domestic Portfolio Transactions', 'AU',
                                           'Date', 'Code', 'Movement Type', 'Quantity', 'Transaction Price', 3)
                    df_dom = df_dom.dropna(subset=['Date', 'Code', 'Movement Type'])
                    for row_index, row in df_dom.iterrows():
                        try:
                            tx_type = transaction_type(row['Movement Type'])
                            ticker = security_code(row['Code'], 'AU')
                            date = transaction_date(row['Date'], 'AU')
                            if date >= fy_exclusive_end:
                                continue
                            if tx_type in transfer_types:
                                record_transfer(row, tx_type, 'Nabtrade/Domestic Portfolio Transactions', row_index + 3, ticker, 'Date', 'Quantity')
                                continue
                            if 'TRANSFER' in tx_type:
                                uncertain_assets.add(('AU', ticker))
                                unresolved.append(f'Nabtrade 第 {row_index + 3} 行 {ticker}: {tx_type} 不能确定为普通转仓')
                                continue
                            all_transactions.append({
                                'Date': date,
                                'Sale ID': row.get('_Sale ID', ''),
                                'Ticker': ticker,
                                'Market': 'AU',
                                'Type': tx_type,
                                'Quantity': float(abs(row['Quantity'])),
                                'Signed Quantity': float(row['Quantity']),
                                'Reported Value': row.get('Value', np.nan),
                                'Report': nabtrade_file.name,
                                'Account': str(row.get('Account', '')),
                                'Price': float(row['Transaction Price']),
                                'Fees': fee_value(row, 'Brokerage') + fee_value(row, 'Other Fees'),
                                'ROC Per Share': row.get('Capital Return Per Share (AUD)', np.nan),
                                'Source': f'{nabtrade_file.name}/Domestic Portfolio Transactions 第 {row_index + 3} 行'
                            })
                            mark_sale(row, '已读入，等待 FIFO 计算')
                        except Exception as exc:
                            mark_sale(row, '解析失败，原始卖出行已保留', str(exc))
                            parse_errors.append(f'{nabtrade_file.name}/Domestic Portfolio Transactions 第 {row_index + 3} 行' + f'：{exc}')
                            uncertain_assets.add(('AU', ticker))
                    st.success(f"✅ 成功载入原券商国内交易记录: {len(df_dom)} 条")
                except Exception as e:
                    if 'Worksheet named' not in str(e):
                        parse_errors.append(f'Nabtrade 国内工作表：{e}')

                # 国际交易
                try:
                    int_sheet = next((name for name in pd.ExcelFile(nabtrade_file).sheet_names
                                      if ' '.join(name.split()) == 'Int. Portfolio Transactions'), None)
                    if int_sheet is None:
                        raise ValueError('Worksheet named international not found')
                    df_int = pd.read_excel(nabtrade_file, sheet_name=int_sheet, skiprows=1)
                    df_int = capture_sales(df_int, f'{nabtrade_file.name}/{int_sheet}', 'US',
                                           'Date', 'Code', 'Movement Type', 'Quantity', 'Transaction Price (Exch CCY)', 3)
                    df_int = df_int.dropna(subset=['Date', 'Code', 'Movement Type'])
                    for row_index, row in df_int.iterrows():
                        try:
                            tx_type = transaction_type(row['Movement Type'])
                            ticker = security_code(row['Code'], 'US')
                            date = transaction_date(row['Date'], 'US')
                            if date >= fy_exclusive_end:
                                continue
                            if tx_type in transfer_types:
                                record_transfer(row, tx_type, f'Nabtrade/{int_sheet}', row_index + 3, ticker, 'Date', 'Quantity', market='US')
                                continue
                            if 'TRANSFER' in tx_type:
                                uncertain_assets.add(('US', ticker))
                                unresolved.append(f'Nabtrade 国际第 {row_index + 3} 行 {ticker}: 无法确认转仓类型')
                                continue
                            if tx_type in ['BUY', 'B', 'SELL', 'S'] and pd.isna(row.get('Average Price (AUD) *1', np.nan)):
                                uncertain_assets.add(('US', ticker))
                                unresolved.append(f'Nabtrade 国际第 {row_index + 3} 行 {ticker}: 缺少 AUD 单价，未把外币当作澳元')
                                mark_sale(row, '待换算 AUD，未计入澳元汇总', '原始卖出行已保留；缺少明确 AUD 单价')
                                continue
                            all_transactions.append({
                                'Date': date,
                                'Sale ID': row.get('_Sale ID', ''),
                                'Ticker': ticker,
                                'Market': 'US',
                                'Type': tx_type,
                                'Quantity': float(abs(row['Quantity'])),
                                'Signed Quantity': float(row['Quantity']),
                                'Reported Value': row.get('Value (AUD)', np.nan),
                                'Report': nabtrade_file.name,
                                'Account': str(row.get('Account', '')),
                                'Price': float(row.get('Average Price (AUD) *1', 0)),
                                'Fees': fee_value(row, 'Brokerage (AUD)') + fee_value(row, 'Other Fees (AUD)'),
                                'ROC Per Share': row.get('Capital Return Per Share (AUD)', np.nan),
                                'Source': f'{nabtrade_file.name}/{int_sheet} 第 {row_index + 3} 行'
                            })
                            mark_sale(row, '已读入，等待 FIFO 计算')
                        except Exception as exc:
                            mark_sale(row, '解析失败，原始卖出行已保留', str(exc))
                            parse_errors.append(f'{nabtrade_file.name}/{int_sheet} 第 {row_index + 3} 行' + f'：{exc}')
                            uncertain_assets.add(('US', ticker))
                    st.success(f"✅ 成功载入原券商国际交易记录: {len(df_int)} 条")
                except Exception as e:
                    if 'Worksheet named' not in str(e):
                        parse_errors.append(f'Nabtrade 国际工作表：{e}')

            # --- 2. 解析多个 Stake 文件 ---
            if stake_files:
                stake_sheets = ['Aus Equities', 'Wall St Equities']
                for file_number, f_file in enumerate(stake_files):
                    st.caption(f"🔍 发现符合条件的 Stake 文件: {f_file.name}")
                    for sheet in stake_sheets:
                        try:
                            df_stake = pd.read_excel(f_file, sheet_name=sheet)
                            if df_stake.empty:
                                continue

                            df_stake.columns = df_stake.columns.str.strip()
                            market = 'AU' if sheet == 'Aus Equities' else 'US'
                            df_stake = capture_sales(df_stake, f'{f_file.name}/{sheet}', market,
                                                     'Trade Date', 'Symbol', 'Side', 'Units', 'Avg. Price', 2)
                            df_stake = df_stake.dropna(subset=['Trade Date', 'Symbol', 'Side'])

                            s_count = 0
                            d_skipped = 0
                            occurrences = {}
                            for row_index, row in df_stake.iterrows():
                                try:
                                    tx_type = transaction_type(row['Side'])
                                    market = 'AU' if sheet == 'Aus Equities' else 'US'
                                    ticker = security_code(row['Symbol'], market)
                                    date = transaction_date(row['Trade Date'], market)
                                    if date >= fy_exclusive_end:
                                        continue
                                    if tx_type in transfer_types:
                                        record_transfer(row, tx_type, f'{f_file.name}/{sheet}', row_index + 2, ticker, 'Trade Date', 'Units', market=market)
                                        continue
                                    if 'TRANSFER' in tx_type:
                                        uncertain_assets.add((market, ticker))
                                        unresolved.append(f'{f_file.name}/{sheet} 第 {row_index + 2} 行 {ticker}: 无法确认转仓类型')
                                        continue
                                    if tx_type not in ['BUY', 'SELL', 'B', 'S']:
                                        continue

                                    price = row['Avg. Price']
                                    fees = fee_value(row, 'Fees') + fee_value(row, 'GST')
                                    if market == 'US' and str(row.get('Currency', '')).strip().upper() != 'AUD':
                                        price = row.get('Avg. Price (AUD)', np.nan)
                                        if pd.isna(price) or (fees and pd.isna(row.get('Fees (AUD)', np.nan))):
                                            uncertain_assets.add((market, ticker))
                                            unresolved.append(f'{f_file.name}/{sheet} 第 {row_index + 2} 行 {ticker}: 缺少明确 AUD 价格/费用，未混算币种')
                                            mark_sale(row, '待换算 AUD，未计入澳元汇总', '原始卖出行已保留；原币价格/费用不可直接作为 AUD')
                                            continue
                                        fees = fee_value(row, 'Fees (AUD)') + fee_value(row, 'GST (AUD)')

                                    # 精准安全去重标识
                                    raw_id = row.get('Trade Identifier', '')
                                    trade_id = '' if pd.isna(raw_id) else str(raw_id).strip()
                                    canonical_side = {'B': 'BUY', 'S': 'SELL'}.get(tx_type, tx_type)
                                    if not trade_id:
                                        fingerprint = (str(date), ticker, canonical_side,
                                                       float(abs(row['Units'])), float(price), float(fees))
                                        occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
                                        trade_id = ('no-id', fingerprint, occurrences[fingerprint])
                                    else:
                                        trade_id = ('id', trade_id)
                                    trade_id = (sheet, str(row.get('Account Number', '')), trade_id)

                                    current_content = (date, ticker, tx_type, float(abs(row['Units'])), float(price), float(fees))
                                    previous = seen_stake_identifiers.setdefault(trade_id, [])
                                    if any(content == current_content and other_file != file_number for content, other_file in previous):
                                        d_skipped += 1
                                        mark_sale(row, '重复记录', '跨文件编号/指纹及成交内容一致，未重复计入')
                                        continue
                                    if previous:
                                        unresolved.append(f'{f_file.name}/{sheet} 第 {row_index + 2} 行：同一编号重复出现或内容不同，原始成交行均保留待核对')
                                    previous.append((current_content, file_number))

                                    all_transactions.append({
                                        'Date': date,
                                        'Sale ID': row.get('_Sale ID', ''),
                                        'Ticker': ticker,
                                        'Market': market,
                                        'Type': tx_type,
                                        'Quantity': float(abs(row['Units'])),
                                        'Price': float(price),
                                        'Fees': float(fees),
                                        'Source': f'{f_file.name}/{sheet} 第 {row_index + 2} 行'
                                    })
                                    mark_sale(row, '已读入，等待 FIFO 计算')
                                    s_count += 1
                                except Exception as exc:
                                    mark_sale(row, '解析失败，原始卖出行已保留', str(exc))
                                    parse_errors.append(f'{f_file.name}/{sheet} 第 {row_index + 2} 行' + f'：{exc}')
                                    uncertain_assets.add((market, ticker))
                            if s_count > 0:
                                st.success(f"✅ 来自 [{f_file.name}] 的 [{sheet}]: 成功载入新记录 {s_count} 条")
                        except Exception as e:
                            if 'Worksheet named' not in str(e):
                                parse_errors.append(f'{f_file.name}/{sheet}：{e}')

            if transfer_records:
                message = f'检测到 {len(transfer_records)} 条普通转仓记录：不作为新买卖、不重置历史购入日期或成本。'
                if same_owner:
                    st.info(message + '受益所有权未变由用户确认；未进行自动身份验证。')
                else:
                    st.warning(message + '受益所有权尚未核实，下面的金额仅供核对。')
                with st.expander('查看转仓记录'):
                    st.dataframe(pd.DataFrame(transfer_records), hide_index=True, width='stretch')

            # --- 3. 运行 FIFO 清算引擎（保持原版全局历史匹配） ---
            if not all_transactions:
                show_calculation_notes()
                show_sales([])
                st.error("❌ 未能在您上传的所有 Excel 中解析到合规的 BUY/SELL 交易。")
            else:
                all_transactions = prepare_corporate_actions(all_transactions)
                df_all = pd.DataFrame(all_transactions)
                df_all = df_all.sort_values('Date', kind='stable').reset_index(drop=True)

                type_map = {'BUY': 'BUY', 'B': 'BUY', 'SELL': 'SELL', 'S': 'SELL', 'RETURN OF CAPITAL': 'RETURN_OF_CAPITAL', 'ROC': 'RETURN_OF_CAPITAL'}
                df_all['Type'] = df_all['Type'].map(type_map).fillna(df_all['Type'])

                portfolio = {}
                cgt_events = []

                for idx, row in df_all.iterrows():
                    ticker = row['Ticker']
                    action = row['Type']
                    date = row['Date']
                    qty = row['Quantity']
                    price = row['Price']
                    fees = row['Fees']
                    source = row['Source']

                    asset = (row['Market'], ticker)
                    if date >= fy_exclusive_end:
                        continue
                    if action in ('DIVIDEND', 'INTEREST', 'CASH', 'DEPOSIT', 'WITHDRAWAL'):
                        continue
                    if action in ('PAIRED_ACTION_LEG', 'ACQUIRE_UNVERIFIED', 'REMOVE_UNVERIFIED', 'MOVE_LOTS', 'EXERCISE_RIGHTS'):
                        if not np.isfinite(qty) or qty <= 0:
                            unresolved.append(f'{source}: 公司行动数量无效')
                            continue
                        apply_corporate_action(row, portfolio)
                        continue
                    if action not in ('BUY', 'SELL', 'RETURN_OF_CAPITAL', 'TRANSFER_CHECK'):
                        uncertain_assets.add(asset)
                        unresolved.append(f'{source} {ticker} {date.date()}: {action} 需核对是否涉及公司行动或成本调整')
                        continue
                    if (not all(np.isfinite(value) for value in (qty, price, fees)) or qty <= 0 or price < 0):
                        uncertain_assets.add(asset)
                        unresolved.append(f'{source} {ticker} {date}: 数量、价格或费用无效')
                        mark_sale(row, '数量/价格/费用无效，待核对', '原始卖出行保留，未按零盈利处理')
                        continue
                    if asset not in portfolio:
                        portfolio[asset] = []

                    if action == 'TRANSFER_CHECK':
                        holding = sum(lot['qty'] for lot in portfolio[asset])
                        if qty > holding + 1e-9:
                            uncertain_assets.add(asset)
                            unresolved.append(f'{source} {ticker} {date.date()}: 转仓数量 {qty:g} 超过已知历史持仓 {holding:g}，需核对原始买入')
                        continue

                    if action == 'RETURN_OF_CAPITAL':
                        total_holding = sum([lot['qty'] for lot in portfolio[asset]])
                        reduction_per_share = row.get('ROC Per Share', np.nan)
                        if pd.isna(reduction_per_share) or not np.isfinite(float(reduction_per_share)) or float(reduction_per_share) < 0:
                            uncertain_assets.add(asset)
                            unresolved.append(f'{source} {ticker} {date.date()}: 资本返还缺少明确的每股 AUD 金额，未猜测成本调整')
                            continue
                        reduction_per_share = float(reduction_per_share)
                        if total_holding > 0:
                            for lot in portfolio[asset]:
                                if not lot.get('cost_known', True) or not np.isfinite(lot['price']):
                                    if fy_start <= date < fy_exclusive_end:
                                        unresolved.append(f'{source} {ticker}: 资本返还涉及尚未确认的公司行动成本，未猜测超额利得')
                                    continue
                                original_cost = lot['price'] + lot['fees'] / lot['qty']
                                excess = max(0.0, reduction_per_share - original_cost)
                                if excess > 0 and fy_start <= date < fy_exclusive_end:
                                    cgt_events.append({'Ticker': ticker, 'Market': asset[0],
                                                       'Buy Date': lot['date'].strftime('%Y-%m-%d'),
                                                       'Sell Date': date.strftime('%Y-%m-%d'), 'Quantity': lot['qty'],
                                                       'Category': 'Held >= 1 Year (Discountable)' if held_over_twelve_months(lot['date'], date) else 'Held < 1 Year (No Discount)',
                                                       'Net Gain/Loss': excess * lot['qty'], 'Buy Platform': lot['source'], 'Sell Platform': source})
                                lot['price'] = max(0.0, original_cost - reduction_per_share)
                                lot['fees'] = 0.0
                        else:
                            uncertain_assets.add(asset)
                            unresolved.append(f'{source} {ticker} {date.date()}: 资本返还缺少历史持仓')
                        continue

                    if action == 'BUY':
                        portfolio[asset].append({
                            'date': date, 'qty': qty, 'price': price, 'fees': fees, 'source': source
                        })
                    elif action == 'SELL':
                        qty_to_sell = qty
                        quantity_tolerance = np.finfo(float).eps * max(1.0, qty) * 16
                        sell_fee_share = fees / qty_to_sell if qty_to_sell > 0 else 0

                        while qty_to_sell > quantity_tolerance:
                            if not portfolio[asset]:
                                uncertain_assets.add(asset)
                                unresolved.append(f'{source} {ticker} {date.date()}: {qty_to_sell:g} 股卖出未匹配到原始买入成本；需检查导入、代码及去重，未按零盈利处理')
                                mark_sale(row, '部分或全部买入成本未匹配', f'{qty_to_sell:g} 股尚未找到买入批次；需核对已读入的买入，已匹配批次仍保留')
                                break

                            first_lot = portfolio[asset][0]
                            buy_date = first_lot['date']
                            is_discounted = held_over_twelve_months(buy_date, date)

                            matched_qty = min(qty_to_sell, first_lot['qty'])
                            buy_fee_share = first_lot['fees'] / first_lot['qty'] if first_lot['qty'] > 0 else 0
                            cost_known = first_lot.get('cost_known', True) and np.isfinite(first_lot['price'])
                            cost_base_per_share = first_lot['price'] + buy_fee_share if cost_known else np.nan
                            proceeds_per_share = price - sell_fee_share
                            total_gain_lot = (proceeds_per_share - cost_base_per_share) * matched_qty

                            if fy_start <= date < fy_exclusive_end:
                                if not cost_known:
                                    uncertain_assets.add(asset)
                                    unresolved.append(f'{source} {ticker}: 数量已匹配，但对应公司行动税务成本需核对，不按零成本计算')
                                cgt_events.append({
                                    'Ticker': ticker,
                                    'Sale ID': row.get('Sale ID', ''),
                                    'Market': asset[0],
                                    'Buy Date': buy_date.strftime('%Y-%m-%d'),
                                    'Sell Date': date.strftime('%Y-%m-%d'),
                                    'Quantity': matched_qty,
                                    'Buy Unit Price AUD': first_lot['price'],
                                    'Buy Fee Per Share AUD': buy_fee_share,
                                    'Matched Cost Base AUD': cost_base_per_share * matched_qty,
                                    'Sell Unit Price AUD': price,
                                    'Sell Fee Per Share AUD': sell_fee_share,
                                    'Matched Net Proceeds AUD': proceeds_per_share * matched_qty,
                                    'Cost Status': '已确认' if cost_known else '公司行动成本待核对',
                                    'Category': ('Held >= 1 Year (Discountable)' if is_discounted else 'Held < 1 Year (No Discount)') if cost_known else 'Cost/Acquisition Unverified',
                                    'Net Gain/Loss': total_gain_lot,
                                    'Buy Platform': first_lot['source'],
                                    'Sell Platform': source
                                })

                            qty_to_sell -= matched_qty
                            first_lot['fees'] = max(0.0, first_lot['fees'] - buy_fee_share * matched_qty)
                            first_lot['qty'] -= matched_qty
                            if first_lot['qty'] <= quantity_tolerance:
                                portfolio[asset].pop(0)

                # Preserve every matched FIFO batch. An unresolved record must
                # never delete other sale profits just because the ticker matches.
                if start_year > 2025:
                    unresolved.append('当前使用原版个人 50% 折扣模型；2026-27 及以后财年政策未验证，金额仅供核对')
                show_calculation_notes()
                if corporate_records:
                    st.info(f'已识别 {len(corporate_records)} 项增持/公司行动；数量已参与库存，成本或重组条件待核对的记录保留在明细中。')
                    with st.expander('查看增持及公司行动处理明细'):
                        st.dataframe(pd.DataFrame(corporate_records), hide_index=True, width='stretch')
                show_sales(cgt_events)
                with st.expander('已读取的历史买入记录（核对导入是否完整）'):
                    buys = df_all[df_all['Type'] == 'BUY'][['Date', 'Market', 'Ticker', 'Quantity', 'Price', 'Fees', 'Source']]
                    st.dataframe(buys, hide_index=True, width='stretch')
                    st.download_button('下载已读入买入记录 CSV', buys.to_csv(index=False).encode('utf-8-sig'),
                                       file_name='imported_buys.csv', mime='text/csv', on_click='ignore')
                is_complete = not (parse_errors or unresolved or (transfer_records and not same_owner))

                # ==========================================
                # 📢 移动端完美适配的 UI 渲染设计
                # ==========================================
                st.markdown("---")
                st.header(f"🇦🇺 澳洲 ATO {TARGET_FY} 财年 CGT 申报清算单")
                st.caption("✨ 跨财年多文件融合版 · 全局全历史采购关联")

                if not cgt_events:
                    st.info('该财年没有可计算的已知处置记录。' if not is_complete else '该财年未检测到卖出或资本返还利得；其他收入和 CGT 事件仍需另外核对。')
                    if is_complete and opening_loss:
                        st.metric('往年资本亏损继续结转', f'${opening_loss:,.2f}')
                else:
                    cgt_df = pd.DataFrame(cgt_events)

                    # 1. 严格拆分提取原始（Gross）长、短期独立盈亏项
                    lt_mask = cgt_df['Category'].str.contains('Held >= 1 Year')
                    st_mask = cgt_df['Category'].str.contains('Held < 1 Year')

                    lt_gains = cgt_df[lt_mask & (cgt_df['Net Gain/Loss'] > 0)]['Net Gain/Loss'].sum()
                    lt_losses = abs(cgt_df[lt_mask & (cgt_df['Net Gain/Loss'] < 0)]['Net Gain/Loss'].sum())

                    st_gains = cgt_df[st_mask & (cgt_df['Net Gain/Loss'] > 0)]['Net Gain/Loss'].sum()
                    st_losses = abs(cgt_df[st_mask & (cgt_df['Net Gain/Loss'] < 0)]['Net Gain/Loss'].sum())

                    # 汇总展示数据指标用
                    short_term_total = st_gains - st_losses
                    long_term_total = lt_gains - lt_losses
                    all_gains = lt_gains + st_gains
                    all_losses = lt_losses + st_losses
                    raw_net_result = all_gains - all_losses

                    # ----------------------------------------------------
                    # ATO 官方标准核心算法对冲状态机（修正亏损抵扣顺序错误）
                    # ----------------------------------------------------
                    total_losses_to_offset = all_losses + opening_loss

                    # 先抵扣不享受折扣的盈利，再抵扣可折扣盈利。
                    short_offset = min(st_gains, total_losses_to_offset)
                    net_st_gain = st_gains - short_offset
                    total_losses_to_offset -= short_offset
                    long_offset = min(lt_gains, total_losses_to_offset)
                    net_lt_gain = lt_gains - long_offset
                    total_losses_to_offset -= long_offset
                    taxable_gain = net_st_gain + net_lt_gain * 0.5
                    carried_loss = max(0.0, total_losses_to_offset)

                    # ----------------------------------------------------
                    # 【核心模块一】：ATO 最终报税申报结论 & 最终计入总收入统计
                    # ----------------------------------------------------
                    st.subheader('📢 ATO 股票 CGT 计算结果' if is_complete else '📢 已知交易小计（待核对，非最终报税金额）')
                    if taxable_gain > 0:
                        st.success(f"""
                        **1. {'计入收入的净资本利得' if is_complete else '已知交易按当前假设计算的净资本利得小计（非最终报税金额）'}：**
                        ## **${taxable_gain:,.2f}**

                        **2. 减免明细预览（50% Individual Discount 节省额）：**
                        * 当期折扣前净收益（未扣往年亏损）: `${raw_net_result:,.2f}`
                        * 剩余长期盈利的 50% CGT 折扣金额（不是节省的税款）: `${(net_lt_gain * 0.5):,.2f}`

                        *💡 该系统已帮您自动应用折减。上述高亮的倍数金额将作为【应纳税所得（Assessable Income）】直接并入您当期的个人综合总收入（包含工资、利息、海外收入等），并最终按您的年度个人边际税率（Marginal Tax Rate）计税。*
                        """)
                    else:
                        st.error(f"""
                        **1. {'计入收入的净资本利得' if is_complete else '已知交易净资本利得小计（非最终报税金额）'}：** ## **$0.00**
                        **2. {'可结转的资本亏损' if is_complete else '已知交易计算的亏损余额（待核对）'}：** ## **${carried_loss:,.2f}**

                        *💡 注：在澳洲，股票净亏损不能用来抵扣当年的打工工资收入（Salary/Wages），只能锁死在税务账户中，永久结转用于抵扣未来年份的股票或房产等资本增值盈利。*
                        """)

                    # ----------------------------------------------------
                    # 【核心模块二】：整体原始资本账目汇总（手机端自动向下排列）
                    # ----------------------------------------------------
                    st.subheader("📊 整体原始资本账目汇总 (Gross)")

                    # 利用 st.columns 实现移动端自动流式堆叠（PC端并排，手机端自动变单行纵向列表）
                    m_col1, m_col2 = st.columns(2)
                    m_col1.metric("当期短期持有(未满一年)变现", f"${short_term_total:,.2f}")
                    m_col2.metric("当期长期持有(满一年以上)变现", f"${long_term_total:,.2f}")

                    m_col3, m_col4 = st.columns(2)
                    m_col3.metric("本财年纯盈利项加总", f"${all_gains:,.2f}")
                    m_col4.metric("本财年纯亏损项加总", f"-${all_losses:,.2f}")

                    # ----------------------------------------------------
                    # 【核心模块三】：各资产独立结算明细（卡片流布局，免除横向滚动）
                    # ----------------------------------------------------
                    st.subheader("📋 各资产独立结算明细")
                    st.download_button('下载已知交易逐批次明细 CSV', cgt_df.to_csv(index=False).encode('utf-8-sig'),
                                       file_name=f'cgt_{TARGET_FY}.csv', mime='text/csv', on_click='ignore')
                    with st.expander('每笔卖出对应的买入批次、成本及手续费'):
                        st.dataframe(cgt_df, hide_index=True, width='stretch')

                    asset_summary = cgt_df.groupby(['Market', 'Ticker', 'Category'])['Net Gain/Loss'].sum(min_count=1).reset_index()

                    for _, row in asset_summary.iterrows():
                        ticker = f"{row['Market']}:{row['Ticker']}"
                        cat = row['Category']
                        gain_loss = row['Net Gain/Loss']

                        # 根据盈亏赋予不同的视觉前缀和颜色
                        if pd.isna(gain_loss):
                            status_badge = '⚪ 公司行动成本待核对'
                            bg_color = 'rgba(128, 128, 128, 0.1)'
                        elif gain_loss >= 0:
                            status_badge = "🟢 净盈利"
                            bg_color = "rgba(40, 167, 69, 0.1)"
                        else:
                            status_badge = "🔴 净亏损"
                            bg_color = "rgba(220, 53, 69, 0.1)"

                        # 移动端友好型卡片：用 Markdown 容器模拟，文字自动换行适配屏幕宽度
                        st.markdown(
                            f"""
                            <div style="
                                padding: 12px;
                                border-radius: 8px;
                                background-color: {bg_color};
                                margin-bottom: 8px;
                                border-left: 5px solid {'#28a745' if gain_loss >= 0 else '#dc3545'};
                            ">
                                <b style="font-size:16px;">资产代码: {ticker}</b><br/>
                                <span style="font-size:13px; color:#6c757d;">持有类型: {cat}</span><br/>
                                <span style="font-size:14px; font-weight:bold;">{status_badge}: {'暂不可计算' if pd.isna(gain_loss) else f'${abs(gain_loss):,.2f}'}</span>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )
else:
    st.info("💡 请在左侧侧边栏上传相应的 Nabtrade/Stake 表格，并点击 [开始融合账目并计算] 按钮。")
