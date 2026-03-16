"""
Email formatter — renders the HTML email digest from event lists.
Uses templates/email.html (Jinja2).
"""

import os
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

from models.event import Event, EventCategory, EventPriority

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

CATEGORY_LABEL = {
    EventCategory.LOCAL:        "LOCAL",
    EventCategory.ESTATE_SALES: "ESTATE SALES",
    EventCategory.SPORTS:       "SPORTS",
    EventCategory.ESPORTS:      "ESPORTS",
}

CATEGORY_ORDER = [EventCategory.LOCAL, EventCategory.ESTATE_SALES, EventCategory.SPORTS, EventCategory.ESPORTS]


def _fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return local.strftime("%-I:%M %p ET").replace(":00 ", " ")  # "7 PM ET" / "1:05 PM ET"


def _group_by_category(events: list[Event]) -> dict:
    """Return {category: [event, ...]} dict ordered by CATEGORY_ORDER."""
    grouped = defaultdict(list)
    for e in events:
        grouped[e.category].append(e)
    return {cat: grouped[cat] for cat in CATEGORY_ORDER if grouped[cat]}


def _group_by_date(events: list[Event], tz: ZoneInfo) -> list[tuple[date, list[Event]]]:
    """Return [(date, [events])] sorted by date."""
    grouped = defaultdict(list)
    for e in events:
        d = e.start.astimezone(tz).date()
        grouped[d].append(e)
    return sorted(grouped.items())


def _event_display(event: Event, tz: ZoneInfo) -> dict:
    """Pre-compute all display fields for a single event."""
    local_start = event.start.astimezone(tz)
    time_str = _fmt_time(event.start, tz) if event.start.hour or event.start.minute else "All day"
    return {
        "title": event.title,
        "subtitle": event.subtitle,
        "time": time_str,
        "location": event.location,
        "url": event.url,
        "result": event.result,
        "priority": event.priority,
        "is_high": event.priority == EventPriority.HIGH,
        "category": event.category,
        "category_label": CATEGORY_LABEL[event.category],
    }


def format_email(
    today_events: list[Event],
    yesterday_results: list[Event],
    upcoming: list[Event],
    config: dict,
    ai_summary: str = "",
) -> str:
    """Render and return the HTML email body string."""
    tz = ZoneInfo(config.get("timezone", "America/Detroit"))
    today = date.today()

    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=True,
    )
    tmpl = env.get_template("email.html")

    today_by_cat = _group_by_category(today_events)
    results_display = [_event_display(e, tz) for e in yesterday_results if e.result]
    upcoming_by_date = [
        (d, _group_by_category(evts))
        for d, evts in _group_by_date(upcoming, tz)
    ]

    return tmpl.render(
        today=today,
        today_by_cat=today_by_cat,
        results_display=results_display,
        upcoming_by_date=upcoming_by_date,
        category_order=CATEGORY_ORDER,
        category_label=CATEGORY_LABEL,
        event_display=_event_display,
        tz=tz,
        config=config,
        EventPriority=EventPriority,
        EventCategory=EventCategory,
        ai_summary=ai_summary,
    )
