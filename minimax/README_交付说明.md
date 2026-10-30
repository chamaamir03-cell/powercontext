# MiniMax 600077 SDD 上下文模块交付包

## 0. 来源

- 源码分支：`fix/minimax-600077`
- 源码 commit：`2d0cdc3129717a2ed36ef20e78b57a41f03585ca`
- 基线分支：`2026-competition-ecosystem` @ `89a87db`
- 源码目录：`minimax/`
- 完整实现 diff：`minimax-600077.patch`

本包包含可运行源码、MiniMax 录制补丁、验证记录和操作说明。默认验证路径是 **replay**，不需要 MiniMax 账号；只有重新跑 live 模式才需要 MiniMax Code 登录。

## 1. 这个模块做了什么

题目是“规有所依——面向软件持续演进的 SDD 上下文管理系统”。模块把开发任务变成可验证的上下文包：

1. 把规格、代码和测试导入嵌入式 **seekdb**；
2. 用 SQL 从 seekdb 取出与任务相关的文档，并保证四个必需来源一定进入上下文；
3. 把上下文交给 **MiniMax**，让它修改示例代码；
4. 把任务进度保存到 **PowerContext Memory**，新进程可以重新读回；
5. 用 pytest 证明修改前目标测试失败、修改后目标测试和回归测试全部通过；
6. 更新规格后重新构建上下文，证明上下文随规格版本变化。

示例任务是给 `inventory.search.search_products` 增加可选的 `min_price` / `max_price` 过滤，同时不改变原有 query、category、排序和分页行为。

## 2. 代码结构

```text
minimax/
  run.py                     CLI 入口
  sdd/
    config.py                路径、任务、必需来源
    store.py                 seekdb 文档库和 SQL 检索
    context.py               任务词项、上下文选择、Prompt 渲染
    state.py                 PowerContext 任务状态保存/读取
    minimax_client.py        MiniMax live / 录制补丁 replay
    workflow.py              init/context/apply/test/status/...
    cli.py                   argparse 命令定义
  specs/inventory_search.md  v1 规格
  validation/
    task.md                  验证任务
    spec_update.md           v2 规格
    required_sources.json    必需来源和标记
  baseline/                  修改前的示例工程
  demo_project/              修改后的示例工程
  patches/price_filter.patch MiniMax 录制补丁
  records/                   MiniMax、seekdb、PowerContext、测试证据
  scripts/                   prompt/patch 辅助脚本
```

## 3. 关键实现

### 3.1 seekdb 文档库：`sdd/store.py`

- 使用 `pylibseekdb` 打开 `minimax/.state/seekdb`。
- 表结构：

```sql
engineering_docs(
  doc_id, kind, path, title, content, metadata, version, updated_at
)
```

- `init` 导入 8 份文档：
  - `specs/inventory_search.md`
  - `baseline/inventory/*.py` 四个代码文件
  - `baseline/tests/*.py` 三个测试文件
- `search()` 对 `content`、`title`、`path` 执行 `INSTR(LOWER(...), term) > 0` 的 SQL。
- 检索结果再做确定性加权：
  - 路径命中 +4
  - 标题命中 +2
  - 正文命中 +1
  - 对 `inventory_search.md`、`inventory/search.py`、`test_price_filter.py`、`test_regression.py` 额外加权
- 每次查询都记录 SQL、行数和返回的 `doc_id`，写入 `records/seekdb_queries.log` 和上下文 JSON 的 `db.queries`。

### 3.2 上下文选择：`sdd/context.py`

- 固定领域词包括 `min_price`、`max_price`、`price`、`filter`、`inventory`、`search`、`spec`、`regression`、`pagination`、`bounds`、`validation`。
- 再从句子里提取英文标识符，去掉停用词。
- 对 `spec`、`code`、`test` 三类分别检索，再合并。
- 四个必需来源始终加入上下文；缺失就报错，不会静默回退到硬编码内容。
- `context_text` 包含规格全文、代码全文、测试全文、PowerContext 状态和来源清单。
- 同时输出 `spec_marker`、`spec_version`、`sources`、`task_state` 和 `db.queries`。

### 3.3 MiniMax：`sdd/minimax_client.py`

- `apply --mode replay`：直接用 `git apply`（或 `patch -p1`）应用 `patches/price_filter.patch`，不需要账号。
- `apply --mode live`：
  - 调用 `mcode exec` 或本地 `@minimax-ai/code/cli.js`；
  - 把 `context_text` 作为 prompt 输入；
  - 保存响应、最后一条消息和生成的 `live.patch`。
- 录制会话信息：
  - MiniMax Code `0.6.13`
  - 模型 `MiniMax-M3.1-Flash`（thinking）
  - 实际任务只改 `inventory/search.py`

### 3.4 PowerContext 状态：`sdd/state.py`

- 通过 PowerContext 的 `Scope` + `Memory` artifact 保存一条以 `SDD_STATE ` 开头的 JSON 记录。
- 保存的内容包括：
  - `completed`
  - `pending`
  - `required_sources`
  - `validation`
  - `task_id`
  - `updated_at`
- `status` 在新进程中重新打开 SQLite，使用 FTS 搜索 `SDD_STATE`，取 `updated_at` 最新的一条。
- 同时调用 `prepare_context`，确认状态能被 PowerContext 重新组装。

### 3.5 工作流：`sdd/workflow.py`

命令链路：

```text
reset -> init -> context -> apply -> test -> status
      -> spec-update -> context -> before-after -> validate
```

`apply` 会复制 `baseline` 到 `minimax/.work/demo`，再应用补丁。
`test` 在补丁后的工作区运行目标测试和回归测试。
`before-after` 先在未打补丁的副本上跑测试，再打补丁重跑，证明行为差异。
`validate` 从干净状态执行完整验收流程。

## 4. 具体操作

### 4.1 环境

要求：

- Python 3.11+，已验证 3.12.12
- Linux 或 macOS（`pylibseekdb` 不提供 Windows wheel）
- `powercontext[builtin]==1.2.0`
- `pylibseekdb==1.4.0.post1`
- pytest 9.x

安装：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r minimax/requirements.txt
```

如果仓库根目录已经有竞赛提供的 PowerContext 源码，也可以保留：

```bash
export PYTHONPATH="$PWD/src:$PWD/minimax:$PYTHONPATH"
```

### 4.2 初始化

```bash
python minimax/run.py reset --yes
python minimax/run.py init
```

`init` 会：

- 初始化嵌入 seekdb；
- 导入规格、baseline 代码和测试；
- 创建 PowerContext 任务状态；
- 生成 `records/ingest.json`。

### 4.3 构建上下文

```bash
python minimax/run.py context \
  --task "Add optional min_price and max_price filters to inventory.search.search_products; keep existing behavior."
```

看结果中的：

- `sources`
- `task_state`
- `powercontext`
- `db.queries`
- `context_text`
- `spec_marker` / `spec_version`

### 4.4 应用补丁并测试

默认 replay：

```bash
python minimax/run.py apply --mode replay
python minimax/run.py test
```

等价测试：

```bash
python -m pytest minimax/demo_project/tests/test_price_filter.py -q
python -m pytest minimax/demo_project/tests/test_regression.py -q
```

### 4.5 新进程读取状态

```bash
python minimax/run.py status
```

应看到 `completed`、`pending`、`validation` 和 `task_id`。

### 4.6 更新规格并重新构建

```bash
python minimax/run.py spec-update --file minimax/validation/spec_update.md
python minimax/run.py context \
  --task "Add optional min_price and max_price filters to inventory.search.search_products; keep existing behavior."
```

第二次上下文应出现：

- `spec_marker: "v2"`
- `REQ-PRICE-STRICT`
- 新的 `spec_version`

### 4.7 修改前后证明

```bash
python minimax/run.py before-after
```

预期：

- 修改前目标测试失败：`TypeError: search_products() got an unexpected keyword argument 'min_price'`
- 修改前回归测试通过
- 修改后目标测试 12/12 通过
- 修改后回归测试 12/12 通过

### 4.8 全量验收

```bash
python minimax/run.py validate
```

结果写入 `minimax/records/validation.json`。

### 4.9 Live MiniMax 模式（可选）

```bash
mcode login --region cn
python minimax/scripts/build_prompt.py
python minimax/run.py apply --mode live \
  --prompt-file minimax/records/minimax_prompt.txt
python minimax/run.py test
```

如果 `mcode` 不在 PATH：

```bash
export MCODE_BIN=/absolute/path/to/mcode
```

## 5. 已有验证结果

`minimax/records/validation.json` 记录了完整验收，所有检查为 `true`：

- seekdb 已导入 8 份文档；
- 上下文包含 spec/code/test 必需来源；
- replay 补丁应用成功；
- 目标测试 12 passed；
- 回归测试 12 passed；
- PowerContext 状态可在新进程读回；
- 规格更新后 marker 为 `v2`，版本哈希变化；
- before/after 证明目标测试确实先失败、后通过。

## 6. 注意

- 默认验收不调用 MiniMax 在线模型，而是 replay 录制补丁；这是为了保证环境无关、可复现。
- live 模式需要 MiniMax Code 登录；不要把 OAuth token、cookie 或私钥提交到仓库。
- `pylibseekdb` 在 Windows 上不可用，Windows 请使用 WSL2 或 Linux/macOS。
- `minimax/.state/` 和 `minimax/.work/` 是生成目录，不包含在源码包中；执行 `init`/`apply` 会重新生成。
- PowerContext 是外部依赖，本包不复制其源码；可按 requirements 安装，或使用仓库根目录已有的 `src/`。
