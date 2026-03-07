"""
Liquipedia collector — scrapes upcoming and ongoing matches for
LoL, VALORANT, CS2, and Dota 2.

Liquipedia allows scraping at reasonable rates. This collector:
- Respects robots.txt (only /Upcoming_and_ongoing_matches pages)
- Identifies itself via User-Agent
- Caches results for the full run (no re-scrape per game)
- Only targets major/premier tier matches when configured

MediaWiki API endpoint used for structured wikitext:
  https://liquipedia.net/{game}/api.php?action=parse&page=...&format=json
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")

GAME_SLUGS = {
    "League of Legends": "leagueoflegends",
    "VALORANT":          "valorant",
    "CS2":               "counterstrike",
    "Dota 2":            "dota2",
}

# Tier keywords in tournament names that indicate major+ events
MAJOR_KEYWORDS = re.compile(
    r"\b(world|worlds|major|masters|champions|international|playoff|finals?|"
    r"grand final|msi|vct|iem|esl|blast|rmr)\b",
    re.IGNORECASE,
)

_session = requests.Session()
_session.headers.update({
    "User-Agent": "daily-digest-bot/1.0 (personal calendar aggregator; respectful scraper)",
    "Accept-Language": "en-US,en;q=0.9",
})


def _fetch_upcoming_html(game_slug: str) -> str:
    """Fetch the Upcoming_and_ongoing_matches wiki page as rendered HTML."""
    url = f"https://liquipedia.net/{game_slug}/Liquipedia:Upcoming_and_ongoing_matches"
    resp = _session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def _parse_matches(html: str, game_name: str, min_tier: str) -> list[dict]:
    """
    Parse match rows from the Liquipedia upcoming matches page.

    The page structure uses .wikitable rows with:
    - Tournament name (often in a header row or cell)
    - Team 1 name
    - Team 2 name
    - Match date/time (UTC, in a <span> or abbr with ISO datetime)
    - Best-of format
    """
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    # Liquipedia match tables use class="wikitable matchlist" or similar
    # Each match is typically a <tr> with team names and a datetime
    for table in soup.find_all("table", class_=re.compile(r"wikitable|match")):
        # Try to find the tournament name from the table caption or preceding heading
        tournament = ""
        caption = table.find("caption")
        if caption:
            tournament = caption.get_text(strip=True)
        if not tournament:
            prev = table.find_previous(["h2", "h3", "h4"])
            if prev:
                tournament = prev.get_text(strip=True).strip("[]").strip()

        # Filter by tier if required
        if min_tier == "major" and not MAJOR_KEYWORDS.search(tournament):
            continue

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            # Look for datetime in <abbr> or <span data-timestamp>
            dt_el = row.find("abbr", title=True) or row.find("span", attrs={"data-timestamp": True})
            if not dt_el:
                continue

            start_utc = None
            if dt_el.name == "abbr":
                try:
                    start_utc = datetime.fromisoformat(
                        dt_el["title"].replace("Z", "+00:00")
                    )
                except Exception:
                    pass
            elif dt_el.get("data-timestamp"):
                try:
                    ts = int(dt_el["data-timestamp"])
                    start_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
                except Exception:
                    pass

            if not start_utc:
                # Try parsing visible text as a date
                time_text = dt_el.get_text(strip=True)
                try:
                    from dateutil import parser as dateparser
                    start_utc = dateparser.parse(time_text, fuzzy=True).replace(tzinfo=timezone.utc)
                except Exception:
                    continue

            # Extract team names from cell text (skip cells that are dates/scores)
            team_cells = [c for c in cells if not c.find(["abbr", "span"]) or len(c.get_text(strip=True)) > 3]
            team_names = []
            for tc in team_cells:
                text = tc.get_text(strip=True)
                # Filter out pure numbers, short strings, "vs", "bo3" etc.
                if text and len(text) > 1 and not re.match(r"^[\d:.\-/]+$", text) and text.lower() not in ("vs", "v", "tbd"):
                    team_names.append(text)

            if len(team_names) >= 2:
                title = f"{team_names[0]} vs {team_names[1]}"
            elif len(team_names) == 1:
                title = team_names[0]
            else:
                title = f"{game_name} Match"

            # Best-of format
            bo_el = row.find(string=re.compile(r"Bo\d|Best of \d", re.IGNORECASE))
            bo_text = str(bo_el).strip() if bo_el else ""

            subtitle_parts = [tournament] if tournament else []
            if bo_text:
                subtitle_parts.append(bo_text)
            subtitle = " · ".join(subtitle_parts) if subtitle_parts else None

            matches.append({
                "title": title,
                "start_utc": start_utc,
                "tournament": tournament,
                "subtitle": subtitle,
                "game": game_name,
            })

    return matches


class LiquipediaCollector(BaseCollector):
    """Collects esports match schedules from Liquipedia."""

    def __init__(self, config: dict):
        self.config = config
        self.games = [
            g for g in config.get("esports", {}).get("games", [])
            if g.get("source") == "liquipedia"
        ]

    @property
    def source_name(self) -> str:
        return "liquipedia"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        import cache

        cutoff = today + timedelta(days=lookahead_days)
        events: list[Event] = []

        for game_cfg in self.games:
            game_name = game_cfg["name"]
            min_tier = game_cfg.get("min_tier", "major")
            game_slug = GAME_SLUGS.get(game_name)
            if not game_slug:
                continue

            cache_key = f"liquipedia:{game_slug}"
            cached_matches = cache.get(cache_key, ttl_seconds=3600 * 6)

            if cached_matches is None:
                try:
                    html = _fetch_upcoming_html(game_slug)
                    cached_matches = _parse_matches(html, game_name, min_tier)
                    cache.set(cache_key, cached_matches)
                except Exception as e:
                    print(f"  [liquipedia] Warning: {game_name} scrape failed: {e}")
                    cached_matches = []

            for match in cached_matches:
                start_utc = match["start_utc"]
                if isinstance(start_utc, str):
                    try:
                        start_utc = datetime.fromisoformat(start_utc)
                    except Exception:
                        continue

                event_date = start_utc.astimezone(LOCAL_TZ).date()
                if event_date < today or event_date > cutoff:
                    continue

                # Priority: check for high-tier keywords
                tournament = match.get("tournament", "")
                priority = EventPriority.NORMAL
                if re.search(r"\b(world|worlds|the international|vct champions|cs2? major)\b",
                             tournament, re.IGNORECASE):
                    priority = EventPriority.HIGH

                game_tag = game_name.lower().replace(" ", "_")
                event = Event(
                    id=f"liquipedia:{game_slug}:{uuid.uuid5(uuid.NAMESPACE_URL, match['title'] + str(start_utc))}",
                    title=f"🎮 {match['title']}",
                    category=EventCategory.ESPORTS,
                    start=start_utc,
                    source="liquipedia",
                    subtitle=match.get("subtitle"),
                    priority=priority,
                    tags=["esports", game_tag],
                )
                events.append(event)

        return events
