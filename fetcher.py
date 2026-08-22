"""HTTP layer: robots-aware, retrying session plus a polite parallel fetcher.

Politeness model: one worker thread per domain, requests to the same domain run
sequentially with a configurable delay, different domains are fetched in
parallel.  This keeps total runtime low without hammering any single site.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass
class FetchResult:
    url: str
    ok: bool
    status: int | str
    html: str = ""
    final_url: str = ""
    elapsed: float = 0.0
    error: str = ""


@dataclass
class SourceStats:
    requests: int = 0
    failures: int = 0
    jobs_found: int = 0
    relevant_found: int = 0
    elapsed: float = 0.0
    notes: list[str] = field(default_factory=list)


class HttpClient:
    """requests.Session with retries, robots.txt enforcement and per-domain caching."""

    def __init__(self, user_agent: str, timeout: int) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
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
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()

    # -- robots ----------------------------------------------------------

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        root = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            if root in self._robots:
                return self._robots[root]
        robot = RobotFileParser()
        robot.set_url(urljoin(root, "/robots.txt"))
        try:
            robot.read()
        except Exception as exc:  # unreachable/blocked robots -> assume allowed
            logging.info("robots.txt unavailable for %s: %s", root, exc)
            robot = None
        with self._robots_lock:
            self._robots[root] = robot
        return robot

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        robot = self._robots_for(url)
        if robot is None:
            return True
        try:
            is_allowed = robot.can_fetch(self.user_agent, url)
        except Exception:
            return True
        if not is_allowed:
            logging.info("robots.txt: disallowed %s", url)
        return is_allowed

    # -- requests ----------------------------------------------------------

    def get(self, url: str, params: dict[str, str] | None = None) -> requests.Response | None:
        display_url = f"{url}?{urlencode(params)}" if params else url
        if not self.allowed(display_url):
            return None
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            logging.info("HTTP %s: %s", response.status_code, display_url)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "Error")
            logging.warning("HTTP %s: %s - %s", status, display_url, exc)
            return None


def fetch_all(
    client: HttpClient,
    tasks: list[dict[str, Any]],
    max_workers: int,
    politeness_delay: float,
) -> tuple[dict[int, FetchResult], dict[str, SourceStats]]:
    """Fetch tasks grouped by domain; same domain is fetched sequentially.

    Each task: {"id": int, "source": str, "url": str, "params": dict|None}.
    Returns results keyed by task id plus per-source stats.
    """
    stats: dict[str, SourceStats] = {}

    def note(source: str) -> SourceStats:
        return stats.setdefault(source, SourceStats())

    by_domain: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        domain = urlparse(task["url"]).netloc
        by_domain.setdefault(domain, []).append(task)
        note(task["source"]).requests += 1

    results: dict[int, FetchResult] = {}
    results_lock = threading.Lock()

    def run_domain(tasks_for_domain: list[dict[str, Any]]) -> None:
        for index, task in enumerate(tasks_for_domain):
            source = task["source"]
            started = time.monotonic()
            display = f"{task['url']}?{urlencode(task['params'])}" if task.get("params") else task["url"]
            if index > 0 and politeness_delay > 0:
                time.sleep(politeness_delay)
            if not client.allowed(display):
                with results_lock:
                    results[task["id"]] = FetchResult(
                        url=display, ok=False, status="robots", error="disallowed by robots.txt"
                    )
                note(source).failures += 1
                note(source).notes.append("robots.txt disallow")
                continue
            try:
                response = client.session.get(task["url"], params=task.get("params"), timeout=client.timeout)
                logging.info("HTTP %s: %s", response.status_code, display)
                response.raise_for_status()
                with results_lock:
                    results[task["id"]] = FetchResult(
                        url=display,
                        ok=True,
                        status=response.status_code,
                        html=response.text,
                        final_url=str(response.url),
                        elapsed=time.monotonic() - started,
                    )
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "Error")
                logging.warning("HTTP %s: %s - %s", status, display, exc)
                with results_lock:
                    results[task["id"]] = FetchResult(
                        url=display,
                        ok=False,
                        status=status,
                        elapsed=time.monotonic() - started,
                        error=str(exc)[:200],
                    )
                note(source).failures += 1
                if status in (403, 429):
                    note(source).notes.append(f"HTTP {status}")

    workers = max(1, min(max_workers, len(by_domain) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run_domain, by_domain.values()))

    return results, stats
