# Cleanup Log - 2026-04-05

## Scope
- Target: `x-bookmarks/`
- Purpose: clean wrong sync content, normalize structure, keep traceability.

## Actions

### Moved to curated
- `tools/2026-03-17/英语口语好...-2033236042057388464.md`
  -> `curated/x/2026-03-17/2033236042057388464.md`

### Moved to inbox (retry needed)
- `tools/2026-03-17/刹车皮 on X- -Claude Code 命令大全- - X.md`
  -> `inbox/retry/2032036375894413458.md`
  - Reason: captured body is login/landing text, not expected post content.

### Archived (legacy / duplicates / wrong-source)
- `tools/2026-03-17/2033236042057388464.md`
  -> `archive/legacy-2026-04-05/duplicates/2033236042057388464.truncated.md`
- Old structure trees archived:
  - `ai/`, `career/`, `eda/`, `misc/`, `tools/`, `verification/`, `_raw/`, `_state/`
  -> `archive/legacy-2026-04-05/...`

### Raw snapshot normalized
- Copied latest snapshot per tweet to active raw:
  - `_raw/.../20260317T080800_2033236042057388464_oembed.json`
    -> `raw/oembed/2026-03-17/2033236042057388464.json`
  - `_raw/.../20260317T081049_2032036375894413458_oembed.json`
    -> `raw/oembed/2026-03-17/2032036375894413458.json`

### Deleted
- `archive/legacy-2026-04-05/_state/index.sqlite`
  - Reason: runtime DB artifact, not a source-of-truth document.
