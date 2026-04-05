# x-bookmarks

该目录保存同步产物，分层如下：

- `raw/`: 原文归档层（每条包含 `json/html/md`，可含 `assets/`）
- `curated/`: 知识卡片层（仅质量门禁通过后生成）
- `index/`: SQLite 索引
- `meta/`: 运行摘要、隔离记录
- `archive/`: 历史结构与隔离样本

推荐从项目根目录使用统一 CLI：

```bash
python3 /Users/paipai_1/Work/projects/x_to_cdns/x_links_to_kb.py status
python3 /Users/paipai_1/Work/projects/x_to_cdns/x_links_to_kb.py sync --text 'https://x.com/<user>/status/<id>'
```

如果出现 degraded capture，查看：
- `.state/runs/*.json`
- `meta/run-log.jsonl`
- `archive/quarantine/`

说明：
- `archive/quarantine/degraded-curated/`：被门禁判定为退化后隔离的旧卡片
- `archive/quarantine/orphan-curated/`：未被当前索引引用的历史卡片归档
