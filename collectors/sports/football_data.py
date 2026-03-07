"""
football-data.org collector — covers Arsenal (EPL) and any other
football-data.org configured teams.

Free tier: 10 requests/min. Register at football-data.org for a free API key.
"""

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")
API_BASE = "https://api.football-data.org/v4"

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


class FootballDataCollector(BaseCollector):
    """Collects matches from football-data.org (Arsenal / EPL)."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
        self.teams = [
            t for t in config.get("sports", {}).get("teams", [])
            if t.get("source") == "football_data"
        ]

    @property
    def source_name(self) -> str:
        return "football_data"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        if not self.api_key:
            print("  [football_data] No API key set — skipping.")
            return []

        cutoff = today + timedelta(days=lookahead_days)
        yesterday = today - timedelta(days=1)
        events: list[Event] = []

        headers = {"X-Auth-Token": self.api_key}

        for team_cfg in self.teams:
            team_name = team_cfg["name"]
            team_id = team_cfg.get("football_data_team_id")
            if not team_id:
                continue

            date_from = yesterday.strftime("%Y-%m-%d")
            date_to = cutoff.strftime("%Y-%m-%d")

            url = f"{API_BASE}/teams/{team_id}/matches"
            params = {"dateFrom": date_from, "dateTo": date_to, "limit": 20}

            try:
                resp = _session.get(url, headers=headers, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [football_data] Warning: {team_name} fetch failed: {e}")
                continue

            for match in data.get("matches", []):
                utc_date = match.get("utcDate", "")
                try:
                    start_utc = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
                except Exception:
                    continue

                event_date = start_utc.astimezone(LOCAL_TZ).date()
                if event_date < yesterday or event_date > cutoff:
                    continue

                home = match.get("homeTeam", {}).get("name", "Home")
                away = match.get("awayTeam", {}).get("name", "Away")
                competition = match.get("competition", {}).get("name", "")
                matchday = match.get("matchday")
                status = match.get("status", "SCHEDULED")

                title = f"⚽ {home} vs {away}"
                subtitle_parts = []
                if competition:
                    subtitle_parts.append(competition)
                if matchday:
                    subtitle_parts.append(f"Matchday {matchday}")
                subtitle = " · ".join(subtitle_parts) if subtitle_parts else None

                result = None
                if status == "FINISHED":
                    score = match.get("score", {}).get("fullTime", {})
                    home_goals = score.get("home")
                    away_goals = score.get("away")
                    if home_goals is not None and away_goals is not None:
                        # Determine our team's result
                        our_team_is_home = (
                            match.get("homeTeam", {}).get("id") == team_id
                        )
                        if our_team_is_home:
                            our, opp = home_goals, away_goals
                        else:
                            our, opp = away_goals, home_goals
                        outcome = "W" if our > opp else ("D" if our == opp else "L")
                        result = f"{outcome} {our}–{opp}"

                event = Event(
                    id=f"football_data:{match.get('id', uuid.uuid4())}",
                    title=title,
                    category=EventCategory.SPORTS,
                    start=start_utc,
                    source="football_data",
                    subtitle=subtitle,
                    result=result,
                    tags=["soccer", "epl", team_name.lower().replace(" ", "_"), "sports"],
                )
                _apply_priority(event, self.config)
                events.append(event)

        return events
