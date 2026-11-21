# 澳洲股票 CGT 计算

使用完整历史交易进行跨 Nabtrade / Stake 的 FIFO 匹配，计算目标财年股票净资本利得。所有计算金额为 AUD，内部使用 Decimal，显示时保留两位小数。股息、利息、ETF 分配等需要另外汇总。

## 运行

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

## 使用

1. 选择目标财年，输入该财年期初已确认且尚未使用的资本亏损余额。历史交易仅还原库存，不重新推算历史亏损余额，避免重复抵扣。
2. 明确选择是否符合个人 50% CGT 折扣资格。当前规则范围为 2025-26 及以前财年，后续财年暂停输出，需另行核对政策。
3. 上传完整的历史买入与卖出；跨券商只合并同一受益所有人的同一市场证券。国际交易当前只支持美国股票。
4. Stake Wall St 的价格、Fees 和 GST 默认同为 USD（Currency 列可明确指定币种），按每笔交易当日汇率换算。澳股金额须为 AUD。Nabtrade 国际表必须提供明确的 AUD 价格及手续费列，不再退回未换算的交易币种价格。
5. 确认数据范围并计算，可下载逐批次 CSV 明细。

逐日汇率 CSV 格式（每 1 单位外币对应多少 AUD，**不是**反向汇率）：

```csv
Date,Currency,AUD per unit
2024-08-01,USD,1.53
2025-08-01,USD,1.55
```

须覆盖报表中全部外币交易日期，包括历史买入。日期字符串使用 ISO 格式，其他券商日期字符串按澳洲日/月/年解析；带时区的值须先核对为报表成交日期。

## 校验与边界

- 分批卖出使用固定每股买入成本（含买入手续费）；卖出手续费按卖出股数分配。
- 财年区间为当年 7 月 1 日（含）至次年 7 月 1 日（不含），6 月 30 日带时间的交易不会遗漏。
- 按日历周年判断持有期限，排除买入日和卖出日，不使用固定 365 天。
- 先应用当年亏损，再应用输入的往年未用亏损；各自优先抵扣不能享受折扣的盈利，再对剩余符合条件的盈利应用折扣。
- 空白手续费单元格按零；缺失手续费列、无效数字、未知交易类型、缺失库存、缺少汇率会明确报错，并阻止生成最终金额。
- 完全相同的重复文件直接跳过；Stake 有编号的重复交易会核验内容。无编号时保留同文件内的重复成交，不猜测跨文件重叠记录；无法判断的重叠会报错。
- 同日混合买卖/资本返还或不同成本的买入，日期不足以确立 FIFO 顺序，暂停计算。当前版本不支持日内顺序补录。
- 仅跳过同一受益所有人的转仓，并显示提示。转仓之前的原始买入成本和时间必须保留。
- 资本返还必须有 `Capital Return Per Share (AUD)` 列，表示每股 AUD 金额，数量必须等于当日全部持仓；超出包含买入手续费的成本基础部分生成资本利得，剩余成本降为零。
- 拆股、合股、换代码、合并、特殊税务身份及早期资产规则不自动处理。未知公司行动会报错；缺失于报表中的公司行动需使用者核对。1999-09-21 以前的交易需单独核对。

## 验证

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```

测试包括计算边界、Excel 内存样表导入、去重、币种换算及页面流程。尚未用使用者实际券商报表核对列名与金额单位。

规则参考：[ATO 资本亏损抵扣](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/using-capital-losses-to-reduce-capital-gains)、[ATO CGT 折扣](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/cgt-discount)。
