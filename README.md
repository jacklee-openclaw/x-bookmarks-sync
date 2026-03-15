# X Bookmarks -> GitHub Sync

把个人电脑可访问的 X 书签，定时同步为结构化文件并自动推送到 GitHub 仓库。

## 目录结构

- `bookmarks/raw/YYYY-MM-DD/*.json`：X API 原始响应
- `bookmarks/posts/<tweet_id>.md`：可读快照
- `bookmarks/index.sqlite`：检索索引
- `bookmarks/state/checkpoint.json`：增量游标

## 1) 准备环境

1. 复制环境变量模板：

```bash
cp .env.example .env
```

2. 编辑 `.env`，至少填入：

```bash
X_ACCESS_TOKEN=...
```

3. 确保当前目录是 git 仓库，且已设置远程仓库：

```bash
git status
git remote -v
```

如果你刚初始化仓库，可用一键脚本绑定远程并推送：

```bash
./scripts/bootstrap_github_remote.sh <github_repo_url>
```

## 2) 手工执行一次同步

```bash
source .env && make sync
```

同步完成后会输出 JSON 摘要（新增条数、分页数、git 推送状态）。

## 3) OpenClaw 自动化

推荐让 OpenClaw 定时执行下面脚本：

```bash
/Users/paipai_1/Work/projects/x_to_cdns/scripts/openclaw_sync.sh
```

建议频率：每 30 分钟一次。

## 4) 关键实现说明

- 增量策略：用 `checkpoint.json` 记录上次前沿 `latest_tweet_id`，新一轮同步遇到该 ID 即停止下翻。
- 幂等策略：`tweet_id` 为主键，Markdown 和 SQLite 都走 upsert。
- 可靠性：文件先落本地，再统一 `git add/commit/push`。

## 5) 常见问题

- 401/403：通常是 token 过期或 scope 不足（至少 `bookmark.read tweet.read users.read`）。
- 无法推送：检查 `git remote`、分支名、凭据（PAT/SSH key）。
- 新增书签未被抓到：提高 `X_MAX_PAGES`，避免前沿 ID 因大量更新被“顶掉”。

## 6) 常用命令速查

```bash
# 语法检查
make check

# 同步（含 git push）
source .env && make sync

# 只拉取，不提交推送
source .env && make sync-no-git

# 绑定远程并推送 main
./scripts/bootstrap_github_remote.sh <github_repo_url>
```
