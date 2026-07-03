import streamlit as st
import pandas as pd
import numpy as np

st.title("🔍 Streamlit 数据读取底层诊断诊断工具")
st.markdown("由于 Colab 和 Streamlit 环境对 Excel 文件流的处理可能存在差异，我们通过此工具打印底层真实数据。")

# 文件上传组件
nabtrade_file = st.sidebar.file_uploader("1. 上传 Nabtrade 报告", type=["xlsx", "xls"])
stake_files = st.sidebar.file_uploader("2. 上传 Stake 报告 (支持多选)", type=["xlsx", "xls"], accept_multiple_files=True)

run_diagnostic = st.sidebar.button("🚀 开始诊断底层数据结构")

if run_diagnostic:
    # ==========================================
    # 诊断 1：Nabtrade 文件
    # ==========================================
    st.header("📦 1. Nabtrade 文件诊断结果")
    if nabtrade_file is not None:
        try:
            xl_nab = pd.ExcelFile(nabtrade_file)
            st.success(f"成功读取 Nabtrade 文件！包含的标签页 (Sheets): `{xl_nab.sheet_names}`")
            
            for sheet in xl_nab.sheet_names:
                st.markdown(f"---")
                st.subheader(f"标签页: `{sheet}`")
                
                # 模拟您原本代码中的 skiprows=1
                if 'Transactions' in sheet:
                    df = pd.read_excel(nabtrade_file, sheet_name=sheet, skiprows=1)
                else:
                    df = pd.read_excel(nabtrade_file, sheet_name=sheet)
                
                st.write(f"📊 该页原始形状 (Shape): {df.shape} (行数, 列数)")
                st.write("📋 识别到的原始列名 (Columns):", list(df.columns))
                st.write("👀 前 3 行原始数据样例:")
                st.dataframe(df.head(3), use_container_width=True)
        except Exception as e:
            st.error(f"读取 Nabtrade 时发生异常: {str(e)}")
    else:
        st.warning("未上传 Nabtrade 文件。")

    # ==========================================
    # 诊断 2：Stake 文件
    # ==========================================
    st.header("📦 2. Stake 文件诊断结果")
    if stake_files:
        try:
            for idx, f_file in enumerate(stake_files):
                xl_stake = pd.ExcelFile(f_file)
                st.success(f"成功读取第 {idx+1} 个 Stake 文件！包含的标签页 (Sheets): `{xl_stake.sheet_names}`")
                
                for sheet in xl_stake.sheet_names:
                    st.markdown(f"---")
                    st.subheader(f"文件 {idx+1} -> 标签页: `{sheet}`")
                    
                    df_stake = pd.read_excel(f_file, sheet_name=sheet)
                    st.write(f"📊 该页原始形状 (Shape): {df_stake.shape} (行数, 列数)")
                    
                    # 打印清洗前后的列名，看是否有不可见字符
                    raw_cols = list(df_stake.columns)
                    stripped_cols = [str(c).strip() for c in df_stake.columns]
                    st.write("📋 原始列名 (Raw Columns):", raw_cols)
                    if raw_cols != stripped_cols:
                        st.warning("⚠️ 警告：检测到列名两端带有隐藏的空格或换行符！")
                    
                    st.write("👀 前 3 行原始数据样例:")
                    st.dataframe(df_stake.head(3), use_container_width=True)
                    
                    # 检查您的核心判断列
                    target_cols = ['Trade Date', 'Symbol', 'Side', 'Units', 'Avg. Price']
                    missing = [c for c in target_cols if c not in stripped_cols]
                    if missing:
                        st.error(f"❌ 严重错误：按照您的代码逻辑，该页缺失了以下必要列: {missing}")
                    else:
                        st.success("✅ 必要列（Trade Date, Symbol, Side 等）均存在。")
                        
                        # 打印 Side 列的独特值，看看究竟是不是 BUY/SELL
                        if 'Side' in df_stake.columns:
                            st.info(f"💡 Side 列中的唯一值有: {df_stake['Side'].dropna().unique()}")
                            
        except Exception as e:
            st.error(f"读取 Stake 时发生异常: {str(e)}")
    else:
        st.warning("未上传 Stake 文件。")

else:
    st.info("💡 请在左侧上传您的原始 Excel 文件，然后点击 [🚀 开始诊断底层数据结构] 按钮。")
