"""Streamlit interface for validated, AUD-denominated FIFO CGT calculations."""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from cgt import CalculationError, summarize
from importers import (BrokerImporter, load_rates, load_accounts, load_transfer_reviews,
                       CONFIRMED, REVIEW, BLOCKED, INVALID)


def show_import_audit(importer, ledger):
    """Bounded summary banners, with every original row available for inspection."""
    if importer.transfer_audit:
        records = pd.DataFrame(importer.transfer_audit).fillna('')
        counts = records['Status'].value_counts()
        message = (f'检测到 {len(records)} 条转仓记录（报表行）：'
                   f"{counts.get(CONFIRMED, 0)} 条已确认，{counts.get(REVIEW, 0)} 条需要核对，"
                   f"{counts.get(BLOCKED, 0)} 条计算受阻，{counts.get(INVALID, 0)} 条数据错误。")
        if any(r['Blocks CGT'] for r in importer.transfer_audit):
            st.warning(message)
        else:
            st.info(message)
        st.caption('“已确认”依据提供的账户受益权证据、两端记录及历史批次校验；不是独立身份认证。转入/转出分别计行，重复腿只保留审计，不重复计仓。')
        with st.expander('转仓核对明细与原因汇总'):
            groups = records.groupby(['Status', 'Reason'], dropna=False).size().reset_index(name='记录数')
            st.dataframe(groups.rename(columns={'Status': '状态', 'Reason': '原因'}), hide_index=True, width='stretch')
            labels = {'Record ID': '记录标识', 'File': '文件', 'Sheet': '工作表', 'Excel Row': 'Excel 行号',
                      'Ticker': '股票/ETF', 'Date': '日期', 'Quantity': '数量', 'Direction': '方向',
                      'Account': '报告账户', 'Status': '状态', 'Reason': '识别结果',
                      'Original Purchase': '原始买入记录', 'Cost Complete': '成本完整（基于导入数据）',
                      'Blocks CGT': '阻止本财年计算', 'CGT Impact': 'CGT 影响', 'Required Action': '所需操作'}
            st.dataframe(records.reindex(columns=list(labels), fill_value='').rename(columns=labels),
                         hide_index=True, width='stretch')
            st.download_button('下载完整转仓审计 CSV', records.to_csv(index=False).encode('utf-8-sig'),
                               'transfer_audit.csv', 'text/csv', on_click='ignore')
            review_columns = ['Record ID', 'Direction', 'Transfer Identifier', 'Reconciliation ID', 'Counterparty Broker', 'Counterparty Account', 'Evidence']
            pending = records[records['Status'] != CONFIRMED].reindex(columns=review_columns, fill_value='')
            st.download_button('下载待核对转仓补充证据模板', pending.to_csv(index=False).encode('utf-8-sig'),
                               'transfer_reviews.csv', 'text/csv', on_click='ignore')
    if importer.warnings:
        st.info(f'另有 {len(importer.warnings)} 条导入提示（如重复文件/成交），已合并到下方。')
    with st.expander('完整导入审计与计算问题'):
        if importer.warnings:
            st.dataframe(pd.DataFrame({'导入提示': importer.warnings}), hide_index=True, width='stretch')
        errors = importer.errors + importer.ordering_errors() + ledger.errors
        if errors:
            st.dataframe(pd.DataFrame({'问题': list(dict.fromkeys(errors))}), hide_index=True, width='stretch')
        st.download_button('下载全部导入行审计 CSV', pd.DataFrame(importer.audit).to_csv(index=False).encode('utf-8-sig'),
                           'import_audit.csv', 'text/csv', on_click='ignore')
        st.download_button('下载文件导入记录 JSON', json.dumps(importer.import_attempts, ensure_ascii=False, indent=2),
                           'import_attempts.json', 'application/json', on_click='ignore')

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
accounts = st.sidebar.file_uploader('账户受益权及报表账户映射 CSV（转仓核对）', type=['csv'],
                                    help='Broker,Account,Owner ID,Evidence；原表无账户列时另填 File,Sheet。Owner ID 须代表完整受益权关系及比例。')
transfer_reviews = st.sidebar.file_uploader('逐行转仓补充证据 CSV（可选）', type=['csv'],
                                           help='先运行下载模板，再按记录标识补充方向/共同转仓编号及凭据。不能用勾选确认代替证据。')
st.sidebar.caption('Stake Wall St 默认 USD；澳股默认 AUD。Nabtrade 国际交易只接受明确的 AUD 价格和手续费列。')
confirmed = st.sidebar.checkbox('我已核对申报人并提供完整买卖历史及相关成本调整，国际交易如有均为美股；转仓受益权另按证据核对')

with st.expander('数据要求与计算范围'):
    st.markdown('''
    - 所有历史买入及卖出均需提供，包含目标财年前已卖出的交易；过往年度亏损余额单独填写。
    - 空白手续费单元格按 0 处理；手续费列缺失会报错，请核对是否确实没有收费。
    - FIFO 以成交日期匹配；同一股票同日有不同交易类型或不同买入成本时，需要核对日内顺序，系统会暂停计算。
    - 国际交易目前只支持美国股票；股票按 AU / US 市场区分。代码变更、拆合股、合并及其他公司行动需要先调整并核对完整历史。
    - 资本返还必须提供 `Capital Return Per Share (AUD)`（每股澳元金额），数量必须等于当天全部持仓；不会猜测原报表的金额单位。
    - 没有卖出也不代表没有其他应申报收入或 CGT 事件。
    - 转仓不按名称推定免税：需账户受益权证据、转入/转出配对和原始批次。未核对记录可能阻止计算，可展开查看及下载完整审计。
    - 成本完整性仅能按已导入数据验证；ETF 年度 AMIT 等未在交易报表出现的调整仍需核对，本版本不自动处理这些调整。
    ''')
    st.markdown('[ATO：亏损抵扣顺序](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/using-capital-losses-to-reduce-capital-gains) · [ATO：CGT 折扣与持有期限](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/cgt-discount)')

if st.sidebar.button('🚀 合并并计算'):
    if not nab and not stake:
        st.error('请至少上传一份券商报表。')
        st.stop()
    if not confirmed or discount_mode == '请选择':
        st.error('请确认历史数据及申报范围，并选择折扣资格；转仓所有权将另行核对。')
        st.stop()
    if fy > 2025:
        st.error('当前计算规则尚未核对该财年政策，暂不输出该财年的金额。')
        st.stop()
    try:
        importer = BrokerImporter(load_rates(fx), load_accounts(accounts), load_transfer_reviews(transfer_reviews))
        with st.spinner('正在校验报表并匹配历史库存…'):
            if nab:
                importer.add_file(nab, 'Nabtrade')
            for report in stake or []:
                importer.add_file(report, 'Stake')
            ledger = importer.reconcile(fy)
            show_import_audit(importer, ledger)
            errors = importer.errors + importer.ordering_errors() + ledger.errors
            if errors or any(r['Blocks CGT'] for r in importer.transfer_audit):
                st.error('数据校验未通过，暂不输出净资本利得。请展开核对明细/完整导入审计，补齐数据后重试。')
                st.stop()
            if not importer.transactions:
                raise CalculationError('未解析到支持的交易记录。')
            events = ledger.events
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
        with st.expander('期末持仓批次（含在途库存）'):
            st.dataframe(pd.DataFrame(ledger.lots), hide_index=True, width='stretch')
            st.download_button('下载期末批次与转仓检查 JSON',
                               json.dumps({'lots': ledger.lots, 'transfers': ledger.transfer_checks}, ensure_ascii=False, indent=2),
                               f'lot_audit_{fy}.json', 'application/json', on_click='ignore')
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
