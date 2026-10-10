"""Example fetcher plugin: the `direct` fetcher with an on-disk cache, handy while you
develop a source plugin and want to re-run it without hitting the site again.

    sources:
      my_portal:
        path: plugins/my_portal.py
        fetchers: [{path: plugins/cached_fetcher.py, ttl_s: 3600}]

A fetcher plugin exposes get(url, *, params, timeout, options) and returns an object with
.status_code, .text, .headers and .url. The chain around it (radar/fetchers/__init__.py)
adds the politeness gap and runs the wall detector on whatever this returns. Respect each
site's terms of use in anything you plug in here.
"""
import hashlib
import json
import time
from types import SimpleNamespace

import workspace
from fetchers import direct


def get(url, *, params=None, timeout=30, options=None):
    opts = options or {}
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
    path = workspace.root() / "cache" / f"{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < opts.get("ttl_s", 3600):
        d = json.loads(path.read_text())
        return SimpleNamespace(**d)
    r = direct.get(url, params=params, timeout=timeout, options=opts)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"status_code": r.status_code, "text": r.text, "headers": dict(r.headers), "url": r.url}
    path.write_text(json.dumps(d))
    return SimpleNamespace(**d)
