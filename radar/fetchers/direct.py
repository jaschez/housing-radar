"""The default fetcher: a plain HTTP GET with a browser-like User-Agent, no cookies kept
between runs, no JavaScript. Options: `user_agent`, `headers` (merged over the defaults).
"""
from __future__ import annotations

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

_session = requests.Session()


def get(url, *, params=None, timeout=30, options=None):
    opts = options or {}
    headers = {**HEADERS, **(opts.get("headers") or {})}
    if opts.get("user_agent"):
        headers["User-Agent"] = opts["user_agent"]
    return _session.get(url, params=params, timeout=timeout, headers=headers)
