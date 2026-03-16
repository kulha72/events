"""
AI summary — uses Google Gemini to generate a short daily briefing from collected events.
Requires GOOGLE_API_KEY env var. Returns "" if disabled or on any error.
"""

from __future__ import annotations

import os
from datetime import date
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

from models.event import Event, EventCategory, EventPriority

_SYSTEM = """\
You are a deadpan, dry-witted personal assistant delivering a daily briefing.
Write 3–5 sentences. Be accurate and informative, but let a little sardonic personality seep through —
a raised eyebrow at a rough sports stretch, mild skepticism about an overhyped event, a dry observation
about the day's lineup. Never try-hard or mean; just the kind of wit that comes from someone who has
seen it all and still shows up. Lead with what actually matters today.
Skip categories with nothing notable. No bullet points, no headers, no sign-off. Plain prose only.\
"""


def _build_context(
    today: date,
    today_events: list[Event],
    yesterday_results: list[Event],
    upcoming: list[Event],
    tz: ZoneInfo,
) -> str:
    lines = [f"Today is {today.strftime('%A, %B %-d, %Y')}."]

    if today_events:
        lines.append("\nTODAY'S EVENTS:")
        for e in today_events:
            star = " [HIGH PRIORITY]" if e.priority == EventPriority.HIGH else ""
            time_str = e.start.astimezone(tz).strftime("%-I:%M %p") if e.start.hour or e.start.minute else "All day"
            loc = f" @ {e.location}" if e.location else ""
            sub = f" — {e.subtitle}" if e.subtitle else ""
            lines.append(f"  [{e.category.value.upper()}]{star} {e.title}{sub} | {time_str}{loc}")
    else:
        lines.append("\nNo events today.")

    results = [e for e in yesterday_results if e.result]
    if results:
        lines.append("\nYESTERDAY'S RESULTS:")
        for e in results:
            lines.append(f"  {e.title}: {e.result}")

    if upcoming:
        lines.append("\nCOMING UP (next 7 days, highlights):")
        shown = 0
        for e in upcoming:
            if shown >= 10:
                break
            if e.priority == EventPriority.HIGH or shown < 6:
                time_str = e.start.astimezone(tz).strftime("%a %-d, %-I %p")
                sub = f" — {e.subtitle}" if e.subtitle else ""
                lines.append(f"  [{e.category.value.upper()}] {e.title}{sub} | {time_str}")
                shown += 1

    return "\n".join(lines)


def generate_summary(
    today_events: list[Event],
    yesterday_results: list[Event],
    upcoming: list[Event],
    config: dict,
) -> str:
    """Return a 3–5 sentence AI-generated daily briefing, or '' if disabled/failed."""
    ai_cfg = config.get("ai_summary", {})
    if not ai_cfg.get("enabled", False):
        return ""

    tz = ZoneInfo(config.get("timezone", "America/Detroit"))
    model = ai_cfg.get("model", "gemini-2.5-flash-lite")
    today = date.today()
    context = _build_context(today, today_events, yesterday_results, upcoming, tz)

    try:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        response = client.models.generate_content(
            model=model,
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                max_output_tokens=400,
            ),
        )
        text = response.text.strip()
        print(f"  [ai_summary] Generated ({response.usage_metadata.candidates_token_count} tokens)")
        return text
    except Exception as exc:
        print(f"  [ai_summary] Failed: {exc}")
        return ""
