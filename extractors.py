"""Extraction layer: turn raw HTTP responses into Job objects.

Every extractor is a pure function ``(html_or_soup, source, page_url, default_location) -> list[Job]``
so it can be unit-tested without network access.  Selectors were verified against
the live sites in August 2026; see README for the source-by-source status table.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

try:  # optional at import time so unit tests can run without the package
    import dateparser
except ImportError:  # pragma: no cover
    dateparser = None


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Job:
    source: str
    title: str
    company: str
    location: str
    url: str
    posted_at: datetime | None = None
    summary: str = ""
    match_score: int = 0
    match_reason: str = ""
    freshness_rank: int = 2
    matched_skills: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        base = "|".join(
            [
                normalize_url(self.url),
                normalize_text(self.company),
                normalize_text(self.title),
            ]
        )
        return sha256(base.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "summary": self.summary,
            "match_score": self.match_score,
            "match_reason": self.match_reason,
            "matched_skills": self.matched_skills,
        }


# --------------------------------------------------------------------------
# Text / date helpers
# --------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = re.sub(r"/+$", "", parsed.path)
    return parsed._replace(query="", fragment="", path=path).geturl().lower()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: str | None) -> datetime | None:
    """Parse RFC-2822, ISO and French/English relative dates into aware UTC."""
    if not value:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    if dateparser is None:  # pragma: no cover
        return None
    parsed = dateparser.parse(
        value,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "UTC",
            "TO_TIMEZONE": "UTC",
            "PREFER_DATES_FROM": "past",
        },
        languages=["en", "fr"],
    )
    return parsed.astimezone(timezone.utc) if parsed else None


RELATIVE_DATE_PATTERNS = [
    r"\b(?:posted|published|il y a|publi[ée]e?|date de publication)\s*[^\n,.]{0,60}",
    r"\b\d+\s+(?:hours?|days?|heures?|jours?|weeks?|semaines?)\s+ago\b",
    r"\bil y a\s+\d+\s+(?:heures?|jours?|semaines?|mois)\b",
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
]


def infer_date_from_text(text: str) -> datetime | None:
    for pattern in RELATIVE_DATE_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_date(match.group(0))
            if parsed:
                return parsed
    return None


def age_text(posted_at: datetime | None) -> str:
    if not posted_at:
        return "Unknown Date"
    delta = now_utc() - posted_at
    if delta < timedelta(hours=1):
        return "posted less than 1 hour ago"
    if delta < timedelta(days=1):
        return f"posted {int(delta.total_seconds() // 3600)} hours ago"
    days = delta.days
    return f"posted {days} day{'s' if days != 1 else ''} ago"


# --------------------------------------------------------------------------
# Keyword matching (word-boundary aware, kills "intern" -> "international")
# --------------------------------------------------------------------------

# Terms that are common words when lowercased ("IT" -> "it") must match
# case-exactly, otherwise every English summary matches them.
EXACT_CASE_TERMS = {"it"}


def term_pattern(term: str) -> re.Pattern[str]:
    """Compile a term into a word-boundary anchored pattern.

    Boundaries are only added next to alphanumeric characters so terms like
    ``C++`` or ``.NET`` still match correctly.
    """
    original = re.sub(r"\s+", " ", term or "").strip()
    if not original:
        return re.compile(r"(?!x)x")  # never-matching pattern for empty terms
    escaped = re.escape(original)
    prefix = r"\b" if original[:1].isalnum() else ""
    suffix = r"\b" if original[-1:].isalnum() else ""
    flags = 0 if original.lower() in EXACT_CASE_TERMS else re.IGNORECASE
    return re.compile(f"{prefix}{escaped}{suffix}", flags)


class Matcher:
    """Precompiled keyword sets for relevance filtering and scoring."""

    def __init__(self, locations: list[str], internship_terms: list[str],
                 skills: list[str], exclude_title_terms: list[str] | None = None) -> None:
        self.locations = [normalize_text(t) for t in locations]
        self.internship_terms = [normalize_text(t) for t in internship_terms]
        self.skills = [normalize_text(t) for t in skills]
        self.exclude_title_terms = [normalize_text(t) for t in (exclude_title_terms or [])]
        self._location = [term_pattern(t) for t in locations]
        self._terms = [term_pattern(t) for t in internship_terms]
        self._skills = [term_pattern(t) for t in skills]
        self._excluded = [term_pattern(t) for t in (exclude_title_terms or [])]

    def matched_locations(self, haystack: str) -> list[str]:
        return [t for t, p in zip(self.locations, self._location) if p.search(haystack)]

    def matched_terms(self, haystack: str) -> list[str]:
        return [t for t, p in zip(self.internship_terms, self._terms) if p.search(haystack)]

    def matched_skills(self, haystack: str) -> list[str]:
        return [t for t, p in zip(self.skills, self._skills) if p.search(haystack)]

    def matched_excluded(self, title: str) -> list[str]:
        return [t for t, p in zip(self.exclude_title_terms, self._excluded) if p.search(title)]


# --------------------------------------------------------------------------
# Generic extractors (apply to any HTML page)
# --------------------------------------------------------------------------

def soup_of(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup or "", "html.parser")


def _text(node: Any, selectors: list[str]) -> str:
    for selector in selectors:
        item = node.select_one(selector) if hasattr(node, "select_one") else None
        if item:
            value = item.get("content") or item.get("datetime") or item.get_text(" ", strip=True)
            if value:
                return value
    return ""


def extract_json_ld(soup: BeautifulSoup, source: str, page_url: str, default_location: str = "") -> list[Job]:
    """schema.org JobPosting entries embedded as JSON-LD."""
    jobs: list[Job] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in flatten_json_ld(items):
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization") or {}
            company = org.get("name") if isinstance(org, dict) else org
            location = item.get("jobLocation") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address") if isinstance(location, dict) else {}
            location_text = ""
            if isinstance(address, dict):
                location_text = " ".join(
                    part
                    for part in (str(address.get(k, "")).strip() for k in ("addressLocality", "addressRegion", "addressCountry"))
                    if part
                )
            elif isinstance(address, str):
                location_text = address
            remote = str(item.get("jobLocationType", "")).lower() == "TELECOMMUTE".lower()
            final_location = location_text.strip() or ("Remote" if remote else "") or default_location
            description = BeautifulSoup(str(item.get("description") or ""), "html.parser").get_text(" ", strip=True)
            jobs.append(
                Job(
                    source=source,
                    title=str(item.get("title") or "").strip(),
                    company=str(company or "").strip(),
                    location=final_location,
                    url=urljoin_page(page_url, str(item.get("url") or page_url)),
                    # only datePosted: validThrough is the expiry, not the publication
                    posted_at=parse_date(str(item.get("datePosted") or "")),
                    summary=description[:800],
                )
            )
    return jobs


def flatten_json_ld(items: list[Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "@graph" in item and isinstance(item["@graph"], list):
            found.extend(flatten_json_ld(item["@graph"]))
        else:
            found.append(item)
    return found


GENERIC_CARD_SELECTORS = [
    "[data-job-id]",
    "[data-testid*=job]",
    ".job_seen_beacon",
    ".jobsearch-SerpJobCard",
    ".job-card",
    ".job",
    ".offer",
    ".annonce",
    ".list-jobs .item",
    ".jobs-list .job-item",
    "div[class*='job-card']",
    "div[class*='offer-card']",
]


def extract_generic_cards(soup: BeautifulSoup, source: str, page_url: str, default_location: str = "") -> list[Job]:
    jobs: list[Job] = []
    for card in soup.select(",".join(GENERIC_CARD_SELECTORS))[:100]:
        link = card.select_one("a[href]")
        if not link:
            continue
        title = _text(card, ["h1", "h2", "h3", "[class*=title]", "[data-testid*=title]", "a[href]"])
        if len(title) < 4:
            continue
        company = _text(card, ["[class*=company]", "[data-testid*=company]", ".brand", ".recruiter"])
        location = _text(card, ["[class*=location]", "[data-testid*=location]", ".city", ".region"])
        date_value = _text(card, ["time", "[datetime]", "[class*=date]", "[class*=time]", "[class*=posted]"])
        summary = card.get_text(" ", strip=True)
        jobs.append(
            Job(
                source=source,
                title=title,
                company=company or "Unknown Company",
                location=location or default_location,
                url=urljoin_page(page_url, link.get("href", "")),
                posted_at=parse_date(date_value) or infer_date_from_text(summary),
                summary=summary[:1000],
            )
        )
    return jobs


def extract_generic(source: str, html: str, page_url: str, default_location: str = "",
                     company_hint: str = "") -> list[Job]:
    """JSON-LD first, CSS card heuristics as fallback."""
    soup = soup_of(html)
    jobs = extract_json_ld(soup, source, page_url, default_location) + extract_generic_cards(
        soup, source, page_url, default_location
    )
    if company_hint:
        for job in jobs:
            if not job.company or job.company == "Unknown Company":
                job.company = company_hint
    return jobs


# --------------------------------------------------------------------------
# Site-specific extractors (selectors verified 2026-08)
# --------------------------------------------------------------------------

def extract_wordpress(source: str, html: str, page_url: str, default_location: str = "",
                      company_hint: str = "") -> list[Job]:
    """WordPress listing pages (dreamjob.ma / stagiaire.ma family).

    Cards are ``article`` elements (Jannah/JNews themes use ``jeg_post``),
    with the title link inside an h2/h3 heading and a summary paragraph.
    """
    soup = soup_of(html)
    jobs: list[Job] = []
    for card in soup.select("article")[:60]:
        link = card.select_one("h2 a[href], h3 a[href], h1 a[href], .entry-title a[href]")
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        if len(title) < 6:
            continue
        href = link.get("href", "")
        summary_node = card.select_one(".entry-summary, .entry-content p, .jeg_post_excerpt, p")
        summary = summary_node.get_text(" ", strip=True) if summary_node else ""
        date_node = card.select_one("time[datetime], time")
        posted_at = None
        if date_node is not None:
            posted_at = parse_date(date_node.get("datetime") or date_node.get_text(" ", strip=True))
        if posted_at is None:
            posted_at = infer_date_from_text(card.get_text(" ", strip=True))
        company = company_hint
        if not company:
            m = re.search(
                r"\bchez\s+(?:la|le|les|l’|l')?\s*([A-ZÀ-Þ][\w&'’\-]*(?:\s+[A-ZÀ-Þ][\w&'’\-]*){0,3})",
                title + " " + summary,
            )
            company = m.group(1).strip() if m else ""
        if not company:
            # dreamjob headline style: "<Company> recrute/lance/ouvre ..."
            m = re.match(
                r"^([A-ZÀ-Þ0-9][\w&'’\-]*(?:\s+[A-ZÀ-Þ0-9][\w&'’\-]*){0,3})\s+"
                r"(?:recrute|lance|ouvre|propose|recherche|publie|recherche)",
                title,
            )
            company = m.group(1).strip() if m else ""
        # guard against "ADKA Auto ADKA Auto"-style doubling
        if company:
            half, rem = company[: len(company) // 2], company[len(company) // 2 :]
            if half and half.strip() == rem.strip():
                company = half.strip()
        jobs.append(
            Job(
                source=source,
                title=title,
                company=company or "Unknown Company",
                location=default_location,
                url=urljoin_page(page_url, href),
                posted_at=posted_at,
                summary=summary[:800],
            )
        )
    return jobs


def extract_rekrute(source: str, html: str, page_url: str, default_location: str = "",
                    company_hint: str = "") -> list[Job]:
    """rekrute.com search results.

    Offer links look like ``/offre-emploi-{slug}-recrutement-{company}-{city}-{id}.html``
    and the anchor text is ``Title | City (Maroc)``.
    """
    soup = soup_of(html)
    jobs: list[Job] = []
    seen_hrefs: set[str] = set()
    for link in soup.select("a[href*='/offre-emploi-']")[:80]:
        href = link.get("href", "")
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        raw_title = link.get_text(" ", strip=True)
        if len(raw_title) < 6:
            continue
        title, _, tail = raw_title.partition("|")
        title = title.strip()
        location = tail.replace("(Maroc)", "").replace("(Morocco)", "").strip() or default_location
        company = ""
        m = re.search(r"recrutement-(.+?)-\d+\.html?$", href.split("?")[0].rstrip("/"))
        if m:
            slug_parts = m.group(1).split("-")
            # drop the trailing city token(s): keep everything before the final 1-2 segments
            company = "-".join(slug_parts[:-1]).replace("-", " ").strip() if len(slug_parts) > 1 else ""
        # deliberately NOT ingesting the surrounding container text: on rekrute
        # the parent holds neighboring offers, and their words ("stage", "java")
        # would make non-internship jobs pass the relevance filter
        jobs.append(
            Job(
                source=source,
                title=title or raw_title,
                company=company or "Unknown Company",
                location=location or default_location,
                url=urljoin_page(page_url, href),
                posted_at=None,
                summary=raw_title,
            )
        )
    return jobs


def extract_marocannonces(source: str, html: str, page_url: str, default_location: str = "",
                          company_hint: str = "") -> list[Job]:
    """marocannonces.com listings: ``article.listing`` rows with ``/annonce/{id}/`` links."""
    soup = soup_of(html)
    jobs: list[Job] = []
    for card in soup.select("article")[:80]:
        link = card.select_one("a[href*='/annonce/']")
        if not link:
            continue
        raw_title = link.get_text(" ", strip=True)
        if len(raw_title) < 6:
            continue
        title = re.split(r"\s*Niveau d[’']", raw_title)[0].strip()
        summary = card.get_text(" ", strip=True)
        jobs.append(
            Job(
                source=source,
                title=title or raw_title,
                company="Unknown Company",
                location=default_location,
                url=urljoin_page(page_url, link.get("href", "")),
                posted_at=infer_date_from_text(summary),
                summary=summary[:800],
            )
        )
    return jobs


def extract_talent(source: str, html: str, page_url: str, default_location: str = "",
                   company_hint: str = "") -> list[Job]:
    """ma.talent.com SSR results: ``article[data-testid=job-card-unified]`` cards
    (CSS-module class names are hashed, so we hook onto the stable data-testid)."""
    soup = soup_of(html)
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select("article[data-testid='job-card-unified'], article[class*='JobCard_card']")[:60]:
        link = card.select_one("a[href]")
        title_node = card.select_one("h2, h3, [class*='JobCard_title']")
        title = re.sub(r"\s+", " ", title_node.get_text(" ", strip=True) if title_node else "").strip()
        if len(title) < 6 or link is None:
            continue
        href = link.get("href", "") or ""
        url = urljoin_page(page_url, href)
        if href in seen:
            continue
        seen.add(href)
        card_text = card.get_text(" ", strip=True)
        jobs.append(
            Job(
                source=source,
                title=title,
                company=company_hint or "Unknown Company",
                location=default_location,
                url=url,
                posted_at=infer_date_from_text(card_text),
                summary=card_text[:600],
            )
        )
    return jobs


def extract_successfactors(source: str, html: str, page_url: str, default_location: str = "",
                           company_hint: str = "") -> list[Job]:
    """SAP SuccessFactors career sites (e.g. apply.deloitte.com): ``/JobDetail/`` links."""
    soup = soup_of(html)
    jobs: list[Job] = []
    seen: set[str] = set()
    for link in soup.select("a[href*='JobDetail']")[:80]:
        href = link.get("href", "") or ""
        title = link.get_text(" ", strip=True)
        if len(title) < 6 or href in seen:
            continue
        seen.add(href)
        card = link.find_parent(["li", "div", "tr", "article"])
        card_text = card.get_text(" ", strip=True)[:600] if card else ""
        jobs.append(
            Job(
                source=source,
                title=title,
                company=company_hint or "Unknown Company",
                location=default_location,
                url=urljoin_page(page_url, href),
                posted_at=infer_date_from_text(card_text),
                summary=card_text,
            )
        )
    return jobs


def extract_rss_entries(source: str, feed: Any, default_location: str = "") -> list[Job]:
    """feedparser feed object -> Jobs."""
    jobs: list[Job] = []
    for entry in feed.entries[:50]:
        title = getattr(entry, "title", "") or ""
        if len(title) < 4:
            continue
        jobs.append(
            Job(
                source=source,
                title=title,
                company=getattr(entry, "author", "") or source,
                location=default_location,
                url=getattr(entry, "link", "") or "",
                posted_at=parse_date(getattr(entry, "published", "") or getattr(entry, "updated", "")),
                summary=BeautifulSoup(getattr(entry, "summary", "") or "", "html.parser").get_text(" ", strip=True)[:800],
            )
        )
    return jobs


def urljoin_page(base: str, href: str) -> str:
    return urljoin(base, href or "")


# kind name -> extractor(source, html, page_url, default_location, **opts)
EXTRACTORS: dict[str, Callable[..., list[Job]]] = {
    "generic": extract_generic,
    "wordpress": extract_wordpress,
    "rekrute": extract_rekrute,
    "marocannonces": extract_marocannonces,
    "talent": extract_talent,
    "successfactors": extract_successfactors,
}
