# Contributing to Eugene Plexus `workbench`

Thanks for your interest. This app reaches Eugene only through the public contracts in [`eugene-plexus/specs`](https://github.com/eugene-plexus/specs) — please read this before opening a PR.

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/) instead of a CLA. **Every commit must be signed off** with `git commit -s`:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match your `git config user.name` and `git config user.email`. CI blocks PRs whose commits are missing matching sign-offs.

If you forgot to sign off, fix the most recent commit:

```bash
git commit --amend -s --no-edit
```

…or for a whole branch:

```bash
git rebase --signoff main
```

The full DCO text is in [the specs CONTRIBUTING.md](https://github.com/eugene-plexus/specs/blob/main/CONTRIBUTING.md).

## Wire contract changes go in `specs`, not here

Workbench reaches Eugene only through public contracts: the gateway's OpenAI-compatible doors, Eugene's OpenID Connect sign-in, and the config trio its settings page uses. If Workbench needs something those do not offer, that is a gap in the contract. It is fixed in [`eugene-plexus/specs`](https://github.com/eugene-plexus/specs) for every client, never by a private route into the hub.

## Local setup

```bash
git clone https://github.com/eugene-plexus/workbench
cd workbench
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
cd web && npm ci
```

## Git hooks

We use [pre-commit](https://pre-commit.com/) to auto-format staged files with Ruff before they reach CI. Enable it once per clone:

```bash
pip install pre-commit
pre-commit install
```

## Style

- **Python 3.12+**, Ruff for lint and format, mypy strict. The `_generated/` directory is excluded.
- **TypeScript strict**, ESLint and Prettier, in `web/`.
- **Comments say why**, never what.
- **Model output is untrusted.** Answers render as Markdown with no raw HTML, and an image an answer names is never fetched (`workbench-v1.md` W5). Keep it that way.
- **Plain words on screen.** The console's copy rules apply here too.

## Running checks

```bash
ruff check . && ruff format --check . && mypy src/ && pytest
python scripts/codegen.py && git diff --exit-code src/eugene_plexus_workbench/_generated/
cd web && npm run typecheck && npm run lint && npm test && npm run build
```

## Reporting issues

File issues at <https://github.com/eugene-plexus/workbench/issues>. Cross-component questions belong in [specs issues](https://github.com/eugene-plexus/specs/issues).
