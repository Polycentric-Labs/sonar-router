"""Tests for the sonar-router classifier (``scripts/route.py``).

Covers every decision-tree branch, the CVE / advisory-ID calibration added on
2026-05-31, the scoring invariants, and the CLI / arg-parsing surface.
"""

from __future__ import annotations

import json

import pytest

import route
from fixtures import (
    ASK,
    DECOMPOSE,
    DEEP_RESEARCH,
    GH_API,
    REASON,
    SEARCH_FETCH,
    ADVISORY_VARIANTS,
    BRANCH_CASES,
    CVE_BROAD_THREE,
    CVE_FIVE_VERIFY,
    CVE_NONE,
    CVE_THREE_VERIFY,
    GHSA_ONE,
    MIXED_PROPER_NOUNS,
    VMSA_ONE,
)


# --- Decision-tree branches -------------------------------------------------

@pytest.mark.parametrize("case", BRANCH_CASES, ids=lambda c: c.id)
def test_branch_routing(case):
    """Each representative query lands on its intended tool."""
    result = route.classify(case.query)
    assert result["recommended_tool"] == case.expected_tool


def test_comparison_branch_precedes_breadth():
    """A query with BOTH a comparison marker and >=2 breadth markers routes to
    reason, proving the comparison branch is evaluated before Deep Research."""
    result = route.classify(
        "compare the architecture of microservices versus the design of monoliths"
    )
    assert result["recommended_tool"] == REASON


# --- CVE / advisory-ID calibration (the 2026-05-31 fix) ---------------------

def test_cve_ids_count_as_proper_nouns():
    score = route.classify(CVE_THREE_VERIFY)["score"]
    assert score["cve_advisory_count"] == 3
    assert score["proper_noun_signals"] >= 3


def test_cve_heavy_query_routes_away_from_deep_research():
    """The calibration gap this fix closes: a broad, multi-CVE query must NOT be
    sent to Deep Research, which hallucinates advisory details."""
    result = route.classify(CVE_BROAD_THREE)
    assert result["recommended_tool"] != DEEP_RESEARCH
    assert result["recommended_tool"] == SEARCH_FETCH


def test_many_cves_with_verification_decompose():
    result = route.classify(CVE_FIVE_VERIFY)
    assert result["recommended_tool"] == DECOMPOSE


def test_ghsa_id_recognized():
    assert route.classify(GHSA_ONE)["score"]["cve_advisory_count"] == 1


def test_vmsa_id_recognized():
    assert route.classify(VMSA_ONE)["score"]["cve_advisory_count"] == 1


def test_cve_pattern_no_false_positive():
    """A bare mention of "CVE" with no actual ID must not score."""
    assert route.classify(CVE_NONE)["score"]["cve_advisory_count"] == 0


@pytest.mark.parametrize(
    "query, expected_count",
    [(q, c) for _id, q, c in ADVISORY_VARIANTS],
    ids=[_id for _id, _q, _c in ADVISORY_VARIANTS],
)
def test_advisory_id_variants_recognized(query, expected_count):
    """Every advisory-ID family contributes exactly one cve_advisory signal."""
    assert route.classify(query)["score"]["cve_advisory_count"] == expected_count


# --- Output contract & scoring invariants -----------------------------------

def test_classify_contract():
    result = route.classify("anything")
    assert set(result) == {
        "recommended_tool",
        "fallback",
        "score",
        "rationale",
        "schema_version",
    }
    assert result["schema_version"] == 2
    assert set(result["score"]) == {
        "github_ref_count",
        "plugin_marketplace_count",
        "semver_count",
        "quoted_identifier_count",
        "cve_advisory_count",
        "proper_noun_signals",
        "recency_markers",
        "niche_verification_markers",
        "broad_markers",
        "comparison_markers",
    }


def test_proper_noun_signals_is_sum_of_components():
    """proper_noun_signals must equal the sum of its component counts - guards
    against adding a new signal field but forgetting to fold it into the total."""
    score = route.classify(MIXED_PROPER_NOUNS)["score"]
    assert score["proper_noun_signals"] == (
        score["github_ref_count"]
        + score["plugin_marketplace_count"]
        + score["semver_count"]
        + score["quoted_identifier_count"]
        + score["cve_advisory_count"]
    )


# --- Arg parsing / CLI surface ----------------------------------------------

def test_parse_args_positional_join():
    assert route._parse_args(["how", "does", "x"]) == "how does x"


def test_parse_args_json():
    assert route._parse_args(["--json", '{"query": "hello"}']) == "hello"


def test_parse_args_json_missing_argument_raises():
    with pytest.raises(SystemExit):
        route._parse_args(["--json"])


def test_main_happy_path(capsys):
    rc = route.main(["compare REST versus GraphQL tradeoffs"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["recommended_tool"] == REASON


def test_main_empty_query(capsys):
    rc = route.main(["   "])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["error"] == "empty query"


def test_main_bad_json(capsys):
    rc = route.main(["--json", "{not valid json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "error" in out
