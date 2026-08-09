"""
Collects non-fatal source failures so they surface in the digest itself.

Every collector already swallows its own exceptions so one bad source cannot
break the run. That is the right behaviour, but it also means a total outage
looks exactly like a quiet day: the workflow stays green and the page just
gets shorter. Recording failures here lets the formatters render them.

Messages are scrubbed before they leave this module — the static page is
published to a public gh-pages branch, and raw exception text can carry an
API key that was sent as a query parameter.
"""

import os
import re

# Env vars whose values must never appear in a rendered report.
_SECRET_ENV_VARS = (
    "FOOTBALL_DATA_API_KEY",
    "API_FOOTBALL_KEY",
    "PANDASCORE_API_KEY",
    "STARTGG_API_KEY",
    "GMAIL_APP_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "GOOGLE_API_KEY",
)

# Query params that carry credentials, e.g. ...&api_key=abc123&...
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(api[-_]?key|apikey|key|token|auth|password|secret)=[^&\s\"']+"
)

_errors: list[dict] = []
_counts: dict[str, int] = {}


def redact(text: str) -> str:
    """Strip credentials out of a message before it is displayed."""
    out = str(text)
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var, "")
        if value and len(value) >= 8:
            out = out.replace(value, "***")
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=***", out)


_MAX_MESSAGE_LEN = 240


def _condense(text: str) -> str:
    """Flatten to one line and cap the length.

    Some libraries raise multi-line banners (Playwright draws a box-drawing
    ASCII panel), which would blow out the report layout.
    """
    flat = " ".join(str(text).split())
    if len(flat) > _MAX_MESSAGE_LEN:
        flat = flat[:_MAX_MESSAGE_LEN].rstrip() + "…"
    return flat


def record(source: str, message: str) -> None:
    """Log a source failure and keep it for the report.

    Printing is preserved so the CI log reads exactly as it did before.
    """
    clean = _condense(redact(message))
    print(f"  [{source}] Warning: {clean}")
    _errors.append({"source": source, "message": clean})


def note_count(source: str, count: int) -> None:
    """Record how many events a source returned, for the coverage summary."""
    _counts[source] = _counts.get(source, 0) + count


def all_errors() -> list[dict]:
    return list(_errors)


def counts() -> dict[str, int]:
    return dict(_counts)


def summary() -> dict:
    """Report payload: failures, plus which sources came back empty.

    Errors are grouped by source so an outage that trips 30 endpoints renders
    as one line with a count rather than 30 near-identical rows.
    """
    grouped: dict[str, dict] = {}
    for err in _errors:
        entry = grouped.setdefault(
            err["source"], {"source": err["source"], "count": 0, "first": err["message"]}
        )
        entry["count"] += 1

    # A source that already reported a failure is obviously empty; listing it
    # again adds nothing. What matters here is the source that fetched fine and
    # still parsed nothing — the silent-breakage case.
    return {
        "failures": sorted(grouped.values(), key=lambda e: -e["count"]),
        "empty_sources": sorted(
            s for s, n in _counts.items() if n == 0 and s not in grouped
        ),
        "total": len(_errors),
    }


def clear() -> None:
    _errors.clear()
    _counts.clear()
