# sonar-router

A decision-matrix skill and Python classifier that picks the right web-research tool for a query, routing away from Sonar Deep Research when it would hallucinate.

> Independent project; not affiliated with, endorsed by, or sponsored by Perplexity AI.

---

## What it is

sonar-router is a two-part tool:

1. **`SKILL.md`** — a decision-matrix reference for AI agents (Claude Code skills). It encodes when to use each Perplexity / web-research tool based on the query's shape: proper-noun count, recency signals, breadth, and verification intent.

2. **`scripts/route.py`** — a Python CLI that scores a query string and returns a JSON routing verdict. Useful for batch research planning, scripted pipelines, or sanity-checking a tool choice before making an expensive API call.

Tools it routes among:

- `perplexity_research_start` / `_poll` / `_cancel` (Sonar Deep Research — async)
- `perplexity_ask` (Sonar Pro — synchronous, with citations)
- `perplexity_reason` (Sonar Reasoning Pro — step-by-step logic)
- `perplexity_search` → `WebFetch` (URL discovery then direct fetch)
- `gh api` (GitHub REST API — zero AI cost, instant, authoritative)
- `WebFetch` (direct document retrieval)
- `Agent` subagents (multi-file / multi-repo research)

---

## Why

**Sonar Deep Research has documented data-quality problems for niche and recent queries.** Given a query that mentions several specific repo names, CVE IDs, plugin names, or version numbers, it will fabricate at least one of them — returning plausible-sounding but wrong answers. This is not a speculation; it has been observed repeatedly across verification-heavy research passes.

The routing rule is simple: if your query mentions ≥3 specific proper nouns that all need individual verification, decompose it into per-noun `gh api` or `perplexity_ask` calls instead of one Sonar Deep Research job. The per-call cost is comparable (often cheaper), the latency is far lower, and the answers are verifiable.

Sonar Deep Research is still excellent for broad, well-established topics with low proper-noun density — that's what it was built for.

---

## The decision matrix

| Query shape | Recommended tool | Approx. cost | Latency | Notes |
|---|---|---|---|---|
| Broad academic / established topic (low proper-noun density) | `perplexity_research_start` | ~$1–3 | 5–15 min | Avoid if ≥3 specific proper nouns need verification |
| Specific factual question ("does X exist?", "what is Y?") | `perplexity_ask` | ~$0.05–0.20 | 5–30 sec | First choice for plugin / repo verification |
| Logical analysis or comparison ("X vs Y given Z") | `perplexity_reason` | ~$0.10–0.50 | 30–120 sec | Use when reasoning matters more than fresh search |
| Finding specific URLs / recent news | `perplexity_search` → `WebFetch` | ~$0.02 + free | 5–15 sec | Discover URLs first, then fetch authoritative sources |
| Known GitHub repo ("does this repo exist?", star count, metadata) | `gh api repos/<owner>/<repo>` | $0 | <1 sec | Always preferred over AI for GitHub-hosted facts |
| Known documentation page | `WebFetch <url>` | $0 | 2–10 sec | Primary source beats AI summary |
| Multi-file / multi-repo research | `Agent` subagent | varies | 1–10 min | Isolates context; handles many reads in one shot |

### The routing rule

If the query mentions **≥3 specific proper nouns that all need to be verified individually** (repo names, plugin names, version numbers, CVE / security-advisory IDs, organization names, specific feature names) → **do not use Sonar Deep Research**. Decompose into per-noun verifications, then synthesize.

---

## `route.py` usage

```bash
# positional argument
python scripts/route.py "does CVE-2026-1234 affect github.com/foo/bar v1.2.3?"

# JSON input
python scripts/route.py --json '{"query": "compare REST versus GraphQL tradeoffs"}'

# stdin
echo "overview of SLSA supply-chain levels" | python scripts/route.py
```

### Output (schema_version 2)

```json
{
  "recommended_tool": "perplexity_ask + gh api (per-noun decomposition)",
  "fallback": "WebFetch on known URLs",
  "score": {
    "github_ref_count": 1,
    "plugin_marketplace_count": 0,
    "semver_count": 1,
    "quoted_identifier_count": 0,
    "cve_advisory_count": 1,
    "proper_noun_signals": 3,
    "recency_markers": 0,
    "niche_verification_markers": 1,
    "broad_markers": 0,
    "comparison_markers": 0
  },
  "rationale": "...",
  "schema_version": 2
}
```

**Score fields:**

| Field | What it counts |
|---|---|
| `github_ref_count` | `owner/repo`-style GitHub references |
| `plugin_marketplace_count` | `plugin@marketplace`-style references |
| `semver_count` | Version strings (`v1.2.3`, `1.0.0-beta`) |
| `quoted_identifier_count` | Backtick / quote-wrapped identifiers |
| `cve_advisory_count` | CVE, GHSA, VMSA, RHSA, USN, DSA, PYSEC, RUSTSEC, GO advisory IDs |
| `proper_noun_signals` | Sum of all five component counts above |
| `recency_markers` | Recency words ("latest", "current", "as of", year strings) |
| `niche_verification_markers` | Verification intent ("verify", "exist", "what is", "confirm") |
| `broad_markers` | Breadth intent ("overview", "deep-dive", "architecture of", "best practices") |
| `comparison_markers` | Comparison intent ("vs", "versus", "tradeoffs", "pros and cons") |

The decision tree is evaluated in order; first branch that fires wins:

1. `proper_noun_signals >= 5` AND `niche_verification_markers > 0` → per-noun decomposition
2. `github_ref_count > 0` AND `niche_verification_markers > 0` AND `proper_noun_signals < 5` → `gh api`
3. `comparison_markers > 0` AND `proper_noun_signals < 5` → `perplexity_reason`
4. `broad_markers >= 2` AND `proper_noun_signals < 3` → `perplexity_research_start`
5. `recency_markers > 0` → `perplexity_search` → WebFetch
6. default → `perplexity_ask`

---

## Tests

```bash
python -m pytest tests/ -q
```

30 cases covering every decision-tree branch, all recognized advisory-ID families, the scoring invariants, and the CLI argument-parsing surface.

---

## Acknowledgments / upstream

The Perplexity tools this skill routes (`perplexity_search`, `perplexity_ask`, `perplexity_reason`, `perplexity_research_start`, `perplexity_research_poll`, `perplexity_research_cancel`) come from the **official Perplexity MCP server** at [`perplexityai/modelcontextprotocol`](https://github.com/perplexityai/modelcontextprotocol) (MIT license).

The async-job tools (`perplexity_research_start` / `_poll` / `_cancel`) that make Sonar Deep Research usable against timeout-bound MCP clients were contributed upstream via **[PR #111](https://github.com/perplexityai/modelcontextprotocol/pull/111)**. Without those tools, Sonar Deep Research jobs (which run 5–30+ minutes) reliably exceed the MCP `tools/call` timeout and return nothing.

---

## License

MIT — see [LICENSE](LICENSE).

---

## AI Assistance

This project was developed alongside AI platforms.

Details, including the tools used: [`docs/ai-assistance.md`](docs/ai-assistance.md).
