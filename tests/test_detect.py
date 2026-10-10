"""The detector decides whether a response is a wall or content.

The first four tests use pages captured for real on 2026-10-10: they are the only ones the
detector did not write itself, and that is why they matter. The two walls come from a
datacenter address; the WAF block is reconstructed from observed text (see the comment
inside its fixture).
"""
import time
from pathlib import Path

import pytest

from detect import detect, shape_of

FIX = Path(__file__).parent / "fixtures"


def fx(name):
    return (FIX / name).read_text(encoding="utf-8", errors="ignore")


# --- real captures -------------------------------------------------------------------

def test_datadome_interstitial_is_a_wall():
    v = detect(html=fx("datadome-idealista.html"), status=403)
    assert v["challenged"] is True
    assert v["vendor"] == "datadome"


def test_waf_block_without_js_challenge_is_a_block():
    # A status refusal with a short body and no vendor signal is "block", not
    # "js-challenge". The difference matters because the action differs: there is no
    # challenge to run against a block, and a browser does not fix it.
    v = detect(html=fx("waf-cloudfront-403.html"), status=403)
    assert v["challenged"] is True
    assert v["kind"] == "block"


def test_imperva_interstitial_is_a_wall():
    v = detect(html=fx("imperva-milanuncios.html"), status=403)
    assert v["challenged"] is True
    assert v["vendor"] in ("imperva", "recaptcha", "unknown")


def test_healthy_results_page_is_not_a_wall():
    # A detector that calls this a wall leaves the radar blind.
    v = detect(html=fx("sana-pisos-com.html"), status=200)
    assert v["challenged"] is False
    assert v["kind"] == "content"


# --- the trap ------------------------------------------------------------------------

def test_vendor_script_on_a_healthy_page_does_not_fire():
    # THE TRAP: bot managers inject their script into EVERY page they serve, healthy ones
    # included. A weak signal on a page with content proves nothing.
    html = (
        "<html><head><title>Homes in Granada</title></head><body>"
        "<script src='https://js.datadome.co/tags.js'></script>"
        + "".join(f"<a href='/listing/{i}'>Flat {i}</a>" for i in range(40))
        + "<p>" + ("A 90 m2 home with an elevator in Granada. " * 60) + "</p>"
        "</body></html>"
    )
    assert detect(html=html, status=200)["challenged"] is False


# --- signal tiers --------------------------------------------------------------------

def test_definitive_header_fires_even_on_a_page_with_content():
    html = ("<html><body>" + "".join(f"<a href='/x/{i}'>x</a>" for i in range(40))
            + ("text " * 400) + "</body></html>")
    v = detect(html=html, status=503, headers={"cf-mitigated": "challenge"})
    assert v["challenged"] is True
    assert v["tier"] == "header"


def test_403_with_empty_body_is_a_block_not_a_challenge():
    v = detect(html="", status=403)
    assert v["challenged"] is True
    assert v["kind"] == "block"
    assert v["signal"] == "status-403"


def test_200_with_empty_body_is_caught_by_its_shape():
    v = detect(html="", status=200)
    assert v["challenged"] is True
    assert v["tier"] == "structural"


def test_never_classifies_by_response_size():
    # A short but navigable page is content; size does not decide.
    html = "<html><body>" + "".join(f"<a href='/i/{i}'>Flat {i}</a>" for i in range(12)) + "</body></html>"
    assert detect(html=html, status=200)["challenged"] is False


# --- robustness ----------------------------------------------------------------------

@pytest.mark.parametrize("pathological", ["<!--" * 20000, "<body" * 20000, "<script>" * 20000, "<" * 50000])
def test_pathological_inputs_do_not_trigger_redos(pathological):
    # With lazy regexes this took 1.6 s per input. The body is chosen by the crawled site,
    # so a quadratic cost here is a denial of service with its delivery mechanism included.
    t0 = time.monotonic()
    detect(html=pathological, status=200)
    assert time.monotonic() - t0 < 0.5


def test_shape_counts_structure_not_script_text():
    s = shape_of("<html><body><a href='/a'>x</a><script>var secret=1</script><iframe></iframe></body></html>")
    assert s["anchors"] == 1
    assert s["scripts"] == 1
    assert s["iframes"] == 1
    assert s["text_len"] < 10
    assert s["empty"] is True
