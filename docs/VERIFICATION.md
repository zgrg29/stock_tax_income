# 验证与历史回归

## 本轮实际结果

2026-11-21，运行代码基线 `53686b9`；本轮只改 Markdown，不改源码、测试、配置或依赖。

| 检查 | 结果与范围 |
| --- | --- |
| unittest discover | **53 项通过**；合成 Excel、实际页面/计算节点，不是全税法认证 |
| check_startup.py | 真实服务及 WebSocket 执行 app.py 成功，无脚本异常；2 上传、1 财年选择、标题/按钮均存在，随后关闭服务 |
| 本地 | Windows / Python 3.13.15；Streamlit 1.67.0、pandas 3.1.1、numpy 2.5.5、openpyxl 3.1.5、xlrd 2.0.2、tzdata 2026.6、websockets 17.4 |
| 基线 Linux CI | [44889300704](https://github.com/zgrg29/stock_tax_income/actions/runs/44889300704)：Python 3.11/3.12/3.13 全通过，含依赖及真实启动 |
| Cloud/浏览器 | 未读取线上页面、生产 SHA 或日志，不声称线上验收；未用 computer use |
| 私人报表 | 既往授权验收有记录，本轮未重跑；不保存真实附件、账户或金额到仓库 |

本地 venv 没有 pip，本轮不为文档安装/改变依赖；启动检查核对包可用及声明，完整依赖一致性由 Linux CI 的 pip check 验证。以上版本是快照，不是锁文件。

## 重复验证

在安装 requirements 的环境、项目根执行：

```text
python -m unittest discover -v
python check_startup.py
python -m pip check
git diff --check
```

Windows 用 `.venv\Scripts\python.exe`，Unix 用 `.venv/bin/python`。页面测试/真实服务需要本地会话和端口权限。CI 见 [startup.yml](../.github/workflows/startup.yml)；HTTP 健康返回 200 不代表 app.py 已执行成功。

## 现有测试覆盖

| 文件 / 代表测试 | 保护行为 |
| --- | --- |
| `test_cgt.py` / `test_partial_sale_buy_fees_sum_to_original_fee`、`test_fractional_sale_residue_does_not_erase_valid_stock` | 部分卖出费用守恒、零碎股 |
| `test_cgt.py` / `test_june_30_time_included_and_next_year_excluded`、`test_calendar_anniversary_boundary`、`test_losses_offset_non_discount_gains_first` | 财年/周年及亏损顺序；同文件另测市场隔离 |
| `test_cgt.py` / `test_transfer_check_never_adds_or_removes_lots` | 转仓不重复增减批次 |
| `test_cgt.py` / `test_paid_drp_and_spp_create_dated_cost_lots`、`test_code_change_moves_lots_without_resetting_dates_or_recharging_fees` | 公司行动金额、日期、费用连续性 |
| `test_cgt.py` / `test_rights_exercise_and_allotment_do_not_count_subscription_twice` | 认购款不重复、行权取得日 |
| `test_cgt.py` / bonus、reconstruction、negative_adjustment、capital_return 相关测试 | 不臆测税务成本/rollover，未知成本不变零 |
| `test_app.py` / `test_ordinary_transfer_keeps_original_lot_without_account_csv`、`test_large_transfer_batch_is_one_summary_without_stopping_calculation` | 转仓后出售/折扣、86 行汇总；另有零数量转仓 |
| `test_app.py` / `test_ownership_change_cannot_be_overridden_by_global_confirmation`、`test_partial_unmatched_sale_does_not_delete_entire_ticker_profit` | 所有权变化、局部缺成本不删全部利润 |
| `test_app.py` / `test_sale_without_any_matched_cost_is_still_visible`、`test_fifo_costs_for_multiple_lots_are_visible` | 全部卖出可见、逐批成本；另有原版普通 FIFO/长期折扣兼容 |
| `test_importers.py` / `test_axw_cxa_suffixes_and_underscore_transfer_match_stake`、`test_drp_is_not_reported_as_missing_buy` | 真实格式问题的合成回归 |
| `test_importers.py` / `test_bad_row_does_not_drop_later_sales_in_same_report` | 坏行不截断后续卖出 |
| `test_importers.py` 其他 ID/日期/AUD 测试 | 空编号、分笔、跨文件重复、不同内容保留、AU/US 日期、未换算卖出可见 |
| `test_startup.py` / `test_actual_imports_and_dependencies`、`test_old_business_modules_are_not_needed_to_initialize_app`、`test_missing_requirement_is_rejected` | 符号/依赖、假旧模块污染隔离、漏声明失败 |

无正式覆盖率报告；多次/部分跨券商调拨、所有权变化实际税额、复杂公司行动、各财年全部法律、真实海外交易等未全覆盖，见 [项目状态](PROJECT_STATUS.md)。

## 历史事实及根因证据

| 提交/阶段 | Git 能证实的内容及防回归 |
| --- | --- |
| `f5054cc` | 对话前单文件基线，可运行、保留 FIFO 思路；不证明全部算法正确。现有普通卖出及长期折扣兼容测试保护工作流 |
| `959bb58` → `777b47a` | 曾抽出根目录 cgt/importers，增加转仓四状态审计；大量提示/复杂处理造成用户反馈。当前汇总+明细及大批次回归防刷屏 |
| `777b47a` 导入 | app.py 确导入 importers 常量；该提交 importers.py 实际定义这些符号，不能断言常量未定义 |
| `d2d39e4` | 改导入为 stock_tax 包，保留根兼容层、新增真实启动检查。原 Cloud 完整 ImportError、sys.path、部署 SHA 未保存，无法确证当时模块重名/未 Push 等唯一根因 |
| `13519a0` | 恢复单文件/原上传工作流、删除业务包、保留计算修正。现启动检查和假旧模块测试避免再依赖旧接口 |
| `1f02e37` | 去掉按 ticker 整体删除利润，逐行处理、先保留卖出审计和成本明细。局部缺成本、后来异常、坏行测试保护已知结果 |
| `53686b9` | 补 `.AXW` / `.CXA`、下划线转仓、DRP/SPP/权利/代码变更和未知成本保护；合成格式及批次守恒回归 |

启动失败来自用户报告，截图隐藏完整错误；缺当时日志，不能把“可能模块冲突”写成确定根因。记录可证实的修复动作，不能编造上线环境事实。

区分原版遗漏公司行动/手续费和亏损边界，与后续引入的代码匹配和整 ticker 排除回归，不能全部笼统归为用户数据缺失。

## 发布验收

税务/导入变更新增合成回归，跑全部测试、真实初始化与 Linux CI；私人数据不入库。Push 后比对本地/远程 SHA 和三个任务。Cloud 可访问时再查部署分支/入口/SHA、生产 Python/日志，验证首页、上传合成文件和已知计算；不可访问或用户不希望浏览器时，明确线上待验收。

不靠隐藏异常、删除功能、清空问题或整份屏蔽结果“通过”。本轮仅提交文档，运行文件内容保持不变，不创建 Tag 或 Release。
