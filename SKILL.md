---
name: sonar-router
description: Picks the right Perplexity / web-research tool for a query based on query characteristics (proper-noun count, recency signal, breadth, domain). Use BEFORE invoking perplexity_research_start, perplexity_ask, perplexity_reason, perplexity_search, WebFetch, or gh api to verify the right tool. Strongly recommended when the query mentions 3+ specific proper nouns (repo names, plugin names, version numbers) that need verification — Sonar Deep Research is documented to hallucinate names for niche / recent queries. Triggers also when user prompts contain 'research', 'investigate', 'deep research', 'perplexity', 'sonar', or when synthesizing across multiple marketplaces/repos.
license: MIT
---

# /sonar-router — Pick the right Perplexity / web-research tool

Decision-matrix skill for choosing among Perplexity tools, WebFetch, gh api, and Agent subagents based on query shape.

## Why this exists

**Sonar Deep Research has documented data-quality problems for niche / recent queries** — empty search results, confabulated specifics, fabricated CVE numbers, plugin names that don't exist, etc. The async-job tools (`perplexity_research_start`, `perplexity_research_poll`, `perplexity_research_cancel`) in the Perplexity MCP server avoid the MCP `tools/call` timeout for long-running Sonar Deep Research jobs (contributed upstream via [PR #111](https://github.com/perplexityai/modelcontextprotocol/pull/111)).

In short: the timeout problem is solved, but the data-quality problem for niche/recent queries remains. The matrix below is the operating guidance. Use it before calling any web-research tool.

## When to invoke

- Before invoking `perplexity_research_start` (especially if your query mentions specific proper nouns)
- When the user asks Claude to "research" / "investigate" / "find out about" something
- When planning multi-source synthesis across marketplaces / repos
- When deciding between `gh api`, `WebFetch`, `perplexity_ask`, `perplexity_reason`, `perplexity_search`, `perplexity_research_start`, or an Agent subagent

## Decision matrix

| Query shape | Tool | Cost | Latency | Caveat |
|---|---|---|---|---|
| **Broad academic/established topic** (e.g., "how does Envoy credential_injector work", "OWASP Top 10", "what is SLSA L3") | `perplexity_research_start` (Sonar Deep Research) | live pricing | 60-300 sec | async-job tools required for non-timeout; avoid if query mentions ≥3 specific proper nouns |
| **Specific factual question** (e.g., "does `clawlock` plugin exist?", "what's its install command?") | `perplexity_ask` (Sonar Pro) | live pricing | 5-30 sec | First choice for plugin / repo verification |
| **Logical analysis / comparison** (e.g., "compare X vs Y given these constraints") | `perplexity_reason` (Sonar Reasoning Pro) | live pricing | 30-120 sec | Use when synthesis + reasoning matters more than fresh search. A synchronous MCP call times out near 60 s, so a long comparison goes through Sonar reasoning with start and poll (see the chains below) |
| **Find specific URLs / recent news** | `perplexity_search` → `WebFetch` ranked results | live pricing + free fetches | 5-15 sec | Use to discover candidate URLs, then fetch them directly |
| **Known GitHub repo verification** | `gh api repos/<owner>/<repo>` (no AI) | $0 | <1 sec | Always preferred for "does this repo exist?" / "what's its star count?" |
| **Known doc page** | `WebFetch <url>` | $0 | 2-10 sec | Authoritative source > AI summary |
| **Multi-file / multi-repo research** | `Agent` subagent (general-purpose / Explore) | depends on inner calls | 1-10 min | Isolates context, handles many file reads in one shot |

**Prices change, so the matrix no longer carries figures** (the May 2026 ones are gone). Check the live catalogue instead: the `perplexity/*` models on OpenRouter (`search_models` or `get_model_info` on openrouter-multimodal, `list-models` on openrouter-official) and Perplexity's own pricing for the perplexity-mcp tools.

## Ranked chains and `--available` (schema 3, 2026-09-26)

Every route now carries `ranked_tools`, an ordered chain for the query's intent. Each entry names the source it needs: `perplexity-mcp`, `openrouter-multimodal` (Sonar models through OpenRouter), `web` (WebSearch and WebFetch) or `gh`. Pass the sources this session has with `--available` (for example `--available perplexity-mcp,web`), and `recommended_tool` becomes the first ranked tool you can use. `fallback` keeps its schema 2 meaning, the second choice: the schema 2 fallback when you have its source, otherwise the next ranked tool you can use. Without the flag, both keep their schema 2 values (a long comparison, whose Sonar reasoning job ranks first, falls back to `perplexity_reason`), so older callers keep working.

| Intent | Chain, in order |
|---|---|
| Recency | `perplexity_search` → `WebFetch`; Sonar (`perplexity/sonar`, which searches the web itself) through openrouter-multimodal; WebSearch; WebFetch |
| Breadth | `perplexity_research_start`, then `perplexity_research_poll`; Sonar deep research (`perplexity/sonar-deep-research`) through `start_chat_completion`, then `get_chat_completion_status`; WebSearch plus WebFetch |
| Comparison | `perplexity_reason` for a short one; Sonar reasoning (`perplexity/sonar-reasoning-pro`) through start and poll ranks first for a long one (over 400 characters, or three or more proper-noun signals: repository references, plugin@marketplace names, versions, quoted identifiers, CVE and advisory ids; plain product names do not count), since the synchronous call times out near 60 s; WebSearch plus WebFetch |
| GitHub references | `gh api`, then WebFetch; without either, `perplexity_ask`, then Sonar through openrouter-multimodal |
| Per-noun decomposition | unchanged: `perplexity_ask` + `gh api` per noun, then WebFetch on known URLs |
| No strong signal | `perplexity_ask`; Sonar Pro through openrouter-multimodal; WebSearch; WebFetch |

Unknown source names are ignored and listed in `ignored_tools`. When none of the ranked tools is available, the unfiltered choice stands and the rationale says so.

## Routing heuristic (the rule)

If the query mentions **≥3 specific proper nouns that all need to be verified individually** (repo names, plugin names, version numbers, CVE / security-advisory IDs, organization names, file paths, specific feature names) → **do NOT use Sonar Deep Research**. It will hallucinate at least one of them.

Decompose into per-noun verifications via:
- `gh api repos/<owner>/<repo>` for GitHub-hosted things
- `perplexity_ask` per individual question
- `WebFetch` on known canonical URLs

Then synthesize the verified inputs yourself OR via `perplexity_reason`.

## Polling discipline (added 2026-05-25, retention bumped to 120 min)

`perplexity_research_start` returns a `jobId` immediately and runs the Sonar Deep Research job asynchronously (typically 60 to 300 s; an earlier note here said 10 to 25 min, which was too long). The result lives in the MCP server's in-memory job store with a retention TTL. **If you don't poll within the TTL window, the job is swept and `perplexity_research_poll` returns `NOT_FOUND` even if Perplexity's side completed it.** This has been observed as a real failure mode when a conversational turn runs longer than the TTL.

**Recommended retention**: **120 min** from `startedAt`. Sweeper typically runs every 5 min and deletes jobs older than the TTL.

### Operational rules

1. **Wait about 60 s after `_start`, then poll every 30-60 s.** Each poll call blocks up to 45 sec; if status is `IN_PROGRESS` or `CREATED`, call again. Most jobs complete within 5 minutes, far inside the 120-minute retention.
2. **Never let more than 60 min elapse between polls.** Even with 120-min TTL, edge cases (sweep timing, clock skew) can shave 10-15 min off the effective window. 60-min poll cadence keeps a 30+ min safety margin.
3. **Track every active `jobId` in TodoWrite or a tracking task** so a session interruption (new user turn, agent dispatch, sleep) doesn't lose track of in-flight jobs.
4. **When launching N parallel `_start` calls, plan the polling schedule first.** Calculate worst-case polling load (N jobs × 45 sec each × repeat until COMPLETED) and decide whether to launch fewer streams or batch the polls more aggressively.
5. **For sessions where you can't guarantee timely polling** (long agent dispatches, multi-hour user gaps): use `perplexity_ask` or `perplexity_reason` instead — they're synchronous and return in 5-120 sec without retention risk.

### Polling cadence cheat-sheet

| Elapsed since `_start` | Action |
|---|---|
| 0-60 sec | Don't poll yet (the job won't be done; a poll can block 45 sec) |
| 1-5 min | Poll every 30-60 sec; most jobs complete here |
| 5-15 min | Poll every 2-3 min; verify the job isn't FAILED |
| 15-120 min | Unusual; poll every 10-15 min until COMPLETED or FAILED, well before the sweep |
| >120 min | Job is gone. Relaunch with a tighter prompt or switch to `_ask`/`_reason`. |

### Guardrails for "ensure success"

- **Tracking**: every `_start` call must be paired with a `TodoWrite` task ("Poll jobId X by [timestamp T0+1min]"). Lose the tracking, lose the job.
- **Time-budgeted launches**: don't fire 8 parallel Deep Research streams if the next user turn might be 2 hours away. Cap parallelism at ~3-4 streams per conversational segment.
- **Compact prompts**: tighter prompts finish sooner; most jobs take 60 to 300 s, and a broad prompt can run longer. Use the "FOR EACH of N projects, give EXACTLY this 6-line block" framing for batched verifications.
- **Fallback to `_ask` / `_reason` proactively**: any question that can be answered in 30-90 sec by `_ask` or `_reason` is a better choice than `_start` when polling discipline is in doubt. The decision matrix above captures this routing.
- **Persist results immediately on COMPLETION**: when `_poll` returns COMPLETED, save the response to disk before the job ages out.

### Known limitations

- **No keep-alive on poll**: polling a job in `IN_PROGRESS` state does NOT extend its TTL. If the job runs longer than 120 min from `startedAt`, it's swept regardless of how often you polled. Mitigation: don't launch jobs that will take >90 min (use tighter prompts).
- **No persistence across restarts**: the in-memory job store is wiped on MCP server restart. Mitigation: don't restart the MCP server while jobs are in flight.
- **No batch poll**: you must call `_poll(jobId)` once per job. For N parallel jobs, that's N × 45 sec worst case. Mitigation: schedule polls in parallel tool-use blocks where possible.

These are documented enhancement candidates for future iterations (e.g., env-overridable TTL, keep-alive on poll, disk-backed persistence).

## Worked examples

### Example 1 — Niche plugin verification (BAD use of Deep Research)

**Query**: "What is `trailofbits/skills`? And `armorclaude`? And `clawlock`? And `aikido`? And `42crunch-api-security-testing`? And `apiiro`? Maintainers, licenses, install commands, status?"

**Bad path**: Single `perplexity_research_start` call → Sonar Deep Research returns "Search results: None" or hallucinates specifics.

**Good path**:
1. `gh api repos/trailofbits/skills --jq '...'` — confirm exists, get metadata
2. `perplexity_ask` for "What is armorclaude? Confirm GitHub URL, maintainer, install command, status 2026."
3. `perplexity_ask` for "What is clawlock?" (will discover repo is private)
4. `gh api orgs/AikidoSec/repos` to find aikido-claude-plugin
5. `perplexity_ask` for 42crunch / apiiro Claude integration status
6. Synthesize the verified responses

Each decomposed call is cheap on its own and returns an answer you can check, while the single Deep Research call returns invented specifics. For current prices, check the live catalogue (see the matrix above).

### Example 2 — Broad technical topic (GOOD use of Deep Research)

**Query**: "Technical deep-dive: how to repurpose arbitrary npm-published MCP servers into a hardened Docker + 1Password + Envoy sidecar pattern."

**Good path**: Single `perplexity_research_start` call. The topic is broad, well-established (Envoy, Docker, MCP SDK), and Sonar Deep Research can synthesize across many sources.

### Example 3 — Single repo verification (BAD use of Deep Research)

**Query**: "Does plugin X exist in marketplace Y? What's its license? Maintainer?"

**Good path**: Read the marketplace's manifest directly via `WebFetch` or `gh api repos/<owner>/<repo>`. Zero AI cost, instant answer.

## Programmatic scoring

`scripts/route.py` provides a Python classifier. Call it as:

```bash
python scripts/route.py "<query string>"
python scripts/route.py --available perplexity-mcp,web "<query string>"   # rank only what this session has
python scripts/route.py --help
```

Returns JSON with `recommended_tool`, `fallback`, `ranked_tools` (see the chains above), the full `score` breakdown, a `rationale`, `schema_version` (currently `3`), `available_tools` (the recognized `--available` names, or `null` without the flag) and `ignored_tools`. The score counts **CVE / security-advisory IDs** (`CVE-`, `GHSA-`, `VMSA-`, `RHSA-`, `USN-`, `DSA-`, `PYSEC-`, `RUSTSEC-`, `GO-`) as proper-noun signals via `cve_advisory_count`, so multi-CVE verification queries route away from Deep Research:

```json
{
  "recommended_tool": "perplexity_ask + gh api (per-noun decomposition)",
  "fallback": "WebFetch on known URLs",
  "score": {
    "github_ref_count": 4,
    "plugin_marketplace_count": 1,
    "semver_count": 0,
    "quoted_identifier_count": 0,
    "cve_advisory_count": 2,
    "proper_noun_signals": 7,
    "recency_markers": 0,
    "niche_verification_markers": 1,
    "broad_markers": 0,
    "comparison_markers": 0
  },
  "rationale": "7 proper-noun signals (4 GitHub refs, 1 plugin@marketplace refs, 0 quoted IDs, 0 version refs, 2 CVE/advisory IDs) + 1 verification markers. Sonar Deep Research is documented to hallucinate names for queries like this. Decompose into per-noun perplexity_ask or gh api calls.",
  "schema_version": 3,
  "available_tools": null,
  "ignored_tools": []
}
```

The output also carries `ranked_tools`, left out above for brevity: here `perplexity_ask + gh api (per-noun decomposition)` (requires `perplexity-mcp`), then `WebFetch on known URLs` (requires `web`), each entry with a `note` on how to use it and `available: true`.

Useful for batch / scripted research planning. Not required for ad-hoc session use — the decision matrix above is sufficient.

## Tests

`tests/` holds a 51-test pytest suite (run `python -m pytest tests` from the skill root): the 30 schema 2 cases covering every decision-tree branch, all recognized advisory-ID families, the scoring invariants and the CLI surface, plus 21 schema 3 tests for each chain, the `--available` filtering and `--help`. The CVE cases pin the current calibration: a broad multi-CVE query must route to `perplexity_search` → WebFetch, never to Deep Research.

## What this skill is NOT

- Not a hook (doesn't fire automatically). You can wire it with a cost-gatekeeper hook for runtime soft-warnings if desired.
- Not a wrapper around Perplexity tools. Use the actual tools after consulting this skill.
- Not authoritative on cost. Prices change, so the matrix points at the live catalogue rather than carrying figures.

## Validation

Two-level validation expected before considering this skill production-ready:

1. **Static**: `python -m pytest tests/ -q` — all 30 cases must pass.
2. **Runtime**: invoke a research task on a real query, observe that Claude consults this skill before picking a tool, then verifies the tool choice produces verifiable output.

## Author

Allen Byrd. License: MIT.
