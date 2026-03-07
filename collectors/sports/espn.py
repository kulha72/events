"""
ESPN collector — covers NFL, MLB, NBA, NHL, NCAA FB, NCAA MB via the
unofficial but stable ESPN public API. No auth required.

Handles both upcoming schedules and yesterday's completed results.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Map config league slug -> (sport_path, league_path)
LEAGUE_MAP = {
    "nfl":         ("football",    "nfl"),
    "mlb":         ("baseball",    "mlb"),
    "nba":         ("basketball",  "nba"),
    "nhl":         ("hockey",      "nhl"),
    "ncaaf":       ("football",    "college-football"),
    "ncaamb":      ("basketball",  "mens-college-basketball"),
    "liga_betplay":              ("soccer", "col.1"),
    "epl":                       ("soccer", "eng.1"),
    "laliga":                    ("soccer", "esp.1"),
    "bundesliga":                ("soccer", "ger.1"),
    "seriea":                    ("soccer", "ita.1"),
    "ligue1":                    ("soccer", "fra.1"),
    "mls":                       ("soccer", "usa.1"),
    "conmebol.libertadores":     ("soccer", "conmebol.libertadores"),
    "conmebol.sudamericana":     ("soccer", "conmebol.sudamericana"),
    "uefa.champions":            ("soccer", "uefa.champions"),
    "uefa.europa":               ("soccer", "uefa.europa"),
    "uefa.europa.conference":    ("soccer", "uefa.europa.conference"),
}

SPORT_EMOJI = {
    "nfl":         "🏈",
    "mlb":         "⚾",
    "nba":         "🏀",
    "nhl":         "🏒",
    "ncaaf":       "🏈",
    "ncaamb":      "🏀",
    "liga_betplay":           "⚽",
    "epl":                    "⚽",
    "laliga":                 "⚽",
    "bundesliga":             "⚽",
    "seriea":                 "⚽",
    "ligue1":                 "⚽",
    "mls":                    "⚽",
    "conmebol.libertadores":  "⚽",
    "conmebol.sudamericana":  "⚽",
    "uefa.champions":         "⚽",
    "uefa.europa":            "⚽",
    "uefa.europa.conference": "⚽",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; daily-digest-espn/1.0)"})


def _team_schedule_url(league: str, team_id: int) -> str:
    sport, lg = LEAGUE_MAP[league]
    return f"{ESPN_BASE}/{sport}/{lg}/teams/{team_id}/schedule"


def _scoreboard_url(league: str) -> str:
    sport, lg = LEAGUE_MAP[league]
    return f"{ESPN_BASE}/{sport}/{lg}/scoreboard"


def _fetch_json(url: str, params: dict | None = None) -> dict:
    resp = _session.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _parse_competition(comp: dict, our_team_id: str, league: str, team_name: str) -> dict | None:
    """
    Extract display data from a single competition dict.
    Returns a dict of displayable fields, or None if unparseable.
    """
    date_str = comp.get("date") or ""
    try:
        start_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        return None

    status = comp.get("status", {})
    status_type = status.get("type", {})
    completed = status_type.get("completed", False)
    status_name = status_type.get("name", "STATUS_SCHEDULED")

    venue = comp.get("venue", {})
    venue_name = venue.get("fullName", "")

    competitors = comp.get("competitors", [])
    our_comp = next((c for c in competitors if str(c.get("team", {}).get("id")) == str(our_team_id)), None)
    opp_comp = next((c for c in competitors if str(c.get("team", {}).get("id")) != str(our_team_id)), None)

    if not our_comp or not opp_comp:
        return None

    our_team = our_comp.get("team", {})
    opp_team = opp_comp.get("team", {})
    home_away = our_comp.get("homeAway", "home")
    opp_name = opp_team.get("displayName", "Opponent")

    if home_away == "home":
        title = f"{team_name} vs {opp_name}"
    else:
        title = f"{team_name} @ {opp_name}"

    result = None
    if completed:
        our_score_raw = our_comp.get("score", "0")
        opp_score_raw = opp_comp.get("score", "0")
        our_score = our_score_raw.get("displayValue", "0") if isinstance(our_score_raw, dict) else our_score_raw
        opp_score = opp_score_raw.get("displayValue", "0") if isinstance(opp_score_raw, dict) else opp_score_raw
        winner = our_comp.get("winner", False)
        outcome = "W" if winner else "L"
        result = f"{outcome} {our_score}–{opp_score}"

    notes = comp.get("notes", [])
    note_text = notes[0].get("headline", "") if notes else ""

    links = comp.get("links", [])
    event_url = next((lk["href"] for lk in links if "gamecast" in lk.get("rel", [])), None)

    return {
        "start_utc": start_utc,
        "title": title,
        "venue": venue_name,
        "note": note_text,
        "result": result,
        "completed": completed,
        "status_name": status_name,
        "event_url": event_url,
        "home_away": home_away,
        "opp_name": opp_name,
    }


def _apply_priority(event: Event, config: dict) -> None:
    """Mutate event.priority based on config high_keywords and rivalry_matchups."""
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


class ESPNCollector(BaseCollector):
    """Collects schedule + results for all ESPN-sourced teams from config."""

    def __init__(self, config: dict):
        self.config = config
        self.teams = [
            t for t in config.get("sports", {}).get("teams", [])
            if t.get("source") == "espn" and t.get("league") in LEAGUE_MAP
        ]

    @property
    def source_name(self) -> str:
        return "espn"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        cutoff = today + timedelta(days=lookahead_days)
        yesterday = today - timedelta(days=1)
        events: list[Event] = []

        for team_cfg in self.teams:
            team_name = team_cfg["name"]
            team_id = team_cfg["espn_team_id"]
            leagues_to_check = [team_cfg["league"]] + team_cfg.get("extra_leagues", [])

            for league in leagues_to_check:
                if league not in LEAGUE_MAP:
                    print(f"  [espn] Warning: unknown league '{league}' for {team_name}, skipping")
                    continue

                emoji = SPORT_EMOJI.get(league, "")
                sport, _ = LEAGUE_MAP[league]
                is_soccer = sport == "soccer"

                if is_soccer:
                    # Soccer team schedules only return played games; use the
                    # scoreboard endpoint with a date range to get upcoming fixtures.
                    date_from = (yesterday).strftime("%Y%m%d")
                    date_to = cutoff.strftime("%Y%m%d")
                    sb_url = _scoreboard_url(league)
                    try:
                        data = _fetch_json(sb_url, params={"dates": f"{date_from}-{date_to}"})
                    except Exception as e:
                        print(f"  [espn] Warning: {team_name} ({league}) scoreboard fetch failed: {e}")
                        continue
                    # Filter to only events involving this team
                    all_sb_events = data.get("events", [])
                    raw_events = [
                        ev for ev in all_sb_events
                        if any(
                            str(c.get("team", {}).get("id")) == str(team_id)
                            for comp in [ev.get("competitions", [{}])[0]]
                            for c in comp.get("competitors", [])
                        )
                    ]
                else:
                    url = _team_schedule_url(league, team_id)
                    try:
                        data = _fetch_json(url)
                    except Exception as e:
                        print(f"  [espn] Warning: {team_name} ({league}) schedule fetch failed: {e}")
                        continue

                    raw_events = data.get("events", [])
                    if not raw_events:
                        # Some leagues wrap in a different key
                        raw_events = data.get("schedule", {})
                        if isinstance(raw_events, dict):
                            # Flatten date-keyed schedule dict
                            flat = []
                            for date_key, day_data in raw_events.items():
                                flat.extend(day_data.get("games", []))
                            raw_events = flat

                for raw in raw_events:
                    competitions = raw.get("competitions", [])
                    if not competitions:
                        continue
                    comp = competitions[0]

                    parsed = _parse_competition(comp, str(team_id), league, team_name)
                    if not parsed:
                        continue

                    start_utc = parsed["start_utc"]
                    event_date = start_utc.astimezone(LOCAL_TZ).date()

                    # Include yesterday (for results) through lookahead
                    if event_date < yesterday or event_date > cutoff:
                        continue

                    week_info = raw.get("week", {})
                    week_num = week_info.get("number")
                    subtitle_parts = []
                    if week_num:
                        subtitle_parts.append(f"Week {week_num}")
                    if parsed["venue"]:
                        subtitle_parts.append(parsed["venue"])
                    if parsed["note"]:
                        subtitle_parts.append(parsed["note"])
                    subtitle = " · ".join(subtitle_parts) if subtitle_parts else None

                    event = Event(
                        id=f"espn:{league}:{raw.get('id', uuid.uuid4())}",
                        title=f"{emoji} {parsed['title']}" if emoji else parsed["title"],
                        category=EventCategory.SPORTS,
                        start=start_utc,
                        location=parsed["venue"] or None,
                        source="espn",
                        url=parsed["event_url"],
                        subtitle=subtitle,
                        result=parsed["result"],
                        tags=[league, team_name.lower().replace(" ", "_"), "sports"],
                    )

                    _apply_priority(event, self.config)
                    events.append(event)

        return events
