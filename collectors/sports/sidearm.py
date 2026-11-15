"""
Sidearm Sports collector — college teams whose athletics site runs on Sidearm
(Adrian College's does) and that ESPN does not carry.

Sidearm sites expose the schedule their own calendar page draws from:

    <site>/services/responsive-calendar.ashx?type=month&sport=<id>&date=<m/d/Y>

It answers with one entry per day of that month's calendar grid, each holding
the games on that day with opponent, home/away, venue and — once played — the
score. No auth. The sport id is the number the site uses for one team; a school
with NCAA and club sides in the same sport has a separate id for each.

Config (under sports.teams):

    - name: "Adrian Hockey"
      league: "NCAA D-III"            # shown in the subtitle
      source: "sidearm"
      url: "https://adrianbulldogs.com/sports/mens-ice-hockey/schedule"   # link for games without a recap
      sidearm_site: "https://adrianbulldogs.com"
      sidearm_sport_id: 13
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from collectors.sports.espn import _apply_priority
from models.event import Event, EventCategory

import errors

# Sidearm prints kickoff in the school's own wall-clock time with no offset.
# Every school this digest follows is in the Eastern zone.
LOCAL_TZ = ZoneInfo("America/Detroit")

_CLOCK_RE = re.compile(r"\d{1,2}(:\d{2})?\s*[ap]\.?m", re.I)

# Sidearm answers the default python-requests User-Agent with an empty 404
# (checked 2026-11-15 against adrianbulldogs.com); a named client gets the data.
_session = requests.Session()
_session.headers.update({"User-Agent": "daily-digest/1.0"})


def _months_between(first: date, last: date) -> list[date]:
    """The first day of every month the window touches."""
    months = []
    cursor = first.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _result(game: dict) -> str | None:
    res = game.get("result") or {}
    status = (res.get("status") or "").strip()
    ours, theirs = res.get("team_score"), res.get("opponent_score")
    if not status or ours in (None, "") or theirs in (None, ""):
        return None
    return f"{status} {ours}–{theirs}"


def parse_game(game: dict, team_cfg: dict, emoji: str = "🏒") -> Event | None:
    """Turn one calendar entry into an Event, or None if it has no usable date."""
    try:
        local = datetime.fromisoformat(game.get("date") or "").replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None

    team_name = team_cfg["name"]
    site = team_cfg["sidearm_site"].rstrip("/")
    opponent = ((game.get("opponent") or {}).get("title") or "").strip() or "TBD"

    # "H" home, "A" away, "N" neutral. Neutral-site games read as "vs".
    away = (game.get("location_indicator") or "").upper() == "A"
    title = f"{emoji} {team_name} {'@' if away else 'vs'} {opponent}"

    # A game with no announced time comes back as midnight with "TBA"/"TBD".
    has_clock = bool(_CLOCK_RE.search(game.get("time") or ""))

    facility = ((game.get("facility") or {}).get("title") or "").strip()
    place = (game.get("location") or "").strip()
    venue = ", ".join(p for p in (facility, place) if p) or None

    parts = [team_cfg.get("league", "")]
    if game.get("conference"):
        parts.append("Conference")
    if game.get("noplay_text"):
        parts.append(game["noplay_text"])
    # The venue goes in `location`, which the page prints beside the subtitle.
    subtitle = " · ".join(p for p in parts if p) or None

    res = game.get("result") or {}
    link = (res.get("recap") or {}).get("url") or (res.get("boxscore") or {}).get("url")
    url = f"{site}{link}" if link and link.startswith("/") else (link or team_cfg.get("url"))
    if not url:
        url = f"{site}/calendar"

    slug = team_name.lower().replace(" ", "_")
    return Event(
        id=f"sidearm:{slug}:{game.get('id')}",
        title=title,
        category=EventCategory.SPORTS,
        start=local.astimezone(timezone.utc),
        location=venue,
        source="sidearm",
        url=url,
        subtitle=subtitle,
        result=_result(game),
        tags=[slug, "hockey", "sports"],
        all_day=not has_clock,
    )


class SidearmCollector(BaseCollector):
    """Schedule + yesterday's result for every team routed to `source: sidearm`."""

    def __init__(self, config: dict):
        self.config = config
        self.teams = [
            t for t in config.get("sports", {}).get("teams", [])
            if t.get("source") == "sidearm"
        ]

    @property
    def source_name(self) -> str:
        return "sidearm"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        if not self.teams:
            errors.note_not_configured("sidearm", "no team in config sets source: sidearm")
            return []

        yesterday = today - timedelta(days=1)
        cutoff = today + timedelta(days=lookahead_days)
        events: list[Event] = []
        fetched = 0

        for team_cfg in self.teams:
            name = team_cfg["name"]
            site = (team_cfg.get("sidearm_site") or "").rstrip("/")
            sport_id = team_cfg.get("sidearm_sport_id")
            if not site or not sport_id:
                errors.note_suspect(
                    "sidearm", f"{name} needs sidearm_site and sidearm_sport_id in config"
                )
                continue

            seen: set = set()
            for month in _months_between(yesterday, cutoff):
                try:
                    resp = _session.get(
                        f"{site}/services/responsive-calendar.ashx",
                        params={
                            "type": "month",
                            "sport": sport_id,
                            "location": "all",
                            "date": month.strftime("%m/%d/%Y"),
                        },
                        timeout=20,
                    )
                    resp.raise_for_status()
                    days = resp.json()
                except Exception as e:
                    errors.record("sidearm", f"{name} schedule fetch failed: {e}")
                    continue
                fetched += 1

                for day in days or []:
                    for game in day.get("events") or []:
                        if game.get("id") in seen:
                            continue
                        seen.add(game.get("id"))
                        event = parse_game(game, team_cfg)
                        if not event:
                            continue
                        event_date = event.start.astimezone(LOCAL_TZ).date()
                        if event_date < yesterday or event_date > cutoff:
                            continue
                        _apply_priority(event, self.config)
                        events.append(event)

        if fetched and not events:
            errors.note_idle("sidearm", "schedules fetched; no games in the window")
        return events
