# sanctions-address-lists

Downloads official sanctions lists and extracts **only the EVM addresses each
source explicitly publishes**, keeping every source's output separate so
consumers can decide which sanctions regimes to apply.

## Scope and limitations

This is **address-only screening, not complete sanctions screening**. It does
not, and cannot, cover:

- sanctioned entities that have no published EVM address;
- name matching against sanctioned individuals/entities;
- ownership-or-control ("50% rule") aggregation;
- addresses inferred by blockchain analytics or clustering heuristics;
- any complete legal sanctions-compliance obligation.

Addresses are extracted only when a source **explicitly publishes** them —
either in a dedicated, typed field (e.g. OFAC's "Digital Currency Address -
ETH" identifiers), or as a literal `0x` + 40-hex-character token inside an
official record's own free-text fields (e.g. a designation's remarks). This
project never infers an address from an entity's name, and never enriches
data via third-party providers. See [FEASIBILITY.md](FEASIBILITY.md) for the
per-source findings behind these choices.

Accepted address format: `0x` followed by exactly 40 hexadecimal characters
(EVM address format). Addresses are normalized to lowercase.

## Supported sources

| ID | Jurisdiction | Status | Official page | Machine-readable source |
|---|---|---|---|---|
| `us-ofac` | US | Supported | [OFAC Sanctions List Service](https://ofac.treasury.gov/sanctions-list-service) | `sanctionslistservice.ofac.treas.gov/api/download/SDN.XML` |
| `eu` | EU | Supported | [EU consolidated list](https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions) | `webgate.ec.europa.eu` consolidated XML |
| `uk` | UK | Supported | [UK Sanctions List](https://www.gov.uk/government/publications/the-uk-sanctions-list) | `sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml` |
| `un` | UN | Supported | [UN SC Consolidated List](https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list) | `scsanctions.un.org/resources/xml/en/consolidated.xml` |
| `ch-seco` | CH | Supported | [SECO sanctions search](https://www.seco.admin.ch/en/searching-for-subjects-sanctions) | `sesam.search.admin.ch` consolidated XML |

All five sources are fetched, parsed, and validated against live data; a
failure in any of them fails the CLI run (`GATES_BUILD = True`) and the daily
publish job.

`ch-seco` does not work over a VPN — its host times out for VPN clients.
Disable your VPN if it fails for you. See FEASIBILITY.md for details.

None of these sources require an API key or a paid subscription.

## Installation

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Dependencies (`httpx`, `defusedxml`) are pinned with compatible version
ranges directly in `pyproject.toml`. `requirements.txt` (runtime) and
`requirements-dev.txt` (adds lint/type/test tooling, layered on top via
`-r requirements.txt`) are fully pinned, `pip freeze`-generated lock files
used by CI and the publish workflow for reproducible installs:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -e . --no-deps
```

Regenerate them after changing `pyproject.toml`'s dependencies by installing
into a clean virtualenv and re-running `pip freeze` (see the comment at the
top of each file).

## CLI usage

```bash
# Download all sources and write dist/csv, dist/json, dist/txt, dist/manifest.json
sanctions-address-lists run

# Only specific sources, custom output directory / timeout / retries
sanctions-address-lists run --source us-ofac --source uk --output-dir dist --timeout 30 --retries 3

# List registered sources and whether they gate a build failure
sanctions-address-lists list-sources

# Equivalent module invocation
python -m sanctions_address_lists run
```

By default, `run` prints one concise, color-coded status line per source
(record/address counts, and the error reason if a source failed) plus a
one-line summary — not raw request/parsing logs:

```
✓ us-ofac      success      19202 records   104 addresses
✓ eu           success       6234 records     2 addresses
✓ uk           success       6334 records     6 addresses
✓ un           success       1011 records     0 addresses
✓ ch-seco      success       8667 records     2 addresses
5/5 sources succeeded, 114 total addresses
```

- `-v` / `--verbose` — show detailed diagnostic logs instead (HTTP
  request/response internals, and every individual free-text address match
  with its `keyword_nearby` review signal — see FEASIBILITY.md).
- `-q` / `--quiet` — suppress all per-source and summary output; only
  `manifest.json` and stderr (on a bad `--source` id) carry information.
- `--no-color` — disable ANSI colors (colors are already auto-disabled when
  stdout isn't a terminal, e.g. when piped or redirected in CI).

The CLI exits non-zero if any **supported** (gating) source ends in
`"failed"` status. `"unsupported"` and `"unverified"` sources never affect
the exit code.

## Output formats

```text
dist/
    csv/{source_id}.csv
    json/{source_id}.json
    txt/{source_id}.txt
    manifest.json
```

**CSV** — one row per address-source-record relationship, deduplicated by
`(address, source_record_id, source_url)`, sorted by `(address,
source_record_id)`:

```csv
address,jurisdiction,source,source_record_id,source_url
```

**JSON** — a minimal sorted array of unique addresses: `["0x...", "0x..."]`.

**TXT** — one sorted, unique, normalized address per line (a zero-address
source produces a single blank line, never a literal empty file — GitHub's
release-asset API rejects those).

CSV/JSON/TXT files never contain timestamps or other run-specific metadata,
so identical inputs always produce byte-identical address files —
`retrieved_at` and `resolved_url` live only in `manifest.json`.

**`manifest.json`** is a JSON array with one entry per source:

```json
{
  "source_id": "us-ofac",
  "jurisdiction": "US",
  "source_url": "https://sanctionslistservice.ofac.treas.gov/api/download/SDN.XML",
  "resolved_url": "https://sanctionslistservice.ofac.treas.gov/api/download/SDN.XML",
  "retrieved_at": "2026-08-20T12:00:00+00:00",
  "content_hash": "sha256 of the raw downloaded bytes",
  "status": "success | unsupported | failed | unverified",
  "record_count": 19202,
  "address_count": 104,
  "generated_files": ["csv/us-ofac.csv", "json/us-ofac.json", "txt/us-ofac.txt"],
  "error": null
}
```

`source_url` is the stable, hardcoded endpoint used for every run (and what
appears in the CSV's `source_url` column); `resolved_url` is what was
actually fetched *this* run after redirects (e.g. the UN's time-limited
signed blob URL) — kept out of the address files specifically so they stay
reproducible.

`generated_files` is empty whenever `status` is `"failed"`, `"unsupported"`,
or `"unverified"` — no files are written for those runs, so a previous good
output for that source is never overwritten with an empty or partial one.

## Stable download URLs

The repo uses [immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases):
once published, a release's assets and tag can't be changed. So instead of
overwriting one release in place, the daily workflow publishes `dist/**` as a
new release tagged `data-YYYY-MM-DD` (UTC) whenever the lists change, and
marks it as GitHub's "Latest" release. That gives stable per-file URLs that
always resolve to the newest data:

```
https://github.com/safe-fndn/sanctions-address-lists/releases/latest/download/us-ofac.csv
https://github.com/safe-fndn/sanctions-address-lists/releases/latest/download/us-ofac.json
...
https://github.com/safe-fndn/sanctions-address-lists/releases/latest/download/manifest.json
```

Note the path order: `/releases/latest/download/…` (the release marked
"Latest"), not `/releases/download/latest/…` (a tag literally named `latest`,
which is no longer published).

Generated datasets are published as release assets rather than committed to
git history; since each release is immutable, past releases double as a
version history.

The daily job only publishes a new release when the CSV/JSON/TXT files
actually differ from the current "Latest" release — it downloads that
release's assets first and byte-compares them against the freshly generated
ones, skipping publishing entirely on a day with no address changes.
`manifest.json` is excluded from that comparison since it always contains
volatile, per-run fields; it describes the run that published its release, so
on a no-change day it is not refreshed. Whether the daily job ran (and which
sources failed) is visible in the repository's Actions tab.

### Historical snapshots

Every release stays fetchable by its tag, giving a permanent snapshot of the
lists as of that day:

```
https://github.com/safe-fndn/sanctions-address-lists/releases/download/data-2026-08-24/us-ofac.csv
```

If the lists change more than once on the same UTC day (e.g. a manual re-run),
the later release gets a time-suffixed tag such as `data-2026-08-24T153012Z`,
since published tag names can't be reused. Each release's notes list which
sources changed since the prior release.

## Update cadence

- **`publish.yml`** (main branch) runs on a daily schedule and can also be
  triggered manually via `workflow_dispatch`. It's the one that publishes a
  new release when the lists change, as described above.
- **`pr-preview.yml`** runs on every pull request: it fetches and parses all
  sources against live data, uploads the full `dist/` output (CSV/JSON/TXT +
  manifest) as a downloadable build artifact on the workflow run, and posts
  (or updates, on subsequent pushes) a single sticky comment on the PR
  summarizing each source's status/record/address counts. It does **not**
  create or touch any GitHub Release — it's purely a preview so reviewers can
  see the effect of adapter changes before merging. Note this means every
  push to a PR triggers live network calls to all government sources.

## Failure behavior

- Fetching and parsing use explicit timeouts and bounded retries with linear
  backoff (`http.py`). Parsing happens inside that single retry loop, so a
  payload that fails to parse is re-fetched like a transport error or 5xx (up
  to three attempts in total): a gzip stream cut short inside an otherwise
  complete HTTP response decodes without error and only surfaces at parse time.
- Structural validation happens in each adapter's `parse()` method (expected
  root element, expected record count > 0), not by trusting `Content-Type`
  headers — official endpoints sometimes serve `application/octet-stream`
  for XML/XLSX payloads.
- Zero **records** parsed is treated as a failure (`EmptyResultError`); zero
  **addresses** found in an otherwise well-formed dataset is a valid, correct
  result (most sources have no crypto data today — see FEASIBILITY.md).
- Output files are only written when a source's status is `"success"` — a
  failed, unsupported, or unverified run never touches (and never clobbers)
  a prior good output for that source.
- If a source fails on a run that publishes a new release, its CSV/JSON/TXT
  files are carried over unchanged from the previous release, so
  `/releases/latest/download/<file>` keeps working. The release notes name
  carried-over sources, and `manifest.json` still records them as `failed`.
- One source's failure never stops other sources from running; the CLI
  processes every registered source and reports per-source status in
  `manifest.json`, then exits non-zero only if a gating source failed.

## Adding a source adapter

1. Create `src/sanctions_address_lists/sources/<id>.py` subclassing
   `SourceAdapter` (`sources/base.py`), setting `SOURCE_ID`, `JURISDICTION`,
   `SOURCE_URL`, and `GATES_BUILD` (start with `False` until you've verified
   it against live data).
2. Implement `parse(raw: bytes) -> tuple[list[SourceRecord], list[AddressLink]]`.
   Use `sources/xmlutil.py` for namespace-agnostic XML parsing, and
   `textscan.scan_text_fields()` if the source has no dedicated crypto field
   but does have official free-text fields worth scanning.
3. Register the adapter in `sources/registry.py`.
4. Add a minimal fixture under `tests/fixtures/` derived from the real
   format, plus tests in `tests/test_sources.py` covering: a valid address
   extraction, a rejected malformed/wrong-length value, an empty-input
   failure, and a wrong-root-element failure.
5. Once you've run it successfully against the live endpoint, flip
   `GATES_BUILD` to `True`.

## Development

```bash
ruff format --check .
ruff check .
mypy src
pytest                       # unit tests only, no network
pytest -m integration        # opt-in tests against live government endpoints
```
