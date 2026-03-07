"""
Static page formatter — renders index.html for GitHub Pages deployment.
Uses templates/page.html (Jinja2).
"""

import os
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

from models.event import Event, EventCategory, EventPriority
from formatters.email_formatter import (
    CATEGORY_LABEL,
    CATEGORY_ORDER,
    _event_display,
    _group_by_category,
    _group_by_date,
)

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")


def format_static_page(
    today_events: list[Event],
    yesterday_results: list[Event],
    upcoming: list[Event],
    config: dict,
) -> str:
    """Render and return the static HTML page string."""
    tz = ZoneInfo(config.get("timezone", "America/Detroit"))
    today = date.today()
    generated_at = datetime.now(tz).strftime("%B %-d, %Y at %-I:%M %p ET")

    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=True,
    )
    tmpl = env.get_template("page.html")

    today_by_cat = _group_by_category(today_events)
    results_display = [_event_display(e, tz) for e in yesterday_results if e.result]
    upcoming_by_date = [
        (d, _group_by_category(evts))
        for d, evts in _group_by_date(upcoming, tz)
    ]

    html = tmpl.render(
        today=today,
        today_by_cat=today_by_cat,
        results_display=results_display,
        upcoming_by_date=upcoming_by_date,
        category_order=CATEGORY_ORDER,
        category_label=CATEGORY_LABEL,
        event_display=_event_display,
        tz=tz,
        config=config,
        generated_at=generated_at,
        EventPriority=EventPriority,
        EventCategory=EventCategory,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [static] Written to {out_path}")

    return html
