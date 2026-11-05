# 评估记录

验证环境为 Windows、Python 3.9.5、Ollama 0.40.12 和本地 `qwen2.5:latest`。评审可按 [README](../README.md) 复跑。案例脚本使用实际 CLI 输出和 Trace，核对每项素材的五项交付字段和 Trace 字段，独立查询 SQLite 行数，并核对重复草稿 ID、冲突后的动作及结果。

## 真实模型验收

| 场景 | 输入与条件 | 预期及实际结果 | 记录 |
| --- | --- | --- | --- |
| 正常排期 | [M01 请求](../examples/inputs/schedule.json)，18:00 可用 | 审核通过，保存 18:00 草稿，库内一条 | [输出](../examples/runs/ollama/normal.json) · [Trace](../examples/runs/ollama/normal.trace.json) |
| 时段冲突 | 相同 M01 请求，[18:00 占用](../data/schedule_busy.json) | 提议 19:00 待确认，保留原要求，库内零条 | [输出](../examples/runs/ollama/conflict.json) · [Trace](../examples/runs/ollama/conflict.trace.json) |
| 用户确认 | [显式提交 19:00](../examples/inputs/confirmed.json)，复用冲突场景的库 | 重新查到 19:00 可用，保存 19:00 草稿，库内一条 | [输出](../examples/runs/ollama/confirmed.json) · [Trace](../examples/runs/ollama/confirmed.trace.json) |
| 重复提交 | 正常请求再跑一次，复用正常场景的库 | 返回 `reused` 和相同草稿 ID，库内仍为一条 | [输出](../examples/runs/ollama/duplicate.json) · [Trace](../examples/runs/ollama/duplicate.trace.json) |
| 素材信息缺失 | [审核请求](../examples/inputs/review.json) 的 M02 | 缺 `owner`，返回 `needs_info` | [输出](../examples/runs/ollama/review.json) · [Trace](../examples/runs/ollama/review.trace.json) |
| 规则风险 | 同一审核请求的 M03 | 命中 R01 授权规则，返回 `blocked`，该批库内零条 | [输出](../examples/runs/ollama/review.json) · [Trace](../examples/runs/ollama/review.trace.json) |
| 保存失败 | [M04 请求](../examples/inputs/save_failure.json)，`--fail save_draft` | `draft.state=failed`，原因 `tool_failure`，库内零条 | [输出](../examples/runs/ollama/save_failure.json) · [Trace](../examples/runs/ollama/save_failure.trace.json) |
| 步数上限 | M01 正常请求，`--max-steps 1` | 返回 `incomplete` 和 `step_limit`，库内零条 | [输出](../examples/runs/ollama/step_limit.json) · [Trace](../examples/runs/ollama/step_limit.trace.json) |

相同请求的第 3 步均为 `check_slot`。正常场景返回 `available` 后，第 4 步为 `save_draft`。冲突场景返回 `conflict` 后，第 4 步为 `find_alternatives`，查到 19:00 后结束为待确认。[真实模型运行汇总](../examples/runs/ollama/summary.json)

## 离线及工程验证

离线 Mock 的七个复跑场景均通过，用于验证工具流程和写入结果。自动化测试 11 项通过，另外覆盖不存在的素材、授权字段类型错误或缺少渠道、跨进程复用、并发写入、SQLite 触发器拒绝插入、拒绝发布动作、拦截越权保存和无依据的成功结论，以及步数耗尽后保留已写入状态。[离线运行汇总](../examples/runs/mock/summary.json) · [测试记录](../examples/test-results.txt)

## 早期失败与限制

- 初版把动作和终态放在两个字段里，模型在准备保存时同时填入了完成终态，触发格式校验失败。改成单一动作枚举，分别表达工具调用和结束。[首轮正常失败 Trace](../examples/runs/initial/normal.trace.json) · [首轮冲突失败 Trace](../examples/runs/initial/conflict.trace.json)
- 改用单一枚举后，模型仍有一次把时段冲突解释成规则风险。增加明确的状态区分，突出最近观察，并允许模型在终态校验拒绝后重新选择。[误判 Trace](../examples/runs/initial/conflict-v2.trace.json)
- 真实模型验证覆盖本地模型与这组构造用例。更换模型或改写规则后，仍需要重新运行验收。排期工具的候选范围和静态排期来源属于原型限制。
