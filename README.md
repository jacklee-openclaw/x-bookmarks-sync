# x_to_cdns

将 X（Twitter/X）链接同步为两层数据：
- 原文归档层（保真存档）
- curated 知识卡片层（便于阅读/检索）

当前主入口：`python3 x_links_to_kb.py`

## 1. 核心原则

- 原文归档优先：每条任务先落原文归档，再决定是否生成 curated。
- 质量门禁强制执行：命中登录页/占位页污染时，禁止写入 curated。
- 状态机显式化：`pending -> processing -> done / retry / error`。

## 2. 目录结构与数据分层

```text
x_to_cdns/
├─ x_links_to_kb.py
├─ config/categories.json
├─ templates/
│  ├─ bookmark.md
│  ├─ original_archive.md
│  └─ agent-ingest.md
├─ .state/
│  ├─ pending/ processing/ done/ retry/ error/
│  ├─ locks/
│  └─ runs/
└─ x-bookmarks/
   ├─ raw/<source>/<YYYY-MM-DD>/
   │  ├─ <tweet_id>.json      # 结构化原文归档
   │  ├─ <tweet_id>.html      # 页面原始快照/HTML片段
   │  ├─ <tweet_id>.md        # 可读原文归档
   │  └─ assets/<tweet_id>/   # 图片本地缓存（best effort）
   ├─ curated/<category>/<YYYY-MM-DD>/*.md
   ├─ index/bookmarks.sqlite
   ├─ meta/run-log.jsonl
   └─ archive/
```

## 3. 同步产物说明

每条成功同步（通过质量门禁）会产出：
- `raw/.../<tweet_id>.json`
- `raw/.../<tweet_id>.html`
- `raw/.../<tweet_id>.md`
- `raw/.../assets/<tweet_id>/...`（若可下载到媒体）
- `curated/.../*.md`
- `index/bookmarks.sqlite` 对应索引更新

若抓取退化（degraded capture）：
- 仍会保存 raw 原文归档（用于追溯）
- 不生成正常 curated
- 任务进入 `error`（或配置下的 `retry`）
- run log 写入失败原因与归档路径

## 4. 质量门禁（关键）

默认门禁变量：
- `KB_CONTENT_MIN_LEN=120`
- `KB_MIN_ACCEPT_SCORE=70`

以下情况会判定退化或高风险：
- 登录/占位页污染关键词命中：
  - `Don’t miss what’s happening`
  - `Sign up`
  - `Log in`
  - `Join X today`
  - `Terms of Service / Privacy Policy / Cookie Policy`
- 正文过短/结构不完整
- 核心元信息缺失（author/post_time）
- 质量分低于阈值

## 5. 环境配置

```bash
cd /Users/paipai_1/Work/projects/x_to_cdns
cp .env.example .env
```

关键变量：
- `KB_ROOT=x-bookmarks`
- `KB_STATE_ROOT=.state`
- `KB_CATEGORIES_CONFIG=config/categories.json`
- `KB_TEMPLATE_DIR=templates`
- `KB_CONTENT_MIN_LEN=120`
- `KB_MIN_ACCEPT_SCORE=70`
- `KB_DOWNLOAD_MEDIA=1`
- `KB_MAX_MEDIA_DOWNLOAD=4`
- `KB_AUTO_GIT_PUSH=1`

## 6. 常用命令

### 6.1 状态与路径

```bash
python3 x_links_to_kb.py path
python3 x_links_to_kb.py status
```

### 6.2 同步一条链接

```bash
python3 x_links_to_kb.py sync \
  --text 'https://x.com/<user>/status/<id>' \
  --source manual
```

### 6.3 索引与查询

```bash
python3 x_links_to_kb.py index --check
python3 x_links_to_kb.py list --limit 20
python3 x_links_to_kb.py search 'keyword'
```

## 7. 如何判断是否真正抓到原文

以 `<tweet_id>` 为例，检查：

```bash
find x-bookmarks/raw -type f | rg '<tweet_id>\\.(json|html|md)$'
```

并确认：
- `json` 中存在 `text`、`quality_flags`、`marker_hits`
- `md` 不是登录页文案
- `status` 中该任务进入 `done`

## 8. degraded 抓取处理与补救

### 8.1 查看最近运行日志

```bash
python3 x_links_to_kb.py status
ls -lt .state/runs | head
```

### 8.2 重新处理指定链接（强制）

```bash
python3 x_links_to_kb.py sync \
  --text 'https://x.com/<user>/status/<id>' \
  --source manual \
  --force
```

说明：
- 若仍 degraded，会保留 raw 原文归档证据并进入 `error`。
- 若历史上已有错误 curated，系统会移动到 `x-bookmarks/archive/quarantine/degraded-curated/...`。

## 9. OpenClaw/Telegram 接入

- 入站：`scripts/openclaw_ingest_link.sh`
- 兼容：`scripts/openclaw_ingest_x_link.sh`
- 定时消费：`scripts/openclaw_sync.sh`

## 10. 兼容说明

`legacy/sync_bookmarks.py` 是旧链路，仅兼容，不是推荐主流程。
