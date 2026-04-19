"""
Static page formatter — renders index.html for GitHub Pages deployment.
Uses templates/page.html (Jinja2).
"""

import os
import ssl
import urllib.request
import xml.etree.ElementTree as ET
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

_NPR_RSS = "https://feeds.npr.org/1001/rss.xml"


def _fetch_npr_headlines(n: int = 5) -> list[dict]:
    """Fetch top N headlines from NPR News RSS. Returns [] on any error."""
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(_NPR_RSS, headers={"User-Agent": "daily-digest/1.0"})
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            root = ET.fromstring(resp.read())
        items = root.findall("./channel/item")[:n]
        return [
            {"title": (item.findtext("title") or "").strip(),
             "url": (item.findtext("link") or "").strip()}
            for item in items
            if item.findtext("title")
        ]
    except Exception as exc:
        print(f"  [npr] Failed to fetch headlines: {exc}")
        return []


def format_static_page(
    today_events: list[Event],
    yesterday_results: list[Event],
    upcoming: list[Event],
    config: dict,
    ai_summary: str = "",
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

    npr_headlines = _fetch_npr_headlines(5)
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
        quick_links=config.get("quick_links", []),
        npr_headlines=npr_headlines,
        ai_summary=ai_summary,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [static] Written to {out_path}")

    return html
