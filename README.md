# X 链接 -> Markdown 知识库

主目标：把 iPhone 上看到的重要 X 帖子，快速沉淀为可检索、可版本化的 Markdown 知识库。

## 主链路（当前默认）

```text
iPhone 刷 X
  -> 分享帖子链接给 openclaw 机器人
  -> openclaw 执行本仓库入站脚本
  -> 抓取内容 + 清洗 + 分类
  -> 输出统一 Markdown 到 x-bookmarks/
  -> git commit/push 到 GitHub
  -> 公司电脑直接看 repo / README
```

## 目录结构

```text
x-bookmarks/
  README.md
  _raw/YYYY-MM-DD/*.json
  _state/index.sqlite
  ai/
  eda/
  verification/
  career/
  tools/
  misc/
```

## Markdown 模板（工具自动生成）

```md
# 标题
- 作者:
- 时间:
- 原始链接:
- 标签:
- 线程:
- 图片说明:

## 核心观点
- point 1
- point 2
- point 3

## 关键原文摘录
> ...

## 我的理解
- ...

## 可执行动作
- ...

## 相关主题
- ...
```

## 快速开始

1. 准备 `.env`：

```bash
cp .env.example .env
```

2. 手工喂 1 条链接并同步：

```bash
source .env && make kb-capture-sync TEXT='https://x.com/jack/status/20'
```

3. 查看输出：

```bash
ls -la x-bookmarks
```

## OpenClaw 接入

openclaw 可直接调用：

```bash
/Users/paipai_1/Work/projects/x_to_cdns/scripts/openclaw_ingest_x_link.sh 'https://x.com/xxx/status/123'
```

该脚本做了并发锁，避免重复触发时并行写入。

## 命令速查

```bash
# 语法检查
make check

# 只收链接到 inbox（不处理）
make kb-capture TEXT='https://x.com/xxx/status/123'

# 处理 pending 链接并推送
make kb-sync

# 收链接并立即处理
make kb-capture-sync TEXT='https://x.com/xxx/status/123'
```

## 抓取能力说明

- 无 `X_ACCESS_TOKEN`：使用 oEmbed（可抓正文/作者/发布时间文本，线程/图片说明可能缺失）
- 有 `X_ACCESS_TOKEN`：优先走 X API，能拿到更完整字段（作者、时间、conversation_id、图片 alt 等）

## 保留功能（旧方案）

仓库仍保留 `sync_bookmarks.py`（X 书签 API 同步）用于兼容；新主流程建议使用 `x_links_to_kb.py`。
