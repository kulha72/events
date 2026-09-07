from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class EventCategory(Enum):
    LOCAL = "local"
    ESTATE_SALES = "estate_sales"
    SPORTS = "sports"
    PLAYOFFS = "playoffs"
    ESPORTS = "esports"


class EventPriority(Enum):
    """Controls look-ahead visibility and email prominence."""
    HIGH = "high"      # Playoffs, rivalry games, major tournaments, Worlds
    NORMAL = "normal"  # Regular season, weekly esports, local festivals
    LOW = "low"        # Minor local events, qualifiers


@dataclass
class Event:
    id: str                          # Unique ID (source:type:identifier)
    title: str                       # "Packers vs Bears" or "Ann Arbor Art Fair"
    category: EventCategory
    start: datetime                  # UTC
    end: Optional[datetime] = None   # UTC, if known
    location: Optional[str] = None   # Venue or city
    source: str = ""                 # "espn", "liquipedia", "tecumseh_scraper"
    url: Optional[str] = None        # Link to event page / ticket / stream
    priority: EventPriority = EventPriority.NORMAL
    tags: list[str] = field(default_factory=list)  # ["nfl", "packers"], ["esports", "lol", "worlds"]
    subtitle: Optional[str] = None   # "Week 12 · Lambeau Field" or "Grand Finals · Bo5"
    result: Optional[str] = None     # "W 27-14" — filled in for yesterday's events
    is_today: bool = False           # Computed at format time
    is_past: bool = False            # Computed at format time
    # True when the source gave a date but no clock time. `start` is still a
    # real datetime — local midnight — so everything sorts on one field, but
    # that midnight is a placeholder rather than a start time. Without this the
    # formatters have to guess from the hour, and every all-day listing in the
    # digest gets announced at 12 AM.
    #
    # Declared last so the field order the collectors rely on does not move.
    all_day: bool = False
