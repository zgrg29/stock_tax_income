# 未来税改跟踪与实施准备

最后核实 **2026-11-21（Asia/Shanghai）**。仅文档，不授权改公式。项目实现见 [PROJECT_STATUS](PROJECT_STATUS.md)；政策成法不代表软件已支持。

## 官方来源与状态标准

区分已适用法律、已立法待相关适用日、正式提案、征求意见、无官方依据。commencement 与税务 application 分开。实施前查最新合并法、后续修法及文书，不能只依赖 as-made 法或本页。

本轮直接读取立法原文及 Treasury 咨询页；部分 ATO 直开返回 403，其内容经搜索索引核对。未决细则不能仅凭摘要实施。

| ID | 官方资料与日期 |
| --- | --- |
| S1 | [Income Tax Rates Act 1986，2026-07-01 汇编，Schedule 7](https://www.legislation.gov.au/C2004A03348/2026-07-01/2026-07-01/text/original/epub/OEBPS/document_1/document_1.html) |
| S2 | [ATO 两阶段个人减税](https://www.ato.gov.au/about-ato/new-legislation/in-detail/individuals/personal-income-tax-new-tax-cuts-for-every-australian-taxpayer)，更新 2026-05-13 |
| S3 | [Treasury Laws Amendment (Tax Reform No. 1) Act 2026，No.49](https://www.legislation.gov.au/C2026A00049/asmade/2026-06-26/text/original/epub/OEBPS/document_1/document_1.html)，Royal Assent 2026-06-26；as-made 版本 |
| S4 | [ATO CGT reform](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/capital-gains-tax-reform)，发布 2026-10-13 |
| S5 | [Treasury tranche 2 / apportionment 咨询](https://consult.treasury.gov.au/c2026-792170)，开放 2026-08-03，结束 08-21；结束不等于成法 |
| S6 | [ATO Budget advice under development](https://www.ato.gov.au/about-ato/ato-advice-and-guidance/advice-under-development-program/advice-under-development-2026-budget-related-issues)，后续指导跟踪入口 |
| S7 | [Treasury Budget 政策概览](https://treasury.gov.au/policy-topics/taxation/budget2026-27)，不能代替完整适用法 |
| S8 | [ATO 改革状态表（官方内容接口）](https://www.ato.gov.au/api/public/content/0-75df51f6-df0f-4a25-8fb2-446269c0e68a)，搜索索引列示 No.3 Bill 于 2026-10-14 提出；直开受限，最终议会进度仍须复核 |

## 政策登记

各项最后核实日均为 2026-11-21。“待核实”表示本项目尚未完成法律映射，不意味着所有细节在法律上都不存在。

| 改革 | 公布/法律状态 | 适用日期、对象/收入/交易 | 过渡及未决问题 | 软件状态 |
| --- | --- | --- | --- | --- |
| 居民个人低档税率（S1/S2） | 2025–26 Budget 公布，已成法 | 2026-07-01：16%→15%；2027-07-01：15%→14%，税免门槛以上至 $45,000 档，非所有收入统一税率 | 居民/部分年度、offsets 和 Medicare 需独立核实 | 无所得税模块；第一步已适用，第二步待适用，均未开发 |
| CGT 首阶段（S3/S4） | 2026-05-12 宣布、06-26 成法；相关税务适用日未到 | 2027-07-01 起，符合条件居民个人等指数化、个人某些净利得最低 30% 税；不是出售款×30% | 资格/实体、最低税与普通税及亏损衔接要完整审查 | 未实现，旧模型不能最终申报改革后结果 |
| 既有资产过渡（S3/S4） | 已立法框架 | 符合条件跨 2027-07-01 持有资产；视同处置/取得及递延 | 原始历史不能被过渡日期覆盖；估值、pre-CGT 分支须核实 | 未实现 |
| CGT tranche 2 / apportionment（S5/S6/S8） | 8 月草稿咨询已结束，后续 No.3 Bill 已于 2026-10-14 提出；正式提案与草稿分别追踪，本轮未证实最终法律/文书登记 | 最终范围及适用日需查后续成文法/文书 | 替代估值方法的最终公式、资格、选择及后续修改未确认，不能因 Bill 提出就认为文书成法 | 不实施草稿，不预设时间比例 |
| Working Australians Tax Offset（S3 Schedule 3、S7） | 已有立法/政策资料，详细资格未完成审查 | 官方概览 2027–28 起劳动收入相关 $250 offset | 适用对象、收入定义、排除及衔接须查完整条文 | 无所得税模块，另立未来范围 |
| $1,000 standard deduction（S3 Schedule 4） | 已有成文法，详细资格未审查 | 2026–27 起相关居民劳动收入 | 与实际扣除选择及资格需单独审查，不能作为股票成本 | 未实现 |
| Medicare 未来调整 | 本轮未建立具体年度修法登记，不宣称没有变更 | 非当前功能 | 开发时单独核实年度门槛、家庭/豁免等 | 未开发 |
| 其他传闻或替代税率 | 无已纳入本项目的可靠官方依据 | 未指定 | 有官方来源、日期/状态再登记，禁止猜数字 | 不实施 |

不能将全部改革称为纯提案，也不能把 tranche 2 草稿当作首阶段已通过条款。本表对额外所得税措施仅作范围登记，不是完整税额规格。

## 跨财年研究与验收矩阵

| 场景 | 必须解决的问题 |
| --- | --- |
| A 改革前工资、B 改革后工资 | 各收入年度选适用税率/资格，不以新规则回算旧年；现软件无工资模块 |
| C 改革前取得、后出售 | 分别保存真实取得/处置日、法定过渡事件及估值；不能只按申报财年选法 |
| D 改革前未实现利得 | 核对视同事件、递延及实际实现时点，不假定持有跨期即立即申报全部增值 |
| E 多制度时期持仓 | 保留原始成本及调整，不用最新规则覆盖全部历史 |
| F 旧制度亏损在新制使用 | 核实分类、顺序、限制及最低税衔接，不能默认现短期优先算法通用 |
| G 特殊日期门槛 | 记录精确时间/时区，核对 pre-CGT、1999 历史制度、居民变化及重组承接 |

**跨改革日期持有不自动按持有天数线性拆分利得。** S4 指向市场估值及可能的替代方法；替代方法须最终依据。若法条映射、资格、估值不足，给明确缺项和可核对小计，不给确定税额；税法虚拟事件必须区别于券商买卖，避免重复消耗库存。

## 指数化/CPI 核对清单

S3 已有指数化框架，不是完全没有法律依据。但本轮不形成可执行公式，以下实施审查尚未完成：

- 查最新 Division 114、Subdivision 960-M 及过渡条款，确认成本要素、发生/支付日期、指数化范围。
- 核实法律指定 CPI 系列、发布机构、系列 ID、季度/年度、基期、修订和舍入；向 [ABS CPI](https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia) 等官方源核对，不自选 headline 通胀率。
- 形成有条文依据的取得/支出/处置期间及过渡基准公式；核实分期付款和后续费用，不能只用单一购入日。
- 核实改革前资产的估值、损失、折扣和指数化的相互关系及例外，不假定一律可叠加。
- 未来 CPI 未公布时标缺失，不预测/插值后当正式税务结果；发布后重算保留数据版本。

不得将 1999 前旧指数化直接套到 2027 新机制。

## 未来设计（未实现）

1. 先建立法律适用矩阵：`rule_id`、版本/条款、法律状态、核实日期、实体、事件/取得日期及财年。区分 commencement/application，路由不只有 `FY >= ...`。
2. 保持原两上传流程和已验证 FIFO；获授权后才考虑最小提取规则纯函数，避免再大规模重构。
3. 保存原始交易、批次 ID、真实日期、成本要素/调整链；过渡估值、税法视同日期、递延损益另存并引用原批次，不伪造原始 BUY/SELL。
4. 事件级选择旧/过渡/新规则；批次计算与年度个人税分层。所得税、最低税、Medicare 不混成一个净资本利得常数。
5. 结果记录规则 ID、法条/数据版本、输入哈希、成本/估值/CPI 来源、假设和未决问题；当前 CSV 尚无此能力，新增须兼容。
6. 区分未立法、未完成法律映射、数据缺失和资格不明；保留已知批次，不清零 ticker 或把未知当零。
7. 先锁定旧年合成回归，再加入 A–G、临界日期、旧损失、部分处置、重组、缺 CPI/估值及重复导入。新规则预期结果应来自官方示例或独立有依据手算。

预计影响 `app.py` 的财年/资格输入、解析契约、批次成本结构、事件输出、亏损/折扣汇总和导出；还需四个测试文件、`check_startup.py`、CI、文档。是否拆新模块由未来授权任务决定，本轮不实施。
