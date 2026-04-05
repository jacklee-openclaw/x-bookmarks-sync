# x_to_cdns

将 X（Twitter/X）链接沉淀为本地 Markdown 知识库，并可自动提交到 GitHub。

当前仓库是“可运行主链路 + 历史遗留链路并存”的状态：
- 推荐主链路：`x_links_to_kb.py`（`capture` / `sync` / `capture-sync`）
- 保留旧链路：`legacy/sync_bookmarks.py`（基于 X Bookmarks API 的老同步脚本）

## 1. 项目解决的问题

在无法直接访问 X 的环境下，把手机/机器人转发过来的 X 链接转成可检索、可版本化的 Markdown，并同步到 GitHub，供公司电脑查看。

## 2. 当前项目结构（按实际文件）

```text
x_to_cdns/
├─ x_links_to_kb.py                  # 主入口：X 链接 -> Markdown
├─ Makefile                          # 常用命令封装
├─ .env.example                      # 环境变量模板
├─ legacy/
│  └─ sync_bookmarks.py              # 旧版：X Bookmarks API 增量同步
├─ scripts/
│  ├─ bootstrap_github_remote.sh     # 初始化/更新 origin
│  ├─ openclaw_sync.sh               # 定时执行 sync
│  ├─ openclaw_ingest_x_link.sh      # 兼容入口（转发到 ingest_link）
│  ├─ openclaw_ingest_link.sh        # Telegram/OpenClaw 入库入口（当前工作区存在）
│  ├─ fetch_web_meta.py              # 网页文本抓取（当前工作区存在）
│  └─ fetch_with_browser.py          # Playwright 兜底抓取（当前工作区存在）
└─ x-bookmarks/                      # 知识库输出目录
   ├─ _state/index.sqlite            # 运行时索引（inbox + entries）
   ├─ _raw/YYYY-MM-DD/*.json         # 抓取原始响应
   ├─ ai|eda|verification|career|tools|misc/YYYY-MM-DD/*.md
   ├─ README.md                      # 由主脚本自动生成
   ├─ raw/inbox/curated/archive/...  # 历史整理目录（与主链路并存）
   └─ metadata/cleanup-log-*.md      # 清理记录
```

说明：`x-bookmarks/` 目前存在“新旧目录并存”。`x_links_to_kb.py` 当前实际写入的是：
- `_state/`
- `_raw/`
- `ai|eda|verification|career|tools|misc/`

## 3. 环境要求

基础运行（主链路）
- Python 3.9+
- Git

可选增强
- X API Token（`X_ACCESS_TOKEN`）用于更高质量抓取
- Playwright（仅在浏览器兜底抓取时需要）

## 4. 安装与准备

### 4.1 初始化环境变量

```bash
cd /Users/paipai_1/Work/projects/x_to_cdns
cp .env.example .env
```

最关键变量（按需配置）：
- `KB_ROOT=x-bookmarks`
- `KB_AUTO_GIT_PUSH=1`
- `KB_GIT_REMOTE=origin`
- `KB_GIT_BRANCH=main`
- `X_ACCESS_TOKEN=`（可空；为空时走公开端点 + 兜底）

### 4.2 检查脚本可运行

```bash
make check
python3 x_links_to_kb.py --help
```

成功判据：命令无报错，显示 `capture/sync/capture-sync` 子命令。

## 5. 使用流程（推荐）

下面是当前最稳定、最可复制的流程。

### 步骤 1：采集链接并立即处理（单条最快）

目的：把输入文本中的 X 链接直接入库并尝试推送。

```bash
source .env
python3 x_links_to_kb.py capture-sync --text 'https://x.com/<user>/status/<id>' --source manual
```

预期输出：终端打印两段 JSON（`capture` 和 `sync` 结果）。

成功判据：
- `sync` JSON 中 `processed` 非空
- 生成 Markdown 文件到 `x-bookmarks/<category>/<YYYY-MM-DD>/`
- 若启用自动推送，`git.status` 为 `ok`

### 步骤 2：批量模式（先收集，再统一处理）

目的：先把多条链接放入 inbox，再批量处理。

```bash
source .env
python3 x_links_to_kb.py capture --text '...可包含多条 x.com/.../status/... 链接...'
python3 x_links_to_kb.py sync --limit 30
```

预期输出：
- `capture` 返回 `inserted/updated`
- `sync` 返回 `processed/errors`

成功判据：
- `x-bookmarks/_state/index.sqlite` 中 pending 数量下降
- `x-bookmarks/README.md` 被自动刷新

### 步骤 3：用 Makefile 简化调用（等价命令）

```bash
make kb-capture TEXT='https://x.com/<user>/status/<id>'
make kb-sync
make kb-capture-sync TEXT='https://x.com/<user>/status/<id>'
```

说明：这 3 个命令分别对应 `capture`、`sync`、`capture-sync`。

## 6. 输入与输出说明

输入
- 必须包含至少一个符合格式的链接：`https://x.com/<user>/status/<id>`（或 twitter.com 同构链接）
- 可附带标签：`--tags ai,tools`
- 可附带来源：`--source openclaw|manual|telegram-auto`

输出
- Markdown：`x-bookmarks/<category>/<YYYY-MM-DD>/<title>.md`
- 原始抓取：`x-bookmarks/_raw/<YYYY-MM-DD>/<timestamp>_<tweet_id>_<mode>.json`
- 索引库：`x-bookmarks/_state/index.sqlite`
- 知识库首页：`x-bookmarks/README.md`（自动生成）

分类逻辑
- 优先使用显式 tags
- 否则按关键词推断到 `ai/eda/verification/career/tools`
- 都不命中则落到 `KB_DEFAULT_CATEGORY`（默认 `tools`）

## 7. 旧链路（仅兼容）

`legacy/sync_bookmarks.py` 仍可运行，但属于历史方案：

```bash
source .env
python3 legacy/sync_bookmarks.py --help
python3 legacy/sync_bookmarks.py --no-git
```

用途：通过 X Bookmarks API 拉取“账号书签列表”增量，不是当前“链接驱动入库”的主路径。

## 8. 常见问题与踩坑

### 8.1 `No valid X status URL found in input text`
原因：输入文本里没有匹配 `x.com/.../status/<id>`。
处理：先确认链接格式，再执行 `capture` 或 `capture-sync`。

### 8.2 抓到的正文很短或像登录页
原因：公开端点内容不完整。
处理：
- 优先配置 `X_ACCESS_TOKEN`
- 启用浏览器兜底（`KB_BROWSER_FALLBACK_ENABLED=1` 且安装 Playwright）

### 8.3 自动 push 没生效
原因：远端/分支未配置或无权限。
处理：
```bash
git remote -v
git branch --show-current
```
并检查 `.env` 中 `KB_GIT_REMOTE/KB_GIT_BRANCH`。

## 9. 验证一次完整执行是否成功

执行：

```bash
source .env
python3 x_links_to_kb.py capture-sync --text 'https://x.com/<user>/status/<id>' --source smoke-test
```

验证：

```bash
find x-bookmarks -maxdepth 4 -type f | sort
sqlite3 x-bookmarks/_state/index.sqlite 'select count(*) from entries;'
git log -1 --oneline
```

成功标准：
- 新增至少 1 个 `.md`
- `entries` 计数增加
- 启用自动 git 时，最新 commit 包含 `x-bookmarks` 变更

## 10. 维护建议（基于当前仓库状态）

- 若要给新同事直接使用，建议先把 `scripts/openclaw_ingest_link.sh`、`scripts/fetch_web_meta.py`、`scripts/fetch_with_browser.py` 纳入版本管理，避免“README 有命令但远端缺脚本”。
- 当前 `x-bookmarks/` 有历史目录与运行目录并存；如后续继续清理，优先“归档/迁移”，避免直接删除可能有价值的数据。
