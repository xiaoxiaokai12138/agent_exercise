# agent_exercise · 素材审核与发布排期 Agent

小影校招 AI Agent 开发考核 · 第 1 题

给定一批素材和各自的硬性发布时间，CLI 返回每项素材的审核状态、风险、建议时段、待确认项和草稿写入结果。真实模型根据工具观察决定下一步，草稿写入本地 SQLite。

## 运行

需要 Python 3.9 或更新版本。项目只使用 Python 标准库。离线模式可以直接运行。

```sh
python -X utf8 -m src --mode mock --request examples/inputs/schedule.json
python -X utf8 -m unittest discover -s tests -v
```

真实模型模式需要可访问的 Ollama 服务和 `qwen2.5:latest`。本地服务未启动时，另开终端运行 `ollama serve`。模型未安装时运行 `ollama pull qwen2.5:latest`。默认服务地址为 `http://127.0.0.1:11434`，可通过进程环境变量 `OLLAMA_HOST` 和 `OLLAMA_MODEL` 覆盖，见 [.env.example](.env.example)。项目无需 API 密钥。

```sh
python -X utf8 -m src --mode ollama --request examples/inputs/schedule.json --schedule data/schedule_busy.json --trace .runtime/conflict.trace.json
```

默认草稿库为 `.runtime/drafts.sqlite3`，用 `--db` 指定其他文件。`--output` 和 `--trace` 分别保存结果和执行记录。CLI 将业务未完成状态写入 JSON。运行参数或请求格式错误由 CLI 返回非零退出码。

## 评审入口

以下命令分别用新的临时草稿库复跑七个场景，并验证业务状态、草稿行数及两条路径的关键动作。重复提交场景复用首次保存的库。两种模式的复跑结果分别写入 `.runtime/reproduced/` 下的同名目录。

```sh
python -X utf8 examples/reproduce.py --mode ollama
python -X utf8 examples/reproduce.py --mode mock
```

- **真实模型对照。** 相同 [M01 请求](examples/inputs/schedule.json)，仅切换占用记录。对照 [正常 Trace](examples/runs/ollama/normal.trace.json) 和 [冲突 Trace](examples/runs/ollama/conflict.trace.json) 中 `check_slot` 后的 `next_action`。
- **其他验收。** 查看 [验收对照表](docs/03-evaluation.md)、[真实模型汇总](examples/runs/ollama/summary.json)、[离线汇总](examples/runs/mock/summary.json) 和 [自动化测试记录](examples/test-results.txt)。Mock 用固定动作脚本验证业务流程。
- **项目文档。** [需求](docs/01-requirements.md)、[方案](docs/02-solution.md)、[AI 使用](docs/04-ai-usage.md)、[复盘](docs/05-retrospective.md)。

`requested_at` 使用 `YYYY-MM-DDTHH:MM` 格式，按上海时间解释。遇到冲突，`pending_confirmation` 列出备选时段。用户确认后，用修改了 `requested_at` 的请求重新提交，程序会重新审核并查询可用性。该项目仅保存草稿。

每项素材都返回以下字段，`[]` 或 `null` 表示本次没有相应内容。

| 字段 | 含义 |
| --- | --- |
| `audit_status` | 审核状态，包括通过、缺少素材或信息、规则风险、未审核或工具错误 |
| `risks` | 命中的规则 ID 与风险原因 |
| `suggested_slots` | 原时段可用时给出原时段；原时段冲突时列出查到的备选 |
| `pending_confirmation` | 备选时段及等待确认的原因 |
| `draft.state` | 草稿写入状态，包括已保存、已复用、未尝试、冲突或失败 |

## GitHub 提交

项目仓库为 [xiaoxiaokai12138/agent_exercise](https://github.com/xiaoxiaokai12138/agent_exercise)。

提交信息包括第 1 题、项目 GitHub URL 和已推送的最终 commit SHA。私有仓库需核对指定评审账号的读取权限。个人贡献、实测结果与耗时记录在 [AI 使用与贡献记录](docs/04-ai-usage.md)。
