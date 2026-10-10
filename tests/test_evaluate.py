"""Evaluation: the prompt carries the criteria and the data; the answer is validated."""
import json
import sys
import types

import pytest

import evaluate
import llm

BATCH = [{"listing_id": "fotocasa:1", "price": 150000}, {"listing_id": "fotocasa:2", "price": 120000}]


def test_prompt_renders_criteria_notes_language_and_listings(cfg):
    c = {**cfg, "language": "es", "evaluation": {"notes": "A VPO is not a reject."}}
    p = evaluate.build_prompt(c, BATCH)
    assert "Buying: price up to 200,000 EUR" in p and "At least 70 m² and 2 bedrooms" in p
    assert "A VPO is not a reject." in p
    assert "Spanish (Spain)" in p
    assert '"listing_id": "fotocasa:2"' in p and "Listings (2)" in p
    assert "{CRITERIA}" not in p and "{LISTINGS}" not in p


def test_parse_accepts_fenced_json_and_drops_strangers():
    text = ("Here you go:\n```json\n" + json.dumps([
        {"listing_id": "fotocasa:1", "fit": "High", "priority": "P1", "summary": "ok", "extra": 1},
        {"listing_id": "fotocasa:999", "fit": "High"},          # not in today's batch
        {"listing_id": "fotocasa:2", "fit": "Maybe"},           # invalid fit
    ]) + "\n```")
    out = evaluate.parse(text, BATCH)
    assert [e["listing_id"] for e in out] == ["fotocasa:1"]
    assert "extra" not in out[0]


def test_run_writes_evals_and_is_idempotent(cfg, tmp_path, monkeypatch):
    (tmp_path / "to_evaluate.json").write_text(json.dumps(BATCH))
    calls = []
    monkeypatch.setattr(llm, "complete", lambda p, **kw: calls.append(kw) or json.dumps(
        [{"listing_id": "fotocasa:1", "fit": "Medium"}, {"listing_id": "fotocasa:2", "fit": "Reject"}]))
    c = {**cfg, "evaluation": {"backend": "claude-cli", "model": "sonnet"}}
    assert evaluate.run(c, tmp_path) == 0
    assert len(json.loads((tmp_path / "evals.json").read_text())) == 2
    assert evaluate.run(c, tmp_path) == 0 and len(calls) == 1       # evals.json exists: no second call
    assert calls[0] == {"backend": "claude-cli", "model": "sonnet"}


def test_backend_none_skips_the_call(cfg, tmp_path, monkeypatch):
    (tmp_path / "to_evaluate.json").write_text(json.dumps(BATCH))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: pytest.fail("must not call"))
    assert evaluate.run({**cfg, "evaluation": {"backend": "none"}}, tmp_path) == 0
    assert not (tmp_path / "evals.json").exists()


def test_garbage_answer_fails_without_writing(cfg, tmp_path, monkeypatch):
    (tmp_path / "to_evaluate.json").write_text(json.dumps(BATCH))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "I could not do it")
    assert evaluate.run({**cfg, "evaluation": {"backend": "claude-cli"}}, tmp_path) == 1
    assert not (tmp_path / "evals.json").exists() and (tmp_path / "evaluate.raw.txt").exists()


def test_anthropic_backend_uses_the_fallback_on_supported_models(monkeypatch):
    seen = {}

    class Msg:
        stop_reason = "end_turn"
        content = [types.SimpleNamespace(type="thinking", thinking=""), types.SimpleNamespace(type="text", text="[]")]

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return Msg()

    class Messages:
        def stream(self, **kw):
            seen.update(kw)
            return Stream()

    class Client:
        def __init__(self, **kw):
            self.messages = Messages()
            self.beta = types.SimpleNamespace(messages=Messages())

    fake = types.SimpleNamespace(Anthropic=Client, AuthenticationError=type("A", (Exception,), {}),
                                 NotFoundError=type("N", (Exception,), {}), APIStatusError=type("S", (Exception,), {}),
                                 APIConnectionError=type("C", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert llm.complete("hi", backend="anthropic-api", model="claude-opus-5-5") == "[]"
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]
    seen.clear()
    llm.complete("hi", backend="anthropic-api", model="claude-haiku-5-5")
    assert "fallbacks" not in seen
