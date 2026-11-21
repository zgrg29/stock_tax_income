"""Streamlit interface for validated, AUD-denominated FIFO CGT calculations."""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from cgt import CalculationError, calculate_events, summarize
from importers import BrokerImporter, load_rates

st.set_page_config(page_title='澳洲股票 CGT 计算', page_icon='🇦🇺', layout='wide')
st.title('🇦🇺 澳洲跨券商股票 CGT 计算')
st.markdown('按 FIFO 撮合买卖，以澳元计算指定财年的净资本利得。股息、利息、ETF 分配和个人全部收入需另外汇总。')

st.sidebar.header('⚙️ 报税参数')
today = datetime.now(ZoneInfo('Australia/Sydney')).date()
latest_completed = today.year - (1 if today.month >= 7 else 2)
years = list(range(max(2000, latest_completed - 10), latest_completed + 1))
fy = st.sidebar.selectbox('目标财年', years, index=len(years) - 1, format_func=lambda y: f'{y}-{y + 1}')
opening_loss = st.sidebar.text_input('目标财年期初尚未使用资本亏损（AUD）', '0',
                                    help='填写此前报税确认、截至本财年开始仍可结转的总余额。历史成交仅用于还原库存，不会再次计算历史亏损。')
discount_mode = st.sidebar.selectbox('CGT 折扣资格', ['请选择', '符合条件的澳洲税务居民个人：50%', '不适用 CGT 折扣：0%'])
st.sidebar.caption('50% 模型用于已核对政策的 2025-26 及以前财年；其他身份、特殊规则或后续财年需另行核对。')

st.sidebar.header('📁 券商交易报表')
nab = st.sidebar.file_uploader('Nabtrade Excel', type=['xlsx', 'xls'])
stake = st.sidebar.file_uploader('Stake Excel（可多选）', type=['xlsx', 'xls'], accept_multiple_files=True)
fx = st.sidebar.file_uploader('外币逐日汇率 CSV（有外币交易时提供）', type=['csv'],
                              help='列名：Date,Currency,AUD per unit；例如 2025-08-01,USD,1.55。需覆盖所有历史买入及卖出日期。')
st.sidebar.caption('Stake Wall St 默认 USD；澳股默认 AUD。Nabtrade 国际交易只接受明确的 AUD 价格和手续费列。')
confirmed = st.sidebar.checkbox('我已提供完整买入历史；报表属于同一受益所有人，转仓未改变受益所有人，国际交易如有均为美股')

with st.expander('数据要求与计算范围'):
    st.markdown('''
    - 所有历史买入及卖出均需提供，包含目标财年前已卖出的交易；过往年度亏损余额单独填写。
    - 空白手续费单元格按 0 处理；手续费列缺失会报错，请核对是否确实没有收费。
    - FIFO 以成交日期匹配；同一股票同日有不同交易类型或不同买入成本时，需要核对日内顺序，系统会暂停计算。
    - 国际交易目前只支持美国股票；股票按 AU / US 市场区分。代码变更、拆合股、合并及其他公司行动需要先调整并核对完整历史。
    - 资本返还必须提供 `Capital Return Per Share (AUD)`（每股澳元金额），数量必须等于当天全部持仓；不会猜测原报表的金额单位。
    - 没有卖出也不代表没有其他应申报收入或 CGT 事件。
    ''')
    st.markdown('[ATO：亏损抵扣顺序](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/using-capital-losses-to-reduce-capital-gains) · [ATO：CGT 折扣与持有期限](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/cgt-discount)')

if st.sidebar.button('🚀 合并并计算'):
    if not nab and not stake:
        st.error('请至少上传一份券商报表。')
        st.stop()
    if not confirmed or discount_mode == '请选择':
        st.error('请确认历史数据及受益所有人，并选择折扣资格。')
        st.stop()
    if fy > 2025:
        st.error('当前计算规则尚未核对该财年政策，暂不输出该财年的金额。')
        st.stop()
    try:
        importer = BrokerImporter(load_rates(fx))
        with st.spinner('正在校验报表并匹配历史库存…'):
            if nab:
                importer.add_file(nab, 'Nabtrade')
            for report in stake or []:
                importer.add_file(report, 'Stake')
            for warning in dict.fromkeys(importer.warnings):
                st.warning(warning)
            errors = importer.errors + importer.ordering_errors()
            if errors:
                st.error('数据校验未通过，暂不输出净资本利得。请修正以下问题后重试。')
                for error in errors:
                    st.write(error)
                st.stop()
            if not importer.transactions:
                raise CalculationError('未解析到支持的交易记录。')
            events = calculate_events(importer.transactions, fy)
            rate = Decimal('0.5') if '50%' in discount_mode else Decimal('0')
            result = summarize(events, opening_loss, rate)
        st.header(f'{fy}-{fy + 1} 财年股票 CGT 计算结果（AUD）')
        st.caption(f'已校验并载入 {len(importer.transactions)} 条交易；完整历史用于 FIFO，当期范围为 {fy}-07-01 至 {fy + 1}-06-30。')
        left, right = st.columns(2)
        left.metric('计入收入的净资本利得', f"${result['taxable_gain']:,.2f}")
        right.metric('期末可结转资本亏损', f"${result['carried_loss']:,.2f}")
        st.write(f"折扣前资本盈利合计：${result['gross_gains']:,.2f}；当期资本亏损：${result['current_losses']:,.2f}。")
        st.write(f"使用往年亏损：${result['opening_loss_used']:,.2f}；CGT 折扣金额：${result['discount']:,.2f}。")
        st.caption('CGT 折扣金额是资本利得的扣减额，并非实际节省的税款。此结果仅涵盖已导入交易。')
        if not events:
            st.info('已导入数据在本财年没有可计算的股票处置或资本返还利得；往年未用亏损余额继续保留。')
        else:
            records = []
            for event in events:
                record = asdict(event)
                for field in ('quantity', 'gain'):
                    record[field] = str(record[field])
                records.append(record)
            details = pd.DataFrame(records)
            st.subheader('逐批次计算明细')
            st.dataframe(details, hide_index=True, width='stretch')
            st.download_button('下载逐批次 CSV', details.to_csv(index=False).encode('utf-8-sig'),
                               file_name=f'cgt_{fy}_{fy + 1}.csv', mime='text/csv', on_click='ignore')
    except (CalculationError, ValueError) as exc:
        st.error(f'无法完成计算：{exc}')
else:
    st.info('请在侧栏选择财年、填写参数并上传交易报表。')
