# Data Contract (x_to_cdns)

## 1. Layers

- Raw layer: `x-bookmarks/raw/<source>/<YYYY-MM-DD>/<tweet_id>.json`
- Curated layer: `x-bookmarks/curated/<category>/<YYYY-MM-DD>/<title>.md`
- Index layer: `x-bookmarks/index/bookmarks.sqlite`
- Meta layer: `x-bookmarks/meta/run-log.jsonl`
- Archive layer: `x-bookmarks/archive/` (legacy layout and quarantine)
- Runtime state: `.state/{pending,processing,done,error,retry,locks,runs}`

## 2. Queue Task Schema

Each queue file (`.state/<state>/<task_id>.json`) keeps:
- `task_id`: tweet id (string)
- `url`: canonical X URL
- `raw_text`: original inbound message
- `tags`: parsed tags
- `note`: optional note
- `source`: source label (`manual`, `telegram-auto`, ...)
- `status`: one of pending/processing/done/error/retry
- `attempts`: integer retry counter
- `last_error`: last failure message
- `created_at` / `updated_at`: UTC ISO8601

## 3. State Machine

`pending -> processing -> done`

Failure transitions:
- if `attempts <= KB_MAX_RETRY`: `processing -> retry`
- else: `processing -> error`

`retry` entries are re-consumed by `sync` unless `--no-retry` is set.

## 4. Classification Contract

Rules are loaded from `config/categories.json` with fields:
- `match`: keyword array
- `action`: currently `file`
- `folder`: target category folder
- `template`: template basename in `templates/`

Priority:
1. explicit tags match rule `name`/`folder`
2. text/url keyword match
3. `default_category`

## 5. Observability Contract

Per run:
- `.state/runs/<run_id>.json` includes queue stats, processed entries, errors, git status.

Summary stream:
- append one JSON line to `x-bookmarks/meta/run-log.jsonl`.

## 6. Git Contract

- Default push scope: `x-bookmarks/`
- State directory is excluded from git push unless `KB_GIT_INCLUDE_STATE=1`.
