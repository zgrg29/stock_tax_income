import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime

# ==========================================
# Streamlit 页面基础配置
# ==========================================
st.set_page_config(
    page_title="澳洲 ATO 跨券商股票 CGT 报税清算系统",
    page_icon="🇦🇺",
    layout="wide"
)

st.title("🇦🇺 澳洲 ATO 股票资本利得税 (CGT) 联合清算系统")
st.markdown("""
本工具支持跨券商、跨历史时期的股票库存撮合。当您在 Stake 卖出股票时，系统将通过 **FIFO (先进先出)** 引擎自动追溯您早前在 **Nabtrade** 的初始买入成本及持有天数，并自动适用澳洲满一年的 **50% CGT 税收减免 (Individual Discount)**。
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

st.sidebar.markdown("---")
st.sidebar.header("📁 第二步：上传券商原始 Excel")

# 1. 上传 Nabtrade 报告
nabtrade_file = st.sidebar.file_uploader(
    "上传原券商报告 (Nabtrade)", 
    type=["xlsx", "xls"], 
    help="请上传包含 'Domestic Portfolio Transactions' 或 'Int.  Portfolio Transactions' 工作表的完整 Excel 报告。"
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
seen_stake_identifiers = set()

# 开始处理按钮
run_analysis = st.sidebar.button("🚀 开始融合账目并计算")

# ==========================================
# 核心数据处理逻辑
# ==========================================
if run_analysis:
    if not nabtrade_file and not stake_files:
        st.error("❌ 请至少上传一个 Nabtrade 或 Stake 的 Excel 文件后再点击运行。")
    else:
        with st.spinner("正在解析数据并进行跨平台 FIFO 库存撮合，请稍候..."):
            
            # --- 1. 解析 Nabtrade 文件 ---
            if nabtrade_file is not None:
                # 国内交易
                try:
                    df_dom = pd.read_excel(nabtrade_file, sheet_name='Domestic Portfolio Transactions', skiprows=1)
                    df_dom = df_dom.dropna(subset=['Date', 'Code', 'Movement Type'])
                    for _, row in df_dom.iterrows():
                        tx_type = str(row['Movement Type']).upper().strip()
                        if 'TRANSFER' in tx_type:
                            continue
                        all_transactions.append({
                            'Date': pd.to_datetime(row['Date']).tz_localize(None),
                            'Ticker': str(row['Code']).split('.')[0].strip(),
                            'Type': tx_type,
                            'Quantity': float(abs(row['Quantity'])),
                            'Price': float(row['Transaction Price']),
                            'Fees': float(row.get('Brokerage', 0)) + float(row.get('Other Fees', 0)),
                            'Source': 'Nabtrade_Domestic'
                        })
                    st.success(f"✅ 成功载入 Nabtrade 国内交易记录: {len(df_dom)} 条")
                except Exception as e:
                    st.info(f"ℹ️ 未检测到 Nabtrade 国内交易记录或解析跳过。")

                # 国际交易
                try:
                    df_int = pd.read_excel(nabtrade_file, sheet_name='Int.  Portfolio Transactions', skiprows=1)
                    df_int = df_int.dropna(subset=['Date', 'Code', 'Movement Type'])
                    for _, row in df_int.iterrows():
                        tx_type = str(row['Movement Type']).upper().strip()
                        if 'TRANSFER' in tx_type:
                            continue
                        all_transactions.append({
                            'Date': pd.to_datetime(row['Date']).tz_localize(None),
                            'Ticker': str(row['Code']).strip(),
                            'Type': tx_type,
                            'Quantity': float(abs(row['Quantity'])),
                            'Price': float(row['Average Price (AUD) *1']) if 'Average Price (AUD) *1' in row else float(row['Transaction Price (Exch CCY)']),
                            'Fees': float(row.get('Brokerage (AUD)', 0)) + float(row.get('Other Fees (AUD)', 0)),
                            'Source': 'Nabtrade_International'
                        })
                    st.success(f"✅ 成功载入 Nabtrade 国际交易记录: {len(df_int)} 条")
                except Exception as e:
                    st.info(f"ℹ️ 未检测到 Nabtrade 国际交易记录或解析跳过。")

            # --- 2. 解析多个 Stake 文件 ---
            if stake_files:
                stake_sheets = ['Aus Equities', 'Wall St Equities']
                for f_file in stake_files:
                    for sheet in stake_sheets:
                        try:
                            df_stake = pd.read_excel(f_file, sheet_name=sheet)
                            if df_stake.empty:
                                continue
                            
                            df_stake.columns = df_stake.columns.str.strip()
                            df_stake = df_stake.dropna(subset=['Trade Date', 'Symbol', 'Side'])
                            
                            s_count = 0
                            d_skipped = 0
                            for _, row in df_stake.iterrows():
                                tx_type = str(row['Side']).upper().strip()
                                if tx_type not in ['BUY', 'SELL', 'B', 'S']:
                                    continue
                                
                                # 精准安全去重标识
                                trade_id = str(row.get('Trade Identifier', '')).strip()
                                if not trade_id:
                                    trade_id = f"{row['Trade Date']}_{row['Symbol']}_{tx_type}_{row['Units']}_{row['Avg. Price']}"
                                
                                if trade_id in seen_stake_identifiers:
                                    d_skipped += 1
                                    continue
                                
                                seen_stake_identifiers.add(trade_id)
                                
                                all_transactions.append({
                                    'Date': pd.to_datetime(row['Trade Date']).tz_localize(None),
                                    'Ticker': str(row['Symbol']).strip(),
                                    'Type': tx_type,
                                    'Quantity': float(abs(row['Units'])),
                                    'Price': float(row['Avg. Price']),
                                    'Fees': float(row.get('Fees', 0)) + float(row.get('GST', 0)),
                                    'Source': f'Stake_{sheet.split()[0]}'
                                })
                                s_count += 1
                            if s_count > 0:
                                st.success(f"✅ 成功从 Stake 文件 [{f_file.name}] - [{sheet}] 中载入新记录: {s_count} 条 (查重安全跳过: {d_skipped} 条)")
                        except:
                            pass

            # --- 3. 运行 FIFO 清算引擎 ---
            if not all_transactions:
                st.error("❌ 未能在您上传的所有 Excel 中解析到合规的 BUY/SELL 交易，请确认格式是否正确。")
            else:
                df_all = pd.DataFrame(all_transactions)
                # 严格按全局时间线升序排列
                df_all = df_all.sort_values('Date').reset_index(drop=True)
                
                # 规范化动作名称
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
                    
                    if ticker not in portfolio:
                        portfolio[ticker] = []
                        
                    if action == 'RETURN_OF_CAPITAL':
                        total_holding = sum([lot['qty'] for lot in portfolio[ticker]])
                        if total_holding > 0:
                            reduction_per_share = price / total_holding if price > total_holding else price
                            for lot in portfolio[ticker]:
                                lot['price'] = max(0.0, lot['price'] - reduction_per_share)
                        continue
                        
                    if action == 'BUY':
                        portfolio[ticker].append({
                            'date': date, 'qty': qty, 'price': price, 'fees': fees, 'source': source
                        })
                    elif action == 'SELL':
                        qty_to_sell = qty
                        sell_fee_share = fees / qty_to_sell if qty_to_sell > 0 else 0
                        
                        while qty_to_sell > 0:
                            if not portfolio[ticker]:
                                if fy_start <= date <= fy_end:
                                    cgt_events.append({
                                        'Ticker': ticker, 'Buy Date': '历史不可考', 'Sell Date': date.strftime('%Y-%m-%d'),
                                        'Quantity': qty_to_sell, 'Category': '持有未满1年 (无减免) [缺失买入记录]',
                                        'Net Gain/Loss': 0.0, 'Buy Platform': '未知', 'Sell Platform': source
                                    })
                                break
                                
                            first_lot = portfolio[ticker][0]
                            buy_date = first_lot['date']
                            days_held = (date - buy_date).days
                            is_discounted = days_held >= 365
                            
                            matched_qty = min(qty_to_sell, first_lot['qty'])
                            buy_fee_share = first_lot['fees'] / first_lot['qty'] if first_lot['qty'] > 0 else 0
                            cost_base_per_share = first_lot['price'] + buy_fee_share
                            proceeds_per_share = price - sell_fee_share
                            total_gain_lot = (proceeds_per_share - cost_base_per_share) * matched_qty
                            
                            if fy_start <= date <= fy_end:
                                cgt_events.append({
                                    'Ticker': ticker,
                                    'Buy Date': buy_date.strftime('%Y-%m-%d'),
                                    'Sell Date': date.strftime('%Y-%m-%d'),
                                    'Quantity': matched_qty,
                                    'Category': '持有满1年以上 (享50%减免)' if is_discounted else '持有未满1年 (无减免)',
                                    'Net Gain/Loss': total_gain_lot,
                                    'Buy Platform': first_lot['source'],
                                    'Sell Platform': source
                                })
                                
                            qty_to_sell -= matched_qty
                            first_lot['qty'] -= matched_qty
                            if first_lot['qty'] <= 0:
                                portfolio[ticker].pop(0)

                # ==========================================
                # 渲染报税可视化结果
                # ==========================================
                st.markdown(f"### 📊 清算账目报告：财年 {TARGET_FY}")
                st.info(f"统计周期: {fy_start.date()} 至 {fy_end.date()}")
                
                if not cgt_events:
                    st.warning(f"💡 该财年内 ({TARGET_FY}) 没有检测到任何资产变现卖出的动作，当期无需申报该部分的 CGT 资本利得收入。")
                else:
                    cgt_df = pd.DataFrame(cgt_events)
                    
                    # 算账指标汇总
                    short_term_total = cgt_df[cgt_df['Category'].str.contains('未满1年')]['Net Gain/Loss'].sum()
                    long_term_total = cgt_df[cgt_df['Category'].str.contains('满1年以上')]['Net Gain/Loss'].sum()
                    raw_net_result = short_term_total + long_term_total
                    
                    all_gains = cgt_df[cgt_df['Net Gain/Loss'] > 0]['Net Gain/Loss'].sum()
                    all_losses = abs(cgt_df[cgt_df['Net Gain/Loss'] < 0]['Net Gain/Loss'].sum())
                    
                    # 模拟 ATO 计算税额结论
                    if raw_net_result <= 0:
                        taxable_gain = 0.0
                        carried_loss = abs(raw_net_result)
                    else:
                        carried_loss = 0.0
                        net_long_term = max(0.0, long_term_total) if short_term_total >= 0 else max(0.0, long_term_total + short_term_total)
                        net_short_term = max(0.0, short_term_total) if short_term_total >= 0 else 0.0
                        taxable_gain = net_short_term + (net_long_term * 0.5)

                    # 第一排小卡片组件
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("纯盈利项目加总", f"${all_gains:,.2f}")
                    col2.metric("纯亏损项目加总", f"-${all_losses:,.2f}")
                    col3.metric("短期资本损益 (Gross)", f"${short_term_total:,.2f}")
                    col4.metric("长期资本损益 (Gross)", f"${long_term_total:,.2f}")
                    
                    # 报税核心结论看板
                    st.markdown("#### 📢 ATO 最终报税申报结论")
                    res_box = st.container()
                    with res_box:
                        if taxable_gain > 0:
                            st.success(f"➡️ **最终应填入税单中 [Taxable Net Capital Gain] 处的总金额:  `${taxable_gain:,.2f}`** \n*(注：已自动应用 50% 折扣。该金额将直接并入当期个人总收入，按您的年度个人边际税率计税)*")
                        else:
                            st.error(f"➡️ **最终应计入税单的净资本利得:  `$0.00`** \n➡️ **可永久结转至未来财年用于抵扣盈利的资本净亏损 (Capital Loss Carried Forward):  `${carried_loss:,.2f}`**")

                    # 明细表格展示
                    st.markdown("#### 🔍 详细成交对账流水清单 (包含跨平台匹配来源)")
                    # 格式化输出格式
                    display_df = cgt_df.copy()
                    display_df['Net Gain/Loss'] = display_df['Net Gain/Loss'].map(lambda x: f"${x:,.2f}")
                    st.dataframe(display_df, use_container_width=True)
                    
                    # 资产分类小计
                    st.markdown("#### 🏷️ 各资产独立结算统计")
                    asset_summary = cgt_df.groupby(['Ticker', 'Category'])['Net Gain/Loss'].sum().reset_index()
                    asset_summary['Net Gain/Loss'] = asset_summary['Net Gain/Loss'].map(lambda x: f"${x:,.2f}")
                    st.table(asset_summary)
else:
    st.info("💡 请在左侧侧边栏上传相应的 Nabtrade/Stake 表格，并点击 [开始融合账目并计算] 按钮。")
