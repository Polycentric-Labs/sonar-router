#!/usr/bin/env python3
"""sonar-router classifier.

Scores a research query and recommends the best Perplexity / web-research tool.

Usage:
    python route.py "<query string>"
    python route.py --available perplexity-mcp,web "<query string>"
    echo "<query>" | python route.py
    python route.py --json '{"query": "...", "available": ["perplexity-mcp", "web"]}'
    python route.py --help

Returns JSON to stdout:
    {
        "recommended_tool": "perplexity_ask",
        "fallback": "WebFetch",
        "ranked_tools": [{"tool": ..., "requires": ..., "note": ..., "available": ...}, ...],
        "score": { ... },
        "rationale": "...",
        "schema_version": 3,
        "available_tools": null,
        "ignored_tools": []
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

Schema 3 (2026-09-26): each branch has a ranked tool chain (ranked_tools), and every entry names the source it
needs: perplexity-mcp, openrouter-multimodal (Sonar models through OpenRouter), web (WebSearch, WebFetch) or gh.
With --available, recommended_tool is the first ranked tool whose source this session has. fallback keeps its
version 2 meaning, the second choice for quality or cost: the version 2 fallback when its source is available,
else the next available ranked tool. Without --available, both keep their version 2 values, except that a long
comparison ranks the asynchronous Sonar reasoning job first (the synchronous call times out near 60 s) and falls
back to perplexity_reason.

Output history: schema_version 2 added cve_advisory_count to the score breakdown; 3 added ranked_tools,
available_tools and ignored_tools.

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

# Output contract version. 2 (2026-05-31) added `cve_advisory_count` to the score breakdown; 3 (2026-09-26)
# added ranked_tools, available_tools and ignored_tools.
SCHEMA_VERSION = 3

# The sources --available names. "web" is Claude Code's WebSearch and WebFetch; "gh" is the GitHub CLI.
TOOL_SOURCES = ("perplexity-mcp", "openrouter-multimodal", "web", "gh")

# Tools on the openrouter-multimodal MCP server. Sonar models answer from their own live web search. The slow
# ones go through start_chat_completion, then get_chat_completion_status, because the synchronous call times out
# near 60 s. Model ids were checked against the live catalogue on 2026-09-26.
OR_SONAR = "openrouter-multimodal chat_completion, model perplexity/sonar"
OR_SONAR_PRO = "openrouter-multimodal chat_completion, model perplexity/sonar-pro"
OR_REASONING = "openrouter-multimodal start_chat_completion, model perplexity/sonar-reasoning-pro"
OR_DEEP_RESEARCH = "openrouter-multimodal start_chat_completion, model perplexity/sonar-deep-research"
WEB_SEARCH = "WebSearch"
WEB_FETCH = "WebFetch"
WEB_BOTH = "WebSearch + WebFetch"

# Past this length, or with three or more named options, a comparison can outlast the synchronous call.
_LONG_COMPARISON_CHARS = 400
_POLL = "then get_chat_completion_status with the job_id until the job completes"

_USAGE = """usage: route.py [--available SOURCES] [--] QUERY
       route.py [--available SOURCES] --json '{"query": "...", "available": ["perplexity-mcp", "web"]}'
       echo QUERY | route.py [--available SOURCES]

Classifies a web-research query and prints a JSON verdict (schema 3): recommended_tool, fallback,
ranked_tools (the tool chain for the query's intent, each entry with the source it needs) and the score.

  --available SOURCES   comma list of the sources this session has: perplexity-mcp,
                        openrouter-multimodal, web, gh (repeat the flag to add more).
                        recommended_tool is then the first ranked tool from an available source,
                        and fallback the second choice among the available ones. Unknown names are
                        ignored and reported in ignored_tools.
  --                    ends the options: everything after it is the query
  --help                this text"""

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


def _route(score: Score, query: str) -> tuple[str, str, str, str]:
    """Apply decision tree. Returns (branch, recommended_tool, fallback, rationale)."""
    pn = score.proper_noun_signals
    verif = score.niche_verification_markers
    breadth = score.broad_markers
    comparison = score.comparison_markers
    recency = score.recency_markers
    gh = score.github_ref_count

    # 1. High proper-noun density + verification intent -> per-noun decomposition.
    if pn >= 5 and verif > 0:
        return (
            "decompose",
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
            "github",
            "gh api",
            "perplexity_ask",
            f"{gh} GitHub ref(s) with verification intent. gh api is free, instant, and authoritative.",
        )

    # 3. Comparison marker(s) with manageable proper-noun count -> Sonar Reasoning Pro.
    if comparison > 0 and pn < 5:
        return (
            "comparison",
            "perplexity_reason",
            "perplexity_ask",
            f"{comparison} comparison marker(s) with manageable proper-noun count. Sonar "
            f"Reasoning Pro synthesizes well across named options.",
        )

    # 4. Breadth markers + low proper-noun count -> Sonar Deep Research.
    if breadth >= 2 and pn < 3:
        return (
            "breadth",
            "perplexity_research_start",
            "perplexity_reason",
            f"{breadth} breadth markers + low proper-noun count ({pn}). Sonar Deep Research is "
            f"appropriate for this kind of query. The async-job tools "
            f"(perplexity_research_start/_poll/_cancel) in the Perplexity MCP server avoid the "
            f"MCP tools/call timeout for Sonar Deep Research. Expect 60 to 300 s; a job is kept "
            f"for 120 minutes.",
        )

    # 5. Recency marker(s) -> discover URLs via search, then fetch authoritative sources.
    if recency > 0:
        return (
            "recency",
            "perplexity_search → WebFetch",
            "perplexity_ask",
            f"{recency} recency marker(s); use search to discover URLs then fetch authoritative "
            f"sources directly.",
        )

    # default
    return (
        "default",
        "perplexity_ask",
        "WebFetch",
        "No strong signal; default to perplexity_ask (cheap, with citations). If query expands "
        "later, re-route.",
    )


def _entry(tool: str, requires: str, note: str) -> dict[str, Any]:
    return {"tool": tool, "requires": requires, "note": note, "available": True}


def _chain(branch: str, score: Score, query: str) -> list[dict[str, Any]]:
    """The ranked tools for a branch. The first entry is the version 2 recommendation, except for a long
    comparison, where the asynchronous Sonar reasoning job ranks ahead of the synchronous perplexity_reason."""
    web_both = _entry(WEB_BOTH, "web", "search, then read the sources yourself")
    if branch == "decompose":
        return [_entry("perplexity_ask + gh api (per-noun decomposition)", "perplexity-mcp",
                       "one perplexity_ask or gh api call per proper noun, never one query for all of them"),
                _entry("WebFetch on known URLs", "web", "read each noun's authoritative page directly")]
    if branch == "github":
        return [_entry("gh api", "gh", "free, instant and authoritative for repository facts"),
                _entry(WEB_FETCH, "web", "the repository's page when gh is not available"),
                _entry("perplexity_ask", "perplexity-mcp", "when neither gh nor the web is available; verify its answer"),
                _entry(OR_SONAR, "openrouter-multimodal", "Sonar answers from a live web search, with citations")]
    if branch == "comparison":
        reason = _entry("perplexity_reason", "perplexity-mcp", "synchronous: fine for a short comparison")
        start = _entry(OR_REASONING, "openrouter-multimodal",
                       f"asynchronous: {_POLL}; use it when the answer may take longer than about 60 s")
        long = len(query) > _LONG_COMPARISON_CHARS or score.proper_noun_signals >= 3
        return [start, reason, web_both] if long else [reason, start, web_both]
    if branch == "breadth":
        return [_entry("perplexity_research_start", "perplexity-mcp",
                       "then perplexity_research_poll with the jobId until COMPLETED; expect 60 to 300 s, and "
                       "a job is kept for 120 minutes"),
                _entry(OR_DEEP_RESEARCH, "openrouter-multimodal", f"asynchronous: {_POLL}"),
                web_both]
    if branch == "recency":
        return [_entry("perplexity_search → WebFetch", "perplexity-mcp",
                       "search for current URLs, then fetch the authoritative ones"),
                _entry(OR_SONAR, "openrouter-multimodal", "Sonar answers from a live web search, with citations"),
                _entry(WEB_SEARCH, "web", "find current URLs"),
                _entry(WEB_FETCH, "web", "read the authoritative source")]
    return [_entry("perplexity_ask", "perplexity-mcp", "cheap, with citations"),
            _entry(OR_SONAR_PRO, "openrouter-multimodal", "Sonar Pro through OpenRouter, with citations"),
            _entry(WEB_SEARCH, "web", "find sources"),
            _entry(WEB_FETCH, "web", "read them")]


def _names(value) -> list[str]:
    """Source names from a list or a comma-separated string, stripped, blanks dropped."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    return [str(item).strip() for item in items if str(item).strip()]


def _source_of(tool: str) -> str:
    """The source a tool name needs (one of TOOL_SOURCES), read from how the name starts; "" when none fits."""
    for prefix, source in (("perplexity_", "perplexity-mcp"), ("openrouter-multimodal", "openrouter-multimodal"),
                           ("gh api", "gh"), ("WebSearch", "web"), ("WebFetch", "web")):
        if tool.startswith(prefix):
            return source
    return ""


def classify(query: str, available_tools: list[str] | None = None) -> dict[str, Any]:
    """Top-level: classify a query and return the routing recommendation (schema 3).

    available_tools, when given, names the sources this session has (see TOOL_SOURCES; other names are
    ignored and reported). recommended_tool is then the first ranked tool from an available source. fallback
    keeps its version 2 meaning: the version 2 recommendation when the chain
    ranked it second, else the version 2 fallback, else the next ranked tool, whichever comes first with an
    available source ("" when none has one). Without available_tools, both keep their version 2 values,
    except for a long comparison (see _chain)."""
    score = _score_query(query)
    branch, recommended, fallback, rationale = _route(score, query)
    ranked = _chain(branch, score, query)
    known: list[str] | None = None
    ignored: list[str] = []
    if available_tools is not None:
        names = _names(available_tools)
        known = [n for i, n in enumerate(names) if n in TOOL_SOURCES and n not in names[:i]]
        ignored = [n for i, n in enumerate(names) if n not in TOOL_SOURCES and n not in names[:i]]
        for entry in ranked:
            entry["available"] = entry["requires"] in known
    usable = [entry for entry in ranked if entry["available"]]
    if not usable:
        rationale += " None of the ranked tools is in --available; this is the unfiltered choice."
    else:
        first = usable[0]["tool"]
        candidates = ([recommended] if first != recommended else []) + [fallback] + [e["tool"] for e in usable[1:]]
        fallback = next((tool for tool in candidates
                         if tool != first and (known is None or _source_of(tool) in known)), "")
        if first != recommended:
            if known is not None and not ranked[0]["available"]:
                rationale += (f" {ranked[0]['tool']} needs {ranked[0]['requires']}, which is not in --available, "
                              f"so the next ranked tool is recommended.")
            else:
                rationale += (" A long comparison can outlast the synchronous call's ~60 s timeout, so the "
                              "asynchronous Sonar reasoning job ranks first.")
        recommended = first
    return {
        "recommended_tool": recommended,
        "fallback": fallback,
        "ranked_tools": ranked,
        "score": asdict(score),
        "rationale": rationale,
        "schema_version": SCHEMA_VERSION,
        "available_tools": known,
        "ignored_tools": ignored,
    }


def _parse_cli(argv: list[str]) -> tuple[list[str] | None, str | None, list[str]]:
    """(the --available names, or None when the flag is absent; the --json text, or None; the query words).

    "--" ends the options, so a query may itself start with "--". A repeated --available adds to the list.
    Raises ValueError for --available without a list (including one that looks like another flag), --json
    without its argument, and words given together with --json."""
    available: list[str] | None = None
    json_text: str | None = None
    words: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            words += argv[i + 1:]
            break
        if arg == "--available" or arg.startswith("--available="):
            if arg == "--available":
                value = argv[i + 1] if i + 1 < len(argv) else None
                i += 2
            else:
                value = arg.split("=", 1)[1]
                i += 1
            if value is None or value.startswith("-"):
                raise ValueError("--available needs a comma-separated list, for example perplexity-mcp,web")
            available = (available or []) + [n for n in _names(value) if n not in (available or [])]
            continue
        if arg == "--json":
            if i + 1 >= len(argv):
                raise ValueError("--json requires an argument")
            json_text = argv[i + 1]
            i += 2
            continue
        if arg != "-":
            words.append(arg)
        i += 1
    if json_text is not None and words:
        raise ValueError("give the query either as words or inside --json, not both")
    return available, json_text, words


def _read_query(json_text: str | None, words: list[str]) -> tuple[str, list[str] | None]:
    """(the query, the "available" list from --json or None). Reads stdin only when there are no words and no
    --json. Raises ValueError for a --json payload of the wrong shape."""
    if json_text is None:
        return (" ".join(words) if words else sys.stdin.read().strip()), None
    payload = json.loads(json_text)
    if not isinstance(payload, dict):
        raise ValueError('--json needs a JSON object, for example {"query": "..."}')
    query = payload.get("query", "")
    if not isinstance(query, str):
        raise ValueError('"query" in --json must be a string')
    available = payload.get("available")
    if available is None:
        return query, None
    if not (isinstance(available, str) or (isinstance(available, list) and all(isinstance(n, str) for n in available))):
        raise ValueError('"available" in --json must be a list of source names or a comma-separated string')
    return query, _names(available)


def _parse_args(argv: list[str]) -> str:
    """The query from words, --json or stdin: the version 2 interface, kept for its callers and tests. A usage
    error raises SystemExit, as it always did."""
    try:
        _, json_text, words = _parse_cli(argv)
        return _read_query(json_text, words)[0]
    except ValueError as exc:
        raise SystemExit(str(exc))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["--help"], ["-h"]):
        print(_USAGE)
        return 0
    try:
        available, json_text, words = _parse_cli(argv)
        query, from_json = _read_query(json_text, words)
        if from_json is not None:
            available = from_json
    except ValueError as e:   # json.JSONDecodeError is a ValueError
        print(json.dumps({"error": str(e), "schema_version": SCHEMA_VERSION}))
        return 1
    if not query.strip():
        print(json.dumps({"error": "empty query", "schema_version": SCHEMA_VERSION}))
        return 1
    result = classify(query, available)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
