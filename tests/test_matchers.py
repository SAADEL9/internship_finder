"""Word-boundary matcher behavior: the v1 substring false positives must stay dead."""
from extractors import Matcher, term_pattern


def make_matcher() -> Matcher:
    return Matcher(
        locations=["casablanca", "morocco"],
        internship_terms=["stage", "internship", "intern", "pfe", "stagiaire"],
        skills=["java", "javascript", "c++", ".net", "node.js", "react"],
        exclude_title_terms=["senior", "manager"],
    )


def test_intern_does_not_match_international():
    # "International" contains "intern" as substring but not as a word
    assert not term_pattern("intern").search("International Sales Director")
    assert not term_pattern("intern").search("international company")


def test_intern_matches_word_intern():
    assert term_pattern("intern").search("Business Intern wanted")
    assert term_pattern("internship").search("Summer internship 2026")


def test_java_does_not_match_javascript():
    assert not term_pattern("java").search("We use JavaScript everywhere")
    assert term_pattern("java").search("Java backend developer")
    assert term_pattern("java").search("Java/Spring stack")


def test_special_char_terms():
    assert term_pattern("c++").search("Requires C++ experience")
    assert not term_pattern("c++").search("Requires C programming")
    assert term_pattern(".net").search("ASP.NET developer")
    assert term_pattern("node.js").search("node.js runtime")


def test_french_terms():
    assert term_pattern("stage").search("Stage PFE Casablanca")
    assert term_pattern("stagiaire").search("Recherche stagiaire développeur")


def test_matcher_matched_skills():
    m = make_matcher()
    text = "Stage développeur Java et React à Casablanca"
    assert set(m.matched_skills(text)) >= {"java", "react"}
    assert not m.matched_skills(text) or "javascript" not in m.matched_skills(text)


def test_matcher_matched_locations():
    m = make_matcher()
    assert m.matched_locations("Basé à Casablanca, hybride")
    assert m.matched_locations("Remote Morocco position")
    assert not m.matched_locations("Remote Europe position")


def test_matcher_exclusions():
    m = make_matcher()
    assert m.matched_excluded("Senior Software Engineer")
    assert m.matched_excluded("Relationship Manager")
    assert not m.matched_excluded("Junior Developer")


def test_it_acronym_is_case_exact():
    # "IT" must not match the English word "it" in running text
    assert term_pattern("IT").search("Stage IT Casablanca")
    assert not term_pattern("IT").search("Apply for it today")
