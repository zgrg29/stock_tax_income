# 澳洲股票与 ETF CGT 清算

基于 Streamlit，合并 Nabtrade、Stake 的历史成交，以 FIFO 匹配每笔卖出的买入批次，计算指定澳大利亚财年的净资本利得（AUD）。

**当前是交易 CGT 工具，不是完整个人所得税计算器。** 不计算工资、股息、利息、ETF 分配收入、个人所得税或 Medicare levy；净资本利得不是最终应缴税额。

## 主要功能

- 历史报告联合匹配、多批次和部分卖出、买卖手续费分摊。
- 当前个人模型的 50% CGT 折扣、当年及手工输入的往年资本亏损抵扣。
- 普通同受益所有人转仓不生成新买卖，保留全局持仓日期和成本；汇总提示及明细。
- 识别部分 DRP、SPP、权利行权和代码转换；不明确公司行动保留数量及待核对状态。
- 原始卖出审计、逐批次成本及收入、CSV 下载；部分记录待核对时保留已知小计。

## 在线使用

已有实例据项目维护者说明由 Streamlit Community Cloud 从本仓库 `main` 的 `app.py` 部署。使用维护者提供的现有应用地址；仓库未维护经过核验的公开入口。Push 或 CI 成功不代表线上已验收。

上传 Nabtrade 和/或 Stake Excel，选择财年，按实际情况确认普通转仓所有权未变、填写期初尚未使用资本亏损，再点击“开始融合账目并计算”。结果有待核对事项时，应先核对原因。

## 本地安装与运行

CI 验证 Python 3.11、3.12、3.13；开发容器使用 3.11。Windows 示例：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

macOS/Linux 使用 `.venv/bin/python` 替换解释器路径。依赖见 [requirements.txt](requirements.txt)，目前未完整锁定版本。

## 报告与数据要求

支持 `.xlsx` / `.xls`，没有通用 CSV 导入。

| 券商 | 工作表及主要字段 |
| --- | --- |
| Nabtrade | `Domestic Portfolio Transactions`，标题第 2 行：`Date`、`Code`、`Movement Type`、`Quantity`、`Transaction Price`；可选 `Brokerage`、`Other Fees` |
| Nabtrade 国际 | `Int. Portfolio Transactions`（兼容空格差异），同类交易字段；计算需 `Average Price (AUD) *1`，费用使用 AUD 列 |
| Stake | `Aus Equities` / `Wall St Equities`，标题第 1 行：`Trade Date`、`Symbol`、`Side`、`Units`、`Avg. Price`；可选费用及 `Trade Identifier` |

Stake 国际交易须明确 `Currency=AUD` 或提供 `Avg. Price (AUD)` 及必要的 AUD 费用列。软件不自动查询汇率。请上传完整购入历史；仅有目标财年的成交可能无法追溯成本。普通转仓不要求账户映射 CSV，所有权事实由用户核对。

## 财年、范围和限制

2026 年运行时选择器显示 **2021–2022 至 2028–2029**，默认 2025–2026。这是按运行年份生成的日期选项，不是八套已实现税法。当前统一使用个人折扣模型，2026–2027 及以后在代码中标为政策未验证的试算。主要回归和既往报表验收围绕 2025–2026，未认证全部历史特殊规则。

同日成交按报告稳定顺序匹配；没有账户级库存隔离或自动身份验证。ETF 年度 AMIT 调整、复杂重组、赠股成本分配、所有权变化、居民身份等可能需另行处理。未知成本不当零成本；待核对结果不是最终报税金额。

当前尚未实现改革后的指数化、过渡估值或最低税计算；法律状态与项目支持范围见 [税改准备](docs/FUTURE_TAX_REFORM.md)。

## 开发文档

- [AGENTS.md](AGENTS.md)：开发准则。
- [项目状态](docs/PROJECT_STATUS.md)：完成范围、限制和部署状态。
- [规则与架构](docs/TAX_RULES_ARCHITECTURE.md)：实际代码及数据契约。
- [未来税改](docs/FUTURE_TAX_REFORM.md)：官方证据、未决问题及设计。
- [验证与历史回归](docs/VERIFICATION.md)：测试命令及历史问题证据。

## License

仓库没有 LICENSE 文件，不能据此认定为 MIT 或其他开源许可证。授权条款需由仓库所有者明确。
