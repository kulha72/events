"""
Estate Sales collector — scrapes estatesales.net for sales within 15 miles of
Tecumseh, MI 49286.

Parses schema.org JSON-LD (SaleEvent) embedded in the search results page;
no API key required.  Sales that span multiple days are included as long as
they overlap with the lookahead window.
"""

import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from collectors.base import BaseCollector
from models.event import Event, EventCategory

LOCAL_TZ = ZoneInfo("America/Detroit")

# Tecumseh, MI 49286 centroid
TECUMSEH_LAT = 42.0042
TECUMSEH_LON = -84.0058

MAX_MILES = 15

SEARCH_URL = "https://www.estatesales.net/MI/Tecumseh/49286"

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; daily-digest-estatesales/1.0)"
})

# Zip code → (lat, lon) centroids for SE Michigan / NW Ohio.
# Used to filter results to MAX_MILES from Tecumseh.
_ZIP_COORDS: dict[str, tuple[float, float]] = {
    "49201": (42.2485, -84.4060),  # Jackson
    "49202": (42.2431, -84.3964),  # Jackson
    "49203": (42.2193, -84.4449),  # Jackson
    "49220": (41.9842, -84.3626),  # Addison
    "49221": (41.8975, -84.0372),  # Adrian
    "49228": (41.8332, -83.8618),  # Blissfield
    "49229": (41.9764, -83.8298),  # Britton
    "49230": (42.1138, -84.2392),  # Brooklyn
    "49234": (42.0810, -84.3254),  # Cement City
    "49236": (42.0685, -84.0690),  # Clinton
    "49238": (41.8893, -83.7817),  # Deerfield
    "49240": (42.2535, -84.2141),  # Grass Lake
    "49247": (41.8542, -84.3521),  # Hudson
    "49253": (41.9715, -84.2509),  # Manitou Beach / Devils Lake
    "49256": (41.7198, -84.2183),  # Morenci
    "49265": (41.9922, -84.1890),  # Onsted
    "49268": (41.8543, -83.9512),  # Palmyra
    "49276": (41.7903, -83.8218),  # Riga
    "49279": (41.8720, -84.1787),  # Sand Creek
    "49281": (42.1268, -84.4133),  # Somerset Center
    "49286": (42.0042, -84.0058),  # Tecumseh
    "49287": (42.0821, -84.0497),  # Tipton
    "49288": (41.7198, -84.4218),  # Waldron
    "49289": (41.7198, -84.0802),  # Weston
    "48103": (42.2868, -83.7956),  # Ann Arbor
    "48104": (42.2596, -83.7495),  # Ann Arbor
    "48105": (42.3108, -83.7167),  # Ann Arbor
    "48108": (42.2327, -83.7271),  # Ann Arbor area
    "48109": (42.2781, -83.7382),  # Ann Arbor (U of M)
    "48118": (42.3146, -84.0358),  # Chelsea
    "48130": (42.3305, -83.9940),  # Dexter
    "48144": (41.7573, -83.6246),  # Lambertville
    "48160": (42.2031, -83.6496),  # Milan
    "48176": (42.1660, -83.7735),  # Saline
    "48182": (41.7700, -83.5700),  # Temperance
    "48187": (42.3117, -83.5291),  # Canton
    "48188": (42.3025, -83.4798),  # Canton
    "48197": (42.2441, -83.6139),  # Ypsilanti
    "48198": (42.2394, -83.6374),  # Ypsilanti
    # Ohio
    "43528": (41.6259, -83.7119),  # Holland, OH
    "43560": (41.6609, -83.7127),  # Sylvania, OH
    "43612": (41.6882, -83.5958),  # Toledo, OH
    "43613": (41.7112, -83.6027),  # Toledo, OH
    "43614": (41.6295, -83.5911),  # Toledo, OH
    "43615": (41.6642, -83.6673),  # Toledo, OH
    "43616": (41.6868, -83.6584),  # Oregon, OH
    "43617": (41.6698, -83.7404),  # Toledo, OH
    "43623": (41.7073, -83.6576),  # Toledo, OH
}


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _distance_from_tecumseh(zip_code: str) -> float | None:
    coords = _ZIP_COORDS.get(zip_code)
    if not coords:
        return None
    return _haversine_miles(TECUMSEH_LAT, TECUMSEH_LON, coords[0], coords[1])


def _fetch_sales() -> list[dict]:
    try:
        resp = _session.get(SEARCH_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [estatesales] Fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    sales = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") != "SaleEvent":
                continue

            name = item.get("name", "Estate Sale")
            url = item.get("url", SEARCH_URL)
            start_raw = item.get("startDate")
            end_raw = item.get("endDate")

            loc = item.get("location", {})
            addr = loc.get("address", {})
            street = addr.get("streetAddress", "")
            city = addr.get("addressLocality", "")
            state = addr.get("addressRegion", "")
            zip_code = addr.get("postalCode", "")
            location_str = ", ".join(filter(None, [street, f"{city}, {state} {zip_code}".strip()]))

            organizer_name = item.get("organizer", {}).get("name", "")

            sales.append({
                "name": name,
                "url": url,
                "start_raw": start_raw,
                "end_raw": end_raw,
                "location_str": location_str,
                "zip_code": zip_code,
                "organizer": organizer_name,
            })

    return sales


class EstateSalesCollector(BaseCollector):

    def __init__(self, config: dict):
        self.config = config

    @property
    def source_name(self) -> str:
        return "estatesales"

    def collect(self, today: date, lookahead_days: int = 7) -> list[Event]:
        import cache

        cached = cache.get("estatesales", ttl_seconds=3600 * 6)
        if cached:
            return [Event(**e) for e in cached]

        cutoff = today + timedelta(days=lookahead_days)
        raw_sales = _fetch_sales()
        events: list[Event] = []

        for raw in raw_sales:
            # Distance filter — skip if no zip, unknown zip, or beyond MAX_MILES
            zip_code = raw["zip_code"]
            if not zip_code:
                continue
            dist = _distance_from_tecumseh(zip_code)
            if dist is None or dist > MAX_MILES:
                continue

            # Parse ISO 8601 dates from the JSON-LD
            try:
                start_dt = dateparser.parse(raw["start_raw"]).replace(tzinfo=timezone.utc) if raw.get("start_raw") else None
                end_dt = dateparser.parse(raw["end_raw"]).replace(tzinfo=timezone.utc) if raw.get("end_raw") else None
            except Exception:
                continue

            if not start_dt:
                continue

            sd = start_dt.astimezone(LOCAL_TZ).date()
            ed = end_dt.astimezone(LOCAL_TZ).date() if end_dt else sd

            # Include multi-day sales that are still ongoing
            if ed < today - timedelta(days=1) or sd > cutoff:
                continue

            organizer = raw.get("organizer", "")
            dist_str = f"{dist:.0f} mi" if dist is not None else None
            subtitle_parts = [f"by {organizer}" if organizer else None, dist_str]
            subtitle = " · ".join(p for p in subtitle_parts if p) or None

            events.append(Event(
                id=f"estatesales:{uuid.uuid5(uuid.NAMESPACE_URL, raw['url'])}",
                title=raw["name"],
                category=EventCategory.ESTATE_SALES,
                start=start_dt,
                end=end_dt,
                location=raw["location_str"] or "Tecumseh area, MI",
                source="estatesales",
                url=raw["url"],
                subtitle=subtitle,
                tags=["local", "estate-sale"],
            ))

        return events
