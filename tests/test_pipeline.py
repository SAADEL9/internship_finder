"""Task building, dedupe pipeline and Discord payload construction."""
from datetime import timedelta

import yaml
from extractors import Job, Matcher, now_utc
from scraper import build_tasks, chunked, discord_message, filter_rank_dedupe, truncate

CONFIG = yaml.safe_load(
    """
search:
  max_age_days: 14
  priority_hours: 24
  max_results_per_run: 40
queries: ["stage informatique", "stage java"]
sources:
  board:
    enabled: true
    kind: wordpress
    search_url: "https://board.example/?s={query}"
    default_location: "Morocco"
  static_page:
    enabled: true
    kind: generic
    url: "https://static.example/jobs"
    default_location: "Morocco"
  disabled_one:
    enabled: false
    kind: generic
    url: "https://off.example/"
"""
)

MATCHER = Matcher(
    locations=["casablanca", "morocco"],
    internship_terms=["stage", "pfe"],
    skills=["java", "react"],
)


def test_query_sources_get_one_task_per_query():
    tasks = build_tasks(CONFIG)
    board = [t for t in tasks if t["source"] == "board"]
    assert len(board) == 2
    assert board[0]["url"].endswith("stage+informatique") or "stage+informatique" in board[0]["url"]


def test_static_source_fetched_once_not_once_per_query():
    """Regression test for the v1 bug where param-less sources were fetched
    once per query (28 identical homepage hits per run)."""
    tasks = build_tasks(CONFIG)
    static = [t for t in tasks if t["source"] == "static_page"]
    assert len(static) == 1


def test_disabled_and_filtered_sources_excluded():
    tasks = build_tasks(CONFIG)
    assert all(t["source"] != "disabled_one" for t in tasks)
    only = build_tasks(CONFIG, only_sources={"static_page"})
    assert {t["source"] for t in only} == {"static_page"}


def test_limit_queries():
    tasks = build_tasks(CONFIG, limit_queries=1)
    assert len([t for t in tasks if t["source"] == "board"]) == 1


def test_filter_rank_dedupe_drops_seen_and_old():
    fresh = Job("s", "Stage Java", "A", "Casablanca", "https://x.com/1",
                posted_at=now_utc() - timedelta(hours=2), summary="stage java casablanca")
    seen_job = Job("s", "Stage React", "B", "Casablanca", "https://x.com/2",
                   posted_at=now_utc() - timedelta(hours=2), summary="stage react casablanca")
    old = Job("s", "Stage Java Old", "C", "Casablanca", "https://x.com/3",
              posted_at=now_utc() - timedelta(days=30), summary="stage java casablanca")
    seen = {seen_job.dedupe_key: now_utc().isoformat()}
    result = filter_rank_dedupe([fresh, seen_job, old], MATCHER, CONFIG, seen)
    assert [j.url for j in result] == [fresh.url]


def test_filter_keeps_best_duplicate():
    a = Job("s", "Stage Java", "A", "Casablanca", "https://x.com/1",
            posted_at=now_utc() - timedelta(hours=2), summary="stage java casablanca react")
    b = Job("s2", "Stage Java", "A", "Casablanca", "https://x.com/1",
            posted_at=now_utc() - timedelta(hours=3), summary="stage java")
    result = filter_rank_dedupe([b, a], MATCHER, CONFIG, {})
    assert len(result) == 1


def test_filter_collapses_same_url_different_title():
    """Same offer listed twice with slightly different titles (rekrute listing
    vs sidebar) must collapse to one entry."""
    a = Job("s", "Graphic Designer en CDD", "cnexia", "Casablanca", "https://x.com/offre-1.html",
            summary="graphic designer cdd casablanca stage java informatique")
    b = Job("s", "Graphic Designer en CDD  Casablanca", "cnexia", "Casablanca", "https://x.com/offre-1.html",
            summary="graphic designer cdd casablanca stage java informatique")
    result = filter_rank_dedupe([a, b], MATCHER, CONFIG, {})
    assert len({j.url for j in result}) == 1


def test_undated_job_with_no_date_on_page_is_dropped():
    """If we fetched the offer page and it exposes no date at all, freshness
    can't be proven -> dropped ("only latest posts" policy)."""
    j = Job("s", "Stage Java", "A", "Casablanca", "https://x.com/offre-9.html",
            summary="stage java casablanca")
    result = filter_rank_dedupe([j], MATCHER, CONFIG, {}, undated_no_date={"https://x.com/offre-9.html"})
    assert result == []


def test_no_date_drop_matches_full_url_with_query():
    """Offer ids live in the query string (talent.com /view?id=...); the
    no-date set must match on the full URL, not the query-stripped one."""
    j = Job("s", "Stage Java", "A", "Casablanca", "https://x.com/view?id=123",
            summary="stage java casablanca")
    result = filter_rank_dedupe([j], MATCHER, CONFIG, {}, undated_no_date={"https://x.com/view?id=123"})
    assert result == []


def test_undated_job_whose_page_fetch_failed_is_kept():
    """Detail fetch failure (blocked/network) leaves the age unknown — the job
    stays, ranked last, instead of being silently lost."""
    j = Job("s", "Stage Java", "A", "Casablanca", "https://x.com/offre-9.html",
            summary="stage java casablanca")
    result = filter_rank_dedupe([j], MATCHER, CONFIG, {}, undated_no_date=set())
    assert len(result) == 1 and result[0].freshness_rank == 2


def test_job_date_verified_after_check_is_age_filtered():
    """verify_undated_jobs sets posted_at from the detail page; the normal
    max-age rule then applies to it (e.g. 30 days old -> dropped)."""
    from datetime import timedelta

    from extractors import now_utc as _now

    fresh = Job("s", "Stage Java Fresh", "A", "Casablanca", "https://x.com/f.html",
                summary="stage java casablanca", posted_at=_now() - timedelta(days=2))
    old = Job("s", "Stage Java Old", "A", "Casablanca", "https://x.com/o.html",
              summary="stage java casablanca", posted_at=_now() - timedelta(days=30))
    result = filter_rank_dedupe([fresh, old], MATCHER, CONFIG, {})
    assert [j.url for j in result] == [fresh.url]


def test_discord_message_empty():
    msg = discord_message([])
    assert msg["embeds"][0]["title"].startswith("No new matching")


def test_discord_message_fields_and_limits():
    jobs = [
        Job("s", "Stage " + "x" * 400, "A", "Casablanca", "https://x.com/1",
            posted_at=now_utc(), summary="stage java casablanca", matched_skills=["java", "react"])
        for _ in range(25)
    ]
    msg = discord_message(jobs)
    assert len(msg["embeds"]) == 25
    assert all(len(e["title"]) <= 256 for e in msg["embeds"])
    names = {f["name"] for f in msg["embeds"][0]["fields"]}
    assert {"Company", "Location", "Match Score", "Skills", "URL"} <= names


def test_chunked_respects_discord_embed_limit():
    assert chunked(list(range(25)), 10) == [list(range(10)), list(range(10, 20)), list(range(20, 25))]


def test_truncate():
    assert truncate("short", 10) == "short"
    out = truncate("a" * 300, 256)
    assert len(out) == 256 and out.endswith("...")
