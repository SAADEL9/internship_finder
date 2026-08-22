"""Extractor unit tests against realistic HTML fixtures (shapes taken from the
live sites in August 2026)."""
from datetime import timedelta

from extractors import (
    Job,
    extract_json_ld,
    extract_marocannonces,
    extract_rekrute,
    extract_successfactors,
    extract_talent,
    extract_wordpress,
    now_utc,
    soup_of,
)

PAGE = "https://example.com/search"


def test_wordpress_dreamjob_shape():
    html = """
    <html><body>
    <article class="jeg_post format-image">
      <h2 class="jeg_post_title"><a href="https://www.dreamjob.ma/stage/intelcia-it-pfe-informatique/">
        Intelcia IT Propose des Stages PFE en Informatique</a></h2>
      <div class="jeg_post_excerpt"><p>Stages PFE en informatique à Casablanca chez Intelcia IT.</p></div>
      <time class="entry-date published" datetime="2026-08-20T10:00:00+00:00">20 août 2026</time>
    </article>
    <article class="jeg_post">
      <h3 class="jeg_post_title"><a href="https://www.dreamjob.ma/stage/autre-offre/">Autre offre stage web</a></h3>
      <p>Un résumé d'une autre offre.</p>
    </article>
    </body></html>
    """
    jobs = extract_wordpress("dreamjob", html, PAGE, "Morocco")
    assert len(jobs) == 2
    first = jobs[0]
    assert "Stages PFE" in first.title
    assert first.url.endswith("/intelcia-it-pfe-informatique/")
    assert first.posted_at is not None
    assert abs(now_utc() - first.posted_at) < timedelta(days=400)
    assert "Casablanca" in first.summary


def test_rekrute_shape():
    html = """
    <html><body>
    <ul>
      <li><a href="/offre-emploi-stagiaire-crc-recrutement-gsm-al-maghrib-casablanca-185368.html">
        Stagiaire CRC | Casablanca (Maroc)</a></li>
      <li><a href="/offre-emploi-technicien-it-n1-recrutement-btechnologie-rabat-185593.html">
        Technicien IT N1 | Rabat (Maroc)</a></li>
    </ul>
    </body></html>
    """
    jobs = extract_rekrute("rekrute", html, "https://www.rekrute.com/offres.html?x=1", "Morocco")
    assert len(jobs) == 2
    first = jobs[0]
    assert first.title == "Stagiaire CRC"
    assert first.location == "Casablanca"
    assert first.company == "gsm al maghrib"
    assert first.url.startswith("https://www.rekrute.com/offre-emploi-stagiaire")


def test_marocannonces_shape():
    html = """
    <html><body>
    <div class="content_box">
      <article class="listing browsing_result_table_body_even">
        <div class="listing_set list">
          <a href="categorie/309/Offres-emploi/annonce/10429502/Agent-polyvalent.html">
            Développeur stagiaire Casablanca Niveau d'études souhaité: Bac+5 Salaire: 4 000 dh</a>
        </div>
      </article>
    </div>
    </body></html>
    """
    jobs = extract_marocannonces("marocannonces", html, "https://www.marocannonces.com/categorie/309/x.html", "Morocco")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Développeur stagiaire Casablanca"
    assert "Niveau d'études" not in job.title
    assert "Niveau d'études" in job.summary
    assert "/annonce/10429502/" in job.url


def test_talent_shape():
    html = """
    <html><body>
    <div data-job-id="abc" data-testid="jobcard-container-1">
      <article class="JobCard_card__TSiPB" data-testid="job-card-unified">
        <header class="JobCard_header__tgcjv">
          <div class="JobCard_heading__7POIz">
            <h2 class="JobCard_title__X32Qk">Stage Développeur Web</h2>
          </div>
        </header>
        <a href="/view?id=620215160602107082">view</a>
        <div>Casablanca - Stage - 6 mois</div>
      </article>
    </div>
    </body></html>
    """
    jobs = extract_talent("talent", html, "https://ma.talent.com/jobs?k=x", "Casablanca, Morocco")
    assert len(jobs) == 1
    assert jobs[0].title == "Stage Développeur Web"
    assert "/view?id=620215160602107082" in jobs[0].url


def test_successfactors_shape():
    html = """
    <html><body>
    <div class="jobList">
      <a href="/careers/JobDetail/Stagiaire-Maroc/12345">Stagiaire Audit Casablanca</a>
      <a href="/careers/JobDetail/Senior-Consultant/99">Senior Consultant US</a>
    </div>
    </body></html>
    """
    jobs = extract_successfactors("deloitte", html, "https://apply.deloitte.com/en_US/careers/SearchJobs/",
                                  "Global", company_hint="Deloitte")
    assert len(jobs) == 2
    assert jobs[0].company == "Deloitte"
    assert jobs[0].title.startswith("Stagiaire")


def test_json_ld_with_graph_and_removes_html():
    html = """
    <html><body>
    <script type="application/ld+json">
    {"@graph": [{"@type": "JobPosting",
        "title": "Stage Java",
        "hiringOrganization": {"name": "ACME"},
        "jobLocation": {"address": {"addressLocality": "Casablanca", "addressCountry": "MA"}},
        "url": "/jobs/42",
        "datePosted": "2026-08-21",
        "description": "<p>Java <b>Spring</b> stage à Casablanca</p>"}]}
    </script>
    </body></html>
    """
    jobs = extract_json_ld(soup_of(html), "board", PAGE, "Morocco")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Stage Java"
    assert job.company == "ACME"
    assert job.location == "Casablanca MA"
    assert job.posted_at is not None
    assert "<b>" not in job.summary and "Spring" in job.summary


def test_json_ld_ignores_valid_through():
    html = """
    <script type="application/ld+json">
    {"@type": "JobPosting", "title": "Old Job", "hiringOrganization": "X",
     "url": "/jobs/1", "datePosted": "", "validThrough": "2026-08-20"}
    </script>"""
    jobs = extract_json_ld(soup_of(html), "board", PAGE, "Morocco")
    assert jobs[0].posted_at is None  # validThrough must NOT be used as posted date


def test_dedupe_key_stable_and_query_insensitive():
    a = Job("s", "Stage Java", "ACME", "Casa", "https://x.com/job/42?utm=1")
    b = Job("s2", "stage java", "acme", "Rabat", "https://x.com/job/42")
    assert a.dedupe_key == b.dedupe_key
    c = Job("s", "Stage Java", "ACME", "Casa", "https://x.com/job/43")
    assert a.dedupe_key != c.dedupe_key
