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
_strategies: dict[str, dict] = {}

# Why a source returned nothing, when the source itself already knows.
# An empty result is ambiguous on its own: "quiet day", "wired to nothing" and
# "the scraper broke" all look identical from the outside. Collectors that can
# tell the difference say so here, so the report can stop crying wolf over the
# first two and start pointing at the third.
_SUSPECT = "suspect"
_IDLE = "idle"
_NOT_CONFIGURED = "not_configured"

_status: dict[str, dict] = {}


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


def _set_status(source: str, kind: str, reason: str) -> None:
    # A source that fetched several endpoints can report more than once. Keep
    # the most alarming verdict: suspect outranks idle outranks not-configured.
    rank = {_NOT_CONFIGURED: 0, _IDLE: 1, _SUSPECT: 2}
    existing = _status.get(source)
    if existing and rank[existing["kind"]] >= rank[kind]:
        return
    _status[source] = {"source": source, "kind": kind, "reason": _condense(redact(reason))}


def note_not_configured(source: str, reason: str) -> None:
    """Nothing in config routes to this source, so it cannot produce events.

    A dormant source is not a broken one. Reporting it as a daily failure
    trains the reader to ignore the whole health block.
    """
    _set_status(source, _NOT_CONFIGURED, reason)


def note_idle(source: str, reason: str) -> None:
    """Fetches succeeded; there is genuinely nothing scheduled.

    The off-season case — an NBA playoff feed in August is working perfectly.
    """
    _set_status(source, _IDLE, reason)


def note_strategy(source: str, message: str, degraded: bool = False) -> None:
    """Record which extraction path actually produced a source's events.

    A scraper that only works after falling through to its last resort is
    still working, so it belongs nowhere near the failure list — but it is one
    site change away from breaking, and that is worth seeing before it does.
    Only the degraded paths are reported; the healthy ones just log.
    """
    clean = _condense(redact(message))
    print(f"  [{source}] via {clean}")
    _strategies[source] = {"source": source, "reason": clean, "degraded": degraded}


def note_suspect(source: str, message: str) -> None:
    """Fetched real content but parsed nothing out of it.

    This is the case worth waking up for: no exception was raised, so the run
    stays green, but the source has almost certainly changed its markup.
    """
    clean = _condense(redact(message))
    print(f"  [{source}] Suspect: {clean}")
    _set_status(source, _SUSPECT, clean)


def all_errors() -> list[dict]:
    return list(_errors)


def counts() -> dict[str, int]:
    return dict(_counts)


def summary() -> dict:
    """Report payload, split by how much each empty source should worry you.

    Errors are grouped by source so an outage that trips 30 endpoints renders
    as one line with a count rather than 30 near-identical rows.

    An empty source is only interesting when nobody can explain it. Sources
    that explained themselves — nothing configured, nothing in season, markup
    stopped matching — are reported under that explanation instead of being
    swept into one undifferentiated "no events" list.
    """
    grouped: dict[str, dict] = {}
    for err in _errors:
        entry = grouped.setdefault(
            err["source"], {"source": err["source"], "count": 0, "first": err["message"]}
        )
        entry["count"] += 1

    def _of_kind(kind: str) -> list[dict]:
        return sorted(
            (s for s in _status.values() if s["kind"] == kind and s["source"] not in grouped),
            key=lambda s: s["source"],
        )

    # A source that already reported a failure is obviously empty; listing it
    # again adds nothing. What matters here is the source that fetched fine and
    # still parsed nothing — the silent-breakage case.
    return {
        "failures": sorted(grouped.values(), key=lambda e: -e["count"]),
        "suspect": _of_kind(_SUSPECT),
        # Working, but on a fallback path — the warning that arrives before the
        # source breaks rather than after.
        "degraded": sorted(
            (s for s in _strategies.values() if s["degraded"] and s["source"] not in grouped),
            key=lambda s: s["source"],
        ),
        "idle": _of_kind(_IDLE),
        "not_configured": _of_kind(_NOT_CONFIGURED),
        "empty_sources": sorted(
            s for s, n in _counts.items()
            if n == 0 and s not in grouped and s not in _status
        ),
        "total": len(_errors),
    }


def clear() -> None:
    _errors.clear()
    _counts.clear()
    _status.clear()
    _strategies.clear()
