from abc import ABC, abstractmethod
from datetime import date

from models.event import Event


class BaseCollector(ABC):
    """All collectors implement this interface."""

    @abstractmethod
    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        """
        Fetch events from today through today + lookahead_days.
        Return normalized Event objects.
        """
        pass

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Identifier for this source, e.g. 'espn', 'tecumseh'."""
        pass
