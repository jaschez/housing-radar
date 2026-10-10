"""Level 0: tell a challenge page from a content page.

The cheap half, and the one most often skipped. Getting it wrong is expensive in a
specific way: a challenge page served with status 200 has a <title>, a few links and a
plausible size, so a coverage check scores it as fine and you end up storing the wall's
scenery. The opposite also hurts: a false positive puts a browser in front of a source
that never needed one.

The tables live in signals.py. This file is only the decision procedure.
"""
from __future__ import annotations

import re

from signals import BLOCK_CODES, CAPTCHA_IMAGE, HEADERS, STRONG, WEAK

ANCHOR = re.compile(r"<a\s[^>]*href=", re.IGNORECASE)
META_REFRESH = re.compile(r"<meta[^>]+http-equiv=[\"']?refresh[\"']?", re.IGNORECASE)

# Tags whose content is markup machinery, not page text.
BLOCK_TAGS = frozenset({"script", "style", "noscript", "template"})

# Threshold for "there is almost nothing here to navigate to".
EMPTY_ANCHORS = 5
EMPTY_TEXT_LEN = 1500

_TAG_NAME = re.compile(r"^([a-z][a-z0-9]*)")


def extract_text(src: str) -> str:
    """Visible text of the document in ONE linear pass.

    This used to be three regexes (strip comments, strip script/style, strip the remaining
    tags), each over a lazy [\\s\\S]*?. That is quadratic on unterminated input: every
    `<!--` without `-->` rescans to the end, so a page serving `<!--` 20,000 times burned
    1.6 s of CPU. The detector runs on every response of every sweep, and the body is
    chosen by the crawled site.

    A forward scan has no backtracking to exploit: `i` only moves forward, so total work
    is bounded by the input length. Whatever is left unterminated is dropped to the end,
    which is what a real parser would do.
    """
    out: list[str] = []
    n = len(src)
    i = 0
    skip_until: str | None = None

    while i < n:
        lt = src.find("<", i)
        if lt == -1:
            if not skip_until:
                out.append(src[i:])
            break
        if not skip_until and lt > i:
            out.append(src[i:lt])

        if src.startswith("<!--", lt):
            end = src.find("-->", lt + 4)
            if end == -1:
                break
            i = end + 3
            continue

        gt = src.find(">", lt)
        if gt == -1:
            break

        # Only the tag name needs case folding; the attribute span can be huge.
        head = src[lt + 1:min(gt, lt + 12)].lower()
        if skip_until:
            if head.startswith("/" + skip_until):
                skip_until = None
        else:
            m = _TAG_NAME.match(head)
            if m and m.group(1) in BLOCK_TAGS and src[gt - 1] != "/":
                skip_until = m.group(1)
        out.append(" ")
        i = gt + 1
    return "".join(out)


def body_of(src: str) -> str:
    """The document body, found with find() rather than a lazy regex.

    `<body[^>]*>([\\s\\S]*?)</body>` retries from every `<body` when there is no closing
    tag, which is quadratic on a page that repeats `<body`.
    """
    low = src.lower()
    open_ = low.find("<body")
    if open_ == -1:
        return src
    gt = src.find(">", open_)
    if gt == -1:
        return src
    close = low.find("</body", gt)
    return src[gt + 1:] if close == -1 else src[gt + 1:close]


def shape_of(html) -> dict:
    """Structural description of the page, computed on the HTML string.

    Measuring it in a browser DOM would be more correct, but detection has to run on the
    body of a plain fetch, before deciding whether a browser is warranted. Regex over
    markup is the honest trade: that is why structural signals are the LOWEST-confidence
    tier and never fire on their own.
    """
    src = "" if html is None else str(html)
    body = body_of(src)
    text = re.sub(r"\s+", " ", extract_text(body)).strip()
    anchors = len(ANCHOR.findall(body))
    return {
        "bytes": len(src),
        "anchors": anchors,
        "text_len": len(text),
        "scripts": len(re.findall(r"<script\b", body, re.IGNORECASE)),
        "iframes": len(re.findall(r"<iframe\b", body, re.IGNORECASE)),
        "noscripts": len(re.findall(r"<noscript\b", body, re.IGNORECASE)),
        "forms": len(re.findall(r"<form\b", body, re.IGNORECASE)),
        "images": len(re.findall(r"<img\b", body, re.IGNORECASE)),
        "meta_refresh": bool(META_REFRESH.search(src)),
        "empty": anchors < EMPTY_ANCHORS and len(text) < EMPTY_TEXT_LEN,
    }


def _structural_signal(shape: dict):
    """Vendor-less shells: they catch walls we have no signature for."""
    if shape["text_len"] == 0 and shape["anchors"] == 0 and shape["scripts"] == 0 and shape["iframes"] == 0:
        return "structure-blank", "no text, no links, no scripts"
    if shape["scripts"] > 0 and shape["text_len"] < 200 and shape["anchors"] == 0:
        return "structure-script-only", "the body is scripts and little else"
    if shape["iframes"] == 1 and shape["anchors"] == 0 and shape["text_len"] < 200:
        return "structure-single-iframe", "the body is a lone iframe"
    if shape["noscripts"] > 0 and shape["scripts"] == 0 and shape["text_len"] < 200 and shape["anchors"] == 0:
        return "structure-noscript-only", "the body is a lone noscript"
    if shape["meta_refresh"] and shape["anchors"] == 0:
        return "structure-meta-refresh", "meta refresh with nothing to read"
    return None


def header_lines(headers) -> list[str]:
    """Flatten any header container into lowercased "name: value" lines.

    Accepts a dict whose values may be lists, which is how most clients deliver
    `set-cookie`.
    """
    if not headers:
        return []
    out = []
    items = headers.items() if hasattr(headers, "items") else headers
    for k, v in items:
        for one in (v if isinstance(v, (list, tuple)) else [v]):
            out.append(f"{k}: {one}".lower())
    return out


def _match_headers(lines, want_definitive):
    for vendor, signal, rx, definitive in HEADERS:
        if definitive is not want_definitive:
            continue
        if any(rx.search(line) for line in lines):
            return vendor, signal
    return None


def _first_match(table, html):
    for vendor, signal, rx in table:
        if rx.search(html):
            return vendor, signal
    return None


def detect(html: str = "", status: int | None = None, headers=None) -> dict:
    src = "" if html is None else str(html)
    lines = header_lines(headers)
    shape = shape_of(src)

    def clear():
        return {"challenged": False, "kind": "content", "vendor": None, "signal": None,
                "tier": None, "confidence": 0.0, "status": status, "shape": shape}

    def walled(vendor, signal, tier, confidence, note=None):
        kind = "captcha" if _first_match(CAPTCHA_IMAGE, src) else "js-challenge"
        v = {"challenged": True, "kind": kind, "vendor": vendor, "signal": signal,
             "tier": tier, "confidence": confidence, "status": status, "shape": shape}
        if note:
            v["note"] = note
        return v

    # 1. Strong: title and instruction text. Fire on any page shape.
    hit = _first_match(STRONG, src)
    if hit:
        return walled(hit[0], hit[1], "strong", 0.95)

    # 2. Definitive headers: the vendor saying it mitigated.
    hard = _match_headers(lines, True)
    if hard:
        return walled(hard[0], hard[1], "header", 0.95)

    # Everything below requires a page without content. This guard IS the design: bot
    # managers inject their script into every page they serve, healthy ones included.
    if not shape["empty"]:
        return clear()

    # 3. Suggestive headers on an empty page.
    soft = _match_headers(lines, False)
    if soft:
        return walled(soft[0], soft[1], "header", 0.8)

    # 4. Vendor resources on an empty page.
    weak = _first_match(WEAK, src)
    if weak:
        return walled(weak[0], weak[1], "weak", 0.8)

    # 5. A blocking status with nothing to read: a refusal, not a challenge. A browser
    #    does not fix this; another session might. It goes BEFORE the structural signal
    #    because "403 with an empty body" describes a block better than an odd structure,
    #    and because the action that follows is different.
    if status is not None and status in BLOCK_CODES:
        return {"challenged": True, "kind": "block", "vendor": "unknown",
                "signal": f"status-{status}", "tier": "status", "confidence": 0.7,
                "status": status, "shape": shape}

    # 6. Shape only: an unknown wall.
    structural = _structural_signal(shape)
    if structural:
        return walled("unknown", structural[0], "structural", 0.6, note=structural[1])

    return clear()


def wants_browser(verdict: dict) -> bool:
    """True when a browser would be the next step (as opposed to another session).

    The built-in fetchers stop at level 0: this exists so the log says why a source gave
    no data, and so a fetcher plugin can decide whether it is the right next layer.
    """
    return verdict["challenged"] and verdict["kind"] in ("js-challenge", "captcha")
