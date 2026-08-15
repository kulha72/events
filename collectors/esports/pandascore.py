"""
PandaScore collector — supplemental/backup esports data.
Covers LoL, VALORANT, CS2, Dota 2 with structured tournament data.

Free tier: 1000 requests/month.
Set PANDASCORE_API_KEY environment variable.
"""

import os
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

import errors

LOCAL_TZ = ZoneInfo("America/Detroit")
API_BASE = "https://api.pandascore.co"

GAME_SLUGS = {
    "League of Legends": "lol",
    "VALORANT":          "valorant",
    "CS2":               "csgo",
    "Dota 2":            "dota2",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "daily-digest/1.0"})

# The upcoming-matches endpoint caps a page at 50, and the tier-B circuits
# (CCT, ESEA) run enough matches to fill one on their own. Filtering leagues
# client-side over page 1 alone therefore drops real BLAST/ESL fixtures later
# in the same window. Walk pages until the window is covered, capped so a busy
# week cannot eat the free tier's 1000 requests/month.
_PER_PAGE = 50
_MAX_PAGES = 4


def _fetch_all_matches(game_slug: str, headers: dict, today, cutoff) -> list[dict]:
    """Page through every upcoming match in the window."""
    matches: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        resp = _session.get(
            f"{API_BASE}/{game_slug}/matches/upcoming",
            headers=headers,
            params={
                "range[scheduled_at]": f"{today.isoformat()},{cutoff.isoformat()}",
                "sort": "begin_at",
                "per_page": _PER_PAGE,
                "page": page,
            },
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        matches.extend(batch)
        if len(batch) < _PER_PAGE:
            break
    return matches


def _known_leagues(game_slug: str, headers: dict, names: list[str]) -> tuple[set[str], set[str]]:
    """Split configured league names into those PandaScore knows and those it doesn't.

    A filter that matches nothing has two very different causes: the league is
    real but idle this week, or the name in config does not exist and the
    filter can never match. Only the second is worth acting on.
    """
    known, unknown = set(), set()
    for name in names:
        try:
            resp = _session.get(
                f"{API_BASE}/{game_slug}/leagues",
                headers=headers,
                params={"search[name]": name, "per_page": 5},
                timeout=15,
            )
            resp.raise_for_status()
            hits = resp.json()
        except Exception:
            # Can't tell — don't accuse the config of being wrong.
            known.add(name)
            continue
        if isinstance(hits, list) and hits:
            known.add(name)
        else:
            unknown.add(name)
    return known, unknown


class PandaScoreCollector(BaseCollector):
    """Supplemental esports data from PandaScore."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.environ.get("PANDASCORE_API_KEY", "")
        self.games = [
            g for g in config.get("esports", {}).get("games", [])
            # Only use pandascore as fallback if liquipedia is not configured
            if g.get("source") == "pandascore"
        ]

    @property
    def source_name(self) -> str:
        return "pandascore"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        # Check for work before checking for credentials, so a source nothing
        # routes to never reports a missing key as a failure.
        if not self.games:
            errors.note_not_configured(
                "pandascore", "no esports game in config sets source: pandascore"
            )
            return []

        if not self.api_key:
            errors.record("pandascore", "no API key set — source skipped")
            return []

        cutoff = today + timedelta(days=lookahead_days)
        events: list[Event] = []

        headers = {"Authorization": f"Bearer {self.api_key}"}

        for game_cfg in self.games:
            game_name = game_cfg["name"]
            game_slug = GAME_SLUGS.get(game_name)
            if not game_slug:
                continue

            leagues_filter = [l.lower() for l in game_cfg.get("leagues", [])]

            try:
                matches = _fetch_all_matches(game_slug, headers, today, cutoff)
            except Exception as e:
                errors.record("pandascore", f"{game_name} fetch failed: {e}")
                continue

            # The league filter runs client-side over a single capped page, so
            # "0 events" can mean either "nothing upcoming" or "the page was
            # full of other leagues". Count both so the report can say which.
            kept = 0
            dropped_by_league: set[str] = set()

            for match in matches:
                begin_at = match.get("scheduled_at") or match.get("begin_at", "")
                if not begin_at:
                    continue
                try:
                    start_utc = datetime.fromisoformat(begin_at.replace("Z", "+00:00"))
                except Exception:
                    continue

                event_date = start_utc.astimezone(LOCAL_TZ).date()
                if event_date < today or event_date > cutoff:
                    continue

                opponents = match.get("opponents", [])
                team_names = [o.get("opponent", {}).get("name", "TBD") for o in opponents]
                if len(team_names) >= 2:
                    title = f"{team_names[0]} vs {team_names[1]}"
                else:
                    title = match.get("name", f"{game_name} Match")

                league = match.get("league", {}).get("name", "")
                serie = match.get("serie", {}).get("full_name", "")
                tournament_name = match.get("tournament", {}).get("name", "")
                match_type = match.get("match_type", "")

                if leagues_filter and not any(lf in league.lower() for lf in leagues_filter):
                    if league:
                        dropped_by_league.add(league)
                    continue
                kept += 1

                subtitle_parts = filter(None, [league, serie or tournament_name, match_type])
                subtitle = " · ".join(subtitle_parts) or None

                tier = match.get("tier", "a")
                priority = EventPriority.HIGH if tier == "s" else EventPriority.NORMAL

                game_tag = game_name.lower().replace(" ", "_")
                event = Event(
                    id=f"pandascore:{match.get('id', uuid.uuid4())}",
                    title=f"🎮 {title}",
                    category=EventCategory.ESPORTS,
                    start=start_utc,
                    source="pandascore",
                    url=match.get("official_stream_url"),
                    subtitle=subtitle,
                    priority=priority,
                    tags=["esports", game_tag],
                )
                events.append(event)

            if not kept:
                if dropped_by_league:
                    configured = game_cfg.get("leagues", [])
                    known, unknown = _known_leagues(game_slug, headers, configured)
                    if unknown:
                        # The filter can never match — that is a config bug and
                        # the one case here worth waking up for.
                        errors.note_suspect(
                            "pandascore",
                            f"{game_name}: no league named {sorted(unknown)} exists in "
                            f"PandaScore, so the filter can never match "
                            f"(configured: {configured})",
                        )
                    else:
                        # Real leagues, just nothing scheduled this week. A
                        # circuit between stages is not a broken feed.
                        errors.note_idle(
                            "pandascore",
                            f"{game_name}: {', '.join(sorted(known))} have no matches in the "
                            f"next {lookahead_days} days ({len(matches)} other matches scheduled)",
                        )
                elif not matches:
                    errors.note_idle(
                        "pandascore",
                        f"{game_name}: API reachable, 0 matches scheduled in the window",
                    )

        return events
