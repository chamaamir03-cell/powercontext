# StepFun 本地知识库

浏览器端文档 embedding + 本地 seekdb 向量与全文混合检索。语料只从浏览器发送到本机应用；模型权重首次使用时从 Hugging Face 下载并缓存在浏览器，不会请求云端推理 API。

提交分支、开发基线、验收命令和归档性能报告入口见 [SUBMISSION.md](./SUBMISSION.md)；一页方案概述见 [TECHNICAL-OVERVIEW.md](./TECHNICAL-OVERVIEW.md)。

## 启动

要求 Node.js 22+、Chrome 或 Edge 最新版。WSL 必须使用 Linux 版 Node/npm；运行 `node -p "process.platform"` 应输出 `linux`。Linux 首次启动会下载 seekdb 原生绑定并为其设置进程级预加载；Ubuntu `libaio1t64` 环境会自动添加本地兼容库名。

```bash
npm ci --ignore-scripts --legacy-peer-deps
npm run dev
```

访问终端显示的 `http://127.0.0.1:4173`。seekdb 使用 `seekdb` JavaScript SDK 的 embedded 模式和 `test` 数据库，数据目录持久保存在 `stepfun/.data/seekdb.db`；停止应用后重新启动，集合与语料仍在。生产静态构建可用 `npm run build`，再用 `npm run start` 启动同一本地应用。`SEEKDB_PATH` 可用于指定数据库目录路径。

支持导入 `.txt`、`.md`、`.markdown` 和 `.csv` 文本文件，单文件不超过 5 MB。重新导入同名文件会以新文本块覆盖旧文本块；语料列表可按文件删除。写入后应用调用 seekdb SDK 的 `refresh_index()`，使新增和删除内容及时反映在检索结果中。

## 模型与隐私

| 项目 | 实现 |
|---|---|
| 模型 | [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5)，BAAI BGE small Chinese v1.5，512 维，模型许可 MIT；中文文档向量使用原文，查询按 BGE 中文检索指令加前缀 |
| 推理运行时 | `@huggingface/transformers` 4.3.2，Web Worker 中运行 |
| 量化 | WebGPU 加载 FP16 ONNX；WASM 加载 Q8/INT8 ONNX；模型初始化或推理出错时自动降级 WASM |
| 推理位置 | 浏览器本地。前端只把本地计算的向量、原文和元数据发送给绑定 `127.0.0.1` 的本机服务 |
| 存储与检索 | `seekdb` JavaScript SDK 1.4.0 的 embedded 模式；向量、原文、来源和分块元数据均进入 seekdb |

本机服务设置 COOP/COEP 响应头以启用 cross-origin isolation 和 WASM 多线程；模型资源通过带 CORS 的 HTTPS 请求加载。

浏览器首次使用必须能访问 Hugging Face 模型文件地址；此后由 Transformers.js 浏览器缓存复用。断网首次启动无法下载权重。文件正文及查询文本不会离开本机，Web Worker 只执行模型计算。

## 数据与检索流程

1. 文件由浏览器读取并按段落和 Unicode 字符切分，每段最多 450 个字符、重叠 64 个字符，保留足够位置编码预算。
2. Transformers.js 在浏览器中以每批最多 16 段运行 mean pooling、L2 normalize 的 embedding。查询向量也在同一浏览器本地生成。
3. 本机服务使用 seekdb SDK `upsert()` 保存向量、正文、来源、分块序号及更新时间；来源 ID 由归一化文件名生成，因此同名导入为增量更新。旧的多余块在新块写入成功后删除。
4. 查询将本地 query embedding 和原查询文本送入 seekdb SDK `hybridSearch()`。向量 KNN 与全文 `$contains` 查询在 seekdb 内执行，使用 RRF 排序并返回原文，不在客户端执行双路召回或合并。

应用不使用其他向量数据库、浏览器内存索引或远端推理服务。Benchmark 集合也由 seekdb 持久化，和用户语料集合隔离。

## 性能与质量复测

页面“运行端侧评测”使用固定的 6 篇中英文技术语料和 6 个带相关性标注的中文查询。它在同一设备、浏览器和推理后端下分别计算 FP32 基线与量化模型的文档/查询 embedding，将测试向量写入独立 seekdb 集合，再由 seekdb 执行相同的混合检索。每种配置先记录 1 次首次运行，再记录 5 次预热运行；界面汇总预热运行的中位数和 nearest-rank P95，下载的 JSON 同时包含全部原始轮次。结果报告 Recall@3、nDCG@3、ONNX 权重文件总体积、模型权重下载耗时、排除下载后的模型初始化、向量化吞吐、seekdb 写入/刷新、混合检索和端到端耗时。

| 配置 | 模型体积 | 文档向量化吞吐 | seekdb 混合检索延迟 | 检索质量 |
|---|---:|---:|---:|---|
| FP32 基线 | 当前设备实测 | 当前设备实测 | 当前设备实测 | Recall@3、nDCG@3 |
| WebGPU FP16 / WASM Q8 | 当前设备实测 | 当前设备实测 | 当前设备实测 | 同一测试集指标及变化 |

这些数据由实际浏览器和本机 seekdb 运行时生成，不在源码中伪造或硬编码。模型权重下载耗时仅统计 ONNX 文件的实际下载区间；命中浏览器缓存时该项为 0，模型初始化单独统计且排除下载时间。模型体积按本轮观测到的不同 ONNX 权重文件大小求和。正式冷缓存测试应使用全新的浏览器配置文件，并在下载完成后保留报告。

验证 WebGPU 不可用时的自动降级，可在应用地址后添加 `?acceptance-webgpu-unavailable=1`。该验收开关会让推理 Worker 把 WebGPU 标记为不可用，但仍走产品的 `auto` 后端选择和自动降级逻辑；页面及 JSON 会明确标记这是模拟环境。完成后移除该参数，再在确实没有 WebGPU 的浏览器或设备上复测，以确认真实环境兼容性。普通 WASM 路径也可在页面选择“WASM：对照测试”后单独运行。

建议正式测试流程：固定设备/浏览器/模型/seekdb/语料/查询；分别测试 FP32、FP16 WebGPU、Q8 WASM；每配置记录首次与 5 次预热测量；保存 JSON 原始报告；在至少 20 条人工标注查询和目标规模语料上复核 Recall@K/nDCG@K，并以中位数/P95 比较下载、初始化、向量化、写入刷新、检索和端到端耗时。固定内置集用于回归冒烟，不替代比赛数据集验收。

优化点：量化权重、WebGPU 优先、WASM 自动降级、16 条批处理、Worker 隔离主线程、Transformers.js 模型缓存、embedding 复用以及每次导入完成后一次性刷新索引。性能指标包含模型加载、向量化、数据库写入/刷新、检索和端到端耗时，避免只报告单次最快延迟。

## 开发测试

```bash
npm test
npm run build
```

单元测试覆盖切块重叠/边界、Recall@K、nDCG@K 与中位数/P95 计算。启动应用后，在另一个终端运行 `npm run test:acceptance-api`，可用隔离的临时来源验证真实 seekdb 健康检查、导入、混合检索、增量替换、刷新和删除；测试结束会清理该临时来源。浏览器侧用正常地址验证 WebGPU，用 `?acceptance-webgpu-unavailable=1` 验证自动降级并完成导入/检索。UI“运行端侧评测”验证固定集 FP32 与 FP16/Q8 的真实浏览器推理和 seekdb 查询，并可下载包含首次轮、5 次预热原始轮次及 P50/P95 的 JSON 报告。
