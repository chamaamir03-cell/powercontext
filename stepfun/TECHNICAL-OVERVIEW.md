# StepFun 端侧 AI 推理与 seekdb 混合检索

## 方案与运行

应用使用 `Xenova/bge-small-zh-v1.5`（512 维）在浏览器 Web Worker 中生成文档和查询向量。首次运行从 Hugging Face 下载模型权重并由浏览器缓存；文档正文和查询仅发送至本机服务，不调用云端推理 API。要求 Node.js 22+、最新版 Chrome 或 Edge；WSL 需使用 Linux 版 Node。启动、依赖和环境说明见 [README.md](./README.md)。

| 推理路径 | 模型格式 | 运行方式与降级 |
|---|---|---|
| WebGPU | FP16 | 优先使用浏览器 GPU；模型初始化或推理失败时自动降级到 WASM |
| WASM | Q8 / INT8 | CPU 对照与兼容路径，支持自动降级 |

## seekdb 与检索

本机 Node 服务使用 seekdb JavaScript SDK embedded 模式，持久化向量、正文、来源、分块序号及更新时间。文档集合为 512 维 cosine 向量索引，并配置全文索引；导入使用 upsert，同名文件以新分块替换旧内容，完成后刷新索引。检索在 seekdb 内执行向量 KNN 与全文 `$contains` 混合搜索，并用 RRF 融合排序。Benchmark 使用独立集合，不与用户语料混用。

文件在浏览器端切分为最多 450 字符、重叠 64 字符的文本块；embedding 按最多 16 段一批运行。Worker 隔离推理任务，模型缓存避免重复下载；每次导入完成后统一刷新索引。

## 实测与复现

固定回归集包含 6 篇文档、6 个带标注查询。每种配置运行 1 次首次轮和 5 次预热轮，应用报告预热 P50/P95、原始轮次、权重体积与下载/初始化耗时、向量化、seekdb 写入/刷新、混合检索、端到端耗时，以及 Recall@3 和 nDCG@3。

归档了两份实际浏览器评测：[WebGPU FP16 报告](./reports/measurement-2026-10-29.json)和 [WASM Q8 报告](./reports/measurement-2026-10-29-wasm.json)。两份均包含 FP32 基线与对应量化配置的各 1 次首次、5 次预热原始轮次、分阶段耗时、环境信息及 P50/P95。

| 后端/配置 | 权重体积 | 文档向量化 P50 | Recall@3 | nDCG@3 |
|---|---:|---:|---:|---:|
| WebGPU FP32 基线 | 90.46 MB | 16.8 ms | 100% | 0.917 |
| WebGPU FP16 | 45.30 MB | 33.4 ms | 100% | 0.917 |
| WASM FP32 基线 | 90.46 MB | 148.7 ms | 100% | 0.917 |
| WASM Q8 | 22.90 MB | 99.6 ms | 100% | 0.917 |

结果来自 6 篇文档、6 个带标注查询的固定小样本，仅适用于报告中的设备和浏览器，不代表比赛规模或其他设备的性能保证；应在评委目标环境重新运行应用评测。

通过 `?acceptance-webgpu-unavailable=1` 可验证自动选择 WASM 的模拟降级流程；需在真实无 WebGPU 设备上另行复测。API 验收脚本覆盖 seekdb 健康检查、导入、混合检索、增量替换、刷新和删除。当前校验结果：8 项单测通过，生产构建与 TypeScript 检查通过，API 验收通过。
