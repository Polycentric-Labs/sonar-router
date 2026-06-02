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
| **Broad academic/established topic** (e.g., "how does Envoy credential_injector work", "OWASP Top 10", "what is SLSA L3") | `perplexity_research_start` (Sonar Deep Research) | ~$1-3 | 5-15 min | async-job tools required for non-timeout; avoid if query mentions ≥3 specific proper nouns |
| **Specific factual question** (e.g., "does `clawlock` plugin exist?", "what's its install command?") | `perplexity_ask` (Sonar Pro) | ~$0.05-0.20 | 5-30 sec | First choice for plugin / repo verification |
| **Logical analysis / comparison** (e.g., "compare X vs Y given these constraints") | `perplexity_reason` (Sonar Reasoning Pro) | ~$0.10-0.50 | 30-120 sec | Use when synthesis + reasoning matters more than fresh search |
| **Find specific URLs / recent news** | `perplexity_search` → `WebFetch` ranked results | ~$0.02 + free fetches | 5-15 sec | Use to discover candidate URLs, then fetch them directly |
| **Known GitHub repo verification** | `gh api repos/<owner>/<repo>` (no AI) | $0 | <1 sec | Always preferred for "does this repo exist?" / "what's its star count?" |
| **Known doc page** | `WebFetch <url>` | $0 | 2-10 sec | Authoritative source > AI summary |
| **Multi-file / multi-repo research** | `Agent` subagent (general-purpose / Explore) | depends on inner calls | 1-10 min | Isolates context, handles many file reads in one shot |

## Routing heuristic (the rule)

If the query mentions **≥3 specific proper nouns that all need to be verified individually** (repo names, plugin names, version numbers, CVE / security-advisory IDs, organization names, file paths, specific feature names) → **do NOT use Sonar Deep Research**. It will hallucinate at least one of them.

Decompose into per-noun verifications via:
- `gh api repos/<owner>/<repo>` for GitHub-hosted things
- `perplexity_ask` per individual question
- `WebFetch` on known canonical URLs

Then synthesize the verified inputs yourself OR via `perplexity_reason`.

## Polling discipline (added 2026-05-25, retention bumped to 120 min)

`perplexity_research_start` returns a `jobId` immediately and runs the Sonar Deep Research job asynchronously (typical 10-25 min, outliers 5-30+ min). The result lives in the MCP server's in-memory job store with a retention TTL. **If you don't poll within the TTL window, the job is swept and `perplexity_research_poll` returns `NOT_FOUND` even if Perplexity's side completed it.** This has been observed as a real failure mode when a conversational turn runs longer than the TTL.

**Recommended retention**: **120 min** from `startedAt`. Sweeper typically runs every 5 min and deletes jobs older than the TTL.

### Operational rules

1. **Poll at least once every 15-20 min** after `_start`. Each poll call blocks up to 45 sec; if status is `IN_PROGRESS` or `CREATED`, call again. With 120-min TTL, polling every 15 min gives you ~6 chances to catch a completed job before sweep.
2. **Never let more than 60 min elapse between polls.** Even with 120-min TTL, edge cases (sweep timing, clock skew) can shave 10-15 min off the effective window. 60-min poll cadence keeps a 30+ min safety margin.
3. **Track every active `jobId` in TodoWrite or a tracking task** so a session interruption (new user turn, agent dispatch, sleep) doesn't lose track of in-flight jobs.
4. **When launching N parallel `_start` calls, plan the polling schedule first.** Calculate worst-case polling load (N jobs × 45 sec each × repeat until COMPLETED) and decide whether to launch fewer streams or batch the polls more aggressively.
5. **For sessions where you can't guarantee timely polling** (long agent dispatches, multi-hour user gaps): use `perplexity_ask` or `perplexity_reason` instead — they're synchronous and return in 5-120 sec without retention risk.

### Polling cadence cheat-sheet

| Elapsed since `_start` | Action |
|---|---|
| 0-5 min | Don't poll yet (job won't be done; wastes 45 sec) |
| 5-10 min | First poll; expect `IN_PROGRESS` |
| 10-30 min | Poll every 5-10 min; most jobs complete here |
| 30-60 min | Poll every 10-15 min; slow-but-not-stuck jobs land here |
| 60-90 min | Poll every 15-20 min; verify the job isn't FAILED |
| 90-120 min | Poll aggressively; less than 30 min until sweep |
| >120 min | Job is gone. Relaunch with a tighter prompt or switch to `_ask`/`_reason`. |

### Guardrails for "ensure success"

- **Tracking**: every `_start` call must be paired with a `TodoWrite` task ("Poll jobId X by [timestamp T0+15min]"). Lose the tracking, lose the job.
- **Time-budgeted launches**: don't fire 8 parallel Deep Research streams if the next user turn might be 2 hours away. Cap parallelism at ~3-4 streams per conversational segment.
- **Compact prompts**: tighter prompts complete in ~10 min (well inside the window) vs. broad prompts that can take 25+ min. Use the "FOR EACH of N projects, give EXACTLY this 6-line block" framing for batched verifications.
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

Per-call cost is ~$0.05-0.20; total <$2 for 6-10 queries. Single Deep Research call would also be ~$3 but returns garbage.

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
```

Returns JSON with `recommended_tool`, `fallback`, the full `score` breakdown, a `rationale`, and `schema_version` (currently `2`). The score counts **CVE / security-advisory IDs** (`CVE-`, `GHSA-`, `VMSA-`, `RHSA-`, `USN-`, `DSA-`, `PYSEC-`, `RUSTSEC-`, `GO-`) as proper-noun signals via `cve_advisory_count`, so multi-CVE verification queries route away from Deep Research:

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
  "schema_version": 2
}
```

Useful for batch / scripted research planning. Not required for ad-hoc session use — the decision matrix above is sufficient.

## Tests

`tests/` holds a 30-case pytest suite (run `python -m pytest tests` from the skill root) covering every decision-tree branch, all recognized advisory-ID families, the scoring invariants, and the CLI surface. The CVE cases pin the current calibration: a broad multi-CVE query must route to `perplexity_search` → WebFetch, never to Deep Research.

## What this skill is NOT

- Not a hook (doesn't fire automatically). You can wire it with a cost-gatekeeper hook for runtime soft-warnings if desired.
- Not a wrapper around Perplexity tools. Use the actual tools after consulting this skill.
- Not authoritative on cost — Perplexity prices change; the matrix is calibrated to 2026-05 pricing. Validate against current pricing periodically.

## Validation

Two-level validation expected before considering this skill production-ready:

1. **Static**: `python -m pytest tests/ -q` — all 30 cases must pass.
2. **Runtime**: invoke a research task on a real query, observe that Claude consults this skill before picking a tool, then verifies the tool choice produces verifiable output.

## Author

Allen Byrd. License: MIT.
