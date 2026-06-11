from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import dateparser
import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
SEEN_PATH = ROOT / "seen_jobs.json"
LOG_PATH = ROOT / "internship_finder.log"


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
        return hashlib.sha256(base.encode("utf-8")).hexdigest()


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    with SEEN_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return set(data.get("seen", []))


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(
        json.dumps({"seen": sorted(seen)}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = re.sub(r"/+$", "", parsed.path)
    return parsed._replace(query="", fragment="", path=path).geturl().lower()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: str | None) -> datetime | None:
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


class HttpClient:
    def __init__(self, user_agent: str, timeout: int) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "en,fr;q=0.9"})
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.robots: dict[str, RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self.robots:
            robot = RobotFileParser()
            robot.set_url(urljoin(root, "/robots.txt"))
            try:
                robot.read()
            except Exception as exc:
                logging.info("robots.txt unavailable for %s: %s", root, exc)
            self.robots[root] = robot
        try:
            return self.robots[root].can_fetch(self.session.headers["User-Agent"], url)
        except Exception:
            return True

    def get(self, url: str, params: dict[str, str] | None = None) -> requests.Response | None:
        if params:
            url = f"{url}?{urlencode(params)}"
        if not self.allowed(url):
            logging.info("Skipping disallowed by robots.txt: %s", url)
            return None
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            logging.warning("Request failed for %s: %s", url, exc)
            return None


def text_from_node(node: Any, selectors: list[str]) -> str:
    for selector in selectors:
        item = node.select_one(selector)
        if item:
            value = item.get("content") or item.get("datetime") or item.get_text(" ", strip=True)
            if value:
                return value
    return ""


def extract_structured_jobs(soup: BeautifulSoup, source: str, page_url: str) -> list[Job]:
    jobs: list[Job] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "{}")
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in flatten_json_ld(items):
            if item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization") or {}
            location = item.get("jobLocation") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address") if isinstance(location, dict) else {}
            location_text = " ".join(
                str(address.get(k, "")) for k in ("addressLocality", "addressRegion", "addressCountry")
            )
            jobs.append(
                Job(
                    source=source,
                    title=str(item.get("title") or "").strip(),
                    company=str(org.get("name") if isinstance(org, dict) else org or "").strip(),
                    location=location_text.strip(),
                    url=urljoin(page_url, str(item.get("url") or page_url)),
                    posted_at=parse_date(item.get("datePosted") or item.get("validThrough")),
                    summary=BeautifulSoup(str(item.get("description") or ""), "html.parser").get_text(" ", strip=True),
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


def generic_cards(soup: BeautifulSoup, source: str, page_url: str) -> list[Job]:
    selectors = [
        "[data-job-id]",
        "[data-testid*=job]",
        ".job_seen_beacon",
        ".jobsearch-SerpJobCard",
        ".job-card",
        ".job",
        ".offer",
        ".annonce",
        "article",
        "li",
    ]
    jobs: list[Job] = []
    for card in soup.select(",".join(selectors))[:80]:
        link = card.select_one("a[href]")
        if not link:
            continue
        title = text_from_node(
            card,
            [
                "h1",
                "h2",
                "h3",
                "[class*=title]",
                "[data-testid*=title]",
                "a[href]",
            ],
        )
        if len(title) < 4:
            continue
        company = text_from_node(card, ["[class*=company]", "[data-testid*=company]", ".brand", ".recruiter"])
        location = text_from_node(card, ["[class*=location]", "[data-testid*=location]", ".city", ".region"])
        date_value = text_from_node(card, ["time", "[datetime]", "[class*=date]", "[class*=time]", "[class*=posted]"])
        summary = card.get_text(" ", strip=True)
        jobs.append(
            Job(
                source=source,
                title=title,
                company=company or "Unknown Company",
                location=location,
                url=urljoin(page_url, link.get("href", "")),
                posted_at=parse_date(date_value) or infer_date_from_text(summary),
                summary=summary[:1000],
            )
        )
    return jobs


def infer_date_from_text(text: str) -> datetime | None:
    patterns = [
        r"(?:posted|published|il y a|publié|publiee|publiée|date de publication)[^\n,.]{0,50}",
        r"\b\d+\s+(?:hours?|days?|heures?|jours?)\s+ago\b",
        r"\bil y a\s+\d+\s+(?:heures?|jours?)\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_date(match.group(0))
            if parsed:
                return parsed
    return None


def fetch_rss(client: HttpClient, source: str, url: str) -> list[Job]:
    if not client.allowed(url):
        logging.info("Skipping disallowed RSS: %s", url)
        return []
    try:
        feed = feedparser.parse(url, request_headers=client.session.headers)
    except Exception as exc:
        logging.warning("RSS failed for %s: %s", url, exc)
        return []
    jobs: list[Job] = []
    for entry in feed.entries[:50]:
        jobs.append(
            Job(
                source=source,
                title=getattr(entry, "title", ""),
                company=getattr(entry, "author", "") or source,
                location="",
                url=getattr(entry, "link", url),
                posted_at=parse_date(getattr(entry, "published", "") or getattr(entry, "updated", "")),
                summary=getattr(entry, "summary", ""),
            )
        )
    return jobs


def build_source_urls(config: dict[str, Any]) -> list[tuple[str, str, dict[str, str] | None]]:
    sources = config["sources"]
    queries = config["queries"]
    urls: list[tuple[str, str, dict[str, str] | None]] = []
    for name, source in sources.items():
        if not source.get("enabled", True):
            continue
        base = source["base_url"]
        for query in queries:
            if name == "linkedin":
                urls.append((name, base, {"keywords": query, "location": "Casablanca, Casablanca-Settat, Morocco"}))
            elif name == "indeed":
                urls.append((name, base, {"q": query, "l": "Casablanca"}))
            elif name == "welcome_to_the_jungle":
                urls.append((name, base, {"query": query, "aroundQuery": "Casablanca"}))
            elif name == "emploi_ma":
                urls.append((name, base, {"f[0]": f"im_field_offre_region:76", "keys": query}))
            elif name == "marocannonces":
                urls.append((name, base, {"kw": query, "ville": "Casablanca"}))
            elif name == "talent":
                urls.append((name, base, {"k": query, "l": "Casablanca"}))
            elif name == "jooble":
                urls.append((name, base, {"ukw": query, "rgns": "Casablanca"}))
            elif name == "jobrapido":
                urls.append((name, base, {"w": query, "l": "Casablanca"}))
            elif name == "monster":
                urls.append((name, base, {"q": query, "where": "Casablanca"}))
            else:
                urls.append((name, base, None))
    return urls


def fetch_source_jobs(client: HttpClient, config: dict[str, Any]) -> list[Job]:
    jobs: list[Job] = []
    for source, url, params in build_source_urls(config):
        response = client.get(url, params=params)
        if not response:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        page_url = response.url
        found = extract_structured_jobs(soup, source, page_url)
        found.extend(generic_cards(soup, source, page_url))
        logging.info("%s produced %s raw jobs from %s", source, len(found), page_url)
        jobs.extend(found)
        time.sleep(0.4)
    return jobs


def fetch_company_jobs(client: HttpClient, config: dict[str, Any]) -> list[Job]:
    jobs: list[Job] = []
    for company in config.get("company_career_pages", []):
        response = client.get(company["url"])
        if not response:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        found = extract_structured_jobs(soup, company["company"], response.url)
        found.extend(generic_cards(soup, company["company"], response.url))
        for job in found:
            if not job.company or job.company == "Unknown Company":
                job.company = company["company"]
            job.source = f"company:{company['company']}"
        logging.info("%s career page produced %s raw jobs", company["company"], len(found))
        jobs.extend(found)
        time.sleep(0.4)
    return jobs


def discover_rss_jobs(client: HttpClient, config: dict[str, Any]) -> list[Job]:
    rss_urls = [
        "https://www.emploi.ma/rss.xml",
        "https://ma.indeed.com/rss?q=stage+pfe+software+engineer&l=Casablanca",
        "https://ma.talent.com/rss?query=stage%20pfe%20software%20engineer&location=Casablanca",
    ]
    jobs: list[Job] = []
    for url in rss_urls:
        jobs.extend(fetch_rss(client, "rss", url))
    return jobs


def is_relevant(job: Job, config: dict[str, Any]) -> bool:
    haystack = normalize_text(" ".join([job.title, job.company, job.location, job.summary]))
    filters = config["filters"]
    has_location = any(normalize_text(term) in haystack for term in filters["locations"])
    has_stage = any(normalize_text(term) in haystack for term in filters["internship_terms"])
    has_skill = any(normalize_text(skill) in haystack for skill in filters["skills"])
    return has_location and has_stage and has_skill


def score_job(job: Job, config: dict[str, Any]) -> None:
    haystack = normalize_text(" ".join([job.title, job.company, job.location, job.summary]))
    filters = config["filters"]
    matched_skills = [skill for skill in filters["skills"] if normalize_text(skill) in haystack]
    matched_terms = [term for term in filters["internship_terms"] if normalize_text(term) in haystack]
    location_match = any(normalize_text(term) in haystack for term in filters["locations"])

    score = 0
    if matched_terms:
        score += 25
    if location_match:
        score += 20
    score += min(30, len(set(map(str.lower, matched_skills))) * 5)

    if job.posted_at:
        age = now_utc() - job.posted_at
        if age <= timedelta(hours=config["search"]["priority_hours"]):
            score += 25
            job.freshness_rank = 0
        elif age <= timedelta(days=config["search"]["max_age_days"]):
            score += 15
            job.freshness_rank = 1
        else:
            job.freshness_rank = 9
    else:
        score += 5
        job.freshness_rank = 2

    title = normalize_text(job.title)
    if any(term in title for term in ("pfe", "stage", "internship", "stagiaire")):
        score += 8
    if any(term in title for term in ("software", "developpeur", "developer", "full stack", "backend", "frontend")):
        score += 7

    job.matched_skills = matched_skills
    job.match_score = min(100, score)
    reasons = []
    if matched_terms:
        reasons.append(f"internship term: {', '.join(sorted(set(matched_terms))[:3])}")
    if location_match:
        reasons.append("Casablanca/Morocco location")
    if matched_skills:
        reasons.append(f"skills: {', '.join(matched_skills[:5])}")
    reasons.append(age_text(job.posted_at))
    job.match_reason = "; ".join(reasons)


def filter_rank_dedupe(jobs: list[Job], config: dict[str, Any], seen: set[str]) -> list[Job]:
    max_age = timedelta(days=config["search"]["max_age_days"])
    unique: dict[str, Job] = {}
    for job in jobs:
        if not job.title or not job.url:
            continue
        if job.posted_at and now_utc() - job.posted_at > max_age:
            continue
        if not is_relevant(job, config):
            continue
        score_job(job, config)
        if job.dedupe_key in seen:
            continue
        existing = unique.get(job.dedupe_key)
        if not existing or job.match_score > existing.match_score:
            unique[job.dedupe_key] = job
    ranked = sorted(
        unique.values(),
        key=lambda item: (
            item.freshness_rank,
            -(item.posted_at.timestamp() if item.posted_at else 0),
            -item.match_score,
        ),
    )
    return ranked[: config["search"]["max_results_per_run"]]


def discord_message(jobs: list[Job]) -> dict[str, Any]:
    if not jobs:
        return {
            "content": "Internship Opportunities Found",
            "embeds": [
                {
                    "title": "No new matching internship opportunities found today.",
                    "color": 0x5865F2,
                }
            ],
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
                    {"name": "Company", "value": truncate(job.company or "Unknown Company", 1024), "inline": True},
                    {"name": "Location", "value": truncate(job.location or "Casablanca/Morocco", 1024), "inline": True},
                    {
                        "name": "Publication Date",
                        "value": job.posted_at.strftime("%Y-%m-%d") if job.posted_at else "Unknown Date",
                        "inline": True,
                    },
                    {"name": "Posted", "value": age_text(job.posted_at), "inline": True},
                    {"name": "Match Score", "value": f"{job.match_score}/100", "inline": True},
                    {"name": "Source", "value": truncate(job.source, 1024), "inline": True},
                    {"name": "URL", "value": truncate(job.url, 1024), "inline": False},
                ],
                "footer": {"text": f"#{index}"},
            }
        )
    return {"content": "Internship Opportunities Found", "embeds": embeds}


def embed_color(job: Job) -> int:
    if job.freshness_rank == 0:
        return 0x2ECC71
    if job.freshness_rank == 1:
        return 0xF1C40F
    return 0x95A5A6


def truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def send_discord_notification(message: dict[str, Any] | str) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logging.warning("Discord webhook URL is not configured; message not sent.")
        print(message)
        return False
    payload = {"content": message} if isinstance(message, str) else message
    embeds = payload.get("embeds", [])
    if not embeds:
        response = requests.post(webhook_url, json=payload, timeout=20)
        response.raise_for_status()
        return True
    for chunk in chunked(embeds, 10):
        chunk_payload = {"content": payload.get("content", ""), "embeds": chunk}
        response = requests.post(
            webhook_url,
            json=chunk_payload,
            timeout=20,
        )
        response.raise_for_status()
    return True


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def main() -> int:
    load_dotenv()
    setup_logging()
    config = load_config()
    client = HttpClient(
        user_agent=config["search"]["user_agent"],
        timeout=int(config["search"]["request_timeout_seconds"]),
    )
    seen = load_seen()
    logging.info("Starting internship search with %s seen keys", len(seen))
    jobs: list[Job] = []
    jobs.extend(fetch_source_jobs(client, config))
    jobs.extend(fetch_company_jobs(client, config))
    jobs.extend(discover_rss_jobs(client, config))
    ranked = filter_rank_dedupe(jobs, config, seen)
    logging.info("Found %s new ranked matching jobs from %s raw jobs", len(ranked), len(jobs))
    sent = send_discord_notification(discord_message(ranked))
    if sent:
        for job in ranked:
            seen.add(job.dedupe_key)
        save_seen(seen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
