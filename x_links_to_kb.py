#!/usr/bin/env python3
"""
Ingest X status links and build a Markdown knowledge base.

Pipeline:
  1) capture links into inbox
  2) sync pending links -> fetch metadata -> render markdown -> update index
  3) optional git commit/push
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sqlite3
import subprocess
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class IngestError(Exception):
    """Raised when the ingest pipeline cannot continue safely."""


STATUS_URL_RE = re.compile(
    r"https?://(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/(\d+)(?:[/?#][^\s]*)?",
    flags=re.IGNORECASE,
)
HASHTAG_RE = re.compile(r"#([0-9A-Za-z_\u4e00-\u9fff]+)")
TAG_SPLIT_RE = re.compile(r"[,\s]+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


@dataclass
class Config:
    kb_root: Path
    state_dir: Path
    raw_dir: Path
    default_category: str
    categories: list[str]
    x_access_token: str | None
    x_api_base: str
    request_timeout_sec: int
    auto_git_push: bool
    git_remote: str
    git_branch: str | None


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_dotenv_if_exists(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def parse_categories(raw: str) -> list[str]:
    items = [x.strip().lower() for x in raw.split(",")]
    cleaned = [x for x in items if x]
    if not cleaned:
        return ["ai", "eda", "verification", "career", "tools", "misc"]
    # Keep order and remove duplicates.
    seen: set[str] = set()
    result: list[str] = []
    for item in cleaned:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def load_config() -> Config:
    load_dotenv_if_exists(Path(".env"))

    kb_root = Path(os.environ.get("KB_ROOT", "x-bookmarks")).expanduser().resolve()
    state_dir = kb_root / "_state"
    raw_dir = kb_root / "_raw"
    default_category = os.environ.get("KB_DEFAULT_CATEGORY", "tools").strip().lower() or "tools"
    categories = parse_categories(os.environ.get("KB_CATEGORIES", "ai,eda,verification,career,tools,misc"))
    if default_category not in categories:
        categories.append(default_category)

    token = os.environ.get("X_ACCESS_TOKEN", "").strip() or None
    api_base = os.environ.get("X_API_BASE", "https://api.x.com/2").rstrip("/")
    timeout = int(os.environ.get("X_REQUEST_TIMEOUT_SEC", "30"))
    auto_git_push = os.environ.get("KB_AUTO_GIT_PUSH", "1") == "1"
    git_remote = os.environ.get("KB_GIT_REMOTE", "origin")
    git_branch = os.environ.get("KB_GIT_BRANCH", "").strip() or None

    if timeout < 5:
        raise IngestError("X_REQUEST_TIMEOUT_SEC must be >= 5")

    return Config(
        kb_root=kb_root,
        state_dir=state_dir,
        raw_dir=raw_dir,
        default_category=default_category,
        categories=categories,
        x_access_token=token,
        x_api_base=api_base,
        request_timeout_sec=timeout,
        auto_git_push=auto_git_push,
        git_remote=git_remote,
        git_branch=git_branch,
    )


def ensure_dirs(cfg: Config) -> None:
    cfg.kb_root.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    for category in cfg.categories:
        (cfg.kb_root / category).mkdir(parents=True, exist_ok=True)


def db_path(cfg: Config) -> Path:
    return cfg.state_dir / "index.sqlite"


def open_db(cfg: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(cfg))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox (
          url TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          raw_text TEXT,
          tags_json TEXT,
          note TEXT,
          source TEXT,
          error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
          tweet_id TEXT PRIMARY KEY,
          url TEXT NOT NULL UNIQUE,
          path TEXT NOT NULL,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          tags_json TEXT NOT NULL,
          author_name TEXT,
          author_username TEXT,
          post_time TEXT,
          ingested_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    return conn


def extract_status_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in STATUS_URL_RE.finditer(text):
        username, tweet_id = match.group(1), match.group(2)
        urls.append(canonical_status_url(username, tweet_id))
    # Keep stable order and dedupe.
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        result.append(u)
    return result


def canonical_status_url(username: str, tweet_id: str) -> str:
    return f"https://x.com/{username}/status/{tweet_id}"


def parse_tags(raw_tags: str | None, text: str) -> list[str]:
    tags: list[str] = []
    if raw_tags:
        for t in TAG_SPLIT_RE.split(raw_tags.strip()):
            t = t.strip().lower()
            if t:
                tags.append(t)
    for t in HASHTAG_RE.findall(text):
        t = t.strip().lower()
        if t:
            tags.append(t)
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def capture_links(
    conn: sqlite3.Connection,
    text: str,
    raw_tags: str | None,
    note: str | None,
    source: str,
) -> dict[str, Any]:
    urls = extract_status_urls(text)
    if not urls:
        raise IngestError("No valid X status URL found in input text")

    tags = parse_tags(raw_tags, text)
    now = utc_now_iso()
    inserted = 0
    updated = 0
    for url in urls:
        row = conn.execute("SELECT status FROM inbox WHERE url = ?", (url,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO inbox (url, status, raw_text, tags_json, note, source, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (url, text, json.dumps(tags, ensure_ascii=False), note, source, now, now),
            )
            inserted += 1
        else:
            conn.execute(
                """
                UPDATE inbox
                SET status = 'pending',
                    raw_text = ?,
                    tags_json = ?,
                    note = ?,
                    source = ?,
                    error = NULL,
                    updated_at = ?
                WHERE url = ?
                """,
                (text, json.dumps(tags, ensure_ascii=False), note, source, now, url),
            )
            updated += 1
    conn.commit()
    return {"captured_urls": urls, "inserted": inserted, "updated": updated, "tags": tags}


def api_get_json(url: str, headers: dict[str, str] | None, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise IngestError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise IngestError(f"Network error for {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise IngestError(f"Invalid JSON response for {url}: {exc}") from exc


def extract_tweet_id(url: str) -> str:
    m = STATUS_URL_RE.search(url)
    if not m:
        raise IngestError(f"Cannot extract tweet id from URL: {url}")
    return m.group(2)


def html_to_text(fragment: str) -> str:
    stripped = HTML_TAG_RE.sub("", fragment)
    unescaped = html.unescape(stripped)
    return SPACE_RE.sub(" ", unescaped).strip()


def fetch_with_oembed(cfg: Config, url: str) -> dict[str, Any]:
    q = urllib.parse.urlencode({"url": url, "omit_script": "1", "dnt": "true"})
    endpoint = f"https://publish.twitter.com/oembed?{q}"
    payload = api_get_json(endpoint, headers=None, timeout=cfg.request_timeout_sec)

    html_block = str(payload.get("html", ""))
    p_match = re.search(r"<p[^>]*>(.*?)</p>", html_block, flags=re.IGNORECASE | re.DOTALL)
    text = html_to_text(p_match.group(1)) if p_match else ""
    anchor_matches = re.findall(r"<a href=\"([^\"]+)\">([^<]+)</a>", html_block)
    published_text = ""
    if anchor_matches:
        published_text = html.unescape(anchor_matches[-1][1]).strip()

    author_name = str(payload.get("author_name", "")).strip()
    author_url = str(payload.get("author_url", "")).strip()
    author_username = ""
    m = re.search(r"/([A-Za-z0-9_]+)$", author_url)
    if m:
        author_username = m.group(1)

    return {
        "source_mode": "oembed",
        "text": text,
        "author_name": author_name,
        "author_username": author_username,
        "post_time": published_text,
        "thread_context": "未抓取（无 API token 模式）",
        "image_alts": [],
        "raw_payload": payload,
    }


def fetch_with_x_api(cfg: Config, tweet_id: str) -> dict[str, Any]:
    if not cfg.x_access_token:
        raise IngestError("X_ACCESS_TOKEN is missing")
    q = urllib.parse.urlencode(
        {
            "tweet.fields": "created_at,author_id,conversation_id,referenced_tweets,attachments",
            "user.fields": "id,name,username",
            "media.fields": "type,alt_text,url,preview_image_url",
            "expansions": "author_id,attachments.media_keys",
        }
    )
    endpoint = f"{cfg.x_api_base}/tweets/{tweet_id}?{q}"
    payload = api_get_json(
        endpoint,
        headers={"Authorization": f"Bearer {cfg.x_access_token}"},
        timeout=cfg.request_timeout_sec,
    )
    data = payload.get("data") or {}
    if not data:
        raise IngestError("X API returned empty tweet data")

    users = {str(u.get("id", "")): u for u in (payload.get("includes") or {}).get("users") or []}
    media_map = {str(m.get("media_key", "")): m for m in (payload.get("includes") or {}).get("media") or []}

    author_id = str(data.get("author_id", "")).strip()
    author = users.get(author_id) or {}
    author_name = str(author.get("name", "")).strip()
    author_username = str(author.get("username", "")).strip()
    text = str(data.get("text", "")).strip()
    post_time = str(data.get("created_at", "")).strip()

    conversation_id = str(data.get("conversation_id", "")).strip()
    thread_context = f"conversation_id={conversation_id}" if conversation_id else "unknown"

    image_alts: list[str] = []
    media_keys = ((data.get("attachments") or {}).get("media_keys")) or []
    for key in media_keys:
        media_obj = media_map.get(str(key))
        if not media_obj:
            continue
        alt = str(media_obj.get("alt_text", "")).strip()
        if alt:
            image_alts.append(alt)

    return {
        "source_mode": "x_api",
        "text": text,
        "author_name": author_name,
        "author_username": author_username,
        "post_time": post_time,
        "thread_context": thread_context,
        "image_alts": image_alts,
        "raw_payload": payload,
    }


def keyword_category_rules() -> dict[str, list[str]]:
    return {
        "ai": ["ai", "llm", "agent", "gpt", "model", "prompt", "大模型", "智能体"],
        "eda": ["eda", "chip", "asic", "rtl", "timing", "place", "route", "芯片", "后端"],
        "verification": ["verification", "uvm", "formal", "coverage", "assertion", "验证", "仿真"],
        "career": ["career", "interview", "leader", "management", "hiring", "职业", "面试"],
        "tools": ["tool", "automation", "script", "workflow", "效率", "自动化", "工具"],
    }


def infer_category(cfg: Config, tags: list[str], text: str) -> str:
    lowered_tags = [t.lower() for t in tags]
    for t in lowered_tags:
        if t in cfg.categories:
            return t

    corpus = (text or "").lower()
    for category, keywords in keyword_category_rules().items():
        if category not in cfg.categories:
            continue
        if any(k in corpus for k in keywords):
            return category
    return cfg.default_category


def infer_title(text: str, author_name: str, tweet_id: str) -> str:
    cleaned = SPACE_RE.sub(" ", text).strip()
    if cleaned:
        return cleaned[:72]
    if author_name:
        return f"{author_name} 的帖子 {tweet_id}"
    return f"Post {tweet_id}"


def split_key_points(text: str, max_points: int = 3) -> list[str]:
    if not text.strip():
        return ["（待补充）"]
    candidates = re.split(r"[。！？!?]\s*|\n+", text.strip())
    points = [SPACE_RE.sub(" ", c).strip(" -") for c in candidates if c.strip()]
    if not points:
        return [text.strip()]
    return points[:max_points]


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def write_raw_payload(cfg: Config, tweet_id: str, payload: dict[str, Any], mode: str) -> Path:
    day = dt.date.today().isoformat()
    day_dir = cfg.raw_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    fp = day_dir / f"{ts}_{tweet_id}_{mode}.json"
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def render_markdown(
    title: str,
    url: str,
    author_name: str,
    author_username: str,
    post_time: str,
    tags: list[str],
    key_points: list[str],
    quote_text: str,
    category: str,
    thread_context: str,
    image_alts: list[str],
) -> str:
    tag_text = ", ".join(tags) if tags else category
    image_alt_text = "；".join(image_alts) if image_alts else "（未抓取到）"
    quote = quote_text.strip() or "（未抓取到）"
    quote = quote.replace("\n", "\n> ")
    author_line = author_name or "unknown"
    if author_username:
        author_line = f"{author_line} (@{author_username})"

    points_block = "\n".join(f"- {p}" for p in key_points)
    return textwrap.dedent(
        f"""\
        # {title}
        - 作者: {author_line}
        - 时间: {post_time or "unknown"}
        - 原始链接: {url}
        - 标签: {tag_text}
        - 线程: {thread_context}
        - 图片说明: {image_alt_text}

        ## 核心观点
        {points_block}

        ## 关键原文摘录
        > {quote}

        ## 我的理解
        - 待补充

        ## 可执行动作
        - 待补充

        ## 相关主题
        - {category}
        """
    ).rstrip() + "\n"


def build_entry_path(cfg: Config, category: str, tweet_id: str) -> Path:
    day = dt.date.today().isoformat()
    directory = cfg.kb_root / category / day
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{sanitize_filename(tweet_id)}.md"


def upsert_entry(
    conn: sqlite3.Connection,
    tweet_id: str,
    url: str,
    path: Path,
    title: str,
    category: str,
    tags: list[str],
    author_name: str,
    author_username: str,
    post_time: str,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO entries (
          tweet_id, url, path, title, category, tags_json,
          author_name, author_username, post_time, ingested_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tweet_id) DO UPDATE SET
          url = excluded.url,
          path = excluded.path,
          title = excluded.title,
          category = excluded.category,
          tags_json = excluded.tags_json,
          author_name = excluded.author_name,
          author_username = excluded.author_username,
          post_time = excluded.post_time,
          ingested_at = excluded.ingested_at
        """,
        (
            tweet_id,
            url,
            str(path),
            title,
            category,
            json.dumps(tags, ensure_ascii=False),
            author_name,
            author_username,
            post_time,
            now,
        ),
    )


def mark_inbox_done(conn: sqlite3.Connection, url: str) -> None:
    conn.execute(
        "UPDATE inbox SET status = 'done', error = NULL, updated_at = ? WHERE url = ?",
        (utc_now_iso(), url),
    )


def mark_inbox_error(conn: sqlite3.Connection, url: str, err: str) -> None:
    conn.execute(
        "UPDATE inbox SET status = 'error', error = ?, updated_at = ? WHERE url = ?",
        (err[:1000], utc_now_iso(), url),
    )


def build_kb_readme(cfg: Config, conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT title, path, category, post_time, author_name, author_username, ingested_at
        FROM entries
        ORDER BY ingested_at DESC
        LIMIT 100
        """
    ).fetchall()
    counts = conn.execute(
        "SELECT category, COUNT(*) FROM entries GROUP BY category ORDER BY category ASC"
    ).fetchall()

    lines: list[str] = []
    lines.append("# X Links Knowledge Base")
    lines.append("")
    lines.append("自动化链路：iPhone 分享 X 链接 -> openclaw 入站 -> Markdown 清洗 -> Git 推送")
    lines.append("")
    lines.append("## 分类统计")
    lines.append("")
    if counts:
        for category, count in counts:
            lines.append(f"- `{category}`: {count}")
    else:
        lines.append("- 暂无数据")
    lines.append("")
    lines.append("## 最新条目")
    lines.append("")
    if rows:
        for title, path, category, post_time, author_name, author_username, _ in rows:
            rel = Path(path).resolve().relative_to(cfg.kb_root)
            author = author_name or "unknown"
            if author_username:
                author = f"{author} (@{author_username})"
            lines.append(
                f"- [{title}]({rel.as_posix()}) | `{category}` | {author} | {post_time or 'unknown'}"
            )
    else:
        lines.append("- 暂无条目")
    lines.append("")
    (cfg.kb_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def git_cmd(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def maybe_git_push(cfg: Config, touched_root: str, commit_msg: str) -> dict[str, Any]:
    rc, _ = git_cmd(["git", "rev-parse", "--is-inside-work-tree"], Path.cwd())
    if rc != 0:
        return {"status": "skipped", "reason": "not_a_git_repo"}

    rc, _ = git_cmd(["git", "add", touched_root], Path.cwd())
    if rc != 0:
        return {"status": "error", "reason": "git_add_failed"}

    rc, diff = git_cmd(["git", "diff", "--cached", "--name-only"], Path.cwd())
    if rc != 0:
        return {"status": "error", "reason": "git_diff_failed"}
    if not diff.strip():
        return {"status": "skipped", "reason": "no_changes"}

    rc, out = git_cmd(["git", "commit", "-m", commit_msg], Path.cwd())
    if rc != 0:
        return {"status": "error", "reason": "git_commit_failed", "detail": out}

    branch = cfg.git_branch
    if not branch:
        rc, branch_out = git_cmd(["git", "branch", "--show-current"], Path.cwd())
        if rc != 0 or not branch_out.strip():
            return {"status": "error", "reason": "cannot_detect_branch"}
        branch = branch_out.strip()

    rc, out = git_cmd(["git", "push", cfg.git_remote, branch], Path.cwd())
    if rc != 0:
        return {"status": "error", "reason": "git_push_failed", "detail": out}
    return {"status": "ok", "branch": branch}


def process_one_link(cfg: Config, conn: sqlite3.Connection, url: str, tags: list[str]) -> dict[str, Any]:
    tweet_id = extract_tweet_id(url)
    metadata: dict[str, Any]
    if cfg.x_access_token:
        try:
            metadata = fetch_with_x_api(cfg, tweet_id)
        except IngestError:
            metadata = fetch_with_oembed(cfg, url)
    else:
        metadata = fetch_with_oembed(cfg, url)

    text = str(metadata.get("text", "")).strip()
    author_name = str(metadata.get("author_name", "")).strip()
    author_username = str(metadata.get("author_username", "")).strip()
    post_time = str(metadata.get("post_time", "")).strip()
    thread_context = str(metadata.get("thread_context", "")).strip() or "unknown"
    image_alts = [str(x).strip() for x in metadata.get("image_alts", []) if str(x).strip()]

    category = infer_category(cfg, tags, text)
    title = infer_title(text, author_name, tweet_id)
    points = split_key_points(text, max_points=3)

    md = render_markdown(
        title=title,
        url=url,
        author_name=author_name,
        author_username=author_username,
        post_time=post_time,
        tags=tags,
        key_points=points,
        quote_text=text,
        category=category,
        thread_context=thread_context,
        image_alts=image_alts,
    )

    out_path = build_entry_path(cfg, category, tweet_id)
    out_path.write_text(md, encoding="utf-8")
    write_raw_payload(cfg, tweet_id, metadata.get("raw_payload") or {}, metadata.get("source_mode", "unknown"))
    upsert_entry(
        conn=conn,
        tweet_id=tweet_id,
        url=url,
        path=out_path,
        title=title,
        category=category,
        tags=tags,
        author_name=author_name,
        author_username=author_username,
        post_time=post_time,
    )
    return {
        "tweet_id": tweet_id,
        "category": category,
        "path": str(out_path),
        "source_mode": metadata.get("source_mode", "unknown"),
    }


def cmd_capture(args: argparse.Namespace, cfg: Config, conn: sqlite3.Connection) -> int:
    result = capture_links(
        conn=conn,
        text=args.text,
        raw_tags=args.tags,
        note=args.note,
        source=args.source,
    )
    print(json.dumps({"ok": True, "action": "capture", **result}, ensure_ascii=False))
    return 0


def cmd_sync(args: argparse.Namespace, cfg: Config, conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT url, tags_json FROM inbox WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
        (args.limit,),
    ).fetchall()
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for url, tags_json in rows:
        tags: list[str]
        try:
            tags = json.loads(tags_json or "[]")
        except json.JSONDecodeError:
            tags = []
        try:
            result = process_one_link(cfg, conn, url, tags)
            mark_inbox_done(conn, url)
            processed.append(result)
        except Exception as exc:  # Keep sync loop running for the remaining links.
            mark_inbox_error(conn, url, str(exc))
            errors.append({"url": url, "error": str(exc)})
        conn.commit()

    build_kb_readme(cfg, conn)
    conn.commit()

    git_result = {"status": "skipped", "reason": "disabled"}
    if cfg.auto_git_push and not args.no_git:
        commit_msg = f"feat(kb): ingest {len(processed)} links @ {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        git_result = maybe_git_push(cfg, cfg.kb_root.name, commit_msg)

    print(
        json.dumps(
            {
                "ok": len(errors) == 0,
                "action": "sync",
                "pending_seen": len(rows),
                "processed": processed,
                "errors": errors,
                "git": git_result,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 3


def cmd_capture_sync(args: argparse.Namespace, cfg: Config, conn: sqlite3.Connection) -> int:
    cmd_capture(args, cfg, conn)
    sync_args = argparse.Namespace(limit=args.limit, no_git=args.no_git)
    return cmd_sync(sync_args, cfg, conn)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest X links into Markdown knowledge base")
    sub = p.add_subparsers(dest="cmd", required=True)

    c1 = sub.add_parser("capture", help="Capture X links into inbox")
    c1.add_argument("--text", required=True, help="Raw message text containing one or more X links")
    c1.add_argument("--tags", default="", help="Comma-separated tags, e.g. ai,tools")
    c1.add_argument("--note", default="", help="Optional note")
    c1.add_argument("--source", default="manual", help="Source label, e.g. openclaw,iphone")
    c1.set_defaults(func=cmd_capture)

    c2 = sub.add_parser("sync", help="Process pending links")
    c2.add_argument("--limit", type=int, default=30, help="Max pending links to process per run")
    c2.add_argument("--no-git", action="store_true", help="Do not commit/push git")
    c2.set_defaults(func=cmd_sync)

    c3 = sub.add_parser("capture-sync", help="Capture and sync in one command")
    c3.add_argument("--text", required=True, help="Raw message text containing one or more X links")
    c3.add_argument("--tags", default="", help="Comma-separated tags, e.g. ai,tools")
    c3.add_argument("--note", default="", help="Optional note")
    c3.add_argument("--source", default="manual", help="Source label, e.g. openclaw,iphone")
    c3.add_argument("--limit", type=int, default=30, help="Max pending links to process per run")
    c3.add_argument("--no-git", action="store_true", help="Do not commit/push git")
    c3.set_defaults(func=cmd_capture_sync)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config()
    ensure_dirs(cfg)
    conn = open_db(cfg)
    try:
        return int(args.func(args, cfg, conn))
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IngestError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
