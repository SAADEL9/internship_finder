"""Relevance filtering and scoring."""
from datetime import timedelta

from extractors import Job, Matcher, now_utc
from scraper import is_relevant, score_job


MATCHER = Matcher(
    locations=["casablanca", "morocco"],
    internship_terms=["stage", "internship", "intern", "pfe", "stagiaire"],
    skills=["java", "react", "python", "spring"],
    exclude_title_terms=["senior", "manager", "commercial"],
)


def job(title="Stage Développeur", company="ACME", location="Casablanca",
        summary="Java Spring stage PFE", url="https://x.com/j/1") -> Job:
    return Job("test", title, company, location, url, summary=summary)


def test_relevant_job_passes():
    assert is_relevant(job(), MATCHER)


def test_requires_location():
    j = job(location="Paris", summary="Java stage")
    assert not is_relevant(j, MATCHER)


def test_requires_term_or_skill():
    j = job(title="CDI Chef cuisinier", summary="Restaurant à Casablanca")
    assert not is_relevant(j, MATCHER)


def test_relaxed_skill_only_match():
    j = job(title="Développeur React", summary="React frontend à Casablanca, CDI junior")
    assert is_relevant(j, MATCHER)  # location + skill, no internship term


def test_international_not_treated_as_intern():
    j = job(title="International Relations Officer", summary="International trade, Casablanca, CDI")
    assert not is_relevant(j, MATCHER)


def test_excluded_title_without_term():
    j = job(title="Senior Commercial Officer", summary="Casablanca, vente, stage possible")
    # exclusion matches title, title has no internship term -> dropped
    assert not is_relevant(j, MATCHER)


def test_excluded_title_with_term_still_passes():
    j = job(title="Stage Commercial Casablanca", summary="Java tools")
    assert is_relevant(j, MATCHER)  # title contains "stage"


def test_scoring_bounds_and_fields():
    j = job(title="Stage PFE Développeur Java Spring", summary="React aussi", location="Casablanca")
    j.posted_at = now_utc() - timedelta(hours=3)
    score_job(j, MATCHER, priority_hours=24, max_age_days=14)
    assert 0 <= j.match_score <= 100
    assert j.freshness_rank == 0
    assert set(j.matched_skills) >= {"java", "spring"}
    assert "internship term" in j.match_reason
    assert "skills" in j.match_reason


def test_scoring_old_job_rank9():
    j = job()
    j.posted_at = now_utc() - timedelta(days=10)
    score_job(j, MATCHER, priority_hours=24, max_age_days=14)
    assert j.freshness_rank == 1
    j.posted_at = now_utc() - timedelta(days=30)
    score_job(j, MATCHER, priority_hours=24, max_age_days=14)
    assert j.freshness_rank == 9


def test_scoring_unknown_date():
    j = job()
    j.posted_at = None
    score_job(j, MATCHER, priority_hours=24, max_age_days=14)
    assert j.freshness_rank == 2
    assert "Unknown" in j.match_reason
