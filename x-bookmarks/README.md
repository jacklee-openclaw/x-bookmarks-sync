# x-bookmarks

该目录是 `x_to_cdns` 的数据落盘区，按“原始层 / 知识层 / 索引层 / 元数据层 / 归档层”组织。

## 目录说明

- `raw/`: 原始抓取快照（JSON），用于审计与回溯
- `curated/`: 已整理的 Markdown 知识内容（按分类/日期）
- `index/`: 本地检索索引（SQLite）
- `meta/`: 运行摘要与维护元数据
- `archive/`: 历史结构与迁移归档（含 `quarantine/` 隔离样本）

## 使用说明

请通过项目根目录的统一 CLI 操作：

```bash
python3 /Users/paipai_1/Work/projects/x_to_cdns/x_links_to_kb.py status
python3 /Users/paipai_1/Work/projects/x_to_cdns/x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>'
```

不要直接手工改写 `raw/` 与 `.state/` 生成的文件；如需迁移旧结构，请使用 `migrate` 子命令。
