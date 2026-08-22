"""Internship Finder: scrape Moroccan job sources, filter for SWE internships,
rank, deduplicate and notify Discord.

Pipeline: config.yml -> build fetch tasks (one per source x query, static pages
once) -> polite parallel fetch -> per-source extraction -> relevance filter ->
scoring -> dedupe against seen_jobs.json -> Discord embeds -> persist state.

CLI:
    python scraper.py [--debug] [--dry-run] [--sources a,b] [--limit-queries N]

Env: DISCORD_WEBHOOK_URL (required to actually send), DEBUG=1 (= --debug).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
import yaml
from dotenv import load_dotenv

from extractors import (
    EXTRACTORS,
    Job,
    Matcher,
    age_text,
    extract_rss_entries,
    normalize_text,
    normalize_url,
    now_utc,
)
from fetcher import HttpClient, SourceStats, fetch_all

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
SEEN_PATH = ROOT / "seen_jobs.json"
LOG_PATH = ROOT / "internship_finder.log"

STATE_RETENTION_DAYS_DEFAULT = 90


# --------------------------------------------------------------------------
# Config / state
# --------------------------------------------------------------------------

def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_seen(path: Path = SEEN_PATH) -> dict[str, str]:
    """Return {dedupe_key: first_seen_iso}. Accepts v2 dict and legacy list."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logging.warning("seen_jobs.json unreadable; starting with empty state")
        return {}
    raw = data.get("seen", {}) if isinstance(data, dict) else {}
    if isinstance(raw, list):  # legacy v1 format
        return {key: now_utc().isoformat() for key in raw}
    return dict(raw)


def save_seen(seen: dict[str, str], retention_days: int, path: Path = SEEN_PATH) -> None:
    cutoff = now_utc() - timedelta(days=retention_days)
    pruned = {k: v for k, v in seen.items() if _parse_iso(v) is None or _parse_iso(v) >= cutoff}
    if len(pruned) != len(seen):
        logging.info("Pruned %s stale seen entries (>%s days)", len(seen) - len(pruned), retention_days)
    payload = {"version": 2, "updated_at": now_utc().isoformat(), "seen": pruned}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Task building
# --------------------------------------------------------------------------

def build_tasks(config: dict[str, Any], only_sources: set[str] | None = None, limit_queries: int | None = None) -> list[dict[str, Any]]:
    """One task per (source, query). Sources with a static ``url`` produce a single
    task regardless of the query list — the v1 duplicate-homepage-fetch bug is
    impossible with this construction."""
    tasks: list[dict[str, Any]] = []
    task_id = 0
    for name, source in config.get("sources", {}).items():
        if not source.get("enabled", True):
            continue
        if only_sources and name not in only_sources:
            continue
        kind = source.get("kind", "generic")
        search_url = source.get("search_url")
        static_url = source.get("url")
        if not search_url and not static_url:
            logging.warning("Source %s has no url/search_url; skipping", name)
            continue
        queries: list[str]
        if search_url and "{query}" in search_url:
            queries = source.get("queries") or config.get("queries") or []
            if limit_queries:
                queries = queries[:limit_queries]
        else:
            queries = [""]  # static page: fetched exactly once
        for query in queries:
            url = search_url.replace("{query}", quote_plus(query)) if search_url else static_url
            tasks.append(
                {
                    "id": task_id,
                    "source": name,
                    "kind": kind,
                    "url": url,
                    "query": query,
                    "default_location": source.get("default_location", "Morocco"),
                    "company": source.get("company", ""),
                }
            )
            task_id += 1
    return tasks


# --------------------------------------------------------------------------
# Relevance & scoring
# --------------------------------------------------------------------------

def haystack_of(job: Job) -> str:
    return " ".join([job.title, job.company, job.location, job.summary])


def is_relevant(job: Job, matcher: Matcher) -> bool:
    """The job must be an internship, in the IT/software domain, in scope:
    Location AND Internship Term AND (Skill / IT-domain term), minus
    title-level exclusions (senior/manager/... unless the title says stage)."""
    haystack = haystack_of(job)
    if not matcher.matched_locations(haystack):
        return False
    if not matcher.matched_terms(haystack):
        return False
    if not matcher.matched_skills(haystack):
        return False
    if matcher.matched_excluded(job.title) and not matcher.matched_terms(job.title):
        return False
    return True


TITLE_TERM_BOOSTS = [
    (("pfe", "stage", "internship", "stagiaire", "intern"), 8),
    (("software", "developpeur", "développeur", "developer", "full stack", "full-stack",
      "backend", "backend", "frontend", "data", "devops", "cloud"), 7),
]


def score_job(job: Job, matcher: Matcher, priority_hours: int, max_age_days: int) -> None:
    haystack = haystack_of(job)
    matched_skills = matcher.matched_skills(haystack)
    matched_terms = matcher.matched_terms(haystack)
    location_match = bool(matcher.matched_locations(haystack))

    score = 0
    if matched_terms:
        score += 25
    if location_match:
        score += 20
    score += min(30, len(set(matched_skills)) * 5)

    if job.posted_at:
        age = now_utc() - job.posted_at
        if age <= timedelta(hours=priority_hours):
            score += 25
            job.freshness_rank = 0
        elif age <= timedelta(days=max_age_days):
            score += 15
            job.freshness_rank = 1
        else:
            job.freshness_rank = 9
    else:
        score += 5
        job.freshness_rank = 2

    title = job.title.lower()
    for needles, boost in TITLE_TERM_BOOSTS:
        if any(n in title for n in needles):
            score += boost
            break

    job.matched_skills = sorted(set(matched_skills))
    job.match_score = min(100, score)
    reasons: list[str] = []
    if matched_terms:
        reasons.append(f"internship term: {', '.join(sorted(set(matched_terms))[:3])}")
    if location_match:
        reasons.append("Casablanca/Morocco location")
    if matched_skills:
        reasons.append(f"skills: {', '.join(sorted(set(matched_skills))[:5])}")
    reasons.append(age_text(job.posted_at))
    job.match_reason = "; ".join(reasons)


def filter_rank_dedupe(
    jobs: list[Job],
    matcher: Matcher,
    config: dict[str, Any],
    seen: dict[str, str],
) -> list[Job]:
    search = config["search"]
    max_age = timedelta(days=search["max_age_days"])
    unique: dict[str, Job] = {}
    by_url_company: dict[tuple[str, str], Job] = {}
    relevant_count = 0
    for job in jobs:
        if not job.title or not job.url:
            continue
        if job.posted_at and now_utc() - job.posted_at > max_age:
            continue
        if not is_relevant(job, matcher):
            continue
        relevant_count += 1
        score_job(job, matcher, search["priority_hours"], search["max_age_days"])
        if job.dedupe_key in seen:
            continue
        existing = unique.get(job.dedupe_key)
        if not existing or job.match_score > existing.match_score:
            unique[job.dedupe_key] = job
    # secondary pass: the same offer can appear twice with slightly different
    # titles (e.g. rekrute listing + sidebar); collapse by URL + company
    for job in unique.values():
        key = (normalize_url(job.url), normalize_text(job.company))
        existing = by_url_company.get(key)
        if not existing or job.match_score > existing.match_score:
            by_url_company[key] = job
    logging.info("Filter summary: %s raw -> %s relevant -> %s new/unique", len(jobs), relevant_count, len(by_url_company))
    ranked = sorted(
        by_url_company.values(),
        key=lambda item: (
            item.freshness_rank,
            -(item.posted_at.timestamp() if item.posted_at else 0),
            -item.match_score,
        ),
    )
    return ranked[: search["max_results_per_run"]]


# --------------------------------------------------------------------------
# Extraction dispatch
# --------------------------------------------------------------------------

def run_extraction(client: HttpClient, tasks: list[dict[str, Any]], config: dict[str, Any],
                   stats: dict[str, SourceStats], matcher: Matcher) -> list[Job]:
    search = config["search"]
    results, fetch_stats = fetch_all(
        client,
        tasks,
        max_workers=int(search.get("max_workers", 8)),
        politeness_delay=float(search.get("politeness_delay_seconds", 0.5)),
    )
    # merge fetch stats into per-source stats
    for source, fstat in fetch_stats.items():
        stats.setdefault(source, fstat)
    jobs: list[Job] = []
    for task in tasks:
        result = results.get(task["id"])
        if result is None or not result.ok:
            continue
        source = task["source"]
        found: list[Job] = []
        try:
            if task["kind"] == "rss":
                import feedparser

                feed = feedparser.parse(result.html)
                found = extract_rss_entries(source, feed, task["default_location"])
            else:
                extractor = EXTRACTORS.get(task["kind"], EXTRACTORS["generic"])
                found = extractor(
                    source,
                    result.html,
                    result.final_url or task["url"],
                    task["default_location"],
                    company_hint=task["company"],
                )
        except Exception:
            logging.exception("Extraction failed for %s (%s)", source, task["url"])
            continue
        st = stats.setdefault(source, SourceStats())
        st.jobs_found += len(found)
        st.relevant_found += sum(1 for j in found if is_relevant(j, matcher))
        jobs.extend(found)
    return jobs


# --------------------------------------------------------------------------
# Discord notifications
# --------------------------------------------------------------------------

def truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def embed_color(job: Job) -> int:
    if job.freshness_rank == 0:
        return 0x2ECC71  # green: posted within priority window
    if job.freshness_rank == 1:
        return 0xF1C40F  # yellow: fresh-ish
    return 0x95A5A6  # grey: unknown/older


def discord_message(jobs: list[Job]) -> dict[str, Any]:
    if not jobs:
        return {
            "content": "Internship Opportunities Found",
            "embeds": [{"title": "No new matching internship opportunities found this run.", "color": 0x5865F2}],
        }
    embeds = []
    for index, job in enumerate(jobs, start=1):
        embeds.append(
            {
                "title": truncate(job.title, 256),
                "url": job.url,
                "description": truncate(job.match_reason, 300),
                "color": embed_color(job),
                "fields": [
                    {"name": "Company", "value": truncate(job.company or "Unknown Company", 200), "inline": True},
                    {"name": "Location", "value": truncate(job.location or "Casablanca/Morocco", 200), "inline": True},
                    {"name": "Posted", "value": age_text(job.posted_at), "inline": True},
                    {"name": "Match Score", "value": f"{job.match_score}/100", "inline": True},
                    {"name": "Skills", "value": truncate(", ".join(job.matched_skills) or "-", 200), "inline": True},
                    {"name": "Source", "value": truncate(job.source, 100), "inline": True},
                    {"name": "URL", "value": truncate(job.url, 400), "inline": False},
                ],
                "footer": {"text": f"#{index}"},
            }
        )
    return {"content": "Internship Opportunities Found", "embeds": embeds}


def send_discord_notification(message: dict[str, Any] | str) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logging.warning("Discord webhook URL is not configured; message not sent.")
        return False
    payload = {"content": message} if isinstance(message, str) else message
    embeds = payload.get("embeds", [])

    def post_with_retry(data: dict[str, Any]) -> bool:
        for attempt in range(3):
            try:
                response = requests.post(webhook_url, json=data, timeout=20)
                if response.status_code == 429:
                    retry_after = response.json().get("retry_after", 5)
                    logging.info("Discord rate limited; waiting %ss", retry_after)
                    time.sleep(float(retry_after))
                    continue
                response.raise_for_status()
                logging.info("Discord notification sent (HTTP %s)", response.status_code)
                return True
            except requests.RequestException as exc:
                logging.warning("Discord notification failed (attempt %s/3): %s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(2**attempt)
        return False

    if not embeds:
        return post_with_retry(payload)
    success = True
    for chunk in chunked(embeds, 10):
        if not post_with_retry({"content": payload.get("content", ""), "embeds": chunk}):
            success = False
    return success


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find SWE internships in Morocco and notify Discord")
    parser.add_argument("--debug", action="store_true", help="dump raw extracted jobs to debug_jobs.json")
    parser.add_argument("--dry-run", action="store_true", help="do everything except send the notification")
    parser.add_argument("--sources", default="", help="comma-separated source names to limit to")
    parser.add_argument("--limit-queries", type=int, default=None, help="use only the first N queries per source")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    debug = args.debug or os.getenv("DEBUG") == "1"
    load_dotenv()
    setup_logging()
    config = load_config()
    filters = config["filters"]
    matcher = Matcher(
        locations=filters["locations"],
        internship_terms=filters["internship_terms"],
        skills=filters["skills"],
        exclude_title_terms=filters.get("exclude_title_terms", []),
    )
    client = HttpClient(
        user_agent=config["search"]["user_agent"],
        timeout=int(config["search"]["request_timeout_seconds"]),
    )

    only = {s.strip() for s in args.sources.split(",") if s.strip()} or None
    tasks = build_tasks(config, only_sources=only, limit_queries=args.limit_queries)
    logging.info("Built %s fetch tasks across %s sources", len(tasks), len({t['source'] for t in tasks}))

    seen = load_seen()
    logging.info("Starting internship search with %s seen keys", len(seen))

    stats: dict[str, SourceStats] = {}
    started = time.monotonic()
    jobs = run_extraction(client, tasks, config, stats, matcher)
    ranked = filter_rank_dedupe(jobs, matcher, config, seen)

    if debug:
        dump = ROOT / "debug_jobs.json"
        dump.write_text(
            json.dumps([j.to_dict() for j in jobs], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logging.info("DEBUG: dumped %s raw jobs to %s", len(jobs), dump)

    logging.info("--- Per-source breakdown ---")
    for source in sorted(stats):
        st = stats[source]
        notes = f" ({'; '.join(st.notes)})" if st.notes else ""
        logging.info(
            "  %s: %s requests, %s failed, %s jobs extracted, %s relevant%s",
            source, st.requests, st.failures, st.jobs_found, st.relevant_found, notes,
        )
    logging.info("--- Final summary ---")
    logging.info("Total raw jobs: %s | new matching jobs: %s | elapsed %.1fs",
                 len(jobs), len(ranked), time.monotonic() - started)

    sent = False
    if not args.dry_run:
        sent = send_discord_notification(discord_message(ranked))
    else:
        logging.info("Dry run: notification skipped")

    if ranked and sent:
        for job in ranked:
            seen[job.dedupe_key] = now_utc().isoformat()
    if not args.dry_run:
        retention = int(config["search"].get("state_retention_days", STATE_RETENTION_DAYS_DEFAULT))
        save_seen(seen, retention)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
