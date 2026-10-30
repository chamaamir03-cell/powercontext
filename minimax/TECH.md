# 一页技术说明：MiniMax SDD 上下文模块

## 1. 模块结构

```text
run.py ──> sdd.cli ──> sdd.workflow
                         ├─ sdd.store      seekdb 文档库：工程资料 + SQL 检索
                         ├─ sdd.context    任务上下文选择、排序、渲染
                         ├─ sdd.state      PowerContext Memory：任务状态存取
                         └─ sdd.minimax_client  MiniMax live / recorded patch replay
```

- `store.py`：在 `minimax/.state/seekdb` 启动嵌入式 seekdb，建立
  `engineering_docs(doc_id, kind, path, title, content, metadata, version,
  updated_at)`。`init` 把规格、`baseline/inventory/*.py` 和
  `baseline/tests/*.py`（补丁前代码）写入该表。
- `context.py`：把任务拆成 `min_price`、`max_price`、`price`、`filter`、
  `inventory`、`search`、`test` 等检索词，对每个 kind 执行
  `INSTR(LOWER(content|title|path), term) > 0` 的 SQL；按路径/标题/正文命中
  加权，再合并四个必需来源，输出 context pack。
- `state.py`：用 PowerContext 的 Scope + Memory artifact 保存一条
  `SDD_STATE {...}` 记录；再次打开同一 SQLite 数据库时，用 FTS 搜索
  `SDD_STATE` 并选择 `updated_at` 最新的状态，同时调用
  `prepare_context` 验证状态可被 PowerContext 组装。
- `workflow.py`：串起 `init → context → apply → test → status →
  spec-update → context → before-after`，并生成全部记录。
- `minimax_client.py`：`replay` 用 `git apply` 应用录制补丁；`live` 调用
  `mcode exec`，把上下文 prompt 交给 MiniMax，保存响应和 diff。

## 2. 上下文选择方式

1. **任务解析**：固定领域词 + 任务文本中的英文标识符，去停用词。
2. **seekdb 检索**：对 `spec`、`code`、`test` 分别执行参数化（字面量转义）
   SQL，记录 SQL、命中行数和 doc_id。
3. **排序**：路径命中 4 分、标题命中 2 分、正文命中 1 分，并对
   `inventory_search.md`、`inventory/search.py`、`test_price_filter.py`、
   `test_regression.py` 加权；同分按路径稳定排序。
4. **必需来源**：`spec:specs/inventory_search.md`、
   `code:baseline/inventory/search.py`、
   `test:baseline/tests/test_price_filter.py`、
   `test:baseline/tests/test_regression.py` 始终进入 context；缺失则
   报错，不回退到硬编码内容。
5. **渲染**：`context_text` 包含规格全文、代码全文、测试全文、PowerContext
   状态 JSON 和来源清单；`sources` 保留版本哈希，便于核对“来源真实存在”。

规格更新后，`spec-update` 覆盖 seekdb 中同一个 `doc_id` 的内容；
`context` 重新查询，因此输出的 `spec_marker` 从 `v1` 变为 `v2`，
`spec_version` 哈希也变化。

## 3. 各项技术的分工

| 技术 | 在本任务中的实际作用 | 证据 |
|---|---|---|
| MiniMax | 读取 SDD 上下文，实际修改 `inventory/search.py`：新增 `_validate_price_bound`、两个可选参数、范围校验和过滤链 | `records/minimax_session.md`、`records/minimax_prompt.txt`、`records/minimax_response.json`、`patches/price_filter.patch` |
| seekdb | 存储 8 份规格/代码/测试文档；`context` 的每个 kind 都执行真实 SQL，查询结果直接进入 context pack | `records/ingest.json`、`records/seekdb_queries.log`、context JSON 的 `db.queries` |
| PowerContext | 保存任务状态（`materials_imported`、`context_built`、`patch_applied`、`tests_run`、`before_after_verified`、`spec_updated`），新进程用 Memory 搜索和 `prepare_context` 读回 | `records/powercontext_state.json`、`records/validation.log` 中的 `status` 命令 |
| pytest | 同一组目标测试在 baseline 上 12 个全部失败（缺少 `min_price` 行为），应用补丁后 12 个全部通过；12 个回归测试前后都通过 | `records/test_before.log`、`records/test_after.log`、`records/before_after.json` |

## 4. 版本与运行

- Python 3.12.12；PowerContext 使用 `powercontext[builtin]==1.2.0`（或竞赛
  `src/`）；`pylibseekdb==1.4.0.post1`；pytest 9.0.3；MiniMax Code 0.6.13，
  模型 `MiniMax-M3.1-Flash`。
- 入口：`python minimax/run.py <init|context|apply|test|status|spec-update|
  before-after|validate|reset>`。
- 数据：`minimax/.state/seekdb`、`minimax/.state/powercontext.db`；
  工作区：`minimax/.work/`；重置：`reset --yes`。
- `apply --mode replay` 不依赖 MiniMax 账号；`--mode live` 需要
  `mcode login --region cn` 或 `MCODE_BIN`。

## 5. 六项验收对应

1. **能够运行**：`validate` 从干净状态执行 `reset → init → context →
   apply → test → status → spec-update → context → before-after`，全部命令
   exit 0；不依赖隐藏本地文件。
2. **MiniMax 实际参与**：开发记录包含任务、8,509 字符上下文、实际调用、
   模型响应和对应补丁；补丁只改 `inventory/search.py`。
3. **上下文正确**：四个必需来源均出现在 `sources`；更新规格后
   `context_text` 包含 `SPEC_VERSION: v2` 和 `REQ-PRICE-STRICT`，版本哈希变化。
4. **数据库实际参与**：spec/code/test 均来自 seekdb 的 `engineering_docs`
   表；`db.queries` 记录 SQL、行数和 doc_id。
5. **状态能够复用**：状态由 PowerContext Memory 保存，`status` 在新进程中
   读取并输出 `validated`、已完成项和测试结果；`prepare_context` 确认状态
   可被重新组装。
6. **代码修改有效**：baseline 的 12 个目标测试因
   `TypeError: ... unexpected keyword argument 'min_price'` 失败；补丁后
   12 + 12 全部通过。失败原因是目标行为缺失，不是依赖或启动错误。

## 6. 设计取舍与限制

- 采用**录制补丁 replay** 作为默认验收路径，保证环境无关、结果可复现；
  live 模式保留真实 MiniMax 调用入口和开发记录。
- seekdb 使用嵌入式单写者模式，每次 CLI 命令独立打开/关闭；不做并发写入。
- 上下文选择是确定性的词项 + 加权排序，不是通用语义检索；验证样例通过
  必需来源约束保证正确性，其他任务可扩展检索词和权重。
- PowerContext 作为外部竞赛依赖，不复制进 `minimax/`；`run.py` 在仓库
  根目录存在 `src/` 时自动加入 `sys.path`。
