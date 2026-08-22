# Internship Finder

A Python scraper that finds fresh Software Engineering internships (PFE / stage) in
Casablanca & Morocco and sends new matches to a Discord channel via GitHub Actions,
twice daily.

## What It Does

- **Verified multi-source scraping**: every enabled source was probed live — only
  sources that actually return parseable HTML with plain HTTP are configured.
- **Per-source extractors**: dedicated parsers for the sites that work
  (dreamjob.ma, rekrute.com, marocannonces.com, talent.com, SuccessFactors boards),
  plus schema.org JSON-LD and CSS card heuristics as a generic fallback.
- **Precise matching**: word-boundary keyword matching (so "intern" no longer
  matches *international* and "java" no longer matches *JavaScript*), with a
  relaxed rule — Location AND (Internship Term OR Skill) — and title-level
  exclusions (senior/manager/… dropped unless the title says stage/intern).
- **Polite & parallel fetching**: one request lane per domain (sequential with a
  delay inside a domain, parallel across domains), `robots.txt` respected,
  retries with exponential backoff on 429/5xx.
- **Durable deduplication**: `seen_jobs.json` (versioned, auto-pruned after 90
  days) committed back to the repo by the workflow.
- **Rich Discord embeds** with match score, skills, freshness color coding,
  rate-limit handling and 10-embed chunking.

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Pipeline: config → fetch → extract → filter/score → dedupe → Discord |
| `extractors.py` | `Job` model, keyword `Matcher`, all HTML/RSS extractors (pure functions) |
| `fetcher.py` | Robots-aware HTTP client, per-domain polite parallel fetcher |
| `config.yml` | Keywords, queries, and source definitions |
| `seen_jobs.json` | Dedup state (v2: `{hash: first_seen_date}`) |
| `tests/` | pytest suite (matchers, extractors, state, pipeline, notifications) |
| `.github/workflows/internship-finder.yml` | Twice-daily workflow (+ tests) |

## Source Status (verified August 2026)

### Enabled — working with plain HTTP

| Source | Kind | Notes |
|---|---|---|
| dreamjob.ma | WordPress search `?s=` | Best internship coverage (was `stagiaire.ma`, now merged) |
| rekrute.com | Dedicated extractor | Search results with company + city in the offer URL/title |
| ma.talent.com | Dedicated extractor | Server-rendered results for Casablanca |
| apply.deloitte.com | SuccessFactors extractor | Global Deloitte board, Morocco hits pass the filter |
| emploi.ma | generic + RSS | 403 (Cloudflare) from some networks; works from others — failures tolerated |

### Not included, and why

| Source / group | Reason |
|---|---|
| LinkedIn, Indeed, Glassdoor | `robots.txt` disallows scraping — respected by design |
| marocannonces.com | `robots.txt` disallows everything (`Disallow: /` for `*`) |
| bayt.com | robots-disallowed search paths + hard bot-blocked (403) |
| Jooble, Monster, Jobrapido, WTTJ | Hard bot-blocked (403/empty responses) |
| novojob.com, inetum.com, ibm.com, oracle.com, DXC | JavaScript-rendered; no data in raw HTML |
| EY, Thales, Atos, Akkodis, Accenture, Stellantis careers | Phenompeople SPA; internal API gated |
| Safran, Valeo (Workday) | Unreachable hosts / unknown tenant |
| Capgemini, CGI, Maroc Telecom, OCP, Attijari, Inwi | Dead URLs (404), 403, or JS-only |

Adding a new source = one `config.yml` entry (see `kinds` at the top of the file).
If it's a plain server-rendered page, `kind: generic` needs zero code.

## Discord Webhook Setup

1. Discord channel → Settings → `Integrations` → `Webhooks` → `New Webhook`, copy the URL.
2. GitHub repo → `Settings` → `Secrets and variables` → `Actions` → new secret
   `DISCORD_WEBHOOK_URL` with that URL.

## GitHub Actions Schedule

Runs twice daily (16:00 and 20:00 Morocco time; UTC+1 except during Ramadan) and
manually via the **Actions** tab. The workflow also runs the test suite first,
uploads logs as artifacts, and commits updated `seen_jobs.json` with `[skip ci]`.

## Local Development

```bash
pip install -r requirements-dev.txt

# full run without sending anything to Discord
python scraper.py --dry-run --debug

# limit to specific sources / first N queries (fast iteration)
python scraper.py --dry-run --sources dreamjob,rekrute --limit-queries 3

# tests
python -m pytest tests/ -q
```

`--debug` (or `DEBUG=1`) dumps every raw extracted job to `debug_jobs.json` —
check it to see exactly what each source yields before filtering.

## Customization (`config.yml`)

- **`filters`** — locations, internship terms, skills, `exclude_title_terms`.
- **`queries`** — search strings used for every source with a `search_url`.
- **`sources`** — enable/disable, per-source `queries` override, `default_location`,
  `tolerate_failure` for sites that are blocked from some networks.
- **`search`** — freshness windows, result cap, parallelism, politeness delay,
  state retention.

## Known Limitations

- The scraper respects `robots.txt`; protected boards (LinkedIn/Indeed) are out
  of scope by design — their Morocco internships surface on rekrute/dreamjob anyway.
- Sites behind Cloudflare may 403 depending on the runner's network; the run
  continues and logs a per-source breakdown so you can see what's alive.
