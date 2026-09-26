"""Schema 3: a ranked tool chain per intent and --available filtering.

The version 2 cases in test_route.py still hold: without --available the first ranked tool is the version 2
recommendation, except for a long comparison, whose asynchronous Sonar reasoning job ranks first.
"""

from __future__ import annotations

import json

import pytest

import route
from fixtures import BRANCH_CASES, DECOMPOSE, DEEP_RESEARCH, GH_API, REASON, SEARCH_FETCH

RECENCY = "what are the latest Next.js features in 2026"
BREADTH = "give me an overview of how distributed consensus works and the best practices for it"
GITHUB = "does anthropics/claude-code exist"
SHORT_COMPARISON = "compare REST versus GraphQL tradeoffs"
LONG_COMPARISON = "compare anthropics/claude-code versus openai/codex versus google/gemini-cli tradeoffs"


def _tools(result):
    return [entry["tool"] for entry in result["ranked_tools"]]


@pytest.mark.parametrize("case", BRANCH_CASES, ids=lambda c: c.id)
def test_without_available_the_first_ranked_tool_is_the_version_2_choice(case):
    result = route.classify(case.query)
    assert result["recommended_tool"] == case.expected_tool == _tools(result)[0]
    assert all(entry["available"] for entry in result["ranked_tools"])
    assert result["available_tools"] is None and result["ignored_tools"] == []


def test_every_entry_names_its_source_and_how_to_use_it():
    for case in BRANCH_CASES:
        for entry in route.classify(case.query)["ranked_tools"]:
            assert set(entry) == {"tool", "requires", "note", "available"}
            assert entry["requires"] in route.TOOL_SOURCES and entry["note"]


def test_recency_chain():
    assert _tools(route.classify(RECENCY)) == [SEARCH_FETCH, route.OR_SONAR, route.WEB_SEARCH, route.WEB_FETCH]


def test_breadth_chain_polls_and_states_the_real_latency():
    result = route.classify(BREADTH)
    assert _tools(result) == [DEEP_RESEARCH, route.OR_DEEP_RESEARCH, route.WEB_BOTH]
    first = result["ranked_tools"][0]["note"]
    assert "perplexity_research_poll" in first and "60 to 300 s" in first and "120 minutes" in first
    assert "get_chat_completion_status" in result["ranked_tools"][1]["note"]
    assert "5-15 min" not in result["rationale"]


def test_short_comparisons_prefer_perplexity_reason_and_long_ones_start_and_poll():
    assert _tools(route.classify(SHORT_COMPARISON)) == [REASON, route.OR_REASONING, route.WEB_BOTH]
    long_result = route.classify(LONG_COMPARISON)
    assert _tools(long_result) == [route.OR_REASONING, REASON, route.WEB_BOTH]
    assert long_result["recommended_tool"] == route.OR_REASONING and long_result["fallback"] == REASON
    assert "60 s" in long_result["rationale"]
    padded = SHORT_COMPARISON + " " + "with detailed context " * 30
    assert _tools(route.classify(padded))[0] == route.OR_REASONING


def test_github_chain_is_gh_api_then_webfetch_then_the_model_servers():
    assert _tools(route.classify(GITHUB)) == [GH_API, route.WEB_FETCH, "perplexity_ask", route.OR_SONAR]


def test_the_decompose_branch_is_unchanged():
    result = route.classify(BRANCH_CASES[0].query)
    assert result["recommended_tool"] == DECOMPOSE and _tools(result)[0] == DECOMPOSE
    assert result["fallback"] == "WebFetch on known URLs"


def test_available_tools_filter_the_recommendation_and_the_fallback():
    result = route.classify(RECENCY, available_tools=["openrouter-multimodal", "web"])
    assert result["recommended_tool"] == route.OR_SONAR and result["fallback"] == route.WEB_SEARCH
    assert [entry["available"] for entry in result["ranked_tools"]] == [False, True, True, True]
    assert result["available_tools"] == ["openrouter-multimodal", "web"]
    assert "perplexity-mcp" in result["rationale"]
    only_gh = route.classify(GITHUB, available_tools=["gh"])
    assert only_gh["recommended_tool"] == GH_API and only_gh["fallback"] == ""


def test_with_nothing_available_the_unfiltered_choice_stands_and_says_so():
    result = route.classify(RECENCY, available_tools=[])
    assert result["recommended_tool"] == SEARCH_FETCH
    assert "none of the ranked tools" in result["rationale"].lower()


def test_unknown_source_names_are_ignored_and_reported():
    result = route.classify(GITHUB, available_tools=["gh", "openrouter-official", "gh"])
    assert result["available_tools"] == ["gh"] and result["ignored_tools"] == ["openrouter-official"]


def test_help_prints_usage_that_names_available(capsys):
    assert route.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--available" in out and "usage" in out.lower()


@pytest.mark.parametrize("argv", [
    ["--available", "openrouter-multimodal,web", RECENCY],
    ["--available=openrouter-multimodal,web", RECENCY],
    ["--json", json.dumps({"query": RECENCY, "available": ["openrouter-multimodal", "web"]})],
    ["--json", json.dumps({"query": RECENCY, "available": "openrouter-multimodal,web"})],
])
def test_main_takes_available_from_the_flag_or_the_json(capsys, argv):
    assert route.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recommended_tool"] == route.OR_SONAR and out["schema_version"] == 3


def test_available_needs_a_value(capsys):
    assert route.main(["--available"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


# ---- review of 71bca95 (2026-09-26) ----

def test_the_version_2_fallback_survives_when_its_source_is_available():
    """fallback keeps its schema 2 meaning, so a session with perplexity-mcp still falls back
    to perplexity_ask for a recency query, as schema 2 callers expect."""
    for available in (["perplexity-mcp"], ["perplexity-mcp", "web", "gh"]):
        result = route.classify(RECENCY, available_tools=available)
        assert result["recommended_tool"] == SEARCH_FETCH and result["fallback"] == "perplexity_ask", available


def test_a_github_query_without_gh_recommends_a_tool_the_session_has():
    result = route.classify(GITHUB, available_tools=["perplexity-mcp", "openrouter-multimodal"])
    assert result["recommended_tool"] == "perplexity_ask" and result["fallback"] == route.OR_SONAR
    assert "none of the ranked tools" not in result["rationale"].lower()
    assert "gh api needs gh" in result["rationale"]


@pytest.mark.parametrize("case", BRANCH_CASES, ids=lambda c: c.id)
def test_recommendation_and_fallback_only_name_available_sources(case):
    sources = route.TOOL_SOURCES
    for mask in range(1, 2 ** len(sources)):
        available = [s for i, s in enumerate(sources) if mask >> i & 1]
        result = route.classify(case.query, available_tools=available)
        usable = [e["tool"] for e in result["ranked_tools"] if e["available"]]
        fallback = result["fallback"]
        assert fallback != result["recommended_tool"], available
        if not usable:   # the unfiltered choice stands, and the rationale says so
            assert "none of the ranked tools" in result["rationale"].lower()
            continue
        assert result["recommended_tool"] == usable[0], available
        assert fallback == "" or route._source_of(fallback) in available, (available, fallback)


def test_without_available_the_fallback_is_the_version_2_one():
    """Unless the chain moved the version 2 recommendation down (a long comparison), which then becomes the
    fallback."""
    for case in BRANCH_CASES + [type(BRANCH_CASES[0])("long", LONG_COMPARISON, route.OR_REASONING)]:
        _, v2_recommended, v2_fallback, _ = route._route(route._score_query(case.query), case.query)
        result = route.classify(case.query)
        moved = result["recommended_tool"] != v2_recommended
        assert result["fallback"] == (v2_recommended if moved else v2_fallback), case.id


@pytest.mark.parametrize("argv, want", [
    (["--available", "openrouter-multimodal", "--available", "web", RECENCY], ["openrouter-multimodal", "web"]),
    (["--available=perplexity-mcp", "--available=perplexity-mcp,gh", RECENCY], ["perplexity-mcp", "gh"]),
    (["--available=", RECENCY], []),
])
def test_a_repeated_available_adds_to_the_list(capsys, argv, want):
    assert route.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["available_tools"] == want


@pytest.mark.parametrize("argv", [
    ["--available", "--json", json.dumps({"query": RECENCY})],
    ["--json", json.dumps({"query": RECENCY, "available": 5})],
    ["--json", json.dumps({"query": RECENCY, "available": ["web", 7]})],
    ["--json", json.dumps({"query": 42})],
    ["--json", json.dumps([RECENCY])],
    ["--json", json.dumps({"query": RECENCY}), "and", "words"],
])
def test_malformed_arguments_are_a_json_error_not_a_traceback(capsys, argv):
    """L6: --available used to swallow a following --json, and a wrong-typed "available" crashed."""
    assert route.main(argv) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_a_double_dash_ends_the_options_and_stdin_is_never_read(capsys, monkeypatch):
    """L6: a query that is itself --available=... used to be taken as the flag, and route.py then waited on
    stdin; labcoat passes "--" before the query."""
    class NoStdin:
        def read(self):
            raise AssertionError("stdin was read")
    monkeypatch.setattr(route.sys, "stdin", NoStdin())
    assert route.main(["--available", "web", "--", "--available=perplexity-mcp", "compare", "A", "versus", "B"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["available_tools"] == ["web"] and out["recommended_tool"] == route.WEB_BOTH
