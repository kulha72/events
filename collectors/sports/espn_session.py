"""
Shared HTTP setup for ESPN's public site API.

ESPN started answering 403 Forbidden to the digest's previous bot-style
User-Agent on 2026-08-05, which silently emptied the sports, playoff and
standings feeds. These headers mirror what a browser loading espn.com sends,
so the requests are no longer trivially identifiable as automated.
"""

import requests

ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espn.com/",
    "Origin": "https://www.espn.com",
    "Connection": "keep-alive",
}


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(ESPN_HEADERS)
    return session
