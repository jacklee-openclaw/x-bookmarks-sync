# Agent Ingest Contract

1. Read `config/categories.json` first.
2. Only enqueue X status links into `.state/pending`.
3. Process state machine strictly: pending -> processing -> done/error/retry.
4. Write raw payload to `x-bookmarks/raw/<source>/<date>/`.
5. Write curated markdown to `x-bookmarks/curated/<category>/<date>/`.
6. Log every run to `.state/runs/<run_id>.json` and `x-bookmarks/meta/run-log.jsonl`.
