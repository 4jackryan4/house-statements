"""Statement storage: one JSONL file per month under data/statements/YYYY/."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import STATE_FILE, STATEMENTS_DIR

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid"}


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication (https, lowercase host, no www/fragment/tracking)."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS))
    return urlunsplit(("https", host, path, query, ""))


def statement_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:16]


def _month_path(record: dict) -> Path:
    date = record.get("date") or ""
    if len(date) >= 7:
        return STATEMENTS_DIR / date[:4] / f"{date[:7]}.jsonl"
    return STATEMENTS_DIR / "undated.jsonl"


def load_all(directory: Path = STATEMENTS_DIR) -> dict[str, dict]:
    records = {}
    for path in sorted(directory.rglob("*.jsonl")):
        with path.open() as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    records[r["id"]] = r
    return records


def save_all(records: dict[str, dict], directory: Path = STATEMENTS_DIR) -> int:
    """Write records back grouped by month. Only rewrites files whose content changed."""
    groups: dict[Path, list[dict]] = defaultdict(list)
    for r in records.values():
        rel = _month_path(r).relative_to(STATEMENTS_DIR)
        groups[directory / rel].append(r)

    written = 0
    for path, rows in groups.items():
        rows.sort(key=lambda r: (r.get("date") or "", r["id"]), reverse=True)
        body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
        if path.exists() and path.read_text() == body:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        written += 1

    # Drop files that no longer hold any records (e.g. an undated record got a date).
    for path in directory.rglob("*.jsonl"):
        if path not in groups:
            path.unlink()
    return written


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
