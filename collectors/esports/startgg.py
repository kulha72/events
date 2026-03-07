"""
start.gg collector — Smash Bros majors and supermajors via the
start.gg GraphQL API.

Requires a free API token from start.gg developer portal.
Set STARTGG_API_KEY environment variable.

Game IDs:
  Melee (SSBM): 1
  Ultimate (SSBU): 1386
"""

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from collectors.base import BaseCollector
from models.event import Event, EventCategory, EventPriority

LOCAL_TZ = ZoneInfo("America/Detroit")
GQL_URL = "https://api.start.gg/gql/alpha"

GAME_IDS = {
    "melee":   1,
    "ultimate": 1386,
}

# Attendee threshold for "major" classification
MAJOR_MIN_ATTENDEES = 300
SUPERMAJOR_MIN_ATTENDEES = 750

QUERY = """
query UpcomingTournaments($videogameIds: [ID], $afterDate: Timestamp!, $beforeDate: Timestamp!, $perPage: Int!) {
  tournaments(query: {
    filter: {
      videogameIds: $videogameIds
      afterDate: $afterDate
      beforeDate: $beforeDate
    }
    perPage: $perPage
    sortBy: "startAt asc"
  }) {
    nodes {
      id
      name
      startAt
      endAt
      numAttendees
      city
      countryCode
      slug
    }
  }
}
"""


def _run_query(token: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        GQL_URL,
        json={"query": QUERY, "variables": variables},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


class StartGGCollector(BaseCollector):
    """Collects Smash Bros major tournaments from start.gg."""

    def __init__(self, config: dict):
        self.config = config
        self.api_key = os.environ.get("STARTGG_API_KEY", "")
        self.smash_cfg = next(
            (g for g in config.get("esports", {}).get("games", []) if g.get("source") == "startgg"),
            None,
        )

    @property
    def source_name(self) -> str:
        return "startgg"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        if not self.api_key:
            print("  [startgg] No API key set — skipping.")
            return []

        if not self.smash_cfg:
            return []

        import cache

        cache_key = f"startgg:{today}"
        cached = cache.get(cache_key, ttl_seconds=3600 * 6)
        if cached is not None:
            return [Event(**e) for e in cached]

        cutoff = today + timedelta(days=lookahead_days)
        games = self.smash_cfg.get("games", ["melee", "ultimate"])
        min_tier = self.smash_cfg.get("min_tier", "major")
        min_attendees = MAJOR_MIN_ATTENDEES if min_tier == "major" else SUPERMAJOR_MIN_ATTENDEES

        game_ids = [GAME_IDS[g] for g in games if g in GAME_IDS]
        if not game_ids:
            return []

        after_ts = int(datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp())
        before_ts = int(datetime(cutoff.year, cutoff.month, cutoff.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())

        try:
            result = _run_query(self.api_key, {
                "videogameIds": game_ids,
                "afterDate": after_ts,
                "beforeDate": before_ts,
                "perPage": 30,
            })
        except Exception as e:
            print(f"  [startgg] Warning: query failed: {e}")
            return []

        nodes = result.get("data", {}).get("tournaments", {}).get("nodes", []) or []
        events: list[Event] = []

        for node in nodes:
            attendees = node.get("numAttendees") or 0
            if attendees < min_attendees:
                continue

            name = node.get("name", "Smash Tournament")
            start_ts = node.get("startAt")
            end_ts = node.get("endAt")

            if not start_ts:
                continue

            start_utc = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            end_utc = datetime.fromtimestamp(end_ts, tz=timezone.utc) if end_ts else None

            city = node.get("city", "")
            country = node.get("countryCode", "")
            location = ", ".join(filter(None, [city, country])) or None

            slug = node.get("slug", "")
            url = f"https://www.start.gg/{slug}" if slug else None

            priority = EventPriority.HIGH if attendees >= SUPERMAJOR_MIN_ATTENDEES else EventPriority.NORMAL
            subtitle_parts = []
            if attendees:
                subtitle_parts.append(f"{attendees:,} entrants")
            subtitle = " · ".join(subtitle_parts) if subtitle_parts else None

            event = Event(
                id=f"startgg:{node.get('id', uuid.uuid4())}",
                title=f"🎮 {name}",
                category=EventCategory.ESPORTS,
                start=start_utc,
                end=end_utc,
                location=location,
                source="startgg",
                url=url,
                priority=priority,
                subtitle=subtitle,
                tags=["esports", "smash", "fighting_game"],
            )
            events.append(event)

        return events
