# Data Contract (x_to_cdns)

## 1. Layers

- Raw archive layer: `x-bookmarks/raw/<source>/<YYYY-MM-DD>/`
  - `<tweet_id>.json` (structured archive)
  - `<tweet_id>.html` (html snapshot)
  - `<tweet_id>.md` (readable original markdown)
  - `assets/<tweet_id>/` (media cache, best effort)
- Curated layer: `x-bookmarks/curated/<category>/<YYYY-MM-DD>/<title>.md`
- Index layer: `x-bookmarks/index/bookmarks.sqlite`
- Meta layer: `x-bookmarks/meta/run-log.jsonl`
- Archive/quarantine layer: `x-bookmarks/archive/`
- Runtime state: `.state/{pending,processing,done,error,retry,locks,runs}`

## 2. Queue Task Schema

Each queue file (`.state/<state>/<task_id>.json`) keeps:
- `task_id`, `url`, `raw_text`, `tags`, `note`, `source`
- `status` in `pending/processing/done/error/retry`
- `attempts`, `last_error`
- `created_at`, `updated_at`

## 3. State Machine

Base flow:
- `pending -> processing -> done`

Failure flow:
- generic fetch/runtime error: `processing -> retry -> error` (bounded by `KB_MAX_RETRY`)
- degraded capture (quality gate reject): `processing -> error`

## 4. Archive-First Contract

Per task process order:
1. fetch content candidates (x api/oembed/browser)
2. write raw archive files (`json/html/md/assets`)
3. evaluate quality gate
4. if pass: generate curated + update index
5. if reject: do not write curated, move task to error/retry and log reason

## 5. Quality Gate Contract

Input checks:
- marker hits (`sign up`, `log in`, `don’t miss what’s happening`, etc.)
- text completeness/length
- essential fields (author/post_time)
- score threshold (`KB_MIN_ACCEPT_SCORE`)

Output fields in raw json:
- `quality_score`
- `quality_flags`
- `marker_hits`

## 6. Index Contract

`entries` table stores only accepted captures (`capture_status='ok'`).
Search/List queries filter by `capture_status` to avoid indexing degraded pages.

## 7. Git Contract

Default git scope is `x-bookmarks/`.
State directory is excluded unless `KB_GIT_INCLUDE_STATE=1`.
