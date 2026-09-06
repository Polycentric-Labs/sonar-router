# Contributing

Thanks for considering a contribution to sonar-router.

## Setup

`scripts/route.py` and its tests use only the Python standard library plus
`pytest`. Clone the repository and make sure Python 3 and `pytest` are
available; there is no separate dependency-install step.

## Running the tests

```bash
python -m pytest tests/ -q
```

The suite covers every branch of the decision tree, all recognized
advisory-ID families, the scoring invariants, and the CLI argument-parsing
surface; see [README.md](README.md) for the decision matrix the tests check
against.

## Reporting issues and proposing changes

Open a GitHub issue to discuss a bug, or a change to the decision matrix or
the scoring heuristics, before sending a pull request. A small fix, such as a
typo, a broken link, or a doc clarification, can go directly via pull
request.

## License

Contributions are submitted under the project's [MIT License](LICENSE).

## AI-assisted contributions

You may use AI tools while contributing. Two rules apply, and they mirror the
project's own disclosure in [`docs/ai-assistance.md`](docs/ai-assistance.md):

- **You are the author.** Understand the change and be able to explain it in
  your own words; review questions are answered by you, not by a tool. Pull
  requests opened by autonomous agents are closed.
- **Disclose significant assistance.** Say so in the pull request description,
  or add an `Assisted-by: <tool>` trailer to the commit message. Do not add
  `Co-authored-by` trailers naming AI tools: they create a contributor identity
  in the repository record, and only people are contributors here.
