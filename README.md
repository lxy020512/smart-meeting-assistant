# 智能会议助手

一个面向中文会议录音的可运行 AI Agent 应用：从音频上传、异步转写和说话人标注，到多 Agent 生成会议摘要、决议和行动项，再到证据审校、历史检索和 Markdown 报告导出。

项目重点是把“能调用模型”做成可以复盘的工程流程：模型调用有超时、有限重试、JSON 结构校验和规则降级；任务队列有 SQLite 事务、租约、心跳、fencing token、崩溃恢复、幂等提交和步骤 checkpoint；报告中的结论必须能定位到真实转写片段。

## 能力

- 上传 `wav/mp3/m4a/flac` 音频，或上传 `txt/md/json/srt/vtt` 进行确定性离线回放。
- 通过 FunASR、Faster-Whisper 或远程 ASR 适配器统一输出 `speaker/start_ms/end_ms/text`。
- 摘要 Agent、行动项 Agent、审校 Agent 分工协作，处理任务创建、改派、改期和取消。
- 证据索引支持 BM25 风格词法召回，可选 embedding 混合检索和跨会议范围过滤。
- 网页工作台查看任务事件、转写时间位置、报告、检索结果，并导出 Markdown。
- DeepSeek 不可用时仍可离线回放；报告会记录降级次数和错误原因，不伪装成在线模型结果。

## 技术栈

Python、FastAPI、Pydantic、SQLite、HTTPX、DeepSeek API、原生多 Agent 编排、FunASR/Faster-Whisper 适配、BM25 风格检索、可选 Embedding、HTML/CSS/JavaScript。

## 快速运行

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

在 `.env` 中填入 `DEEPSEEK_API_KEY`。不填密钥也可以运行离线回放和测试；音频转写需要额外安装对应 ASR 依赖，详见配置项。

启动 API 和内置 Worker：

```powershell
.\.venv\Scripts\python.exe -m app
```

打开 <http://127.0.0.1:8000/>，接口文档在 <http://127.0.0.1:8000/docs>。

## 配置要点

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek JSON 分析调用；空值启用规则降级 |
| `ASR_BACKEND` | `funasr` | `funasr`、`faster-whisper`、`remote` |
| `ASR_DEVICE` | `cpu` | ASR 推理设备 |
| `MODEL_TIMEOUT_SECONDS` | `45` | 单次模型请求超时 |
| `MODEL_RETRIES` | `2` | 额外重试次数，代码限制最大为 3 |
| `WORKER_LEASE_SECONDS` | `300` | 任务租约，过期后可恢复 |
| `SEMANTIC_ENABLED` | `false` | 是否加载可选 embedding 进行混合检索 |
| `API_TOKEN` | 空 | 设置后要求 `Authorization: Bearer ...` |

## 离线回放与测试

```powershell
.\.venv\Scripts\python.exe -m scripts.replay_demo
.\.venv\Scripts\python.exe -m scripts.benchmark
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-local
```

回放会输出完整报告 JSON，基准脚本会输出 5 次处理的平均耗时。耗时取决于机器和磁盘；它是本地回归指标，不是线上 SLA。真实 ASR 的 CER、说话人 DER 应使用带标注的中文会议数据集单独计算。

## 目录

```text
app/
  api.py          FastAPI 路由、鉴权、上传和报告导出
  queue.py        持久化 Worker、租约和崩溃恢复
  pipeline.py     转写、索引、Agent 编排和报告生成
  agents.py       摘要、行动项、审校与任务事件重放
  llm.py          DeepSeek 兼容客户端、重试和结构化输出校验
  transcribe.py   ASR 适配器与统一 Segment 模型
  retrieval.py    词法/语义证据检索与引用校验
  storage.py      SQLite 表、checkpoint 和 fencing 写入
web/               原生网页工作台
tests/             单元、可靠性、API、证据和回放测试
scripts/           离线回放与耗时基准
docs/              面试说明和调用链
```

更完整的架构解释、故障处理和面试问答见 [`docs/interview-guide.md`](docs/interview-guide.md)。

## 设计边界

DeepSeek 是文本分析模型，不负责从音频中识别语音；音频先经过独立 ASR。说话人标签表示声纹聚类结果，不自动等同于真实姓名。当前应用适合本地或单机部署；需要多实例、高吞吐和对象存储时，应替换 SQLite Store 和本地文件层。

## 许可证

Apache-2.0，见 [`LICENSE`](LICENSE)。
