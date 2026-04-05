#!/usr/bin/env python3
"""x_to_cdns unified CLI.

Goals:
- Stable data contract (raw / curated / index / meta)
- Explicit state machine (.state/pending -> processing -> done/error/retry)
- Single CLI entrypoint for sync/index/search/status/list/path
- Structured run logs for observability
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import mimetypes
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUS_URL_RE = re.compile(
    r"https?://(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/(\d+)(?:[/?#][^\s]*)?",
    flags=re.IGNORECASE,
)
HASHTAG_RE = re.compile(r"#([0-9A-Za-z_\u4e00-\u9fff]+)")
TAG_SPLIT_RE = re.compile(r"[,\s]+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+", flags=re.IGNORECASE)

QUEUE_STATES = ("pending", "processing", "done", "error", "retry")
DEGRADED_MARKERS = [
    "don’t miss what’s happening",
    "don't miss what's happening",
    "log in",
    "sign up",
    "join x today",
    "new to x",
    "create account",
    "terms of service",
    "privacy policy",
    "cookie policy",
    "ads info",
]


class CliError(Exception):
    """Raised when an operation cannot continue safely."""


class CaptureQualityError(CliError):
    """Raised when capture is degraded and must not be curated."""

    def __init__(
        self,
        message: str,
        *,
        reason_codes: list[str] | None = None,
        archive_json_path: str = "",
        archive_md_path: str = "",
        archive_html_path: str = "",
        quality_score: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason_codes = reason_codes or []
        self.archive_json_path = archive_json_path
        self.archive_md_path = archive_md_path
        self.archive_html_path = archive_html_path
        self.quality_score = quality_score


@dataclass
class CategoryRule:
    name: str
    match: list[str]
    action: str
    folder: str
    template: str


@dataclass
class CategoryConfig:
    version: int
    default_category: str
    rules: list[CategoryRule]


@dataclass
class Config:
    project_root: Path
    data_root: Path
    raw_root: Path
    curated_root: Path
    index_root: Path
    meta_root: Path
    archive_root: Path

    state_root: Path
    state_pending: Path
    state_processing: Path
    state_done: Path
    state_error: Path
    state_retry: Path
    state_locks: Path
    state_runs: Path

    categories_cfg_path: Path
    templates_root: Path

    x_access_token: str | None
    x_api_base: str
    request_timeout_sec: int
    content_min_len: int
    browser_fallback_enabled: bool
    browser_fallback_cmd: str | None
    browser_fallback_timeout_sec: int
    min_accept_score: int
    download_media: bool
    max_media_download: int

    max_retry: int
    auto_git_push: bool
    git_remote: str
    git_branch: str | None
    git_include_state: bool


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


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


def load_config() -> Config:
    project_root = Path.cwd().resolve()
    load_dotenv_if_exists(project_root / ".env")

    data_root = (project_root / os.environ.get("KB_ROOT", "x-bookmarks")).resolve()
    raw_root = data_root / "raw"
    curated_root = data_root / "curated"
    index_root = data_root / "index"
    meta_root = data_root / "meta"
    archive_root = data_root / "archive"

    state_root = (project_root / os.environ.get("KB_STATE_ROOT", ".state")).resolve()

    categories_cfg_path = (
        project_root / os.environ.get("KB_CATEGORIES_CONFIG", "config/categories.json")
    ).resolve()
    templates_root = (project_root / os.environ.get("KB_TEMPLATE_DIR", "templates")).resolve()

    token = os.environ.get("X_ACCESS_TOKEN", "").strip() or None
    api_base = os.environ.get("X_API_BASE", "https://api.x.com/2").rstrip("/")
    timeout = int(os.environ.get("X_REQUEST_TIMEOUT_SEC", "30"))
    content_min_len = int(os.environ.get("KB_CONTENT_MIN_LEN", "120"))
    browser_fallback_enabled = os.environ.get("KB_BROWSER_FALLBACK_ENABLED", "1") == "1"
    browser_fallback_cmd = os.environ.get("KB_BROWSER_FALLBACK_CMD", "").strip() or None
    browser_fallback_timeout_sec = int(os.environ.get("KB_BROWSER_FALLBACK_TIMEOUT_SEC", "25"))
    min_accept_score = int(os.environ.get("KB_MIN_ACCEPT_SCORE", "70"))
    download_media = os.environ.get("KB_DOWNLOAD_MEDIA", "1") == "1"
    max_media_download = int(os.environ.get("KB_MAX_MEDIA_DOWNLOAD", "4"))

    max_retry = int(os.environ.get("KB_MAX_RETRY", "2"))
    auto_git_push = os.environ.get("KB_AUTO_GIT_PUSH", "0") == "1"
    git_remote = os.environ.get("KB_GIT_REMOTE", "origin")
    git_branch = os.environ.get("KB_GIT_BRANCH", "").strip() or None
    git_include_state = os.environ.get("KB_GIT_INCLUDE_STATE", "0") == "1"

    if timeout < 5:
        raise CliError("X_REQUEST_TIMEOUT_SEC must be >= 5")

    return Config(
        project_root=project_root,
        data_root=data_root,
        raw_root=raw_root,
        curated_root=curated_root,
        index_root=index_root,
        meta_root=meta_root,
        archive_root=archive_root,
        state_root=state_root,
        state_pending=state_root / "pending",
        state_processing=state_root / "processing",
        state_done=state_root / "done",
        state_error=state_root / "error",
        state_retry=state_root / "retry",
        state_locks=state_root / "locks",
        state_runs=state_root / "runs",
        categories_cfg_path=categories_cfg_path,
        templates_root=templates_root,
        x_access_token=token,
        x_api_base=api_base,
        request_timeout_sec=timeout,
        content_min_len=content_min_len,
        browser_fallback_enabled=browser_fallback_enabled,
        browser_fallback_cmd=browser_fallback_cmd,
        browser_fallback_timeout_sec=browser_fallback_timeout_sec,
        min_accept_score=min_accept_score,
        download_media=download_media,
        max_media_download=max_media_download,
        max_retry=max_retry,
        auto_git_push=auto_git_push,
        git_remote=git_remote,
        git_branch=git_branch,
        git_include_state=git_include_state,
    )


def ensure_layout(cfg: Config) -> None:
    for p in (
        cfg.raw_root,
        cfg.curated_root,
        cfg.index_root,
        cfg.meta_root,
        cfg.archive_root,
        cfg.state_pending,
        cfg.state_processing,
        cfg.state_done,
        cfg.state_error,
        cfg.state_retry,
        cfg.state_locks,
        cfg.state_runs,
    ):
        p.mkdir(parents=True, exist_ok=True)


def parse_tags(raw_tags: str | None, text: str) -> list[str]:
    tags: list[str] = []
    if raw_tags:
        for tag in TAG_SPLIT_RE.split(raw_tags.strip()):
            t = tag.strip().lower()
            if t:
                tags.append(t)
    for tag in HASHTAG_RE.findall(text):
        t = tag.strip().lower()
        if t:
            tags.append(t)

    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def canonical_status_url(username: str, tweet_id: str) -> str:
    return f"https://x.com/{username}/status/{tweet_id}"


def extract_status_urls(text: str) -> list[str]:
    urls: list[str] = []
    for m in STATUS_URL_RE.finditer(text):
        urls.append(canonical_status_url(m.group(1), m.group(2)))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_tweet_id(url: str) -> str:
    m = STATUS_URL_RE.search(url)
    if not m:
        raise CliError(f"Cannot parse tweet id from URL: {url}")
    return m.group(2)


def task_path(cfg: Config, state: str, task_id: str) -> Path:
    if state not in QUEUE_STATES:
        raise CliError(f"Unknown queue state: {state}")
    return getattr(cfg, f"state_{state}") / f"{task_id}.json"


def locate_task(cfg: Config, task_id: str) -> tuple[str, Path] | None:
    for state in QUEUE_STATES:
        p = task_path(cfg, state, task_id)
        if p.exists():
            return state, p
    return None


def read_task(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_task(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def move_task(cfg: Config, payload: dict[str, Any], from_state: str, to_state: str) -> Path:
    src = task_path(cfg, from_state, payload["task_id"])
    dst = task_path(cfg, to_state, payload["task_id"])
    payload["status"] = to_state
    payload["updated_at"] = utc_now_iso()
    write_task(src, payload)
    src.replace(dst)
    return dst


# ---------------------------------------------------------------------------
# Categories + template
# ---------------------------------------------------------------------------


def default_categories() -> CategoryConfig:
    return CategoryConfig(
        version=1,
        default_category="misc",
        rules=[
            CategoryRule("github", ["github.com"], "file", "tools", "bookmark"),
            CategoryRule("ai", [" ai ", "llm", "gpt", "agent", "anthropic", "openai"], "file", "ai", "bookmark"),
            CategoryRule("eda", ["eda", "asic", "rtl", "timing", "cadence", "synopsys"], "file", "eda", "bookmark"),
            CategoryRule("verification", ["verification", "uvm", "formal", "coverage", "assertion", "验证"], "file", "verification", "bookmark"),
            CategoryRule("career", ["career", "interview", "management", "hiring", "职业", "面试"], "file", "career", "bookmark"),
            CategoryRule("tools", ["tool", "automation", "script", "workflow", "效率", "自动化", "工具"], "file", "tools", "bookmark"),
            CategoryRule("default", [], "file", "misc", "bookmark"),
        ],
    )


def load_categories(cfg: Config) -> CategoryConfig:
    if not cfg.categories_cfg_path.exists():
        cfg.categories_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        d = default_categories()
        dump = {
            "version": d.version,
            "default_category": d.default_category,
            "rules": [
                {
                    "name": r.name,
                    "match": r.match,
                    "action": r.action,
                    "folder": r.folder,
                    "template": r.template,
                }
                for r in d.rules
            ],
        }
        cfg.categories_cfg_path.write_text(
            json.dumps(dump, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return d

    raw = json.loads(cfg.categories_cfg_path.read_text(encoding="utf-8"))
    rules = [
        CategoryRule(
            name=str(item.get("name", "")).strip() or "default",
            match=[str(x).lower() for x in item.get("match", []) if str(x).strip()],
            action=str(item.get("action", "file")).strip() or "file",
            folder=str(item.get("folder", "misc")).strip() or "misc",
            template=str(item.get("template", "bookmark")).strip() or "bookmark",
        )
        for item in raw.get("rules", [])
    ]
    if not rules:
        return default_categories()
    return CategoryConfig(
        version=int(raw.get("version", 1)),
        default_category=str(raw.get("default_category", "misc")),
        rules=rules,
    )


def choose_rule(cat_cfg: CategoryConfig, tags: list[str], text: str, url: str) -> CategoryRule:
    tag_set = {t.lower() for t in tags}
    corpus = f" {text.lower()} {url.lower()} "

    # explicit tag can route to rule by name/folder first
    for r in cat_cfg.rules:
        if r.name.lower() in tag_set or r.folder.lower() in tag_set:
            return r

    for r in cat_cfg.rules:
        if not r.match:
            continue
        if any(p in corpus for p in r.match):
            return r

    for r in cat_cfg.rules:
        if r.name.lower() == "default":
            return r

    return CategoryRule("default", [], "file", cat_cfg.default_category, "bookmark")


def ensure_default_template(cfg: Config) -> None:
    cfg.templates_root.mkdir(parents=True, exist_ok=True)
    p = cfg.templates_root / "bookmark.md"
    if p.exists():
        return
    p.write_text(
        """# {{title}}
- 作者: {{author}}
- 时间: {{post_time}}
- 原始链接: {{url}}
- 标签: {{tags}}
- 分类: {{category}}
- 线程: {{thread_context}}
- 图片说明: {{image_alts}}
- 抓取质量: {{quality_score}}
- 抓取来源: {{source_mode}}
- 原文归档(JSON): {{original_archive_json}}
- 原文归档(HTML): {{original_archive_html}}
- 原文归档(MD): {{original_archive_md}}

## 核心观点
{{key_points}}

## 关键原文摘录
> {{quote_text}}

## 原文全文
```text
{{full_text}}
```

## 我的理解
- 待补充

## 可执行动作
- 待补充

## 相关主题
- {{category}}
""",
        encoding="utf-8",
    )


def render_template(path: Path, data: dict[str, str]) -> str:
    template = path.read_text(encoding="utf-8")
    out = template
    for k, v in data.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


# ---------------------------------------------------------------------------
# Fetch + transform
# ---------------------------------------------------------------------------


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
        raise CliError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise CliError(f"Network error for {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid JSON from {url}: {exc}") from exc


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
    anchor_matches = re.findall(r'<a href="([^"]+)">([^<]+)</a>', html_block)
    published_text = html.unescape(anchor_matches[-1][1]).strip() if anchor_matches else ""

    author_name = str(payload.get("author_name", "")).strip()
    author_url = str(payload.get("author_url", "")).strip()
    author_username = ""
    m = re.search(r"/([A-Za-z0-9_]+)$", author_url)
    if m:
        author_username = m.group(1)

    try:
        tweet_id = extract_tweet_id(url)
        synd = api_get_json(
            f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=zh-cn",
            headers=None,
            timeout=cfg.request_timeout_sec,
        )
        synd_text = str(synd.get("text", "")).strip()
        if synd_text:
            text = synd_text
        s_user = synd.get("user") or {}
        s_name = str(s_user.get("name", "")).strip()
        s_screen = str(s_user.get("screen_name", "")).strip()
        if s_name:
            author_name = s_name
        if s_screen:
            author_username = s_screen
        s_time = str(synd.get("created_at", "")).strip()
        if s_time:
            published_text = s_time

        vxt = api_get_json(
            f"https://api.vxtwitter.com/Twitter/status/{tweet_id}",
            headers=None,
            timeout=cfg.request_timeout_sec,
        )
        vx_text = str(vxt.get("text", "")).strip()
        vx_user = str(vxt.get("user_name", "")).strip()
        vx_screen = str(vxt.get("user_screen_name", "")).strip()
        vx_date = str(vxt.get("date", "")).strip()
        article = vxt.get("article") or {}
        art_preview = str(article.get("preview_text", "")).strip()

        for candidate in [art_preview, vx_text, synd_text, text]:
            if candidate and not re.fullmatch(r"https?://\S+", candidate):
                text = candidate
                break
        if vx_user:
            author_name = vx_user
        if vx_screen:
            author_username = vx_screen
        if vx_date:
            published_text = vx_date

        payload = {"oembed": payload, "syndication": synd, "vxtwitter": vxt}
    except Exception:
        pass

    return {
        "source_mode": "oembed",
        "text": text,
        "author_name": author_name,
        "author_username": author_username,
        "post_time": published_text,
        "thread_context": "unknown",
        "image_alts": [],
        "raw_payload": payload,
    }


def fetch_with_x_api(cfg: Config, tweet_id: str) -> dict[str, Any]:
    if not cfg.x_access_token:
        raise CliError("X_ACCESS_TOKEN is missing")
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
        raise CliError("X API returned empty tweet data")

    users = {str(u.get("id", "")): u for u in (payload.get("includes") or {}).get("users") or []}
    media_map = {str(m.get("media_key", "")): m for m in (payload.get("includes") or {}).get("media") or []}

    author = users.get(str(data.get("author_id", "")).strip()) or {}
    image_alts: list[str] = []
    for media_key in ((data.get("attachments") or {}).get("media_keys") or []):
        media = media_map.get(str(media_key))
        if media and str(media.get("alt_text", "")).strip():
            image_alts.append(str(media.get("alt_text", "")).strip())

    conversation_id = str(data.get("conversation_id", "")).strip()
    return {
        "source_mode": "x_api",
        "text": str(data.get("text", "")).strip(),
        "author_name": str(author.get("name", "")).strip(),
        "author_username": str(author.get("username", "")).strip(),
        "post_time": str(data.get("created_at", "")).strip(),
        "thread_context": f"conversation_id={conversation_id}" if conversation_id else "unknown",
        "image_alts": image_alts,
        "raw_payload": payload,
    }


def fetch_linked_page_text(cfg: Config, text: str) -> dict[str, str]:
    m = URL_RE.search(text or "")
    if not m:
        return {}
    link = m.group(0).rstrip('.,;:!?')
    try:
        req = urllib.request.Request(link, method="GET", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=cfg.request_timeout_sec) as resp:
            raw = resp.read(600000).decode("utf-8", errors="ignore")
        tm = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
        title = html.unescape(SPACE_RE.sub(" ", tm.group(1))).strip() if tm else ""
        cleaned = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        plain = html.unescape(SPACE_RE.sub(" ", cleaned)).strip()
        return {"title": title[:120], "text": plain[:12000], "html": raw[:400000], "url": link}
    except Exception:
        return {}


def looks_incomplete(text: str, min_len: int) -> bool:
    t = (text or "").strip()
    if len(t) < max(20, min_len):
        return True
    markers = [
        "javascript is not available",
        "enable javascript",
        "log in to x",
        "don’t miss what’s happening",
        "don't miss what's happening",
    ]
    lt = t.lower()
    if any(m in lt for m in markers):
        return True
    return bool(re.fullmatch(r"https?://\S+", t))


def fetch_with_browser_fallback(cfg: Config, url: str) -> dict[str, str]:
    if not cfg.browser_fallback_enabled or not cfg.browser_fallback_cmd:
        return {}
    try:
        cmd_text = cfg.browser_fallback_cmd.replace("{url}", shlex.quote(url))
        proc = subprocess.run(
            cmd_text,
            shell=True,
            text=True,
            capture_output=True,
            timeout=cfg.browser_fallback_timeout_sec,
            check=False,
        )
        if proc.returncode != 0:
            return {}
        out = (proc.stdout or "").strip()
        if not out:
            return {}
        obj = json.loads(out)
        return {
            "title": str(obj.get("title", "")).strip(),
            "text": str(obj.get("text", "")).strip(),
            "source": str(obj.get("source", "browser-playwright")).strip() or "browser-playwright",
            "html": str(obj.get("html", "")).strip(),
            "media_json": json.dumps(obj.get("media", []), ensure_ascii=False),
        }
    except Exception:
        return {}


def split_key_points(text: str, max_points: int = 3) -> list[str]:
    if not text.strip():
        return ["（待补充）"]
    candidates = re.split(r"[。！？!?]\s*|\n+", text.strip())
    points = [SPACE_RE.sub(" ", c).strip(" -") for c in candidates if c.strip()]
    if not points:
        return [text.strip()]
    return points[:max_points]


def sanitize_filename(name: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", "-", name.strip())
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return (s or "untitled")[:100]


def find_degraded_markers(*chunks: str) -> list[str]:
    corpus = " ".join(chunks).lower()
    return sorted({m for m in DEGRADED_MARKERS if m in corpus})


def quality_score(text: str, source_mode: str, min_len: int, marker_count: int = 0) -> int:
    score = 100
    if looks_incomplete(text, min_len):
        score -= 45
    if len(text.strip()) < 280:
        score -= 15
    if marker_count > 0:
        score -= min(40, 18 + marker_count * 4)
    if source_mode == "oembed":
        score -= 10
    if source_mode == "browser-playwright":
        score += 5
    return max(0, min(score, 100))


def evaluate_capture_quality(
    cfg: Config,
    *,
    text: str,
    title: str,
    author_name: str,
    post_time: str,
    source_mode: str,
    page_html: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    hits = find_degraded_markers(text, title, page_html)
    strong_markers = {
        "don’t miss what’s happening",
        "don't miss what's happening",
        "new to x",
        "join x today",
        "terms of service",
        "privacy policy",
        "cookie policy",
    }
    if any(h in strong_markers for h in hits) or len(hits) >= 3:
        reasons.append("login_or_placeholder_markers")
    if looks_incomplete(text, cfg.content_min_len):
        reasons.append("text_incomplete")
    if len((text or "").strip()) < cfg.content_min_len:
        reasons.append("text_too_short")
    if not (author_name or "").strip():
        reasons.append("missing_author")
    if not (post_time or "").strip():
        reasons.append("missing_post_time")

    score = quality_score(text, source_mode, cfg.content_min_len, marker_count=len(hits))
    degraded = bool(hits) or score < cfg.min_accept_score
    return {
        "score": score,
        "degraded": degraded,
        "reason_codes": reasons,
        "marker_hits": hits,
    }


def guess_ext_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        return suffix
    return ".bin"


def render_original_archive_markdown(
    cfg: Config,
    *,
    tweet_id: str,
    url: str,
    title: str,
    author_name: str,
    author_username: str,
    post_time: str,
    source_mode: str,
    quality_score_value: int,
    quality_flags: list[str],
    marker_hits: list[str],
    text: str,
    thread_context: str,
    media: list[dict[str, Any]],
    archive_json_path: Path,
    archive_html_path: Path,
) -> str:
    ensure_default_template(cfg)
    tpath = cfg.templates_root / "original_archive.md"
    if not tpath.exists():
        tpath.write_text(
            """# {{title}}
- tweet_id: {{tweet_id}}
- author: {{author}}
- post_time: {{post_time}}
- url: {{url}}
- source_mode: {{source_mode}}
- quality_score: {{quality_score}}
- quality_flags: {{quality_flags}}
- marker_hits: {{marker_hits}}
- archive_json: {{archive_json}}
- archive_html: {{archive_html}}
- thread_context: {{thread_context}}

## Raw Text
```text
{{text}}
```

## Media
{{media_lines}}
""",
            encoding="utf-8",
        )

    author = author_name or "unknown"
    if author_username:
        author = f"{author} (@{author_username})"
    media_lines = []
    for m in media:
        line = f"- url: {m.get('url', '')}"
        alt = str(m.get("alt", "")).strip()
        if alt:
            line += f" | alt: {alt}"
        local = str(m.get("local_path", "")).strip()
        if local:
            line += f" | local: {local}"
        err = str(m.get("download_error", "")).strip()
        if err:
            line += f" | download_error: {err}"
        media_lines.append(line)
    if not media_lines:
        media_lines = ["- (none)"]

    return render_template(
        tpath,
        {
            "title": title,
            "tweet_id": tweet_id,
            "author": author,
            "post_time": post_time or "unknown",
            "url": url,
            "source_mode": source_mode,
            "quality_score": str(quality_score_value),
            "quality_flags": ", ".join(quality_flags) if quality_flags else "none",
            "marker_hits": ", ".join(marker_hits) if marker_hits else "none",
            "archive_json": str(archive_json_path),
            "archive_html": str(archive_html_path),
            "thread_context": thread_context or "unknown",
            "text": text or "（empty）",
            "media_lines": "\n".join(media_lines),
        },
    )


def collect_media_candidates(
    meta_payload: dict[str, Any],
    browser_media: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    includes = meta_payload.get("includes") or {}
    for item in includes.get("media") or []:
        url = str(item.get("url") or item.get("preview_image_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "url": url,
                "alt": str(item.get("alt_text", "")).strip(),
                "source": "x_api_media",
            }
        )

    for item in browser_media:
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "url": url,
                "alt": str(item.get("alt", "")).strip(),
                "source": str(item.get("source", "browser")).strip() or "browser",
            }
        )
    return out


def download_media_assets(
    cfg: Config,
    *,
    media: list[dict[str, Any]],
    assets_dir: Path,
) -> list[dict[str, Any]]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    for idx, item in enumerate(media[: max(0, cfg.max_media_download)], start=1):
        row = dict(item)
        url = str(item.get("url", "")).strip()
        if not url.startswith("http"):
            row["download_error"] = "non_http_url"
            result.append(row)
            continue
        ext = guess_ext_from_url(url)
        file_name = f"media-{idx:03d}{ext}"
        out_path = assets_dir / file_name
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=cfg.request_timeout_sec) as resp:
                blob = resp.read(4_000_000)
                ctype = str(resp.headers.get("Content-Type", "")).split(";")[0].strip()
                if ext == ".bin" and ctype:
                    guessed = mimetypes.guess_extension(ctype) or ".bin"
                    out_path = assets_dir / f"media-{idx:03d}{guessed}"
                out_path.write_bytes(blob)
            row["local_path"] = str(out_path)
        except Exception as exc:
            row["download_error"] = str(exc)
        result.append(row)
    for item in media[cfg.max_media_download :]:
        row = dict(item)
        row["download_error"] = "skipped_by_max_media_download"
        result.append(row)
    return result


def write_original_archive(
    cfg: Config,
    *,
    tweet_id: str,
    url: str,
    title: str,
    author_name: str,
    author_username: str,
    post_time: str,
    source_mode: str,
    text: str,
    thread_context: str,
    quality_score_value: int,
    quality_flags: list[str],
    marker_hits: list[str],
    raw_payload: dict[str, Any],
    page_html: str,
    media: list[dict[str, Any]],
) -> dict[str, str]:
    day = dt.date.today().isoformat()
    mode = sanitize_filename(source_mode or "unknown").lower()
    mode_dir = cfg.raw_root / mode / day
    mode_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = mode_dir / "assets" / tweet_id

    resolved_media = media
    if cfg.download_media and media:
        resolved_media = download_media_assets(cfg, media=media, assets_dir=assets_dir)

    html_snapshot = page_html.strip()
    if not html_snapshot:
        html_snapshot = (
            "<html><body><h1>Capture Snapshot</h1><pre>"
            + html.escape((text or "").strip())
            + "</pre></body></html>"
        )

    archive_json_path = mode_dir / f"{tweet_id}.json"
    archive_html_path = mode_dir / f"{tweet_id}.html"
    archive_md_path = mode_dir / f"{tweet_id}.md"

    payload = {
        "tweet_id": tweet_id,
        "url": url,
        "title": title,
        "author_name": author_name,
        "author_username": author_username,
        "post_time": post_time,
        "source_mode": source_mode,
        "captured_at": utc_now_iso(),
        "thread_context": thread_context,
        "text": text,
        "quality_score": quality_score_value,
        "quality_flags": quality_flags,
        "marker_hits": marker_hits,
        "media": resolved_media,
        "raw_payload": raw_payload,
    }
    archive_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_html_path.write_text(html_snapshot, encoding="utf-8")

    archive_md = render_original_archive_markdown(
        cfg,
        tweet_id=tweet_id,
        url=url,
        title=title,
        author_name=author_name,
        author_username=author_username,
        post_time=post_time,
        source_mode=source_mode,
        quality_score_value=quality_score_value,
        quality_flags=quality_flags,
        marker_hits=marker_hits,
        text=text,
        thread_context=thread_context,
        media=resolved_media,
        archive_json_path=archive_json_path,
        archive_html_path=archive_html_path,
    )
    archive_md_path.write_text(archive_md, encoding="utf-8")
    return {
        "json_path": str(archive_json_path),
        "html_path": str(archive_html_path),
        "md_path": str(archive_md_path),
        "assets_dir": str(assets_dir),
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def db_path(cfg: Config) -> Path:
    return cfg.index_root / "bookmarks.sqlite"


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def open_db(cfg: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(cfg))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
          tweet_id TEXT PRIMARY KEY,
          url TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          text TEXT NOT NULL,
          category TEXT NOT NULL,
          action TEXT NOT NULL,
          path TEXT,
          tags_json TEXT NOT NULL,
          author_name TEXT,
          author_username TEXT,
          post_time TEXT,
          source_mode TEXT,
          quality_score INTEGER,
          raw_json_path TEXT,
          original_md_path TEXT,
          original_html_path TEXT,
          capture_status TEXT,
          quality_flags_json TEXT,
          ingested_at TEXT NOT NULL
        )
        """
    )
    ensure_column(conn, "entries", "raw_json_path", "TEXT")
    ensure_column(conn, "entries", "original_md_path", "TEXT")
    ensure_column(conn, "entries", "original_html_path", "TEXT")
    ensure_column(conn, "entries", "capture_status", "TEXT")
    ensure_column(conn, "entries", "quality_flags_json", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_post_time ON entries(post_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_ingested_at ON entries(ingested_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_capture_status ON entries(capture_status)")
    conn.commit()
    return conn


def ensure_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
              title,
              text,
              author_name,
              author_username,
              category,
              content='entries',
              content_rowid='rowid'
            )
            """
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def rebuild_fts(conn: sqlite3.Connection) -> bool:
    if not ensure_fts(conn):
        return False
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
    conn.commit()
    return True


def upsert_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO entries (
          tweet_id, url, title, text, category, action, path, tags_json,
          author_name, author_username, post_time, source_mode, quality_score,
          raw_json_path, original_md_path, original_html_path, capture_status, quality_flags_json, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tweet_id) DO UPDATE SET
          url=excluded.url,
          title=excluded.title,
          text=excluded.text,
          category=excluded.category,
          action=excluded.action,
          path=excluded.path,
          tags_json=excluded.tags_json,
          author_name=excluded.author_name,
          author_username=excluded.author_username,
          post_time=excluded.post_time,
          source_mode=excluded.source_mode,
          quality_score=excluded.quality_score,
          raw_json_path=excluded.raw_json_path,
          original_md_path=excluded.original_md_path,
          original_html_path=excluded.original_html_path,
          capture_status=excluded.capture_status,
          quality_flags_json=excluded.quality_flags_json,
          ingested_at=excluded.ingested_at
        """,
        (
            entry["tweet_id"],
            entry["url"],
            entry["title"],
            entry["text"],
            entry["category"],
            entry["action"],
            entry.get("path", ""),
            json.dumps(entry.get("tags", []), ensure_ascii=False),
            entry.get("author_name", ""),
            entry.get("author_username", ""),
            entry.get("post_time", ""),
            entry.get("source_mode", ""),
            int(entry.get("quality_score", 0)),
            entry.get("raw_json_path", ""),
            entry.get("original_md_path", ""),
            entry.get("original_html_path", ""),
            entry.get("capture_status", "ok"),
            json.dumps(entry.get("quality_flags", []), ensure_ascii=False),
            entry["ingested_at"],
        ),
    )


def quarantine_existing_entry(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    tweet_id: str,
    reason: str,
) -> str:
    row = conn.execute("SELECT path FROM entries WHERE tweet_id = ?", (tweet_id,)).fetchone()
    quarantine_path = ""
    if row and row[0]:
        src = Path(str(row[0]))
        if src.exists():
            qdir = cfg.archive_root / "quarantine" / "degraded-curated" / dt.date.today().isoformat()
            qdir.mkdir(parents=True, exist_ok=True)
            dst = qdir / f"{src.stem}-{tweet_id}.md"
            if dst.exists():
                dst = qdir / f"{src.stem}-{tweet_id}-{int(dt.datetime.now().timestamp())}.md"
            shutil.move(str(src), str(dst))
            quarantine_path = str(dst)
    conn.execute("DELETE FROM entries WHERE tweet_id = ?", (tweet_id,))
    conn.commit()
    if quarantine_path:
        marker = cfg.meta_root / "degraded-quarantine.jsonl"
        with marker.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "tweet_id": tweet_id,
                        "reason": reason,
                        "quarantine_path": quarantine_path,
                        "timestamp": utc_now_iso(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return quarantine_path


# ---------------------------------------------------------------------------
# Queue operations
# ---------------------------------------------------------------------------


def enqueue_links(
    cfg: Config,
    text: str,
    raw_tags: str | None,
    note: str | None,
    source: str,
    force: bool = False,
) -> dict[str, Any]:
    urls = extract_status_urls(text)
    if not urls:
        raise CliError("No valid X status URL found in input text")

    tags = parse_tags(raw_tags, text)
    now = utc_now_iso()

    inserted = 0
    updated = 0
    skipped_done = 0

    for url in urls:
        task_id = extract_tweet_id(url)
        payload = {
            "task_id": task_id,
            "url": url,
            "raw_text": text,
            "tags": tags,
            "note": note or "",
            "source": source,
            "status": "pending",
            "attempts": 0,
            "last_error": "",
            "created_at": now,
            "updated_at": now,
        }
        located = locate_task(cfg, task_id)
        if located is None:
            write_task(task_path(cfg, "pending", task_id), payload)
            inserted += 1
            continue

        state, existing_path = located
        existing = read_task(existing_path)
        existing["raw_text"] = text
        existing["tags"] = tags
        existing["note"] = note or ""
        existing["source"] = source
        existing["updated_at"] = now

        if state == "done" and not force:
            skipped_done += 1
            write_task(existing_path, existing)
            continue

        existing["status"] = "pending"
        existing["last_error"] = ""
        dst = task_path(cfg, "pending", task_id)
        write_task(existing_path, existing)
        if existing_path != dst:
            existing_path.replace(dst)
        updated += 1

    return {
        "captured_urls": urls,
        "inserted": inserted,
        "updated": updated,
        "skipped_done": skipped_done,
        "tags": tags,
    }


def collect_tasks(cfg: Config, limit: int, with_retry: bool) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for p in sorted(cfg.state_pending.glob("*.json"), key=lambda x: x.stat().st_mtime):
        items.append(("pending", p))
    if with_retry:
        for p in sorted(cfg.state_retry.glob("*.json"), key=lambda x: x.stat().st_mtime):
            items.append(("retry", p))
    return items[: max(0, limit)]


def acquire_lock(cfg: Config, name: str) -> Path:
    lock = cfg.state_locks / f"{name}.lock"
    if lock.exists():
        raise CliError(f"Another {name} run appears active: {lock}")
    lock.write_text(f"pid={os.getpid()}\nstart={utc_now_iso()}\n", encoding="utf-8")
    return lock


def release_lock(lock: Path) -> None:
    try:
        if lock.exists():
            lock.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Processing core
# ---------------------------------------------------------------------------


def process_one_task(
    cfg: Config,
    cat_cfg: CategoryConfig,
    task: dict[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    url = str(task["url"])
    tweet_id = extract_tweet_id(url)
    tags = [str(x).strip().lower() for x in task.get("tags", []) if str(x).strip()]

    primary_fetch = "oembed"
    if cfg.x_access_token:
        try:
            meta = fetch_with_x_api(cfg, tweet_id)
            primary_fetch = "x_api"
        except Exception:
            meta = fetch_with_oembed(cfg, url)
    else:
        meta = fetch_with_oembed(cfg, url)

    text = str(meta.get("text", "")).strip()
    author_name = str(meta.get("author_name", "")).strip()
    author_username = str(meta.get("author_username", "")).strip()
    post_time = str(meta.get("post_time", "")).strip()
    thread_context = str(meta.get("thread_context", "")).strip() or "unknown"
    image_alts = [str(x).strip() for x in meta.get("image_alts", []) if str(x).strip()]
    source_mode = str(meta.get("source_mode", "unknown")).strip() or "unknown"

    content_text = text
    linked = fetch_linked_page_text(cfg, text)
    if linked.get("text") and not looks_incomplete(linked.get("text", ""), cfg.content_min_len):
        content_text = linked["text"]

    browser = {}
    browser_media: list[dict[str, Any]] = []
    if looks_incomplete(content_text, cfg.content_min_len):
        browser = fetch_with_browser_fallback(cfg, url)
        if browser.get("text"):
            content_text = browser["text"]
            source_mode = browser.get("source", source_mode)
        try:
            browser_media = json.loads(browser.get("media_json", "[]"))
            if not isinstance(browser_media, list):
                browser_media = []
        except Exception:
            browser_media = []

    rule = choose_rule(cat_cfg, tags, content_text, url)
    category = rule.folder
    action = rule.action

    title = (
        browser.get("title")
        or linked.get("title")
        or SPACE_RE.sub(" ", content_text).strip()[:72]
        or f"post-{tweet_id}"
    )
    title = sanitize_filename(title)

    points = split_key_points(content_text, max_points=3)
    points_block = "\n".join(f"- {p}" for p in points if p.strip()) or "- （待补充）"

    page_html = str(browser.get("html", "")).strip() or str(linked.get("html", "")).strip()
    meta_raw_payload = meta.get("raw_payload") or {}
    if not page_html:
        page_html = str(((meta_raw_payload.get("oembed") or {}).get("html", ""))).strip()

    q = evaluate_capture_quality(
        cfg,
        text=content_text,
        title=title,
        author_name=author_name,
        post_time=post_time,
        source_mode=source_mode,
        page_html=page_html,
    )
    score = int(q["score"])
    quality_flags = list(q["reason_codes"])
    marker_hits = list(q["marker_hits"])

    media_candidates = collect_media_candidates(meta_raw_payload, browser_media)
    archive_raw_payload = {
        "primary_fetch": primary_fetch,
        "source_mode": source_mode,
        "meta_raw_payload": meta_raw_payload,
        "linked_page": linked,
        "browser_fallback": {
            "title": browser.get("title", ""),
            "text": browser.get("text", ""),
            "html_size": len(str(browser.get("html", ""))),
            "media_count": len(browser_media),
        },
    }
    archive_paths = write_original_archive(
        cfg,
        tweet_id=tweet_id,
        url=url,
        title=title,
        author_name=author_name,
        author_username=author_username,
        post_time=post_time,
        source_mode=source_mode,
        text=content_text,
        thread_context=thread_context,
        quality_score_value=score,
        quality_flags=quality_flags,
        marker_hits=marker_hits,
        raw_payload=archive_raw_payload,
        page_html=page_html,
        media=media_candidates,
    )

    if q["degraded"]:
        reason_text = ", ".join(quality_flags) if quality_flags else "quality_gate_rejected"
        raise CaptureQualityError(
            f"degraded capture rejected: {reason_text}",
            reason_codes=quality_flags,
            archive_json_path=archive_paths["json_path"],
            archive_md_path=archive_paths["md_path"],
            archive_html_path=archive_paths["html_path"],
            quality_score=score,
        )

    ensure_default_template(cfg)
    template_name = f"{rule.template}.md"
    template_path = cfg.templates_root / template_name
    if not template_path.exists():
        template_path = cfg.templates_root / "bookmark.md"

    full_text = (content_text or "（未抓取到）").strip()
    quote_text = full_text.replace("\n", "\n> ")
    author_line = author_name or "unknown"
    if author_username:
        author_line = f"{author_line} (@{author_username})"

    doc = render_template(
        template_path,
        {
            "title": title,
            "author": author_line,
            "post_time": post_time or "unknown",
            "url": url,
            "tags": ", ".join(tags) if tags else category,
            "category": category,
            "thread_context": thread_context,
            "image_alts": "；".join(image_alts) if image_alts else "（未抓取到）",
            "source_mode": source_mode,
            "quality_score": str(score),
            "original_archive_md": archive_paths["md_path"],
            "original_archive_json": archive_paths["json_path"],
            "original_archive_html": archive_paths["html_path"],
            "key_points": points_block,
            "quote_text": quote_text,
            "full_text": full_text,
        },
    )

    day = dt.date.today().isoformat()
    written_path = ""

    if action == "file":
        out_dir = cfg.curated_root / category / day
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{title}.md"
        if out_path.exists():
            out_path = out_dir / f"{title}-{tweet_id}.md"
        out_path.write_text(doc, encoding="utf-8")
        written_path = str(out_path)
    else:
        capture_dir = cfg.meta_root / "captured"
        capture_dir.mkdir(parents=True, exist_ok=True)
        cap_file = capture_dir / f"{day}.md"
        old = cap_file.read_text(encoding="utf-8") if cap_file.exists() else ""
        block = f"## {title}\n\n{doc}\n\n"
        cap_file.write_text(old + block, encoding="utf-8")
        written_path = str(cap_file)

    entry = {
        "tweet_id": tweet_id,
        "url": url,
        "title": title,
        "text": full_text,
        "category": category,
        "action": action,
        "path": written_path,
        "tags": tags,
        "author_name": author_name,
        "author_username": author_username,
        "post_time": post_time,
        "source_mode": source_mode,
        "quality_score": score,
        "raw_json_path": archive_paths["json_path"],
        "original_md_path": archive_paths["md_path"],
        "original_html_path": archive_paths["html_path"],
        "capture_status": "ok",
        "quality_flags": quality_flags,
        "ingested_at": utc_now_iso(),
    }
    upsert_entry(conn, entry)

    return {
        "task_id": task["task_id"],
        "tweet_id": tweet_id,
        "url": url,
        "category": category,
        "action": action,
        "path": written_path,
        "raw_path": archive_paths["json_path"],
        "original_md_path": archive_paths["md_path"],
        "original_html_path": archive_paths["html_path"],
        "quality_flags": quality_flags,
        "marker_hits": marker_hits,
        "source_mode": source_mode,
        "quality_score": score,
    }


def write_run_log(cfg: Config, record: dict[str, Any]) -> Path:
    run_path = cfg.state_runs / f"{record['run_id']}.json"
    run_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = cfg.meta_root / "run-log.jsonl"
    summary = {
        "run_id": record["run_id"],
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "ok": record["ok"],
        "seen": record["pending_seen"],
        "processed": len(record["processed"]),
        "errors": len(record["errors"]),
        "degraded": len([e for e in record["errors"] if str(e.get("error_type", "")) == "degraded_capture"]),
        "git": record.get("git", {}),
    }
    with summary_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return run_path


def maybe_git_push(cfg: Config, no_git: bool) -> dict[str, Any]:
    if no_git or not cfg.auto_git_push:
        return {"status": "skipped", "reason": "disabled"}

    targets = [str(cfg.data_root.relative_to(cfg.project_root))]
    if cfg.git_include_state:
        targets.append(str(cfg.state_root.relative_to(cfg.project_root)))

    rc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if rc.returncode != 0:
        return {"status": "skipped", "reason": "not_a_git_repo"}

    add = subprocess.run(["git", "add", *targets], capture_output=True, text=True)
    if add.returncode != 0:
        return {"status": "error", "reason": "git_add_failed", "detail": add.stderr.strip()}

    diff = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True)
    if diff.returncode != 0:
        return {"status": "error", "reason": "git_diff_failed"}
    if not diff.stdout.strip():
        return {"status": "skipped", "reason": "no_changes"}

    msg = f"feat(sync): ingest bookmarks @ {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    commit = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True)
    if commit.returncode != 0:
        return {"status": "error", "reason": "git_commit_failed", "detail": commit.stderr.strip()}

    branch = cfg.git_branch
    if not branch:
        b = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
        if b.returncode != 0 or not b.stdout.strip():
            return {"status": "error", "reason": "cannot_detect_branch"}
        branch = b.stdout.strip()

    push = subprocess.run(["git", "push", cfg.git_remote, branch], capture_output=True, text=True)
    if push.returncode != 0:
        return {"status": "error", "reason": "git_push_failed", "detail": push.stderr.strip()}

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return {"status": "ok", "branch": branch, "commit": head.stdout.strip()}


def sync_queue(
    cfg: Config,
    cat_cfg: CategoryConfig,
    conn: sqlite3.Connection,
    limit: int,
    with_retry: bool,
    no_git: bool,
) -> dict[str, Any]:
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lock = acquire_lock(cfg, "sync")

    started = utc_now_iso()
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    try:
        queue_items = collect_tasks(cfg, limit=limit, with_retry=with_retry)
        for from_state, path in queue_items:
            task = read_task(path)
            task["attempts"] = int(task.get("attempts", 0)) + 1
            task["updated_at"] = utc_now_iso()
            task["status"] = "processing"

            write_task(path, task)
            processing_path = task_path(cfg, "processing", task["task_id"])
            if path != processing_path:
                path.replace(processing_path)

            try:
                result = process_one_task(cfg, cat_cfg, task, conn)
                conn.commit()
                task["last_error"] = ""
                move_task(cfg, task, "processing", "done")
                processed.append(result)
            except Exception as exc:
                conn.rollback()
                task["last_error"] = str(exc)
                target = "retry" if task["attempts"] <= cfg.max_retry else "error"
                if isinstance(exc, CaptureQualityError):
                    target = "error"
                move_task(cfg, task, "processing", target)
                err_item: dict[str, Any] = {
                    "task_id": task["task_id"],
                    "url": task.get("url", ""),
                    "error": str(exc),
                    "to": target,
                }
                if isinstance(exc, CaptureQualityError):
                    quarantine_path = quarantine_existing_entry(
                        cfg,
                        conn,
                        tweet_id=str(task.get("task_id", "")).strip(),
                        reason=str(exc),
                    )
                    err_item["error_type"] = "degraded_capture"
                    err_item["reason_codes"] = exc.reason_codes
                    err_item["quality_score"] = exc.quality_score
                    err_item["archive_json_path"] = exc.archive_json_path
                    err_item["archive_md_path"] = exc.archive_md_path
                    err_item["archive_html_path"] = exc.archive_html_path
                    if quarantine_path:
                        err_item["quarantined_curated_path"] = quarantine_path
                errors.append(err_item)

        fts_enabled = rebuild_fts(conn)

        git_result = maybe_git_push(cfg, no_git=no_git)

        finished = utc_now_iso()
        result = {
            "ok": len(errors) == 0,
            "action": "sync",
            "run_id": run_id,
            "started_at": started,
            "finished_at": finished,
            "pending_seen": len(queue_items),
            "processed": processed,
            "errors": errors,
            "fts": "enabled" if fts_enabled else "unavailable",
            "git": git_result,
        }
        run_file = write_run_log(cfg, result)
        result["run_log"] = str(run_file)
        return result
    finally:
        release_lock(lock)


# ---------------------------------------------------------------------------
# Reporting commands
# ---------------------------------------------------------------------------


def cmd_path(cfg: Config, key: str | None) -> dict[str, Any]:
    mapping = {
        "project_root": str(cfg.project_root),
        "data_root": str(cfg.data_root),
        "raw": str(cfg.raw_root),
        "curated": str(cfg.curated_root),
        "index": str(cfg.index_root),
        "meta": str(cfg.meta_root),
        "archive": str(cfg.archive_root),
        "state_root": str(cfg.state_root),
        "pending": str(cfg.state_pending),
        "processing": str(cfg.state_processing),
        "done": str(cfg.state_done),
        "error": str(cfg.state_error),
        "retry": str(cfg.state_retry),
        "locks": str(cfg.state_locks),
        "runs": str(cfg.state_runs),
        "categories_config": str(cfg.categories_cfg_path),
        "templates": str(cfg.templates_root),
        "sqlite": str(db_path(cfg)),
    }
    if key:
        if key not in mapping:
            raise CliError(f"Unknown path key: {key}")
        return {"key": key, "path": mapping[key]}
    return mapping


def count_state_files(cfg: Config) -> dict[str, int]:
    return {
        state: len(list(task_path(cfg, state, "*").parent.glob("*.json")))
        for state in QUEUE_STATES
    }


def cmd_status(cfg: Config, conn: sqlite3.Connection) -> dict[str, Any]:
    queue_counts = count_state_files(cfg)
    db_counts = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    by_cat_rows = conn.execute(
        "SELECT category, COUNT(*) c FROM entries GROUP BY category ORDER BY c DESC"
    ).fetchall()
    by_cat = {str(k): int(v) for k, v in by_cat_rows}

    latest_run = None
    run_files = sorted(cfg.state_runs.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if run_files:
        latest_run = json.loads(run_files[0].read_text(encoding="utf-8"))

    return {
        "action": "status",
        "paths": cmd_path(cfg, None),
        "queue": queue_counts,
        "entries": int(db_counts),
        "categories": by_cat,
        "latest_run": {
            "run_id": latest_run.get("run_id"),
            "ok": latest_run.get("ok"),
            "started_at": latest_run.get("started_at"),
            "finished_at": latest_run.get("finished_at"),
            "processed": len(latest_run.get("processed", [])),
            "errors": len(latest_run.get("errors", [])),
        } if latest_run else None,
    }


def cmd_list(conn: sqlite3.Connection, category: str | None, limit: int) -> dict[str, Any]:
    q = (
        "SELECT tweet_id, title, url, category, post_time, path, quality_score, ingested_at "
        "FROM entries WHERE COALESCE(capture_status, 'ok') = 'ok'"
    )
    params: list[Any] = []
    if category:
        q += " AND category = ?"
        params.append(category)
    q += " ORDER BY ingested_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    items = [
        {
            "tweet_id": r[0],
            "title": r[1],
            "url": r[2],
            "category": r[3],
            "post_time": r[4],
            "path": r[5],
            "quality_score": r[6],
            "ingested_at": r[7],
        }
        for r in rows
    ]
    return {"action": "list", "count": len(items), "items": items}


def cmd_search(conn: sqlite3.Connection, query: str, limit: int) -> dict[str, Any]:
    if ensure_fts(conn):
        rows = conn.execute(
            """
            SELECT e.tweet_id, e.title, e.url, e.category, e.post_time, e.path,
                   e.quality_score, bm25(entries_fts) AS score
            FROM entries_fts
            JOIN entries e ON e.rowid = entries_fts.rowid
            WHERE entries_fts MATCH ? AND COALESCE(e.capture_status, 'ok') = 'ok'
            ORDER BY score
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        items = [
            {
                "tweet_id": r[0],
                "title": r[1],
                "url": r[2],
                "category": r[3],
                "post_time": r[4],
                "path": r[5],
                "quality_score": r[6],
                "score": r[7],
            }
            for r in rows
        ]
        return {"action": "search", "query": query, "engine": "fts5", "count": len(items), "items": items}

    rows = conn.execute(
        """
        SELECT tweet_id, title, url, category, post_time, path, quality_score
        FROM entries
        WHERE (title LIKE ? OR text LIKE ?) AND COALESCE(capture_status, 'ok') = 'ok'
        ORDER BY ingested_at DESC
        LIMIT ?
        """,
        (f"%{query}%", f"%{query}%", limit),
    ).fetchall()
    items = [
        {
            "tweet_id": r[0],
            "title": r[1],
            "url": r[2],
            "category": r[3],
            "post_time": r[4],
            "path": r[5],
            "quality_score": r[6],
        }
        for r in rows
    ]
    return {"action": "search", "query": query, "engine": "like", "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_layout(cfg: Config, apply: bool) -> dict[str, Any]:
    ensure_layout(cfg)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("legacy-layout-%Y%m%dT%H%M%SZ")
    legacy_archive = cfg.archive_root / stamp
    moves: list[dict[str, str]] = []

    # 1) move old category roots into curated/<category>/
    for category in ("ai", "eda", "verification", "career", "tools", "misc"):
        src_dir = cfg.data_root / category
        if not src_dir.exists():
            continue
        for md in src_dir.rglob("*.md"):
            rel = md.relative_to(src_dir)
            dst = cfg.curated_root / category / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(md, dst)
                moves.append({"from": str(md), "to": str(dst), "mode": "copy"})

        if apply:
            legacy_archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_dir), str(legacy_archive / category))
            moves.append({"from": str(src_dir), "to": str(legacy_archive / category), "mode": "move"})

    # 2) old _raw -> raw/legacy/<date>/
    old_raw = cfg.data_root / "_raw"
    if old_raw.exists():
        for p in old_raw.rglob("*.json"):
            day = p.parent.name
            dst = cfg.raw_root / "legacy" / day / p.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(p, dst)
                moves.append({"from": str(p), "to": str(dst), "mode": "copy"})
        if apply:
            legacy_archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_raw), str(legacy_archive / "_raw"))
            moves.append({"from": str(old_raw), "to": str(legacy_archive / "_raw"), "mode": "move"})

    # 3) old _state db -> index/bookmarks.sqlite (if index not exists)
    old_state_db = cfg.data_root / "_state" / "index.sqlite"
    new_state_db = db_path(cfg)
    if old_state_db.exists() and not new_state_db.exists():
        new_state_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_state_db, new_state_db)
        moves.append({"from": str(old_state_db), "to": str(new_state_db), "mode": "copy"})

    if (cfg.data_root / "_state").exists() and apply:
        legacy_archive.mkdir(parents=True, exist_ok=True)
        shutil.move(str(cfg.data_root / "_state"), str(legacy_archive / "_state"))
        moves.append({"from": str(cfg.data_root / "_state"), "to": str(legacy_archive / "_state"), "mode": "move"})

    # 4) metadata -> meta
    old_meta = cfg.data_root / "metadata"
    if old_meta.exists():
        for f in old_meta.rglob("*.md"):
            dst = cfg.meta_root / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
                moves.append({"from": str(f), "to": str(dst), "mode": "copy"})
        if apply:
            legacy_archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_meta), str(legacy_archive / "metadata"))
            moves.append({"from": str(old_meta), "to": str(legacy_archive / "metadata"), "mode": "move"})

    # 5) old inbox -> state/retry/legacy-<id>.json (best effort)
    old_inbox = cfg.data_root / "inbox" / "retry"
    if old_inbox.exists():
        for md in old_inbox.glob("*.md"):
            task_id = md.stem
            payload = {
                "task_id": f"legacy-{task_id}",
                "url": "",
                "raw_text": md.read_text(encoding="utf-8", errors="ignore")[:10000],
                "tags": ["legacy"],
                "note": f"migrated from {md}",
                "source": "legacy",
                "status": "retry",
                "attempts": cfg.max_retry,
                "last_error": "legacy retry item imported as text snapshot",
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            }
            dst = cfg.state_retry / f"legacy-{task_id}.json"
            if not dst.exists():
                write_task(dst, payload)
                moves.append({"from": str(md), "to": str(dst), "mode": "materialize"})

    if (cfg.data_root / "inbox").exists() and apply:
        legacy_archive.mkdir(parents=True, exist_ok=True)
        shutil.move(str(cfg.data_root / "inbox"), str(legacy_archive / "inbox"))
        moves.append({"from": str(cfg.data_root / "inbox"), "to": str(legacy_archive / "inbox"), "mode": "move"})

    return {
        "action": "migrate",
        "apply": apply,
        "archive": str(legacy_archive),
        "moves": moves,
        "moved_count": len(moves),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="x_to_cdns unified CLI (status/path/sync/index/search/list)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c_path = sub.add_parser("path", help="Print canonical paths")
    c_path.add_argument("--key", default="", help="Optional key (e.g. raw, curated, pending, sqlite)")

    sub.add_parser("status", help="Show queue/index/run status")

    c_enqueue = sub.add_parser("enqueue", help="Capture X links into .state/pending")
    c_enqueue.add_argument("--text", required=True, help="Raw input text containing one or more X links")
    c_enqueue.add_argument("--tags", default="", help="Comma-separated tags")
    c_enqueue.add_argument("--note", default="", help="Optional note")
    c_enqueue.add_argument("--source", default="manual", help="Source label")
    c_enqueue.add_argument("--force", action="store_true", help="Re-enqueue even if already done")

    c_sync = sub.add_parser("sync", help="Enqueue(optional) + process pending/retry queue")
    c_sync.add_argument("--text", default="", help="Optional raw input text containing X links")
    c_sync.add_argument("--tags", default="", help="Comma-separated tags for --text")
    c_sync.add_argument("--note", default="", help="Optional note for --text")
    c_sync.add_argument("--source", default="manual", help="Source label for --text")
    c_sync.add_argument("--force", action="store_true", help="Force enqueue for --text")
    c_sync.add_argument("--limit", type=int, default=30, help="Max queue items to process")
    c_sync.add_argument("--no-retry", action="store_true", help="Do not process retry queue in this run")
    c_sync.add_argument("--no-git", action="store_true", help="Disable auto git commit/push in this run")

    c_index = sub.add_parser("index", help="Rebuild full-text index")
    c_index.add_argument("--check", action="store_true", help="Only check if FTS5 is available")

    c_search = sub.add_parser("search", help="Search indexed entries")
    c_search.add_argument("query", help="Query text (FTS5 syntax when available)")
    c_search.add_argument("--limit", type=int, default=20, help="Max results")

    c_list = sub.add_parser("list", help="List recent entries")
    c_list.add_argument("--category", default="", help="Filter by category")
    c_list.add_argument("--limit", type=int, default=30, help="Max results")

    c_migrate = sub.add_parser("migrate", help="Migrate legacy layout to unified contract")
    c_migrate.add_argument("--apply", action="store_true", help="Apply migration moves (default: dry-run style copy report)")

    # Compatibility aliases (reduce breakage during transition)
    c_capture = sub.add_parser("capture", help="Alias of enqueue")
    c_capture.add_argument("--text", required=True)
    c_capture.add_argument("--tags", default="")
    c_capture.add_argument("--note", default="")
    c_capture.add_argument("--source", default="manual")
    c_capture.add_argument("--force", action="store_true")

    c_capture_sync = sub.add_parser("capture-sync", help="Alias of sync --text ...")
    c_capture_sync.add_argument("--text", required=True)
    c_capture_sync.add_argument("--tags", default="")
    c_capture_sync.add_argument("--note", default="")
    c_capture_sync.add_argument("--source", default="manual")
    c_capture_sync.add_argument("--force", action="store_true")
    c_capture_sync.add_argument("--limit", type=int, default=30)
    c_capture_sync.add_argument("--no-retry", action="store_true")
    c_capture_sync.add_argument("--no-git", action="store_true")

    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config()
    ensure_layout(cfg)
    cat_cfg = load_categories(cfg)

    conn = open_db(cfg)
    try:
        if args.cmd == "path":
            payload = cmd_path(cfg, args.key.strip() or None)
            print(json.dumps({"action": "path", "data": payload}, ensure_ascii=False))
            return 0

        if args.cmd == "status":
            print(json.dumps(cmd_status(cfg, conn), ensure_ascii=False))
            return 0

        if args.cmd in ("enqueue", "capture"):
            result = enqueue_links(
                cfg=cfg,
                text=args.text,
                raw_tags=args.tags,
                note=args.note,
                source=args.source,
                force=bool(args.force),
            )
            print(json.dumps({"ok": True, "action": "enqueue", **result}, ensure_ascii=False))
            return 0

        if args.cmd in ("sync", "capture-sync"):
            queued = {"inserted": 0, "updated": 0, "skipped_done": 0, "captured_urls": []}
            input_text = args.text.strip() if hasattr(args, "text") else ""
            if input_text:
                queued = enqueue_links(
                    cfg=cfg,
                    text=input_text,
                    raw_tags=getattr(args, "tags", ""),
                    note=getattr(args, "note", ""),
                    source=getattr(args, "source", "manual"),
                    force=bool(getattr(args, "force", False)),
                )

            result = sync_queue(
                cfg=cfg,
                cat_cfg=cat_cfg,
                conn=conn,
                limit=int(getattr(args, "limit", 30)),
                with_retry=not bool(getattr(args, "no_retry", False)),
                no_git=bool(getattr(args, "no_git", False)),
            )
            result["queued"] = queued
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 3

        if args.cmd == "index":
            ok = ensure_fts(conn)
            if args.check:
                print(json.dumps({"action": "index", "fts5": ok, "sqlite": str(db_path(cfg))}, ensure_ascii=False))
                return 0 if ok else 2
            rebuilt = rebuild_fts(conn)
            total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            print(json.dumps({"action": "index", "fts5": rebuilt, "entries": int(total)}, ensure_ascii=False))
            return 0 if rebuilt else 2

        if args.cmd == "search":
            payload = cmd_search(conn, query=args.query, limit=args.limit)
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        if args.cmd == "list":
            payload = cmd_list(conn, category=args.category.strip() or None, limit=args.limit)
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        if args.cmd == "migrate":
            payload = migrate_layout(cfg, apply=bool(args.apply))
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        raise CliError(f"Unsupported command: {args.cmd}")
    except CliError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
