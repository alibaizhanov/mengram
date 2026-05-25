# Contributing to Mengram

Thanks for helping improve Mengram. This guide covers local setup, running tests, starting the API, and how we review pull requests.

## Prerequisites

- **Python 3.10+** (the repo targets 3.12; see `.python-version`)
- **Git**
- **Docker + Docker Compose** (optional, for running the self-hosted cloud API stack)
- An **OpenAI** or **Anthropic** API key when exercising LLM-backed endpoints locally

## Clone and install

```bash
git clone https://github.com/alibaizhanov/mengram.git
cd mengram

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -U pip
pip install -e ".[dev]"
```

The editable install exposes the `mengram` CLI and pulls in `pytest` via the `dev` extra (see `pyproject.toml`).

Optional extras for specific work:

| Extra | Install | Use case |
|-------|---------|----------|
| `api` | `pip install -e ".[api]"` | FastAPI / cloud server dependencies |
| `async` | `pip install -e ".[async]"` | `AsyncMengram` client |
| `langchain` | `pip install -e ".[langchain]"` | LangChain integration |
| `all` | `pip install -e ".[all]"` | Full optional stack |

## Run tests

From the repository root with your virtualenv active:

```bash
pytest
```

Tests live under `tests/`. Most unit tests mock HTTP and do not need cloud credentials or Docker.

To run a subset:

```bash
pytest tests/test_client.py -q
pytest -k "search" -q
```

## Run the API locally

Mengram has two deployment shapes: **cloud API** (PostgreSQL + pgvector) and **local engine** (Obsidian vault + SQLite). For backend/API work, use Docker Compose.

### Self-hosted stack (recommended)

From the repository root:

```bash
export OPENAI_API_KEY=sk-...   # required for extraction in the default compose config

docker compose up --build
```

This starts PostgreSQL (pgvector), Redis, and the Mengram API. The service listens on **http://localhost:8420**.

To stop and remove containers:

```bash
docker compose down
```

### Cloud folder compose (alternative)

The `cloud/` directory includes a slimmer stack (Postgres + API only):

```bash
cd cloud
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

### Local Python server (without Docker)

If you already have PostgreSQL running and `DATABASE_URL` configured:

```bash
pip install -e ".[api]"
export DATABASE_URL=postgresql://user:pass@localhost:5432/mengram
export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic

python -m cloud.api
# or: mengram api --port 8420
```

See `config.example.yaml` for local-engine (vault) settings when working on `engine/` rather than `cloud/`.

## Project layout (quick map)

| Path | Purpose |
|------|---------|
| `cloud/` | Hosted API, PostgreSQL store, Python SDK client |
| `engine/` | Local vault engine, embeddings, knowledge graph |
| `api/` | MCP and REST servers |
| `integrations/` | LangChain, CrewAI, OpenClaw helpers |
| `tests/` | Pytest suite |
| `cli.py` | `mengram` command-line entry point |

More detail: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Pull request guidelines

1. **Open an issue first** for large features or API changes. Small doc/test fixes can go straight to a PR.
2. **Fork** the repo and branch from `main`.
3. **Keep PRs focused** — one logical change per PR when possible.
4. **Link the issue** in the PR description (`Fixes #19`).
5. **Run tests** before requesting review (`pytest`).
6. **Update docs** when you change CLI flags, env vars, or public API behavior.

### Branch naming

Use short, descriptive prefixes:

- `docs/...` — documentation only
- `feat/...` — new features
- `fix/...` — bug fixes
- `chore/...` — tooling, deps, CI
- `test/...` — tests only

Examples: `docs/contributing`, `fix/search-pagination`, `feat/webhook-retries`.

### Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) style, matching recent history:

```
feat(cloud): add quota header to search responses
fix(parser): handle empty frontmatter blocks
docs: expand self-host Ollama model table
test(client): cover QuotaExceededError retry path
```

Use the imperative mood (`add`, not `added`). Scope is optional but helpful (`cloud`, `engine`, `cli`, `docs`).

## Code style

There is no enforced formatter or linter in CI yet. Please:

- Follow **PEP 8** and keep lines readable (~100 chars when practical).
- Match **existing patterns** in the file you edit (imports, naming, error handling).
- Prefer **clear names** over abbreviations; add docstrings for non-obvious public functions.
- Keep **secrets out of the repo** — use env vars (`OPENAI_API_KEY`, `MENGRAM_API_KEY`, `DATABASE_URL`).
- Avoid drive-by refactors unrelated to your change.

When adding dependencies, update `pyproject.toml` (and `requirements.txt` for Docker/cloud builds if applicable).

## Getting help

- [GitHub Issues](https://github.com/alibaizhanov/mengram/issues) — bugs and feature requests
- [GitHub Discussions](https://github.com/alibaizhanov/mengram/discussions) — questions and use cases
- [mengram.io/docs](https://mengram.io/docs) — product and API documentation

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
