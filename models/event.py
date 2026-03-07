from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class EventCategory(Enum):
    LOCAL = "local"
    SPORTS = "sports"
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
