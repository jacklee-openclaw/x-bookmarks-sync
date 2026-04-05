# x_to_cdns

把 X（Twitter/X）链接沉淀为可版本化的 Markdown 知识库，并通过 GitHub 在受限网络环境中访问。

本仓库当前主链路已经收敛为：
- 单一主入口：`x_links_to_kb.py`
- 统一目录契约：`raw / curated / index / meta / .state`
- 显式状态机：`pending -> processing -> done / error / retry`

## 1. 项目定位

适用场景：
- 手机可访问 X，公司电脑不能访问 X
- 公司电脑可访问 GitHub
- 需要把“重要 X 链接”沉淀为可检索、可追溯的知识库

输入：
- 一条或多条 `https://x.com/<user>/status/<id>` 链接（可混在文本中）

输出：
- Markdown：`x-bookmarks/curated/<category>/<YYYY-MM-DD>/*.md`
- 原始抓取快照：`x-bookmarks/raw/<source>/<YYYY-MM-DD>/*.json`
- 索引库：`x-bookmarks/index/bookmarks.sqlite`
- 运行日志：`.state/runs/*.json` 与 `x-bookmarks/meta/run-log.jsonl`

## 2. 目录结构（当前真实契约）

```text
x_to_cdns/
├─ x_links_to_kb.py                 # 主 CLI（推荐唯一入口）
├─ config/
│  └─ categories.json               # 分类路由规则（match/action/folder/template）
├─ templates/
│  ├─ bookmark.md                   # Markdown 模板
│  └─ agent-ingest.md               # Agent 执行约束
├─ scripts/
│  ├─ openclaw_ingest_link.sh       # Telegram/OpenClaw 入站脚本
│  ├─ openclaw_ingest_x_link.sh     # 兼容包装（转发到 ingest_link）
│  ├─ openclaw_sync.sh              # 定时消费队列
│  ├─ fetch_with_browser.py         # 浏览器兜底抓取
│  └─ fetch_web_meta.py             # 历史网页抓取脚本（非主链路）
├─ .state/
│  ├─ pending/ processing/ done/ error/ retry/
│  ├─ locks/
│  └─ runs/
├─ x-bookmarks/
│  ├─ raw/                          # 原始抓取层（尽量不改）
│  ├─ curated/                      # 知识层（长期保留）
│  ├─ index/                        # 检索层（SQLite/FTS）
│  ├─ meta/                         # 元数据与 run 摘要
│  └─ archive/                      # 历史结构/隔离样本归档（含 quarantine）
└─ legacy/
   └─ sync_bookmarks.py             # 旧方案（X Bookmarks API），仅兼容
```

说明：
- 历史目录 `_raw/_state/ai...` 已迁移到 `x-bookmarks/archive/legacy-layout-*`，用于追溯，不参与主链路。
- 迁移时无法直接消费的历史任务会放到 `x-bookmarks/archive/quarantine/`，避免污染运行态队列。

## 3. CLI 命令面

主命令（`python3 x_links_to_kb.py <cmd>`）：
- `path`：查看统一路径映射
- `status`：查看状态机队列、索引计数、最近一次 run
- `enqueue --text ...`：只入队，不处理
- `sync [--text ...]`：入队（可选）+ 消费队列（主命令）
- `index [--check]`：检查/重建 FTS 索引
- `search <query>`：全文检索
- `list [--category ...]`：列最新条目
- `migrate [--apply]`：历史目录迁移到新契约

兼容别名（过渡期保留）：
- `capture` -> `enqueue`
- `capture-sync` -> `sync --text ...`

## 4. 环境准备

### 4.1 基础依赖

- Python 3.9+
- Git

### 4.2 可选依赖

- X API Token（`X_ACCESS_TOKEN`）：提高抓取完整度
- Playwright：启用浏览器兜底抓取

### 4.3 初始化

```bash
cd /Users/paipai_1/Work/projects/x_to_cdns
cp .env.example .env
```

关键变量：
- `KB_ROOT=x-bookmarks`
- `KB_STATE_ROOT=.state`
- `KB_CATEGORIES_CONFIG=config/categories.json`
- `KB_TEMPLATE_DIR=templates`
- `KB_AUTO_GIT_PUSH=1`
- `KB_GIT_REMOTE=origin`
- `KB_GIT_BRANCH=main`

## 5. 推荐使用流程（可直接执行）

### 步骤 1：检查环境与路径
目的：确认 CLI 与目录契约可用。

```bash
python3 x_links_to_kb.py path
python3 x_links_to_kb.py status
```

预期：返回 JSON，包含 `raw/curated/index/meta/.state` 路径与队列计数。

成功判据：命令返回码为 0，`action` 分别为 `path`、`status`。

### 步骤 2：入队并处理一条链接
目的：跑通最小闭环（入队 -> 处理 -> 落盘）。

```bash
python3 x_links_to_kb.py sync \
  --text 'https://x.com/<user>/status/<id>' \
  --source manual
```

预期：返回 `action=sync` JSON，包含 `processed` 数组。

成功判据：
- `.state/runs/<run_id>.json` 生成
- `x-bookmarks/curated/<category>/<date>/*.md` 生成
- `x-bookmarks/raw/<source>/<date>/*.json` 生成

### 步骤 3：检查索引与检索
目的：验证 index/search 链路。

```bash
python3 x_links_to_kb.py index --check
python3 x_links_to_kb.py search 'agent'
python3 x_links_to_kb.py list --limit 10
```

预期：
- `index --check` 返回 `fts5: true`（若环境支持）
- `search` / `list` 返回条目列表

成功判据：`count > 0` 时能看到 `path/category/url` 等字段。

## 6. 状态机与日志

状态机目录：
- `.state/pending`：待处理任务
- `.state/processing`：处理中
- `.state/done`：成功完成
- `.state/retry`：可重试失败
- `.state/error`：超过重试次数后失败

锁与运行日志：
- `.state/locks/*.lock`：并发保护
- `.state/runs/*.json`：每次运行的完整记录
- `x-bookmarks/meta/run-log.jsonl`：运行摘要（便于巡检）

## 7. 分类规则与模板

分类规则：`config/categories.json`
- 规则字段：`match / action / folder / template`
- 修改规则后，后续新入库任务按新规则路由

模板：`templates/bookmark.md`
- Markdown 内容由模板渲染
- 已修复历史模板重复段落问题（`## 核心观点` 不再重复）

## 8. OpenClaw / Telegram 接入

- 入站脚本：`scripts/openclaw_ingest_link.sh`
- 兼容入口：`scripts/openclaw_ingest_x_link.sh`
- 定时消费：`scripts/openclaw_sync.sh`

示例：

```bash
./scripts/openclaw_ingest_link.sh 'https://x.com/<user>/status/<id>'
```

成功后会输出：文件路径、分类、run log、git push 状态。

## 9. 常见问题

1. `No valid X status URL found in input text`
- 现象：输入文本不含合法状态链接。
- 处理：确认链接格式为 `x.com/<user>/status/<id>`。

2. 内容过短或质量分偏低
- 现象：正文像登录页或只有很短文本。
- 处理：配置 `X_ACCESS_TOKEN`，或启用 Playwright 兜底。

3. 推送未发生
- 现象：`sync` 成功但没有新 commit。
- 处理：检查 `KB_AUTO_GIT_PUSH`、`KB_GIT_REMOTE`、`KB_GIT_BRANCH`；若无变更会返回 `git.status=skipped`。

## 10. 维护约定

- 原始层与知识层分离：`raw` 只存快照，`curated` 存可读资产。
- 不在业务逻辑里硬编码分类规则，统一走 `config/categories.json`。
- 新能力先走 CLI 子命令扩展，不增加平行入口。
- `legacy/` 仅兼容，不作为推荐路径。
