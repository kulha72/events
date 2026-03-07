"""
API-Football collector (via RapidAPI) — covers Millonarios (Liga BetPlay)
and any other api-football.com configured teams.

Free tier: 100 requests/day via RapidAPI.
Register at api-football.com / rapidapi.com for an API key.
"""

import os
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")
API_BASE = "https://api-football-v1.p.rapidapi.com/v3"
API_HOST = "api-football-v1.p.rapidapi.com"

_session = requests.Session()
_session.headers.update({"User-Agent": "daily-digest/1.0"})


def _apply_priority(event: Event, config: dict) -> None:
    rules = config.get("priority_rules", {})
    title_lower = event.title.lower()
    subtitle_lower = (event.subtitle or "").lower()
    combined = title_lower + " " + subtitle_lower

    for kw in rules.get("high_keywords", []):
        if kw.lower() in combined:
            event.priority = EventPriority.HIGH
            return

    for pair in rules.get("rivalry_matchups", []):
        if len(pair) == 2 and pair[0].lower() in title_lower and pair[1].lower() in title_lower:
            event.priority = EventPriority.HIGH
            return


class APIFootballCollector(BaseCollector):
    """Collects fixtures from api-football.com for configured teams."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.environ.get("API_FOOTBALL_KEY", "")
        self.teams = [
            t for t in config.get("sports", {}).get("teams", [])
            if t.get("source") == "api_football"
        ]

    @property
    def source_name(self) -> str:
        return "api_football"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        if not self.api_key:
            print("  [api_football] No API key set — skipping.")
            return []

        cutoff = today + timedelta(days=lookahead_days)
        yesterday = today - timedelta(days=1)
        events: list[Event] = []

        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": API_HOST,
        }

        for team_cfg in self.teams:
            team_name = team_cfg["name"]
            team_id = team_cfg.get("api_football_team_id")
            if not team_id:
                continue

            # Fetch upcoming fixtures
            params_upcoming = {"team": team_id, "next": 10}
            # Fetch recent fixtures (for yesterday's results)
            params_recent = {"team": team_id, "last": 5}

            fixtures = []
            for params in [params_upcoming, params_recent]:
                try:
                    resp = _session.get(
                        f"{API_BASE}/fixtures",
                        headers=headers,
                        params=params,
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    fixtures.extend(data.get("response", []))
                except Exception as e:
                    print(f"  [api_football] Warning: {team_name} fetch failed: {e}")

            seen_ids: set[int] = set()
            for fixture_wrap in fixtures:
                fixture = fixture_wrap.get("fixture", {})
                fixture_id = fixture.get("id")
                if fixture_id in seen_ids:
                    continue
                seen_ids.add(fixture_id)

                date_str = fixture.get("date", "")
                try:
                    start_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except Exception:
                    continue

                event_date = start_utc.astimezone(LOCAL_TZ).date()
                if event_date < yesterday or event_date > cutoff:
                    continue

                teams = fixture_wrap.get("teams", {})
                home = teams.get("home", {}).get("name", "Home")
                away = teams.get("away", {}).get("name", "Away")
                venue = fixture.get("venue", {})
                venue_name = venue.get("name", "")

                league_info = fixture_wrap.get("league", {})
                league_name = league_info.get("name", "")
                league_round = league_info.get("round", "")

                title = f"⚽ {home} vs {away}"
                subtitle_parts = []
                if league_name:
                    subtitle_parts.append(league_name)
                if league_round:
                    subtitle_parts.append(league_round)
                subtitle = " · ".join(subtitle_parts) if subtitle_parts else None

                result = None
                status = fixture.get("status", {})
                if status.get("short") in ("FT", "AET", "PEN"):
                    goals = fixture_wrap.get("goals", {})
                    home_goals = goals.get("home")
                    away_goals = goals.get("away")
                    if home_goals is not None and away_goals is not None:
                        our_team_is_home = teams.get("home", {}).get("id") == team_id
                        our = home_goals if our_team_is_home else away_goals
                        opp = away_goals if our_team_is_home else home_goals
                        outcome = "W" if our > opp else ("D" if our == opp else "L")
                        result = f"{outcome} {our}–{opp}"

                event = Event(
                    id=f"api_football:{fixture_id or uuid.uuid4()}",
                    title=title,
                    category=EventCategory.SPORTS,
                    start=start_utc,
                    location=venue_name or None,
                    source="api_football",
                    subtitle=subtitle,
                    result=result,
                    tags=["soccer", "liga_betplay", team_name.lower().replace(" ", "_"), "sports"],
                )
                _apply_priority(event, self.config)
                events.append(event)

        return events
