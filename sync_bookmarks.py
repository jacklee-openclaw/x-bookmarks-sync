#!/usr/bin/env python3
"""
Sync X bookmarks to local files and optionally push to GitHub.

Directory layout:
  bookmarks/raw/YYYY-MM-DD/*.json
  bookmarks/posts/<tweet_id>.md
  bookmarks/index.sqlite
  bookmarks/state/checkpoint.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_DEFAULT_BASE = "https://api.x.com/2"


class SyncError(Exception):
    """Raised when sync cannot continue safely."""


@dataclass
class Config:
    x_access_token: str
    api_base: str
    output_root: Path
    max_results: int
    max_pages: int
    request_timeout_sec: int
    include_folders: bool
    do_git_push: bool
    git_remote: str
    git_branch: str | None


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_config(args: argparse.Namespace) -> Config:
    token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    if not token:
        raise SyncError("Missing env X_ACCESS_TOKEN")

    output_root = Path(os.environ.get("BOOKMARKS_ROOT", "bookmarks")).expanduser().resolve()
    api_base = os.environ.get("X_API_BASE", API_DEFAULT_BASE).rstrip("/")
    max_results = int(os.environ.get("X_MAX_RESULTS", "100"))
    max_pages = args.max_pages or int(os.environ.get("X_MAX_PAGES", "20"))
    timeout = int(os.environ.get("X_REQUEST_TIMEOUT_SEC", "30"))
    include_folders = args.include_folders and os.environ.get("X_INCLUDE_FOLDERS", "1") == "1"
    do_git_push = (not args.no_git) and os.environ.get("GIT_AUTO_PUSH", "1") == "1"
    git_remote = os.environ.get("GIT_REMOTE", "origin")
    git_branch = os.environ.get("GIT_BRANCH", "").strip() or None

    if max_results < 5 or max_results > 100:
        raise SyncError("X_MAX_RESULTS must be between 5 and 100")
    if max_pages < 1:
        raise SyncError("X_MAX_PAGES must be >= 1")
    if timeout < 5:
        raise SyncError("X_REQUEST_TIMEOUT_SEC must be >= 5")

    return Config(
        x_access_token=token,
        api_base=api_base,
        output_root=output_root,
        max_results=max_results,
        max_pages=max_pages,
        request_timeout_sec=timeout,
        include_folders=include_folders,
        do_git_push=do_git_push,
        git_remote=git_remote,
        git_branch=git_branch,
    )


def api_get(cfg: Config, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.startswith("/"):
        path = "/" + path
    url = f"{cfg.api_base}{path}"
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}?{query}"

    req = urllib.request.Request(url=url, method="GET")
    req.add_header("Authorization", f"Bearer {cfg.x_access_token}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=cfg.request_timeout_sec) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SyncError(f"HTTP {exc.code} {path} failed: {body}") from exc
    except urllib.error.URLError as exc:
        raise SyncError(f"Network error for {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid JSON response for {path}: {exc}") from exc


def ensure_dirs(root: Path) -> dict[str, Path]:
    raw_day = root / "raw" / dt.date.today().isoformat()
    posts = root / "posts"
    state = root / "state"
    raw_day.mkdir(parents=True, exist_ok=True)
    posts.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    return {"raw_day": raw_day, "posts": posts, "state": state}


def checkpoint_path(root: Path) -> Path:
    return root / "state" / "checkpoint.json"


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_checkpoint(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_raw_json(raw_dir: Path, payload: dict[str, Any], page_idx: int, kind: str) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    file_path = raw_dir / f"{ts}_{kind}_p{page_idx:03d}.json"
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


def get_user_id(cfg: Config) -> str:
    resp = api_get(cfg, "/users/me", params={"user.fields": "id,username,name"})
    user = resp.get("data") or {}
    user_id = str(user.get("id", "")).strip()
    if not user_id:
        raise SyncError("Cannot resolve authenticated user id from /2/users/me")
    return user_id


def fetch_bookmarks_pages(
    cfg: Config,
    user_id: str,
    raw_dir: Path,
    frontier_tweet_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    all_new_posts: list[dict[str, Any]] = []
    users: dict[str, dict[str, Any]] = {}
    pagination_token: str | None = None
    reached_frontier = False
    first_page_first_id: str | None = None
    page_count = 0

    while page_count < cfg.max_pages:
        page_count += 1
        params: dict[str, Any] = {
            "max_results": cfg.max_results,
            "tweet.fields": "created_at,author_id,lang,public_metrics,entities",
            "user.fields": "id,username,name",
            "expansions": "author_id",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        resp = api_get(cfg, f"/users/{user_id}/bookmarks", params=params)
        save_raw_json(raw_dir, resp, page_count, "bookmarks")

        for u in (resp.get("includes") or {}).get("users") or []:
            uid = str(u.get("id", "")).strip()
            if uid:
                users[uid] = u

        data = resp.get("data") or []
        if not data:
            break

        if first_page_first_id is None:
            first_page_first_id = str(data[0].get("id", "")).strip() or None

        for post in data:
            post_id = str(post.get("id", "")).strip()
            if not post_id:
                continue
            if frontier_tweet_id and post_id == frontier_tweet_id:
                reached_frontier = True
                break
            all_new_posts.append(post)

        if reached_frontier:
            break

        pagination_token = (resp.get("meta") or {}).get("next_token")
        if not pagination_token:
            break

    summary = {
        "pages_fetched": page_count,
        "reached_frontier": reached_frontier,
        "frontier_tweet_id_old": frontier_tweet_id,
        "frontier_tweet_id_new": first_page_first_id,
    }
    return all_new_posts, users, summary


def fetch_folder_map(
    cfg: Config,
    user_id: str,
    raw_dir: Path,
    target_post_ids: set[str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    if not target_post_ids:
        return {}, {}

    folder_names: dict[str, str] = {}
    folder_map: dict[str, list[str]] = {}
    page_idx = 0

    # Pull folder list first.
    folders_token: str | None = None
    folder_ids: list[str] = []
    while True:
        page_idx += 1
        params: dict[str, Any] = {"max_results": 100}
        if folders_token:
            params["pagination_token"] = folders_token
        resp = api_get(cfg, f"/users/{user_id}/bookmarks/folders", params=params)
        save_raw_json(raw_dir, resp, page_idx, "folders")
        for item in resp.get("data") or []:
            fid = str(item.get("id", "")).strip()
            if not fid:
                continue
            folder_ids.append(fid)
            folder_names[fid] = str(item.get("name", "")).strip()
        folders_token = (resp.get("meta") or {}).get("next_token")
        if not folders_token:
            break

    # For each folder, scan posts and only record ids we just synced.
    for fid in folder_ids:
        folder_token: str | None = None
        while True:
            page_idx += 1
            params = {"max_results": 100}
            if folder_token:
                params["pagination_token"] = folder_token
            resp = api_get(cfg, f"/users/{user_id}/bookmarks/folders/{fid}/posts", params=params)
            save_raw_json(raw_dir, resp, page_idx, "folder_posts")
            for p in resp.get("data") or []:
                pid = str(p.get("id", "")).strip()
                if not pid or pid not in target_post_ids:
                    continue
                folder_map.setdefault(pid, []).append(fid)
            folder_token = (resp.get("meta") or {}).get("next_token")
            if not folder_token:
                break

    return folder_map, folder_names


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks (
          tweet_id TEXT PRIMARY KEY,
          created_at TEXT,
          author_id TEXT,
          author_username TEXT,
          author_name TEXT,
          text TEXT,
          url TEXT,
          fetched_at TEXT,
          folder_ids TEXT,
          folder_names TEXT
        );
        """
    )
    conn.commit()
    return conn


def post_url(post_id: str, author_username: str) -> str:
    if author_username:
        return f"https://x.com/{author_username}/status/{post_id}"
    return f"https://x.com/i/web/status/{post_id}"


def quote_yaml(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def build_markdown(
    post: dict[str, Any],
    author: dict[str, Any] | None,
    fetched_at: str,
    folder_ids: list[str],
    folder_names: list[str],
) -> str:
    post_id = str(post.get("id", ""))
    created_at = str(post.get("created_at", ""))
    text = str(post.get("text", "")).strip()
    author_id = str(post.get("author_id", ""))
    author_name = str((author or {}).get("name", "")).strip()
    author_username = str((author or {}).get("username", "")).strip()
    url = post_url(post_id, author_username)

    # Keep metadata machine-readable for downstream indexing/search.
    front_matter = [
        "---",
        f'tweet_id: "{quote_yaml(post_id)}"',
        f'title: "{quote_yaml((author_name or "unknown") + " | " + (created_at or "unknown-time"))}"',
        f'author_name: "{quote_yaml(author_name)}"',
        f'author_username: "{quote_yaml(author_username)}"',
        f'author_id: "{quote_yaml(author_id)}"',
        f'created_at: "{quote_yaml(created_at)}"',
        f'fetched_at: "{quote_yaml(fetched_at)}"',
        f'url: "{quote_yaml(url)}"',
        "folder_ids:",
    ]
    if folder_ids:
        for fid in folder_ids:
            front_matter.append(f'  - "{quote_yaml(fid)}"')
    else:
        front_matter.append("  - ")
    front_matter.append("folder_names:")
    if folder_names:
        for fname in folder_names:
            front_matter.append(f'  - "{quote_yaml(fname)}"')
    else:
        front_matter.append("  - ")
    front_matter.append("---")

    body = textwrap.dedent(
        f"""
        # {(author_name or "unknown")} | {created_at or "unknown-time"}

        {text}

        ## Metadata
        - url: {url}
        - author: {(author_name or "unknown")} (@{author_username or "unknown"})
        - fetched_at: {fetched_at}
        - bookmark_folders: {", ".join(folder_names) if folder_names else "(none)"}
        """
    ).strip()

    return "\n".join(front_matter) + "\n\n" + body + "\n"


def upsert_post(
    conn: sqlite3.Connection,
    posts_dir: Path,
    post: dict[str, Any],
    author: dict[str, Any] | None,
    fetched_at: str,
    folder_ids: list[str],
    folder_names: list[str],
) -> None:
    post_id = str(post.get("id", "")).strip()
    if not post_id:
        return

    author_id = str(post.get("author_id", "")).strip()
    author_username = str((author or {}).get("username", "")).strip()
    author_name = str((author or {}).get("name", "")).strip()
    created_at = str(post.get("created_at", "")).strip()
    text = str(post.get("text", "")).strip()
    url = post_url(post_id, author_username)

    md = build_markdown(post, author, fetched_at, folder_ids, folder_names)
    (posts_dir / f"{post_id}.md").write_text(md, encoding="utf-8")

    conn.execute(
        """
        INSERT INTO bookmarks (
          tweet_id, created_at, author_id, author_username, author_name,
          text, url, fetched_at, folder_ids, folder_names
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tweet_id) DO UPDATE SET
          created_at = excluded.created_at,
          author_id = excluded.author_id,
          author_username = excluded.author_username,
          author_name = excluded.author_name,
          text = excluded.text,
          url = excluded.url,
          fetched_at = excluded.fetched_at,
          folder_ids = excluded.folder_ids,
          folder_names = excluded.folder_names
        """,
        (
            post_id,
            created_at,
            author_id,
            author_username,
            author_name,
            text,
            url,
            fetched_at,
            json.dumps(folder_ids, ensure_ascii=False),
            json.dumps(folder_names, ensure_ascii=False),
        ),
    )


def run_cmd(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def maybe_git_commit_and_push(cfg: Config, repo_root: Path, commit_msg: str) -> dict[str, str]:
    rc, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], repo_root)
    if rc != 0:
        return {"status": "skipped", "reason": "not_a_git_repo", "detail": out}

    rc, _ = run_cmd(["git", "add", "bookmarks"], repo_root)
    if rc != 0:
        return {"status": "error", "reason": "git_add_failed"}

    rc, diff_cached = run_cmd(["git", "diff", "--cached", "--name-only"], repo_root)
    if rc != 0:
        return {"status": "error", "reason": "git_diff_failed"}
    if not diff_cached.strip():
        return {"status": "skipped", "reason": "no_changes"}

    rc, commit_out = run_cmd(["git", "commit", "-m", commit_msg], repo_root)
    if rc != 0:
        return {"status": "error", "reason": "git_commit_failed", "detail": commit_out}

    branch = cfg.git_branch
    if not branch:
        rc, branch_out = run_cmd(["git", "branch", "--show-current"], repo_root)
        if rc != 0 or not branch_out.strip():
            return {"status": "error", "reason": "cannot_detect_branch"}
        branch = branch_out.strip()

    rc, push_out = run_cmd(["git", "push", cfg.git_remote, branch], repo_root)
    if rc != 0:
        return {"status": "error", "reason": "git_push_failed", "detail": push_out}

    return {"status": "ok", "branch": branch, "detail": push_out}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync X bookmarks to local + git")
    p.add_argument("--max-pages", type=int, default=None, help="Override max pages per run")
    p.add_argument("--no-git", action="store_true", help="Do not commit/push git")
    p.add_argument("--include-folders", action="store_true", default=True, help="Try to resolve bookmark folder membership")
    p.add_argument("--no-folders", action="store_false", dest="include_folders", help="Skip folder membership lookup")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args)
    paths = ensure_dirs(cfg.output_root)
    cp_path = checkpoint_path(cfg.output_root)
    checkpoint = load_checkpoint(cp_path)
    old_frontier = str(checkpoint.get("latest_tweet_id", "")).strip() or None

    user_id = get_user_id(cfg)
    posts, users, sync_meta = fetch_bookmarks_pages(
        cfg=cfg,
        user_id=user_id,
        raw_dir=paths["raw_day"],
        frontier_tweet_id=old_frontier,
    )

    new_post_ids = {str(p.get("id", "")).strip() for p in posts if p.get("id")}
    folder_map: dict[str, list[str]] = {}
    folder_names: dict[str, str] = {}
    if cfg.include_folders and new_post_ids:
        try:
            folder_map, folder_names = fetch_folder_map(
                cfg=cfg,
                user_id=user_id,
                raw_dir=paths["raw_day"],
                target_post_ids=new_post_ids,
            )
        except SyncError as exc:
            # Folder metadata is optional for sync correctness.
            print(f"[WARN] folder lookup failed: {exc}", file=sys.stderr)

    conn = open_db(cfg.output_root / "index.sqlite")
    fetched_at = utc_now_iso()
    inserted = 0
    for p in posts:
        pid = str(p.get("id", "")).strip()
        if not pid:
            continue
        a = users.get(str(p.get("author_id", "")).strip())
        fids = folder_map.get(pid, [])
        fnames = [folder_names.get(fid, "") for fid in fids if folder_names.get(fid, "")]
        upsert_post(
            conn=conn,
            posts_dir=paths["posts"],
            post=p,
            author=a,
            fetched_at=fetched_at,
            folder_ids=fids,
            folder_names=fnames,
        )
        inserted += 1
    conn.commit()
    conn.close()

    new_frontier = sync_meta.get("frontier_tweet_id_new") or old_frontier
    state = {
        "latest_tweet_id": new_frontier,
        "last_run_at": utc_now_iso(),
        "last_success_at": utc_now_iso(),
        "stats": {
            "new_posts": inserted,
            "pages_fetched": sync_meta.get("pages_fetched"),
            "reached_frontier": sync_meta.get("reached_frontier"),
        },
    }
    write_checkpoint(cp_path, state)

    git_result = {"status": "skipped", "reason": "disabled"}
    if cfg.do_git_push:
        msg = f"sync(bookmarks): {inserted} new @ {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        git_result = maybe_git_commit_and_push(cfg, Path.cwd(), msg)

    print(
        json.dumps(
            {
                "ok": True,
                "new_posts": inserted,
                "paths": {
                    "root": str(cfg.output_root),
                    "checkpoint": str(cp_path),
                    "sqlite": str(cfg.output_root / "index.sqlite"),
                },
                "sync_meta": sync_meta,
                "git": git_result,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2)
