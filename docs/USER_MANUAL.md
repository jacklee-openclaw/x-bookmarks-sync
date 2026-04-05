# x_to_cdns 详细操作指南与用户手册

## 0. 文档说明

### 0.1 适用对象
- 日常使用者：希望把 X 链接沉淀为可检索知识卡片。
- 项目接手者：需要快速理解系统如何运行、如何排障。
- 通过手机 / Telegram / CLI 触发流程的操作者。

### 0.2 本手册与 README 的区别
- `README.md`：项目总览与核心命令。
- 本文档：完整用户手册，强调“实际操作路径 + 验证 + 排障”。

---

## 1. 项目简介

`x_to_cdns` 的目标是把 X（Twitter/X）链接转换为两层数据：

1. 原始文档层（Original Archive）
- 保留原文证据（JSON / HTML / Markdown / 媒体）。
- 作用：可追溯、可审计，防止只剩摘要。

2. 整理卡片层（Curated Card）
- 在原文基础上生成结构化知识卡片。
- 作用：便于阅读、分类与检索。

硬规则：
- 没有原始文档层，不算成功同步。
- 命中登录页/占位页污染时，不得伪造成功卡片。

---

## 2. 5 分钟快速上手（最短可用路径）

### 步骤 1：提交一条 X 链接
目的：把链接入队并执行一次完整处理。

```bash
cd /Users/paipai_1/Work/projects/x_to_cdns
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --no-git
```

预期结果：输出 JSON，包含 `processed` 或 `errors`。

### 步骤 2：检查原始文档是否保存成功
目的：确认 raw/original 已落盘。

```bash
find x-bookmarks/raw -type f | rg '<id>\.(json|html|md)$'
```

成功判据：至少看到同一 `<id>` 的 `.json/.html/.md` 三个文件。

### 步骤 3：检查整理卡片是否生成
目的：确认通过门禁后生成 curated。

```bash
python3 x_links_to_kb.py list --limit 5
```

成功判据：`items` 中出现该 `tweet_id`，且 `path` 指向 `x-bookmarks/curated/...`。

### 步骤 4：确认流程整体完成
目的：确认状态机与索引状态。

```bash
python3 x_links_to_kb.py status
```

成功判据：
- `queue.done` 增加
- `entries_ok` 增加
- `entries_degraded` 未异常增长

---

## 3. 工作原理（输入 -> 处理 -> 输出）

### 3.1 输入
支持两类输入：
1. CLI 文本输入：`sync --text '...x.com/.../status/...'`
2. 外部 OpenClaw 调用脚本输入：`scripts/openclaw_ingest_link.sh '<url_or_text>'`

### 3.2 处理链路
1. 解析输入文本中的 X status URL。
2. 写入 `.state/pending/<tweet_id>.json`（入队）。
3. 消费队列：`pending -> processing`。
4. 抓取内容（优先级按当前实现）：
- 有 `X_ACCESS_TOKEN`：优先 X API，失败回退到 oEmbed。
- 无 Token：使用 oEmbed。
- 内容不完整时可触发浏览器回退（Playwright）。
5. 先落原始文档层（Archive First）：
- `raw/<source>/<date>/<tweet_id>.json`
- `raw/<source>/<date>/<tweet_id>.html`
- `raw/<source>/<date>/<tweet_id>.md`
- 可选 `assets/<tweet_id>/...`
6. 质量门禁评估：
- 通过：生成 curated、更新索引、状态转 `done`。
- 退化：不写正常 curated、状态转 `error`、写入 run log。

### 3.3 输出
- 原始文档层：`x-bookmarks/raw/...`
- 整理卡片层：`x-bookmarks/curated/...`
- 索引层：`x-bookmarks/index/bookmarks.sqlite`
- 运行记录：`.state/runs/*.json`、`x-bookmarks/meta/run-log.jsonl`

---

## 4. 目录结构与职责

```text
/Users/paipai_1/Work/projects/x_to_cdns
├─ x_links_to_kb.py                 # 主 CLI 入口
├─ scripts/
│  ├─ openclaw_ingest_link.sh       # 外部入口包装（常用于 Telegram/OpenClaw）
│  ├─ openclaw_sync.sh              # 定时消费包装
│  ├─ fetch_with_browser.py         # 浏览器回退抓取
│  └─ fetch_web_meta.py             # 辅助抓取脚本
├─ config/categories.json           # 分类规则（match/action/folder/template）
├─ templates/
│  ├─ bookmark.md                   # curated 模板
│  ├─ original_archive.md           # 原文归档 md 模板
│  └─ agent-ingest.md               # Agent 约定说明
├─ .state/                          # 状态机目录
│  ├─ pending/ processing/ done/
│  ├─ error/ retry/
│  ├─ locks/
│  └─ runs/
└─ x-bookmarks/
   ├─ raw/                          # 原始文档层
   ├─ curated/                      # 整理卡片层
   ├─ index/bookmarks.sqlite        # 索引层
   ├─ meta/run-log.jsonl            # 运行摘要
   └─ archive/                      # 归档/隔离历史文件
```

用户重点关注目录：
- 日常看结果：`x-bookmarks/curated/`
- 校验真原文：`x-bookmarks/raw/`
- 看失败原因：`.state/error/` + `.state/runs/` + `x-bookmarks/meta/run-log.jsonl`

---

## 5. 一条书签的完整生命周期

1. 用户输入 URL
- 来源：CLI 或外部 OpenClaw 入口脚本。

2. 入队
- 任务写入 `.state/pending/<tweet_id>.json`。

3. 处理
- 进入 `processing`，执行抓取与归档。

4. 先保存原始文档
- 保存 `json/html/md`（必要）与媒体（尽力）。

5. 质量门禁
- 检查污染 marker、文本完整性、元信息完整性、分数阈值。

6. 结果分支
- 通过：写 curated + upsert index + `done`。
- 拒绝：写错误原因 + `error`（并保留原始证据）。

7. 可追溯记录
- `.state/runs/<run_id>.json`
- `x-bookmarks/meta/run-log.jsonl`

---

## 6. 手机端使用方式（当前实现现状）

### 6.1 当前真实支持
本仓库未内置 iOS 应用或直接手机 SDK。当前可行路径是：
- 在手机端复制/分享 X 链接
- 发送到 Telegram 中你已配置的 OpenClaw 通道
- 由外部 OpenClaw/Broker 调用本仓库脚本 `scripts/openclaw_ingest_link.sh`

### 6.2 推荐发送格式
- 最稳妥：消息中包含完整 status URL（可附带少量文字）
- 示例：
```text
https://x.com/<user>/status/<id>
```

### 6.3 发送后系统行为
1. 外部通道调用 `openclaw_ingest_link.sh`。
2. 脚本执行 `python3 x_links_to_kb.py sync --text ... --source telegram-auto`。
3. 终端输出简要回执：文件路径、分类、质量分、run log。

### 6.4 如何判断成功
- 成功回执包含 `✅ 已入库`。
- 或 CLI `status/list` 可看到新增条目。

### 6.5 当前未内置能力（必须注意）
以下能力不在本仓库内实现：
- Telegram Bot token 管理
- webhook/polling 服务常驻进程
- 消息转发网关守护

上述能力依赖你的外部 OpenClaw 环境。

---

## 7. Telegram 使用方式（基于当前实现）

### 7.1 仓库内提供的 Telegram 相关能力
- 仅提供“可被 Telegram/OpenClaw 调用”的入口脚本：
  - `scripts/openclaw_ingest_link.sh`
  - `scripts/openclaw_ingest_x_link.sh`（兼容壳）
- 脚本支持关键参数：
  - `--source <label>`
  - `--no-git`
  - `--force`
  - `--dry-run`（仅入队，不执行抓取）

### 7.2 典型交互链路
`Telegram 消息 -> 外部 OpenClaw/Broker -> openclaw_ingest_link.sh -> x_links_to_kb.py sync`

### 7.3 支持消息形态
- 脚本接收“整段文本”，内部提取 status URL。
- 可含多余描述文本，但必须包含至少一条合法 X status URL。

### 7.4 常见问题
- Telegram 发消息无响应：通常是外部 broker 未运行，不是本仓库解析失败。
- 有响应但未入库：检查输出是否是 degraded capture（会进 `error`）。

---

## 8. 命令行操作手册（CLI）

### 8.1 环境准备

```bash
cd /Users/paipai_1/Work/projects/x_to_cdns
cp .env.example .env
```

建议优先检查：
```bash
python3 x_links_to_kb.py -h
python3 x_links_to_kb.py status
```

可选但推荐的桥接环境变量：
- `KB_PYTHON_BIN`：固定 bridge 脚本使用的 Python 解释器。
- `KB_PROJECT_ROOT`：固定 CLI 的项目根目录（防止在非仓库目录调用时写错路径）。
- `KB_INGEST_FORCE_NO_GIT=1`：在 Telegram/bridge 调试期避免自动 git 提交。

### 8.2 常用命令

```bash
# 查看路径映射
python3 x_links_to_kb.py path

# 查看系统状态
python3 x_links_to_kb.py status

# 手工入队（只入队不处理）
python3 x_links_to_kb.py enqueue --text 'https://x.com/<user>/status/<id>'

# 入队并处理（推荐）
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual

# 不自动 git 提交/推送
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --no-git

# 重建索引
python3 x_links_to_kb.py index

# 检查 FTS 能力
python3 x_links_to_kb.py index --check

# 查看最近条目
python3 x_links_to_kb.py list --limit 20

# 检索（query 是位置参数，不是 --query）
python3 x_links_to_kb.py search 'keyword' --limit 10
```

### 8.3 自动化入口（脚本）

```bash
# 外部系统投喂入口
scripts/openclaw_ingest_link.sh 'https://x.com/<user>/status/<id>'

# 定时消费队列
scripts/openclaw_sync.sh

# 桥接最小联调（不依赖 Telegram）
scripts/test_ingest.sh 'https://x.com/<user>/status/<id>'
```

### 8.4 手工重试失败项
当前没有独立 `retry` 子命令；实践上用 `sync --text ... --force` 重新入队处理：

```bash
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --force --no-git
```

---

## 9. 常见工作流

### 场景 1：手机收藏一条 X 书签
1. 手机复制 X 链接。
2. 发到 Telegram（已接入 OpenClaw 的通道）。
3. 外部系统调用本仓库 `openclaw_ingest_link.sh`。
4. 在项目终端运行 `python3 x_links_to_kb.py status` 验证。

### 场景 2：CLI 手动补录
```bash
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --force --no-git
```

### 场景 3：发现失败后补救
1. 看错误：
```bash
ls .state/error
ls -lt .state/runs | head
```
2. 强制重跑：
```bash
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --force --no-git
```

### 场景 4：重建索引
```bash
python3 x_links_to_kb.py index
python3 x_links_to_kb.py search 'keyword'
```

### 场景 5：确认某条是否具备“双产物”
```bash
# 替换 <id>
find x-bookmarks/raw -type f | rg "<id>\.(json|html|md)$"
python3 x_links_to_kb.py list --limit 50
```

---

## 10. 成功判据与验证清单

对单条链接，成功同步至少满足：
1. 原始文档三件套存在：`json/html/md`
2. `list` 可见该条，且 `path` 指向 curated
3. `status` 中 `entries_ok` 合理增长
4. `.state/done/<tweet_id>.json` 存在

如果失败（degraded / error）：
1. `.state/error/<tweet_id>.json` 存在
2. raw 证据通常仍存在
3. run log 有原因字段（如 marker、quality）

---

## 11. 常见问题与排障

### 11.1 抓到登录页/占位页怎么办
现象：文本出现 `Don’t miss what’s happening` / `Sign up` / `Log in`。

处理：
1. 查看 error 任务与 run log。
2. 确认浏览器回退配置是否可用（Playwright、登录态）。
3. 用 `--force --no-git` 重试并检查 raw json 的 `quality_flags/marker_hits`。

### 11.2 只生成了 curated，没有看到原文
按当前实现不应发生。若发生，优先检查：
- 是否查看了错误目录日期。
- 是否误看了历史旧目录。
- 是否处理的是旧版本产物。

### 11.3 Telegram 无响应
本仓库不包含 Telegram 守护服务。排查顺序：
1. 外部 OpenClaw/Broker 是否在线。
2. 外部通道是否真的调用到 `scripts/openclaw_ingest_link.sh`。
3. 本地终端是否出现脚本执行输出。
4. 查看桥接日志：
```bash
tail -n 50 .state/bridge/openclaw-ingest.log
tail -n 50 .state/bridge/openclaw-sync.log
```

### 11.4 `sync` 自动产生 git 提交
默认 `KB_AUTO_GIT_PUSH=1`。临时关闭：
```bash
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --no-git
```

### 11.5 search 搜不到明明存在的内容
- 先运行 `python3 x_links_to_kb.py index` 重建索引。
- `search` 的参数是位置参数（例如 `search '关键词'`），不是 `--query`。

---

## 12. 当前限制与注意事项

1. Telegram/手机通道不是仓库内闭环
- 仓库只提供入口脚本，不提供 bot/webhook 服务实现。

2. 抓取能力依赖上下文
- 无 X Token 时更依赖 oEmbed 与浏览器回退。
- 私有内容、受限页面、动态渲染复杂页面可能失败或降级。

3. 浏览器回退依赖本机环境
- 需要 Playwright 可用。
- 需要本机持久 profile（`KB_BROWSER_USER_DATA_DIR`）保持可访问状态。

4. 搜索是 FTS5 能力
- 对中英混合短词命中可能不稳定，建议多尝试关键词组合。

5. 历史目录存在归档
- `x-bookmarks/archive/` 下有 legacy/quarantine 历史，不代表当前主路径。

---

## 13. 附录

### 13.1 常用命令速查

```bash
python3 x_links_to_kb.py status
python3 x_links_to_kb.py path
python3 x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>' --source manual --no-git
python3 x_links_to_kb.py list --limit 20
python3 x_links_to_kb.py search 'keyword' --limit 10
python3 x_links_to_kb.py index
```

### 13.2 状态目录速查
- `pending/`：待处理
- `processing/`：处理中
- `done/`：处理成功
- `error/`：处理失败（含 degraded）
- `retry/`：可重试队列
- `runs/`：每次运行明细

### 13.3 产物速查
- 原文归档：`x-bookmarks/raw/<source>/<date>/<tweet_id>.{json,html,md}`
- 整理卡片：`x-bookmarks/curated/<category>/<date>/<title>.md`
- 索引：`x-bookmarks/index/bookmarks.sqlite`
- 汇总日志：`x-bookmarks/meta/run-log.jsonl`
