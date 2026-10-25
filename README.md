# TraceWeave Memory

Evidence-grounded multimodal memory with temporal links and budget-aware retrieval.

面向 AML 多模态记忆评测的开源 Add/Search 服务。保留原始图文证据，以相同 user_id 隔离写入与检索。项目进度与未完成项见 [TODO](TODO.md)。

提供 **B0 离线词法基线**：SQLite/FTS5、作用域内 BM25、原图回传、幂等与认证，以及 Mem-Gallery 来源召回评估。B0 检索原文/caption，纯图查询返回空；独立的 **DeepSeek 模型契约检查**验证图文 JSON、候选 ID 与预算记账，尚未接入 Add/Search。

## Install and verify

需要 Python 3.12 和 uv，在仓库根目录执行：

```powershell
uv sync --frozen
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

离线测试不依赖模型账户或私人数据，临时目录为被忽略的 `tmp/pytest`。`uv.lock` 锁定运行/开发依赖，未锁定操作系统和 Python 补丁版本。

## Run locally

```powershell
uv run traceweave serve --host 127.0.0.1 --port 8000
```

接口为 GET `/health`、POST `/add` 和 POST `/search`。默认数据目录 `data/service`；无Key只用于本机开发。对外运行前设置非空 `TW_API_KEY`，CLI 不允许无Key绑定非回环地址。生产部署还需 HTTPS 与容量验证。

可复制空白 `.env.example` 为 `.env` 后使用 `uv run --env-file .env traceweave serve`。真实密钥仅填 `.env`，不能填可提交的示例文件。服务认证Key与模型API Key不同；B0不读取或调用模型配置。`.env`、`data/`、`runs/`、`tmp/` 和数据库均被忽略。

最小文本调用（另开PowerShell）：

```powershell
$write = @{request_id='demo-1'; user_id='demo'; session_id='session-1'; messages=@(@{role='user'; content='Alice bought a red umbrella.'})} | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/add -Method Post -ContentType 'application/json' -Body $write
$query = @{query='red umbrella'; user_id='demo'; top_k=10} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/search -Method Post -ContentType 'application/json' -Body $query
```

图文content使用有序text/image_url数组和Base64 Data URI，见 [API契约](docs/API契约.md)。返回原始内容、稳定ID及分数，不生成最终答案。

## Public-data retrieval evaluation

Mem-Gallery 的数据和许可由 [上游发布](https://huggingface.co/datasets/Ethan-Bei/Mem-Gallery)；不随本仓库再分发。下载器固定到元数据中的revision，保存文件哈希，并设置总下载字节上限。初轮选择规则与标签映射见 [评测方案](docs/评测方案.md)。

```powershell
New-Item -ItemType Directory -Path data/sources -Force | Out-Null
Invoke-WebRequest 'https://huggingface.co/api/datasets/Ethan-Bei/Mem-Gallery/revision/af912daba984e896e253016b7c7e334ef92c2a6f?blobs=true' -OutFile data/sources/mem-gallery-metadata.json
uv run traceweave download-gallery --metadata data/sources/mem-gallery-metadata.json --topics AI_Robotics_Automation_Future_Tech Astronomy_Physics_Scientific_Experiments_Cosmology --max-mib 200
uv run traceweave evaluate-gallery --topic AI_Robotics_Automation_Future_Tech --output runs/b0-dev
uv run traceweave evaluate-gallery --topic Astronomy_Physics_Scientific_Experiments_Cosmology --output runs/b0-holdout
```

输出目录必须不存在，防止复用旧状态。检索输出只含索引、类别、计时和指标；数据/媒体/SQLite/逐题结果留在忽略目录。本地来源召回不是QA准确率，也不等于AML官方分数。

## Model contract check

在私有.env设置DEEPSEEK_API_KEY及模板中的DeepSeek配置后，从仓库根目录执行：

```powershell
uv run --frozen --env-file .env traceweave model-check --output runs/d1-check-new
```

这会付费调用官方 DeepSeek API，使用合成材料检查文本 JSON、图片 OCR/颜色形状和候选 ID 排序，不发送数据集。输出目录须不存在；缺少密钥直接停止。

各次运行共用 `data/model-budget.sqlite3`，默认累计最多5美元，只能降低既有上限。调用前预留，按 usage 保守估算结算，未知费用保留占额；勿删除账本或换目录重置预算。机制和限制见 [开发设计](docs/开发设计.md#模型调用与预算)。

历史运行结果见 [A—C 基线](docs/验收记录/A-C-2026-10-25.md)与[模型契约检查](docs/验收记录/D.1-2026-10-25.md)；后续任务见 TODO。

## Design and provenance

- [需求与验收](docs/需求.md) · [开发设计](docs/开发设计.md)
- [数据模型](docs/数据模型.md) · [API契约](docs/API契约.md)
- [评测方案](docs/评测方案.md) · [参考与改动](docs/参考与改动.md)
- [协作入口](AGENTS.md) · [任务与进度](TODO.md) · [变化记录](CHANGELOG.md)

MIT License。实现与来源归属见引用清单。
