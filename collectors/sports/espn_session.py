"""
Shared HTTP setup for ESPN's public site API.

Every ESPN endpoint started returning 403 "Access Denied" on 2026-08-05, which
silently emptied the sports, playoff and standings feeds. scripts/espn_probe.py
tested four header sets from a GitHub Actions runner, in both orders and with
5s between requests to rule out rate limiting. The result was the same each way:

    browser UA + Accept + Referer   0/3 endpoints
    browser UA only                 0/3 endpoints
    "Mozilla/5.0 (compatible; daily-digest-espn/1.0)"   0/3   <- what we sent
    no User-Agent override          3/3 endpoints

The block keys on the User-Agent, and it is anything claiming to be Mozilla
that gets denied — the honest python-requests default is allowed through. That
is consistent with a bot manager treating a browser User-Agent attached to a
non-browser TLS fingerprint as impersonation, and scoring it worse than a
client that does not pretend to be a browser at all.

So: do not set a User-Agent here. The default requests sends is the configuration
that was verified working. If ESPN starts refusing that too, re-run the probe
before guessing — the intuitive fix was the wrong one last time.
"""

import requests


def make_session() -> requests.Session:
    return requests.Session()
