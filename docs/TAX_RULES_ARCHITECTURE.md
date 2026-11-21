# 税务规则与现有架构

代码快照及范围见 [项目状态](PROJECT_STATUS.md)。本文描述实际代码，不把当前实现当成全部适用税法；未来方案见 [税改准备](FUTURE_TAX_REFORM.md)。

## 数据流

```text
app.py 侧栏（财年、两 Excel 入口、所有权确认、期初亏损）
 → capture_sales（先保存原始卖出）
 → 两券商内联解析 + 类型/代码/日期标准化
 → all_transactions → prepare_corporate_actions
 → 按 Date 稳定排序 → 内联 FIFO + apply_corporate_action
 → portfolio / cgt_events / unresolved / 审计列表
 → 年度亏损抵扣、固定折扣、结果标签与 CSV
```

没有单独导入器包或纯计算服务。直接 import app.py 会运行 UI；`test_cgt.py` 用 AST 执行真实计算节点，页面测试用 AppTest。未来拆分须同时维护调用方及启动契约。

## 数据契约

| 层级 | 字段及语义 |
| --- | --- |
| 标准交易 | `Date`（交易/发行/行动日期）、`Market` AU/US、`Ticker`、`Type`、`Quantity`（通常绝对值）、`Price` / `Fees`（计算要求 AUD）、`Source`，可选 `Sale ID` |
| Nabtrade 行动字段 | `Signed Quantity`、`Reported Value`、`Report`、`Account`、`ROC Per Share`；国际 Reported Value 只读 AUD 列 |
| 行动解释后 | `Raw Type`、`From Ticker`、`Counterpart Source`、`Tax Review`、`Review Existing Lots` 等内存字段，无 schema 版本 |
| tax lot | `date`、剩余 `qty`、`price`、剩余 `fees`、`source`，可选 `cost_known`（缺省 True） |
| 普通处置批次 | `Buy Date` / `Sell Date`、数量、成本/费用、净收入、`Category`、`Net Gain/Loss`、来源及 Sale ID；ROC 字段较少 |

交易日期与批次购入日期在匹配结果中分开保存；代码转换保留旧日，行权用行权日。没有独立合同日、结算日、支付日、法律事件日、改革估值日或成本支出账本。报表日期直接作为事件日期，未额外核实其法律含义。

## 财年、FIFO 与汇总

- `TARGET_FY` 创建 `[7月1日, 次年7月1日)`；重放该年结束前的所有历史，旧出售消耗库存，仅目标年事件进入结果，未来行不参与。
- `held_over_twelve_months`：归一化卖出日严格大于取得日加一日历年；不判断居民、实体或其他折扣资格。
- `portfolio[(Market, Ticker)]` 是全局 FIFO 队列，每笔卖出逐批消耗。买入费按剩余批次数量分摊并同步扣减，卖出费按本次卖出数量分摊。
- 普通利得为 `matched_qty × (sell_price - sell_fee_per_share - buy_price - buy_fee_per_share)`；未知成本为 NaN，未匹配数量单列，均不当零盈利。
- 内联汇总提取长/短期正利得与损失；`all_losses + opening_loss` 先抵短期盈利、再抵长期，最后 `taxable_gain = net_st_gain + net_lt_gain * 0.5`；剩余损失为 `carried_loss`。这是当前个人模型，不是通用多制度亏损算法。
- `opening_loss` 手工输入，没有年度来源分类或历史申报验证，不自动逐年结转；不抵工资。
- `start_year > 2025` 添加政策未核实提示。没有财年税率表、独立折扣配置或个人所得税计算；选财年主要改变日期筛选。

## 转仓和公司行动

`transaction_type` 对白名单下划线转仓标签标准化，非白名单含 TRANSFER 的类型另行提示。`record_transfer` 保留来源；普通转仓为 `TRANSFER_CHECK`，仅比较全局库存，不增减持仓或重设成本/日期。

所有权不变由用户确认，不是系统认证；明确不同所有人不能被勾选覆盖。无双方自动配对、券商子账或所有权改变的市值处置；零数量只在明细注明。当前用汇总+展开明细，不再使用旧模块四状态 API。

`prepare_corporate_actions` 将明确 DRP/SPP 建独立 BUY，优先实际金额，缺时用单价×数量；免费权利需明确零金额。同日期/市场/文件账户/类型，恰好一正一负、不同代码、等数量才配对。

`apply_corporate_action` 移动批次、处理行权与不明增减。代码变更保留成本、日期及费用；行权加实际认购款并用新股行权日期；一般重组标记税务成本未确认。BSP/调整不把报表估值当税务成本。完整性缺口见项目状态。

ROC 仅用明确的 `Capital Return Per Share (AUD)` 减少含费用成本，超额记利得，无该字段则提示；未导入 ETF 年度 AMIT 调整，不能从成交报告推断完整 ETF 成本。

## 外币、个人所得税和审计

AU 默认 AUD；US 需要明确 AUD 价格/费用，无自动汇率、来源或换算日期逻辑。历史成本与卖出收入须各自有正确 AUD 数据，不能统一用卖出日换算。国际费用和行动路径仍需扩大验证。

无工资/股息/利息/ETF 分配收入汇总、税率、Medicare、offsets 或居民选项。`taxable_gain` 是当前模型的净资本利得，不是税款；待核对时是小计。

`sale_records` 保存原始卖出及状态，`cgt_events` 保存已匹配批次，转仓/行动另有明细。没有全量持久审计、规则 ID、输入哈希或不可变调整链；运行环境也未完整锁定。

## 规则参考

- [ATO CGT discount](https://www.ato.gov.au/individuals-and-families/investments-and-assets/capital-gains-tax/cgt-discount)：资格须核实，UI 假设不能代替。
- [ATO TD 2000/3](https://www.ato.gov.au/law/view/pdf/pbr/td2000-003.pdf)：红利再投资依据。
- [ATO 权利行权](https://www.ato.gov.au/forms-and-instructions/capital-gains-tax-guide-2021/whats-new/investments-in-shares-and-units/exercising-rights-or-options-to-acquire-shares-or-units)：旧年度资料，实施前复查适用版本。
- [ANZ BOP](https://www.anz.com/content/dam/anzcom/shareholder/bonus-option-plan-terms-and-conditions.pdf)：发行人证据，不是所有赠股通用税法。

本轮没有修改上述实现。
