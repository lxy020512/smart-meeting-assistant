# 智能会议助手：面试说明

这份文档用于把项目讲成一条可以现场回放的工程链路。回答时先说用户问题，再说架构决策，最后给出验证方式。

## 项目背景

会议录音经过转写后，仍然需要人工整理摘要、决议和待办事项。项目把上传、异步处理、中文转写、Agent 分析、证据审校、历史检索和 Markdown 报告导出串成一个可运行的后端应用。

## 一分钟介绍

用户上传音频或带时间戳的转写文件后，API 只负责落盘并创建任务，SQLite 队列异步领取任务。Worker 先用 ASR 适配器得到统一的 `Segment`，再按上下文窗口调用摘要 Agent、行动项 Agent 和审校 Agent。模型输出必须通过 Pydantic 校验，决议和任务必须引用真实原文；模型超时、限流或格式错误时，系统按有限次数重试，之后进入规则降级并在报告中记录。最后把转写、证据索引、报告和 Agent trace 持久化，前端可以查看进度、跳到音频位置、检索历史会议并导出 Markdown。

## 调用链回放

```text
POST /api/meetings
  -> 保存上传文件、计算摘要、生成幂等 key
  -> SQLite jobs(state=queued)
  -> DurableWorker.claim_job() 原子领取 + lease_token
  -> MeetingPipeline.execute()
     -> transcribe: FunASR / Faster-Whisper / remote / text replay
     -> index: 词法 BM25 风格检索，可选 embedding 混合召回
     -> summary Agent: 摘要、决议、原文 evidence
     -> action Agent: create/assign/reschedule/cancel 事件
     -> replay_events(): 按时间顺序重放并校验任务状态
     -> critic Agent: 审查语义与证据
     -> save_report(): 报告、指标、降级信息、调用 trace
```

## 为什么这样选型

| 选择 | 原因 | 代价 |
| --- | --- | --- |
| FastAPI | 文件上传、状态查询和 OpenAPI 调试入口实现快 | 需要另配生产进程管理 |
| SQLite | 单机回放成本低，事务能覆盖任务领取和 checkpoint | 多实例高吞吐时应迁移 PostgreSQL |
| 原生 Python Agent 编排 | 每个步骤、重试和证据边界都可见，便于调试 | 没有现成框架提供的通用组件 |
| DeepSeek OpenAI 兼容接口 | 结构化 JSON 适合摘要、行动项和审校 | 网络与配额会影响延迟 |
| FunASR / Faster-Whisper 适配器 | 中文 ASR、VAD 和说话人标签可以替换 | 本地模型需要额外安装和下载权重 |

## Agent 如何体现价值

三个 Agent 不是把同一个 prompt 调三次：摘要 Agent 只产出摘要和决议，行动项 Agent 产出带生命周期的事件，审校 Agent 只负责检查语义与证据。编排层把它们的输入输出隔离，再用程序逻辑重放任务事件和检查引用。这样模型负责语言理解，代码负责状态一致性和安全边界。

## 可靠性设计

- **超时与重试**：HTTP 超时由客户端限定；网络错误、408、429、5xx 在重试预算内退避，401、403 等不可恢复错误立即降级。
- **解析校验**：先解析 JSON，再用 Pydantic 的严格字段和长度约束校验；失败会发送一次格式修复提示，最终使用保守规则并记录 `fallback`。
- **队列恢复**：任务有 `lease_until` 和 `lease_token`。Worker 崩溃后租约过期，其他 Worker 可以重新领取；旧 Worker 即使恢复，也不能用旧 token 发布结果。
- **幂等与 checkpoint**：相同上传幂等键返回同一任务；音频哈希不变时复用转写、检索和各 Agent 步骤，避免重复调用模型。
- **证据约束**：`segment_id` 必须存在，`quote` 必须是对应原文的连续子串；检索问答只能引用当前版本的证据。

## 可量化验证

项目自带 `scripts/replay_demo.py` 和 `scripts/benchmark.py`，不依赖网络密钥也能跑通完整 pipeline。测试覆盖队列并发领取、租约回收、fencing、心跳、幂等、checkpoint、模型重试、错误 JSON、证据拒绝、API 上传、报告导出和历史检索。运行：

```powershell
python -m pytest -q --basetemp=.pytest-local
python -m scripts.replay_demo
python -m scripts.benchmark
```

面试时应把本机命令输出作为结果，不能把离线回放的耗时或规则降级结果包装成真实生产 SLA；真实 ASR 的 CER、说话人 DER 需要在带标注的音频集上单独评测。

## 常见追问

**DeepSeek 能直接识别语音吗？** 这里不让文本模型承担 ASR。音频先交给 FunASR、Faster-Whisper 或远程 ASR 服务，DeepSeek 只处理带说话人和时间戳的文本。

**说话人怎么区分？** FunASR 的 `cam++` 说话人模块输出 `spk`，适配器统一成“说话人 N”；如果输入只有纯文本，就诚实标记为“未标注”，不猜测身份。

**为什么不把所有工作交给一个 Agent？** 摘要、事件抽取和审校的约束不同。拆开后每个输出都能单测、重试和降级，也能定位哪一步失败。

**模型输出错误怎么办？** 先按错误类型决定是否重试；格式错误最多修复一次并重新校验，仍失败就用规则降级，报告保留失败原因，用户知道哪些结论需要人工复核。

**为什么用事件重放而不是直接覆盖待办？** 会议里常见“改派、改期、取消”。把事件按原文顺序重放，可以保留变更历史，并拒绝没有先前创建事件或没有证据的更新。

**SQLite 能不能支撑多实例？** 当前定位是可复现的单机应用；原子事务和租约保证同一数据库下的并发安全。多实例部署时把 Store 换成 PostgreSQL，并将文件放到对象存储即可，Pipeline 接口无需改变。

**项目的主要限制是什么？** 音频 ASR 依赖可选模型和本地算力；说话人标签不等于真实姓名；模型降级后的摘要质量低于在线模型；检索默认是词法召回，语义召回需要安装可选 embedding 依赖。
