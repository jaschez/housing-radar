# housing-radar

A self-hosted radar for homes to buy or rent. Every morning it sweeps the listing portals
you configure, keeps a complete history in SQLite, discards what breaks your criteria,
has Claude evaluate the most promising new listings (location, value for money, red flags)
and e-mails you only when something deserves a look. During the day, a fast cadence
e-mails new matches within the half hour.

It ships with sources for the Spanish market (pisos.com, Fotocasa, Idealista). The
housing market works differently in every country, so sources are plugins: nothing else
in the radar knows about any portal.

```
 sources ──► fetcher chain ──► level-0 wall detector ──► geographic guard ──► hard filter
 (plugins)   (plugins)                                                         │
                                                                               ▼
 e-mail ◄── report ◄── Claude evaluation (top N/day) ◄── pre-score ◄── SQLite history
                                                                     (prices, duplicates,
                                                                      withdrawals)
```

## What it does

- **Complete history.** Rows are never deleted. Every price change goes to `price_history`;
  a listing that a *complete* sweep stops seeing twice, on different days, becomes
  `Withdrawn` (and comes back if it reappears).
- **Hard filter without an LLM.** Budget, floor area, bedrooms, property type, elevator and
  text exclusions (bare ownership, occupied, auctions, room rentals...) with word-prefix
  matching that understands negation ("no necesita reforma" is not "necesita reforma").
  Missing data never discards a listing: it is marked *to confirm*.
- **Duplicates across portals.** Identical figures merge; similar figures (±3 m², ±3 %)
  merge only when the coordinates are within 250 m.
- **Zone context.** Price per m² against the median of comparable listings (flats against
  flats, houses against houses) in the same district or locality. Suspiciously cheap
  (>45 % below) lowers the score instead of raising it.
- **Evaluation with Claude.** The best pre-scored pending listings (25/day by default, in
  turns per area and operation) get a fit, a priority and short assessments of the
  location and the value, in English or Spanish.
- **Polite and honest network access.** A per-host gap between requests and a level-0
  detector on every response: a bot-manager wall is recorded as such instead of being
  stored as an empty result, and it is not retried.

## Quick start

```bash
git clone https://github.com/jaschez/housing-radar && cd housing-radar
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp -r examples/workspace ~/my-housing-radar          # your criteria and history live here
cp ~/my-housing-radar/secrets.env.example ~/my-housing-radar/secrets.env
$EDITOR ~/my-housing-radar/config.yaml ~/my-housing-radar/secrets.env
export RADAR_WORKSPACE=~/my-housing-radar

.venv/bin/python radar/run.py --max-pages 1           # a short first sweep to check the setup
scripts/daily.sh                                      # the full daily run: sweep, evaluate, e-mail
scripts/install-systemd.sh ~/my-housing-radar --enable   # daily at 08:00 + alerts every 30 min
```

Python 3.10+. The evaluation needs either the Claude Code CLI (`claude`) or an Anthropic
API key; with `evaluation.backend: none` the radar still filters, scores and e-mails.

## The workspace

The code is shared; everything personal lives in a workspace directory, selected with
`RADAR_WORKSPACE` or `--workspace` (default: `./workspace`, which is git-ignored).

| Path | What |
|---|---|
| `config.yaml` | criteria, areas, exclusions, sources, notifications ([example](examples/workspace/config.yaml)) |
| `secrets.env` | SMTP and API credentials ([example](examples/workspace/secrets.env.example)) |
| `plugins/` | your own sources and fetchers |
| `data/radar.db` | the history |
| `out/YYYY-MM-DD/` | `to_evaluate.json`, `evals.json`, `stats.json`, the e-mails |
| `logs/` | one log per day |

Several workspaces can share one checkout, one per person, each with its own criteria,
history and timers (`scripts/install-systemd.sh WORKSPACE NAME`).

## Sources

Built in (Spain), enabled by listing them under `sources:`:

| Source | How | Notes |
|---|---|---|
| `pisos_com` | HTML results + `ld+json`, per zone, newest first | full walk: detects withdrawals |
| `fotocasa` | JSON embedded in the results page, per locality | full walk; structured flags (occupied, bare ownership, auction...) |
| `idealista` | `alerts`: no requests (use Idealista's own e-mail alerts); `official`: the official API with a monthly quota | the website answers datacenter addresses with a bot-manager wall |

Add your own by dropping a module in the workspace and pointing the config at it:

```yaml
sources:
  my_portal:
    path: plugins/my_portal.py      # or module: some_package.portal
    base_url: https://...           # any extra keys reach the plugin as `conf`
```

The contract (one `fetch()` function, an optional `detail()`, and a `FULL_WALK` flag) is
documented in [`radar/sources/__init__.py`](radar/sources/__init__.py), with a complete
example in [`examples/plugins/example_source.py`](examples/plugins/example_source.py).

## Fetchers: the access layer

Every request goes through a per-source fetcher chain, `[direct]` by default: a plain,
polite HTTP GET. If a response is classified as a wall and the chain has another fetcher,
the next one is tried; otherwise the sweep records the wall and moves on.

```yaml
sources:
  my_portal:
    path: plugins/my_portal.py
    fetchers: [direct, {path: plugins/my_fetcher.py, any_option: 1}]
    min_gap_s: 3
```

A fetcher is a module with `get(url, *, params, timeout, options)`; see
[`radar/fetchers/__init__.py`](radar/fetchers/__init__.py) and the caching example in
[`examples/plugins/cached_fetcher.py`](examples/plugins/cached_fetcher.py). The project
itself stops at level 0: it does not solve challenges or evade bot protection. If you
plug in another access layer (an authenticated API client, a browser, a data provider),
make sure you are allowed to use it on that site.

## Evaluation and e-mail

| Setting | Options |
|---|---|
| `evaluation.backend` | `claude-cli` (Claude Code, `model: sonnet`), `anthropic-api` (`model: claude-opus-5-5`, key in `secrets.env`), `none` |
| `evaluation.notes` | market knowledge appended to the prompt |
| `notify.backend` | `smtp`, `claude-gmail` (Claude Code with the Gmail connector), `file` |
| `language` | `en`, `es` (e-mails and evaluation text; add more in `radar/i18n.py`) |

The model answers with JSON that is validated before it touches the database: unknown
ids and malformed entries are dropped, and an unparseable answer is kept in
`evaluate.raw.txt` instead of being half-applied. Listings are marked as notified only
after the e-mail is confirmed sent.

## Commands

| Command | What |
|---|---|
| `scripts/daily.sh` | sweep → evaluation → e-mail; idempotent within the day |
| `scripts/watch.sh` | first page of each search → hard filter → e-mail |
| `radar/run.py --skip-sweep` | re-score and re-pick today's batch without downloading (after a config change) |
| `radar/run.py --max-pages 1 --sources fotocasa` | a quick partial sweep |
| `radar/evaluate.py`, `radar/report.py daily`, `radar/notify.py PREFIX` | the daily steps, one by one |

## Design decisions

- **Full daily sweep instead of incremental.** A few hundred polite requests (about 20
  minutes for two Spanish metro areas). It is the only way to see price drops (date-sorted
  results do not move when the price changes) and to call a listing withdrawn.
- **Withdrawn needs two absences on different days.** Paginating a date-sorted list while
  listings are published and removed can skip one.
- **Fast alerts only announce what they saw first**, never before the first daily sweep
  (without history everything looks new), and above a configurable score per operation.
- **The zone median only counts what passed the filter**: single-room rentals and
  bare-ownership sales dragged one city's rent median to 4.4 €/m².
- **Two notification markers** (`notified_at`, `watch_notified_at`): an alert from the fast
  cadence must not stop the evaluated listing from reaching the daily e-mail.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The fixtures in `tests/fixtures/` are real pages captured on 2026-10-10, trimmed and with
vendor tokens redacted (see [`tests/fixtures/README.md`](tests/fixtures/README.md)). The
`anthropic-api` backend and the Idealista `official` backend are covered by tests with
mocked clients only.

## License

MIT
