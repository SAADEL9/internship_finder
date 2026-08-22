"""Relevance filtering and scoring."""
from datetime import timedelta
from pathlib import Path

import yaml
from extractors import Job, Matcher, now_utc
from scraper import is_relevant, score_job

# The user-facing scenarios below run against the REAL config lists, so a
# config edit that breaks matching (e.g. removing "informatique") fails tests.
CONFIG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yml").read_text(encoding="utf-8"))
MATCHER = Matcher(
    locations=CONFIG["filters"]["locations"],
    internship_terms=CONFIG["filters"]["internship_terms"],
    skills=CONFIG["filters"]["skills"],
    exclude_title_terms=CONFIG["filters"].get("exclude_title_terms", []),
)


def job(title="Stage Développeur", company="ACME", location="Casablanca",
        summary="Java Spring stage PFE", url="https://x.com/j/1") -> Job:
    return Job("test", title, company, location, url, summary=summary)


def test_relevant_job_passes():
    assert is_relevant(job(), MATCHER)


def test_requires_location():
    j = job(location="Paris", summary="Java stage")
    assert not is_relevant(j, MATCHER)


def test_requires_skill_or_it_domain():
    # non-IT internships must NOT pass
    j = job(title="Stage RH - 6 mois", summary="Ressources humaines à Casablanca")
    assert not is_relevant(j, MATCHER)


def test_requires_internship_term():
    # non-internship IT jobs must NOT pass
    j = job(title="Développeur React", summary="React frontend à Casablanca, CDI junior")
    assert not is_relevant(j, MATCHER)


def test_stage_plus_it_passes():
    j = job(title="Stage PFE Développeur Java", summary="stage java casablanca")
    assert is_relevant(j, MATCHER)


def test_pre_embauche_variants_pass():
    for title in ("Stage pré-embauche Développeur Informatique",
                  "Stage pré embauche Java Casablanca",
                  "Stagiaire pre-embauche Spring Boot"):
        assert is_relevant(job(title=title, summary="développeur"), MATCHER), title


def test_stage_6_mois_informatique_passes():
    j = job(title="Stage 6 mois Informatique", summary="stage de 6 mois développement web Casablanca")
    assert is_relevant(j, MATCHER)


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
