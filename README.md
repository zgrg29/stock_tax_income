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
- 转仓保留为审计记录，需核对类型、方向、两端账户/受益权、数量及历史批次后才按非处置处理；不会凭名称或总确认勾选直接跳过。
- 资本返还必须有 `Capital Return Per Share (AUD)` 列，表示每股 AUD 金额，数量必须等于当日全部持仓；超出包含买入手续费的成本基础部分生成资本利得，剩余成本降为零。
- 拆股、合股、换代码、合并、特殊税务身份及早期资产规则不自动处理。未知公司行动会报错；缺失于报表中的公司行动需使用者核对。1999-09-21 以前的交易需单独核对。

## 验证

```powershell
.venv\Scripts\python.exe -m unittest discover -v
.venv\Scripts\python.exe check_startup.py
```

测试包括计算边界、Excel 内存样表导入、去重、币种换算及页面流程。尚未用使用者实际券商报表核对列名与金额单位。

### 部署前启动检查

业务实现在 `stock_tax/cgt.py` 和 `stock_tax/importers.py`，包内使用相对导入。`app.py` 只使用项目专用的 `stock_tax.*` 模块；根目录 `cgt.py` / `importers.py` 是同一实现的兼容导出，不维护第二套算法。这避免旧进程缓存或环境中的顶层同名模块使新版入口加载到旧接口。

`check_startup.py` 校验依赖声明及实际安装、关键导入符号、模块实际来源和内部循环依赖，然后启动临时本机 Streamlit 服务。它不仅检查健康端点，还建立真实 WebSocket 会话执行 `app.py`，验证脚本完成、没有异常元素、首页标题、计算按钮及侧栏上传控件齐全。服务检查完成后自动关闭，不需要浏览器或用户的真实 Excel。

GitHub Actions 的 `Startup and regression checks` 在 Push / PR 时使用 Ubuntu 和 Python 3.11、3.12、3.13 执行干净依赖安装、`pip check`、上述启动检查及全部回归测试。Streamlit Cloud 的实际 Python 版本仍由部署设置决定，不由此文件修改；应选择与已通过 CI 的版本一致的解释器。

启动回归额外模拟上一版/同名 `cgt`、`importers` 已存在于 `sys.modules` 的情况，保证这些旧模块不能破坏新版初始化。仅凭隐藏后的 Cloud 错误无法证明线上故障一定由该缓存条件触发；排查中确认本地和远程 `777b47a` 均包含新增符号，且复现了旧模块残留时 `load_accounts` 导入失败。

Push 后请确认 GitHub Actions 对应提交为绿色，再刷新 Cloud 应用。若仍显示原错误，检查 Cloud 日志中的部署提交/解释器并执行 Reboot；不要将 GitHub Push 或本地健康检查当作线上验收结果。本轮不依赖浏览器或线上账号，无法据此声明线上页面已实际恢复。

规则参考：[ATO 资本亏损抵扣](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/using-capital-losses-to-reduce-capital-gains)、[ATO CGT 折扣](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/calculating-your-cgt/cgt-discount)。

## 转仓核对与审计

### 税务边界

关键是受益所有权是否变化，而非是否换券商、HIN 或是否包含 Transfer 字样。普通同一受益所有权的券商转仓不生成买卖/CGT 事件，原始买入日期、含手续费及已导入调整的单位成本继续保留。受益所有权变化可能构成处置或其他 CGT 事件，赠与等可能适用市场价值规则。本版本识别并阻止这类记录，要求单独核对对价、日期、市值及 rollover，不自行补造数据。

账户 `Owner ID` 必须代表**完整受益权关系**，包括共同持有人及持有比例。相同显示姓名、同一共同持有人或账户持有人相同，不足以证明受益权未变化。系统按使用者提供的凭据进行一致性校验，不声称独立核实法律事实。

参考：[ATO 对持续受益所有权的示例](https://www.ato.gov.au/individuals-and-families/investments-and-assets/shares-funds-and-trusts/investing-in-shares/children-s-share-investments)、[ATO 股份处置/赠与](https://www.ato.gov.au/individuals-and-families/investments-and-assets/shares-funds-and-trusts/investing-in-shares/disposing-of-shares)、[ATO 股份与单位记录](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/shares-and-similar-investments/keeping-records-of-shares-and-units)。

### 补充账户关系

在侧栏上传账户关系 CSV，`Evidence` 填原始账户/受益权文件的引用，不需填写 TFN 或其他敏感证件号。下列是格式示例，不是真实税务事实：

```csv
Broker,Account,Owner ID,Evidence,File,Sheet
Nabtrade,N1,person-1,account-statement-A,Transactions_report.xlsx,Domestic Portfolio Transactions
Stake,S1,person-1,account-statement-B,stake.xlsx,Aus Equities
```

`Broker,Account,Owner ID,Evidence` 必填。`File,Sheet` 同时选填：原始表没有 `Account Number` / `Account` 列时，用它们明确整张报表的账户；如果一张表混合多个账户，必须先补齐真实逐行账户信息，不能用单个默认值猜测。映射和原表冲突会阻止计算。

### 核对流程

1. 原始表支持准确的 `TRANSFER IN` / `TRANSFER OUT` 等类型。泛称 `TRANSFER`、`STOCK TRANSFER`、`OFF MARKET TRANSFER` 不根据正负数量猜测方向；名称只是初步分类，仍须所有权和库存证据。`TRANSFER SALE` 等未知复合类型要求人工核对，不会自动认定非处置。
2. 两端分别提供方向、日期、代码、数量和报告账户，使用共同 `Transfer Identifier` 配对（按市场/证券区分）。数量必须相同，转入不得早于转出。仅单端记录不会伪造另一端，需要补齐原始对端记录。
3. 如果原表缺少方向/编号，或两家券商使用不同编号，先运行并在展开明细里下载补充证据模板。只保留需要补充的行，填写 Evidence 引用，再在侧栏上传：

```csv
Record ID,Direction,Transfer Identifier,Reconciliation ID,Counterparty Broker,Counterparty Account,Evidence
<从审计复制转出行标识>,OUT,,match-001,Stake,S1,transfer-confirmation-A
<从审计复制转入行标识>,IN,,match-001,Nabtrade,N1,transfer-confirmation-B
```

`Record ID` 绑定文件内容、工作表和 Excel 物理行号，文件变化后须重新核对；不接受未匹配的旧确认。`Reconciliation ID` 用于链接两端不同券商编号，不覆盖原始 `Transfer Identifier`。对原表已有方向/账户等证据的冲突不静默覆盖。模板中的已存在值须保留，不能随意改写。

4. 核对成功后，复用同一 FIFO 引擎按来源账户取出批次、进入在途库存，再进入目标账户；部分转仓按 FIFO 分配，使用者须核对这与自己的可识别批次记录一致。系统保留每个原始批次的日期、数量、单位成本、成本调整记录及转仓链；不把转仓日价格当买入成本。到达目标账户前不可出售在途股份。
5. 转仓证券的相关买卖也必须有明确账户。卖出只使用该账户可用批次，不能从其他账户借库存。同日其他动作缺少日内顺序、费用税务处理不明、缺失历史或数量不一致时，暂停最终金额，保留明细。

### 展示及导出

页面只显示一条转仓汇总（报表行数，转入/转出各计一行），再按“已确认、需要核对、计算受阻、数据错误”分级。已确认且不影响计算的记录使用普通提示；待核对问题合并为一个黄色汇总。未验证所有权等关键事实即使没有明确数值错误，也会阻止最终金额。范围在目标财年之后的记录明确标记，不推断其税务事实。

展开可见文件、原始工作表名、Excel 行号、证券、日期、数量、账户、识别结果、成本验证、CGT 影响及下一步操作，并按原因合并计数。支持下载转仓审计 CSV、全部非空导入行审计 CSV、文件导入记录 JSON；计算成功时可下载期末批次/在途库存与逐次转仓前后快照，以及带原买入/卖出记录标识和转仓链的 CGT 明细。重复文件/转仓腿也保留导入或行审计，不重复计仓。

“成本完整”仅指基于已提供数据的批次连续性。ETF 年度 AMIT 调整等未出现在交易报表中的事项、指定批次分配与 FIFO 不一致、非标准公司行动及特殊 rollover 仍须人工核对；本版不自动支持这些税务事实。测试使用合成 Excel 样表，不代表已验证任何使用者的真实报表或受益权。
