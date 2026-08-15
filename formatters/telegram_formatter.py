"""
Telegram formatter — produces Telegram Bot API HTML markup.

Telegram supports only: <b>, <i>, <u>, <s>, <a>, <code>, <pre>.
Messages are capped at 4096 chars; this formatter splits into chunks if needed.
"""

from datetime import date
from zoneinfo import ZoneInfo

import errors
from models.event import Event


def _esc(text: str) -> str:
    """Escape for Telegram HTML parse mode.

    Error messages carry raw URLs, whose & would otherwise be read as the
    start of an entity and make the whole message fail to send.
    """
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

_MAX = 4096


def _fmt_event(event: Event, tz: ZoneInfo) -> str:
    time_str = event.start.astimezone(tz).strftime("%-I:%M %p")
    line = f"• <b>{event.title}</b> — {time_str}"
    if event.location:
        line += f" @ {event.location}"
    if event.result:
        line += f"\n  <i>{event.result}</i>"
    return line


def format_telegram(
    today_events: list[Event],
    yesterday_results: list[Event],
    upcoming: list[Event],
    config: dict,
    health: dict | None = None,
) -> list[str]:
    """Return a list of message strings (split if over 4096 chars)."""
    tz = ZoneInfo(config.get("timezone", "America/Detroit"))
    today = date.today()
    header = f"<b>Daily Digest — {today.strftime('%a, %b %-d')}</b>\n"

    repo = config.get("github_pages", {}).get("repo", "")
    if repo:
        site_url = f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}"
        header += f'<a href="{site_url}">View full digest</a>'

    sections: list[str] = [header]

    health = health if health is not None else errors.summary()
    # Explained empties (dormant, out of season) are deliberately left out of
    # the push message — it should only fire on something worth looking at.
    if health.get("failures") or health.get("suspect") or health.get("empty_sources"):
        sections.append("\n⚠️ <b>Feed problems</b>")
        for f in health.get("failures", []):
            times = f" ×{f['count']}" if f["count"] > 1 else ""
            sections.append(f"• <b>{_esc(f['source'])}</b>{times} — {_esc(f['first'])}")
        for s in health.get("suspect", []):
            sections.append(
                f"• <b>{_esc(s['source'])}</b> — fetched but parsed nothing: {_esc(s['reason'])}"
            )
        if health.get("empty_sources"):
            sections.append(f"• <i>No events: {_esc(', '.join(health['empty_sources']))}</i>")

    if today_events:
        sections.append("<b>Today</b>")
        sections.extend(_fmt_event(e, tz) for e in today_events)
    else:
        sections.append("<i>No events today.</i>")

    if yesterday_results:
        sections.append("\n<b>Recent Results</b>")
        sections.extend(_fmt_event(e, tz) for e in yesterday_results)

    if upcoming:
        sections.append("\n<b>Coming Up</b>")
        # Limit to next 10 so the message stays readable
        for event in upcoming[:10]:
            day_str = event.start.astimezone(tz).strftime("%a %-d")
            line = f"• <b>{event.title}</b> — {day_str}"
            if event.location:
                line += f" @ {event.location}"
            sections.append(line)
        if len(upcoming) > 10:
            sections.append(f"<i>…and {len(upcoming) - 10} more</i>")

    full = "\n".join(sections)

    # Split into ≤4096-char chunks on newline boundaries
    chunks: list[str] = []
    while len(full) > _MAX:
        split_at = full.rfind("\n", 0, _MAX)
        if split_at == -1:
            split_at = _MAX
        chunks.append(full[:split_at])
        full = full[split_at:].lstrip("\n")
    chunks.append(full)
    return chunks
