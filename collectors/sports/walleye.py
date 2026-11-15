"""
Toledo Walleye collector (ECHL). ESPN does not carry the ECHL, so this reads
the club's own site, which publishes two things:

  * https://www.toledowalleye.com/schedule-all.ics — every game still to be
    played, with puck-drop time, venue and a link. Games drop out of the feed
    once they are over, so it never holds a score.
  * the calendar grid on the home page — one cell per day of the current
    month; a played game's cell shows "W 4 - 2".

Upcoming games come from the feed, yesterday's score from the grid. The grid
only shows the current month, so on the 1st there is no score for the game
played the night before.

Config (under sports.teams):

    - name: "Walleye"
      league: "ECHL"
      source: "toledo_walleye"
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from collectors.base import BaseCollector
from collectors.sports.espn import _apply_priority
from models.event import Event, EventCategory

import errors

LOCAL_TZ = ZoneInfo("America/Detroit")
SITE = "https://www.toledowalleye.com"
ICS_URL = f"{SITE}/schedule-all.ics"
CLUB = "Toledo Walleye"
EMOJI = "🏒"

# "W 4 - 2", "L 1 - 3", "OTL 2 - 3", "SOW 3 - 2"
_RESULT_RE = re.compile(r"\b((?:OT|SO)?[WL])\s+(\d+)\s*-\s*(\d+)\b")
_ICS_DT_RE = re.compile(r"(\d{8})T(\d{6})(Z?)")

_session = requests.Session()


def _ics_events(text: str) -> list[dict]:
    """Minimal iCalendar reader: unfold lines, return each VEVENT's properties.

    Keys are the property names; a TZID parameter is kept under "<NAME>_TZID".
    """
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    events, current = [], None
    for line in unfolded.splitlines():
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            value = re.sub(r"\\([,;\\])", r"\1", value).replace("\\n", " ").replace("\\N", " ")
            current[name] = value.strip()
            for p in params:
                if p.upper().startswith("TZID="):
                    current[f"{name}_TZID"] = p[5:]
    return events


def _ics_datetime(raw: str, tzid: str | None) -> datetime | None:
    m = _ICS_DT_RE.fullmatch(raw or "")
    if not m:
        return None
    naive = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        return naive.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tzid) if tzid else LOCAL_TZ
    except Exception:
        tz = LOCAL_TZ
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def _matchup(description: str, team_name: str) -> str | None:
    """"Rapid City Rush at Toledo Walleye" -> "Walleye vs Rapid City Rush"."""
    away, sep, home = description.partition(" at ")
    if not sep:
        return None
    away, home = away.strip(), home.strip()
    if home == CLUB:
        return f"{team_name} vs {away}"
    if away == CLUB:
        return f"{team_name} @ {home}"
    return None


def parse_ics(text: str, team_cfg: dict) -> list[Event]:
    """Every game in the club's calendar feed, as Events."""
    team_name = team_cfg["name"]
    league = team_cfg.get("league", "ECHL")
    events = []
    for ve in _ics_events(text):
        start = _ics_datetime(ve.get("DTSTART", ""), ve.get("DTSTART_TZID"))
        if not start:
            continue
        summary = ve.get("SUMMARY", "")
        # Home games are titled after the night's promotion ("Golf Night");
        # the matchup itself is only in the description.
        matchup = _matchup(ve.get("DESCRIPTION", ""), team_name)
        promo = None
        if matchup:
            if CLUB not in summary:
                promo = summary
        else:
            matchup = summary.replace(CLUB, team_name)
        if not matchup:
            continue

        venue = ve.get("LOCATION") or None
        # The venue goes in `location`, which the page prints beside the subtitle.
        subtitle = " · ".join(p for p in (league, promo) if p)
        url = ve.get("URL") or ve.get("UID") or SITE
        events.append(Event(
            id=f"toledo_walleye:{ve.get('UID') or start.isoformat()}",
            title=f"{EMOJI} {matchup}",
            category=EventCategory.SPORTS,
            start=start,
            location=venue,
            source="toledo_walleye",
            url=url,
            subtitle=subtitle,
            tags=["echl", "walleye", "hockey", "sports"],
        ))
    return events


def parse_results(html: str, team_cfg: dict) -> list[Event]:
    """Played games from the home page's month grid, as all-day Events with a result."""
    team_name = team_cfg["name"]
    league = team_cfg.get("league", "ECHL")
    soup = BeautifulSoup(html, "lxml")
    events = []
    for stamp in soup.select("time[datetime]"):
        try:
            day = date.fromisoformat(stamp["datetime"][:10])
        except ValueError:
            continue
        cell = stamp.parent
        for link in cell.find_all("a", href=True):
            if "/games/" not in link["href"]:
                continue
            text = " ".join(link.get_text(" ").split())
            m = _RESULT_RE.search(text)
            if not m:
                continue
            logo = link.find("img", alt=True)
            opponent = (logo["alt"].strip() if logo else "") or "Opponent"
            away = "@" in text.split(m.group(0))[0]
            # The grid prints the two scores in a fixed away–home order.
            # Read them through the W/L instead: the winner has the larger.
            a, b = int(m.group(2)), int(m.group(3))
            won = m.group(1).endswith("W")
            ours, theirs = (max(a, b), min(a, b)) if won else (min(a, b), max(a, b))
            start = datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ)
            events.append(Event(
                id=f"toledo_walleye:{link['href']}",
                title=f"{EMOJI} {team_name} {'@' if away else 'vs'} {opponent}",
                category=EventCategory.SPORTS,
                start=start.astimezone(timezone.utc),
                source="toledo_walleye",
                url=link["href"],
                subtitle=league,
                result=f"{m.group(1)} {ours}–{theirs}",
                tags=["echl", "walleye", "hockey", "sports"],
                all_day=True,
            ))
    return events


class WalleyeCollector(BaseCollector):
    """Upcoming Walleye games plus yesterday's score."""

    def __init__(self, config: dict):
        self.config = config
        self.team = next(
            (t for t in config.get("sports", {}).get("teams", [])
             if t.get("source") == "toledo_walleye"),
            None,
        )

    @property
    def source_name(self) -> str:
        return "toledo_walleye"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        if not self.team:
            errors.note_not_configured(
                "toledo_walleye", "no team in config sets source: toledo_walleye"
            )
            return []

        yesterday = today - timedelta(days=1)
        cutoff = today + timedelta(days=lookahead_days)
        events: list[Event] = []
        fetched = 0

        try:
            resp = _session.get(ICS_URL, timeout=20)
            resp.raise_for_status()
            fetched += 1
            feed = parse_ics(resp.text, self.team)
            if "BEGIN:VCALENDAR" not in resp.text:
                errors.note_suspect("toledo_walleye", "schedule feed is not a calendar")
            events += [
                e for e in feed
                if today <= e.start.astimezone(LOCAL_TZ).date() <= cutoff
            ]
        except Exception as e:
            errors.record("toledo_walleye", f"schedule feed fetch failed: {e}")

        try:
            resp = _session.get(SITE + "/", timeout=20)
            resp.raise_for_status()
            fetched += 1
            # Today's game stays in the feed until it ends, so only take
            # scores for days the feed no longer covers.
            events += [
                e for e in parse_results(resp.text, self.team)
                if e.start.astimezone(LOCAL_TZ).date() == yesterday
            ]
        except Exception as e:
            errors.record("toledo_walleye", f"results page fetch failed: {e}")

        for event in events:
            _apply_priority(event, self.config)

        if fetched and not events:
            errors.note_idle("toledo_walleye", "site fetched; no games in the window")
        return events
