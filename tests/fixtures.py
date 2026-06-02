"""Test fixtures for the sonar-router classifier.

Pure data, no imports of the module under test — one representative query per
decision-tree branch, plus the CVE / advisory-ID calibration cases added by the
2026-05-31 hardening pass. Keeping the case table separate from the assertions
lets it read as a spec you can scan: query in, expected ``recommended_tool`` out.

Every query here was hand-traced against the regexes in ``scripts/route.py`` so
it lands in exactly one branch; the comments record the dominant signal.
"""

from __future__ import annotations

from dataclasses import dataclass

# Exact ``recommended_tool`` strings the classifier emits. These are recovered
# verbatim from the original bytecode and locked by the tests. The search route
# deliberately uses a real Unicode arrow ("→").
ASK = "perplexity_ask"
GH_API = "gh api"
REASON = "perplexity_reason"
DEEP_RESEARCH = "perplexity_research_start"
SEARCH_FETCH = "perplexity_search → WebFetch"
DECOMPOSE = "perplexity_ask + gh api (per-noun decomposition)"


@dataclass(frozen=True)
class Case:
    """One routing expectation: feed ``query``, expect ``expected_tool``."""

    id: str
    query: str
    expected_tool: str


# One query per decision-tree branch, in branch order.
BRANCH_CASES: list[Case] = [
    # Branch 1: >=5 proper-noun signals + verification intent -> decompose.
    Case(
        "decompose__five_github_refs_with_verify",
        "verify these repos exist: anthropics/claude-code openai/openai-python "
        "pytest-dev/pytest astral-sh/uv pallets/flask",
        DECOMPOSE,
    ),
    # Branch 2: a GitHub ref + verification, proper-noun count < 5 -> gh api.
    Case(
        "gh_api__single_repo_existence_check",
        "does anthropics/claude-code exist",
        GH_API,
    ),
    # Branch 3: comparison marker, proper-noun count < 5 -> Sonar Reasoning Pro.
    Case(
        "reason__versus_and_tradeoffs",
        "compare REST versus GraphQL tradeoffs",
        REASON,
    ),
    # Branch 4: >=2 breadth markers + low proper-noun count -> Sonar Deep Research.
    Case(
        "deep_research__broad_low_proper_noun",
        "give me an overview of how distributed consensus works and the best "
        "practices for it",
        DEEP_RESEARCH,
    ),
    # Branch 5: recency marker(s) -> search to discover URLs, then fetch.
    Case(
        "search_fetch__recency_markers",
        "what are the latest Next.js features in 2026",
        SEARCH_FETCH,
    ),
    # Default: no strong signal -> perplexity_ask.
    Case(
        "default__no_strong_signal",
        "explain how garbage collection helps memory management",
        ASK,
    ),
]

# CVE / advisory-ID queries. Before the 2026-05-31 fix these scored zero
# proper-noun signals, so a broad multi-CVE query wrongly fell through to Deep
# Research (which is documented to hallucinate advisory specifics). See the
# diagnosis pass-note referenced by SKILL.md.
CVE_THREE_VERIFY = "verify CVE-2026-0145 and CVE-2026-0146 and CVE-2026-0147"
CVE_BROAD_THREE = (
    "survey the landscape and analysis of CVE-2026-0145, CVE-2026-0146, "
    "and CVE-2026-0147"
)
CVE_FIVE_VERIFY = (
    "verify whether CVE-2026-0145, CVE-2026-0146, CVE-2026-0147, "
    "CVE-2026-0148, and CVE-2026-0149 exist"
)
GHSA_ONE = "verify GHSA-jq35-85cw-mw8p"
VMSA_ONE = "is VMSA-2026-0001 patched"
CVE_NONE = "what CVEs should I worry about in 2026"

# Mixed query exercising every proper-noun sub-signal at once (used to assert the
# proper_noun_signals total stays equal to the sum of its components).
MIXED_PROPER_NOUNS = (
    "verify anthropics/claude-code v1.2.3 'my-id' pkg@market CVE-2026-0145 exists"
)

# (label, query, expected cve_advisory_count) — one example per advisory-ID
# family the classifier recognizes, so every regex alternative has coverage.
ADVISORY_VARIANTS = [
    ("cve", "verify CVE-2026-0145", 1),
    ("ghsa", "verify GHSA-jq35-85cw-mw8p", 1),
    ("vmsa", "is VMSA-2026-0001 patched", 1),
    ("rhsa", "does RHSA-2026:1234 apply", 1),
    ("usn", "check USN-1234-1", 1),
    ("dsa", "check DSA-5678", 1),
    ("pysec", "verify PYSEC-2026-1234", 1),
    ("rustsec", "verify RUSTSEC-2026-0001", 1),
    ("go", "verify GO-2026-1234", 1),
]
