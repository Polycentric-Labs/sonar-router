#!/usr/bin/env python3
"""sonar-router classifier.

Scores a research query and recommends the best Perplexity / web-research tool.

Usage:
    python route.py "<query string>"
    echo "<query>" | python route.py
    python route.py --json '{"query": "...", "context": "..."}'

Returns JSON to stdout:
    {
        "recommended_tool": "perplexity_ask",
        "fallback": "gh api",
        "score": { ... },
        "rationale": "..."
    }

Heuristics (calibrated to the 2026-05-25 diagnosis pass-note):
  - GitHub refs (org/repo): each adds 1 to the proper-noun score
  - plugin@marketplace refs: each adds 1
  - Quoted/backticked identifiers: each adds 1
  - Semver-like strings: each adds 1
  - CVE / security-advisory IDs (CVE-, GHSA-, VMSA-, RHSA-, USN-, DSA-,
    PYSEC-, RUSTSEC-, GO-): each adds 1   [added 2026-05-31]
  - Recency markers ("2026", "as of", "latest", "current"): recency weight
  - Niche markers ("verify", "exist", "does X exist", "what is"): verification weight
  - Broad markers ("compare", "framework", "overview", "how does", "deep-dive"): breadth weight
  - Comparison markers ("vs", "versus", "tradeoffs", "pros and cons"): comparison weight

Decision tree (evaluated in this order; first match wins):
  1. proper_noun_signals >= 5 AND verification > 0
       -> per-noun decomposition (perplexity_ask + gh api)
  2. github_refs > 0 AND verification > 0 AND proper_noun_signals < 5
       -> gh api
  3. comparison > 0 AND proper_noun_signals < 5
       -> perplexity_reason (Sonar Reasoning Pro)
  4. breadth >= 2 AND proper_noun_signals < 3
       -> perplexity_research_start (Sonar Deep Research)
  5. recency > 0
       -> perplexity_search -> WebFetch
  default
       -> perplexity_ask

Output: schema_version 2 (the score breakdown carries cve_advisory_count).

PROVENANCE: the original .py + SKILL.md were lost (the skill was never
git-tracked; only a stale route.cpython-314.pyc remained). On 2026-05-31 the
source was reconstructed from the bytecode constants and then hardened. The
classification logic (regexes, Score fields, decision tree, rationale strings)
is faithful to the bytecode; on top of that, the CVE / advisory signal +
cve_advisory_count field were added, a pytest suite was written (tests/), and
the skill was git-tracked. The reconstruction standardized error reporting on
structured stdout JSON; the original additionally wrote to stderr (per the
dropped ('file',) / ': ' constants in the bytecode), which is immaterial to
routing. See references/pass-notes/perplexity-tool-routing-diagnosis-2026-05-25.md.

License: MIT. Author: Allen Byrd.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from typing import Any

# Output contract version. Bumped to 2 on 2026-05-31 when the score breakdown
# gained `cve_advisory_count` (an additive field; see _score_query).
SCHEMA_VERSION = 2

_GITHUB_REF = re.compile(r"\b[a-zA-Z0-9][\w.-]*/[a-zA-Z0-9][\w.-]+\b")
_PLUGIN_MARKETPLACE = re.compile(r"\b[\w-]{2,}@[\w-]{2,}\b")
_SEMVER = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[\w.-]+)?\b")
_QUOTED_ID = re.compile(r"['\"`]([A-Za-z0-9_-]{3,})['\"`]")
# CVE / security-advisory IDs. Added 2026-05-31: these are specific proper nouns
# to verify, so they belong in the proper-noun score — otherwise a broad
# multi-CVE query falls through to Deep Research, which hallucinates advisory
# details. The digit groups are required, so a bare "CVE" with no ID won't match.
_CVE_ADVISORY = re.compile(
    r"\b(?:"
    r"CVE-\d{4}-\d{4,}"                    # CVE-2026-0145
    r"|GHSA(?:-[0-9a-z]{4}){3}"            # GHSA-jq35-85cw-mw8p
    r"|VMSA-\d{4}-\d{3,}"                  # VMSA-2026-0001
    r"|RHSA-\d{4}:\d{3,}"                  # RHSA-2026:1234
    r"|(?:USN|DSA)-\d{3,}(?:-\d+)?"        # USN-1234-1, DSA-5678
    r"|(?:PYSEC|RUSTSEC|GO)-\d{4}-\d{3,}"  # PYSEC-2026-1234, RUSTSEC-2026-0001, GO-2026-1234
    r")\b",
    re.IGNORECASE,
)
_RECENCY_MARKERS = re.compile(
    r"\b(?:2026|latest|current|as of|recent|just released|new[ -]?in)\b", re.IGNORECASE
)
_NICHE_VERIFICATION_MARKERS = re.compile(
    r"\b(?:verify|exist[s]?|does [^.?!]+ exist|what is|find me|list|enumerate|confirm|check (?:if|whether))\b",
    re.IGNORECASE,
)
_BROAD_MARKERS = re.compile(
    r"\b(?:compare|framework|overview|how does|how do|deep[ -]?dive|why does|architecture of|design of|patterns? for|best practices?|survey|landscape|analysis of)\b",
    re.IGNORECASE,
)
_COMPARISON_MARKERS = re.compile(
    r"\b(?:vs\.?|versus|compared? (?:to|with)|tradeoffs?|pros and cons|differences? between)\b",
    re.IGNORECASE,
)


@dataclass
class Score:
    """Scoring breakdown for transparency."""

    github_ref_count: int
    plugin_marketplace_count: int
    semver_count: int
    quoted_identifier_count: int
    cve_advisory_count: int
    proper_noun_signals: int
    recency_markers: int
    niche_verification_markers: int
    broad_markers: int
    comparison_markers: int


def _score_query(query: str) -> Score:
    """Compute scoring breakdown for a query string."""
    github = len(_GITHUB_REF.findall(query))
    # plugin@marketplace overlaps email-ish; subtract obvious github refs counted twice is
    # not needed — patterns are distinct enough for the heuristic.
    plugin = len(_PLUGIN_MARKETPLACE.findall(query))
    semver = len(_SEMVER.findall(query))
    quoted = len(_QUOTED_ID.findall(query))
    cve = len(_CVE_ADVISORY.findall(query))
    # Overlap (e.g. a quoted CVE matching both _QUOTED_ID and _CVE_ADVISORY) is
    # accepted, consistent with the other signals — it just reads as stronger
    # verification intent and nudges toward decomposition.
    proper_noun = github + plugin + semver + quoted + cve
    recency = len(_RECENCY_MARKERS.findall(query))
    niche = len(_NICHE_VERIFICATION_MARKERS.findall(query))
    broad = len(_BROAD_MARKERS.findall(query))
    comparison = len(_COMPARISON_MARKERS.findall(query))
    return Score(
        github_ref_count=github,
        plugin_marketplace_count=plugin,
        semver_count=semver,
        quoted_identifier_count=quoted,
        cve_advisory_count=cve,
        proper_noun_signals=proper_noun,
        recency_markers=recency,
        niche_verification_markers=niche,
        broad_markers=broad,
        comparison_markers=comparison,
    )


def _route(score: Score, query: str) -> tuple[str, str, str]:
    """Apply decision tree. Returns (recommended_tool, fallback, rationale)."""
    pn = score.proper_noun_signals
    verif = score.niche_verification_markers
    breadth = score.broad_markers
    comparison = score.comparison_markers
    recency = score.recency_markers
    gh = score.github_ref_count

    # 1. High proper-noun density + verification intent -> per-noun decomposition.
    if pn >= 5 and verif > 0:
        return (
            "perplexity_ask + gh api (per-noun decomposition)",
            "WebFetch on known URLs",
            f"{pn} proper-noun signals ({gh} GitHub refs, {score.plugin_marketplace_count} "
            f"plugin@marketplace refs, {score.quoted_identifier_count} quoted IDs, "
            f"{score.semver_count} version refs, {score.cve_advisory_count} CVE/advisory IDs) "
            f"+ {verif} verification markers. Sonar Deep "
            f"Research is documented to hallucinate names for queries like this — see diagnosis "
            f"pass-note. Decompose into per-noun perplexity_ask or gh api calls.",
        )

    # 2. GitHub-only refs with verification intent -> gh api (free, instant, authoritative).
    if gh > 0 and verif > 0 and pn < 5:
        return (
            "gh api",
            "perplexity_ask",
            f"{gh} GitHub ref(s) with verification intent. gh api is free, instant, and authoritative.",
        )

    # 3. Comparison marker(s) with manageable proper-noun count -> Sonar Reasoning Pro.
    if comparison > 0 and pn < 5:
        return (
            "perplexity_reason",
            "perplexity_ask",
            f"{comparison} comparison marker(s) with manageable proper-noun count. Sonar "
            f"Reasoning Pro synthesizes well across named options.",
        )

    # 4. Breadth markers + low proper-noun count -> Sonar Deep Research.
    if breadth >= 2 and pn < 3:
        return (
            "perplexity_research_start",
            "perplexity_reason",
            f"{breadth} breadth markers + low proper-noun count ({pn}). Sonar Deep Research is "
            f"appropriate for this kind of query. The async-job tools "
            f"(perplexity_research_start/_poll/_cancel) in the Perplexity MCP server avoid the "
            f"MCP tools/call timeout for Sonar Deep Research. Expect 5-15 min latency.",
        )

    # 5. Recency marker(s) -> discover URLs via search, then fetch authoritative sources.
    if recency > 0:
        return (
            "perplexity_search → WebFetch",
            "perplexity_ask",
            f"{recency} recency marker(s); use search to discover URLs then fetch authoritative "
            f"sources directly.",
        )

    # default
    return (
        "perplexity_ask",
        "WebFetch",
        "No strong signal; default to perplexity_ask (cheap, with citations). If query expands "
        "later, re-route.",
    )


def classify(query: str) -> dict[str, Any]:
    """Top-level: classify a query and return the routing recommendation."""
    score = _score_query(query)
    recommended_tool, fallback, rationale = _route(score, query)
    return {
        "recommended_tool": recommended_tool,
        "fallback": fallback,
        "score": asdict(score),
        "rationale": rationale,
        "schema_version": SCHEMA_VERSION,
    }


def _parse_args(argv: list[str]) -> str:
    """Read query from args, --json, or stdin."""
    if argv and argv[0] == "--json":
        if len(argv) < 2:
            raise SystemExit("--json requires an argument")
        payload = json.loads(argv[1])
        return payload.get("query", "")
    positional = [a for a in argv if a not in ("-", "--")]
    if positional:
        return " ".join(positional)
    return sys.stdin.read().strip()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        query = _parse_args(argv)
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e), "schema_version": SCHEMA_VERSION}))
        return 1
    if not query.strip():
        print(json.dumps({"error": "empty query", "schema_version": SCHEMA_VERSION}))
        return 1
    result = classify(query)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
