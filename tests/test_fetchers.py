"""The fetcher chain: politeness, level 0 on every response, retries and fallthrough."""
from pathlib import Path

import pytest

import fetchers

FIX = Path(__file__).parent / "fixtures"
HEALTHY = "<html><body>" + "".join(f"<a href='/{i}'>x</a>" for i in range(20)) + "content</body></html>"


class Resp:
    def __init__(self, text, status, headers=None, url="https://site.test/"):
        self.text, self.status_code, self.headers, self.url = text, status, headers or {}, url


class Scripted:
    """A fetcher module stand-in that answers from a list."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def get(self, url, *, params=None, timeout=30, options=None):
        self.calls += 1
        return self.answers.pop(0)


def client_with(*fakes, monkeypatch):
    c = fetchers.Client(min_gap_s=0, jitter_s=0)
    c.chain = [(f"fake{i}", {}, f) for i, f in enumerate(fakes)]
    monkeypatch.setattr(fetchers.time, "sleep", lambda s: None)
    return c


def test_a_challenge_is_not_retried_with_the_same_fetcher(monkeypatch):
    wall = Scripted([Resp((FIX / "datadome-idealista.html").read_text(), 403)] * 3)
    c = client_with(wall, monkeypatch=monkeypatch)
    with pytest.raises(fetchers.Challenged) as e:
        c.get("https://site.test/a")
    assert wall.calls == 1 and e.value.verdict["vendor"] == "datadome"


def test_a_wall_falls_through_to_the_next_fetcher(monkeypatch):
    wall = Scripted([Resp((FIX / "datadome-idealista.html").read_text(), 403)])
    other = Scripted([Resp(HEALTHY, 200)])
    c = client_with(wall, other, monkeypatch=monkeypatch)
    assert c.get("https://site.test/a").status_code == 200
    assert (wall.calls, other.calls) == (1, 1)


def test_an_empty_503_is_retried_as_transient(monkeypatch):
    f = Scripted([Resp("", 503), Resp(HEALTHY, 200)])
    assert client_with(f, monkeypatch=monkeypatch).get("https://site.test/a").status_code == 200


def test_a_persistent_empty_503_ends_as_a_block(monkeypatch):
    f = Scripted([Resp("", 503)] * 3)
    with pytest.raises(fetchers.Challenged) as e:
        client_with(f, monkeypatch=monkeypatch).get("https://site.test/a")
    assert e.value.verdict["kind"] == "block" and f.calls == 3


def test_an_allowed_404_is_returned(monkeypatch):
    f = Scripted([Resp(HEALTHY, 404)])
    assert client_with(f, monkeypatch=monkeypatch).get("https://site.test/a", allow_status=(200, 404)).status_code == 404


def test_an_unexpected_status_raises_fetch_error(monkeypatch):
    f = Scripted([Resp(HEALTHY, 404)])
    with pytest.raises(fetchers.FetchError):
        client_with(f, monkeypatch=monkeypatch).get("https://site.test/a")


def test_a_fetcher_plugin_loads_from_the_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "plugins").mkdir(parents=True)
    (ws / "plugins" / "canned.py").write_text(
        "class R:\n    status_code = 200\n    headers = {}\n    url = 'x'\n"
        f"    text = {HEALTHY!r}\n"
        "def get(url, *, params=None, timeout=30, options=None):\n    r = R(); r.url = options['tag']; return r\n")
    monkeypatch.setenv("RADAR_WORKSPACE", str(ws))
    c = fetchers.client_for({"fetchers": [{"path": "plugins/canned.py", "tag": "from-plugin"}], "min_gap_s": 0})
    assert c.get("https://site.test/a").url == "from-plugin"
