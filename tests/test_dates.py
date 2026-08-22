"""Date parsing and age formatting."""
from datetime import timedelta

from extractors import age_text, infer_date_from_text, now_utc, parse_date


def test_parse_rfc2822():
    parsed = parse_date("Mon, 21 Aug 2026 10:00:00 +0000")
    assert parsed is not None
    assert parsed.year == 2026 and parsed.month == 8 and parsed.day == 21


def test_parse_iso():
    parsed = parse_date("2026-08-21")
    assert parsed is not None and parsed.day == 21


def test_parse_french_relative():
    parsed = parse_date("il y a 3 jours")
    assert parsed is not None
    assert abs((now_utc() - parsed).days - 3) <= 1


def test_parse_english_relative():
    parsed = parse_date("5 hours ago")
    assert parsed is not None
    assert timedelta(hours=4) < now_utc() - parsed < timedelta(hours=6)


def test_parse_garbage_returns_none():
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("Lorem ipsum dolor sit amet") is None


def test_infer_date_from_patterns():
    assert infer_date_from_text("published 2 days ago apply now") is not None
    assert infer_date_from_text("date de publication : 21/08/2026") is not None
    assert infer_date_from_text("no dates in here at all") is None


def test_age_text():
    assert age_text(None) == "Unknown Date"
    assert "hour" in age_text(now_utc() - timedelta(minutes=30))
    assert "hours ago" in age_text(now_utc() - timedelta(hours=5))
    assert "3 days ago" in age_text(now_utc() - timedelta(days=3))
    assert "1 day ago" in age_text(now_utc() - timedelta(days=1))
