"""Pluggable network access: every request a source makes goes through a fetcher chain.

Configured per source (default: `[direct]`):

    sources:
      fotocasa:
        fetchers: [direct]
      my_portal:
        path: plugins/my_portal.py
        fetchers: [direct, {path: plugins/my_fetcher.py, some_option: 1}]

Each response is classified by the level-0 detector (detect.py). If it is a wall and the
chain has another fetcher, the next one is tried; otherwise `Challenged` is raised and
the sweep records it in `runs.challenged` and moves on to the next source.

A fetcher is any module with

    def get(url, *, params=None, timeout=30, options=None) -> response

returning an object with `.status_code`, `.text`, `.headers` and `.url` (a
`requests.Response` qualifies). The built-in `direct` fetcher is a polite plain GET.
Anything else (an authenticated API client, a cache, a headless browser...) is a plugin
you add to your own workspace; see examples/plugins/. Respect each site's terms of use.

Three rules hold for every fetcher, because they live here and not in the plugins:

  1. Politeness: a minimum gap per host between requests (`min_gap_s`, default 2.5 s).
  2. Level 0 on every response. A challenge is not retried with the same fetcher:
     insisting only burns the address.
  3. Retry only what is transient (network errors, 429/5xx, and an empty 429/503 with no
     vendor signature, which is usually a hiccup rather than a block).
"""
from __future__ import annotations

import random
import time
from urllib.parse import urlparse

import plugins
from detect import detect

RETRY_CODES = frozenset({429, 500, 502, 503, 504})
_last: dict[str, float] = {}


class Challenged(Exception):
    """The response was a wall (JS challenge, captcha or block). Carries the verdict."""

    def __init__(self, url: str, verdict: dict):
        self.url = url
        self.verdict = verdict
        super().__init__(f"{verdict['kind']} {verdict['vendor']}/{verdict['signal']} at {url}")

    @property
    def label(self) -> str:
        v = self.verdict
        return f"{v['kind']}:{v['vendor']}:{v['signal']}"


class FetchError(Exception):
    """A non-wall response with an unexpected status, after retries."""

    def __init__(self, url: str, status: int):
        self.url, self.status = url, status
        super().__init__(f"HTTP {status} at {url}")


class Client:
    def __init__(self, chain=None, *, min_gap_s: float = 2.5, jitter_s: float = 1.5):
        specs = chain or ["direct"]
        self.chain = [(*plugins.split(s), plugins.load(s, "fetchers")) for s in specs]
        self.min_gap_s, self.jitter_s = min_gap_s, jitter_s

    def _wait(self, host: str):
        last = _last.get(host)
        if last is not None:
            pause = self.min_gap_s + random.uniform(0, self.jitter_s) - (time.monotonic() - last)
            if pause > 0:
                time.sleep(pause)
        _last[host] = time.monotonic()

    def get(self, url: str, *, params=None, timeout: float = 30, retries: int = 2, allow_status=(200,)):
        """Return the response if its status is in `allow_status` (e.g. (200, 404) to learn
        whether a listing is still online). Raise Challenged when every fetcher in the chain
        hit a wall, or FetchError for any other status once retries are exhausted."""
        host = urlparse(url).netloc
        last_wall: Challenged | None = None
        for _name, options, fetcher in self.chain:
            try:
                return self._one(fetcher, options, host, url, params, timeout, retries, allow_status)
            except Challenged as wall:
                last_wall = wall           # next fetcher in the chain, if any
        raise last_wall if last_wall else RuntimeError(f"empty fetcher chain for {url}")

    def _one(self, fetcher, options, host, url, params, timeout, retries, allow_status):
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                r = fetcher.get(url, params=params, timeout=timeout, options=options)
            except Exception as e:  # noqa: BLE001 - network errors of any client library
                last_exc = e
                time.sleep(5 * (attempt + 1))
                continue
            verdict = detect(r.text, r.status_code, dict(r.headers or {}))
            if verdict["challenged"]:
                if verdict["kind"] == "block" and r.status_code in RETRY_CODES and attempt < retries:
                    time.sleep(15 * (attempt + 1))
                    continue
                raise Challenged(getattr(r, "url", url), verdict)
            if r.status_code in allow_status:
                return r
            if r.status_code in RETRY_CODES and attempt < retries:
                time.sleep(10 * (attempt + 1))
                continue
            raise FetchError(url, r.status_code)
        raise last_exc if last_exc else RuntimeError(f"no response from {url}")


def client_for(source_conf: dict | None) -> Client:
    conf = source_conf or {}
    return Client(conf.get("fetchers"), min_gap_s=float(conf.get("min_gap_s", 2.5)))
