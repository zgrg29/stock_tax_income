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
        # 实时处理日志在手机端单行展示
        st.info("🚀 开始进行跨多券商、多历史期持仓联合 CGT 结算分析...")
        st.caption(f"📅 目标财年: {TARGET_FY} ({fy_start.strftime('%Y-%m-%d')} 至 {fy_end.strftime('%Y-%m-%d')})")
        
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
                    st.success(f"✅ 成功载入原券商国内交易记录: {len(df_dom)} 条")
                except Exception as e:
                    pass

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
                    st.success(f"✅ 成功载入原券商国际交易记录: {len(df_int)} 条")
                except Exception as e:
                    st.success(f"✅ 成功载入原券商国际交易记录: 0 条")

            # --- 2. 解析多个 Stake 文件 ---
            if stake_files:
                stake_sheets = ['Aus Equities', 'Wall St Equities']
                for f_file in stake_files:
                    st.caption(f"🔍 发现符合条件的 Stake 文件: {f_file.name}")
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
                                st.success(f"✅ 来自 [{f_file.name}] 的 [{sheet}]: 成功载入新记录 {s_count} 条")
                        except:
                            pass

            # --- 3. 运行 FIFO 清算引擎 ---
            if not all_transactions:
                st.error("❌ 未能在您上传的所有 Excel 中解析到合规的 BUY/SELL 交易。")
            else:
                df_all = pd.DataFrame(all_transactions)
                df_all = df_all.sort_values('Date').reset_index(drop=True)
                
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
                                        'Quantity': qty_to_sell, 'Category': 'Held < 1 Year (No Discount) [缺失历史买入记录]',
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
                                    'Category': 'Held >= 1 Year (Discountable)' if is_discounted else 'Held < 1 Year (No Discount)',
                                    'Net Gain/Loss': total_gain_lot,
                                    'Buy Platform': first_lot['source'],
                                    'Sell Platform': source
                                })
                                
                            qty_to_sell -= matched_qty
                            first_lot['qty'] -= matched_qty
                            if first_lot['qty'] <= 0:
                                portfolio[ticker].pop(0)

                # ==========================================
                # 📢 移动端完美适配的 UI 渲染设计
                # ==========================================
                st.markdown("---")
                st.header(f"🇦🇺 澳洲 ATO {TARGET_FY} 财年 CGT 申报清算单")
                st.caption("✨ 跨财年多文件融合版 · 全局全历史采购关联")

                if not cgt_events:
                    st.warning("💡 该财年内没有检测到任何资产变现卖出动作，当期无需申报 CGT。")
                else:
                    cgt_df = pd.DataFrame(cgt_events)
                    
                    # 1. 计算各项核心指标
                    short_term_total = cgt_df[cgt_df['Category'].str.contains('Held < 1 Year')]['Net Gain/Loss'].sum()
                    long_term_total = cgt_df[cgt_df['Category'].str.contains('Held >= 1 Year')]['Net Gain/Loss'].sum()
                    raw_net_result = short_term_total + long_term_total
                    
                    all_gains = cgt_df[cgt_df['Net Gain/Loss'] > 0]['Net Gain/Loss'].sum()
                    all_losses = abs(cgt_df[cgt_df['Net Gain/Loss'] < 0]['Net Gain/Loss'].sum())
                    
                    if raw_net_result <= 0:
                        taxable_gain = 0.0
                        carried_loss = abs(raw_net_result)
                    else:
                        carried_loss = 0.0
                        net_long_term = max(0.0, long_term_total) if short_term_total >= 0 else max(0.0, long_term_total + short_term_total)
                        net_short_term = max(0.0, short_term_total) if short_term_total >= 0 else 0.0
                        taxable_gain = net_short_term + (net_long_term * 0.5)

                    # ----------------------------------------------------
                    # 【核心模块一】：ATO 最终报税申报结论（手机端置顶、显眼）
                    # ----------------------------------------------------
                    st.subheader("📢 ATO 报税最终结论")
                    if taxable_gain > 0:
                        st.success(f"""
                        **应填入税单 [Taxable Net Capital Gain] 处的总金额：**
                        ## **${taxable_gain:,.2f}**
                        
                        *💡 注：已自动应用 50% Individual Discount 减免。该金额将直接并入当期个人总收入，按您的年度个人边际税率计税。*
                        """)
                    else:
                        st.error(f"""
                        **最终应计入税单的净资本利得：** ## **$0.00**
                        **可结转至未来财年抵扣的净亏损 (Capital Loss Carried Forward)：** ## **${carried_loss:,.2f}**
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
                    
                    asset_summary = cgt_df.groupby(['Ticker', 'Category'])['Net Gain/Loss'].sum().reset_index()
                    
                    for _, row in asset_summary.iterrows():
                        ticker = row['Ticker']
                        cat = row['Category']
                        gain_loss = row['Net Gain/Loss']
                        
                        # 根据盈亏赋予不同的视觉前缀和颜色
                        if gain_loss >= 0:
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
                                <span style="font-size:14px; font-weight:bold;">{status_badge}: ${abs(gain_loss):,.2f}</span>
                            </div>
                            """, 
                            unsafe_allow_html=True
                        )
else:
    st.info("💡 请在左侧侧边栏上传相应的 Nabtrade/Stake 表格，并点击 [开始融合账目并计算] 按钮。")
