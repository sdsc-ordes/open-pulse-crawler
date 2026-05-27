# Agent & Contributor Guidelines

This document describes conventions and rules for both human contributors and AI coding agents working on **Open Pulse Crawler**.

## Project Structure

```
src/open_pulse_crawler/   # Package source
  models.py               # Pydantic models (UserModel, OrgModel, RepoModel, GraphData)
  github_client.py        # GitHub API wrapper (multi-token, caching, rate-limiting)
  crawler.py              # BFS crawler engine
  cli.py                  # Typer CLI
  api.py                  # FastAPI REST API
  auth.py                 # Bearer-token auth helpers
  gui.py                  # Streamlit GUI
  io_utils.py             # JSON/CSV import & export
  visualization.py        # NetworkX + Matplotlib graph rendering
tests/                    # pytest test suite
docs/                     # All design & feature documentation
```

## Development Setup

```bash
uv pip install -e ".[dev,viz]"   # or: pip install -e ".[dev,viz]"
```

Python >= 3.10 is required (3.12+ recommended).

## Coding Conventions

- **Formatting**: `black` (default settings).
- **Linting**: `ruff check src/`.
- **Type hints**: Use everywhere. Prefer `from __future__ import annotations` in new files.
- **Models**: Use Pydantic `BaseModel` for data transfer objects.
- **Logging**: `logging.getLogger(__name__)` — never `print()` for diagnostics.
- **Secrets**: Never commit tokens or `.env` files. Use environment variables.

## Testing

```bash
pytest                        # run all tests
pytest tests/test_api.py -v   # single file, verbose
pytest --cov                  # with coverage
```

- Place tests in `tests/test_<module>.py`.
- Use `pytest` fixtures; avoid `unittest.TestCase`.
- API tests should use the FastAPI `TestClient` (from `httpx`).

## Git & Changelog

- Follow [Conventional Commits](https://www.conventionalcommits.org/) style.
- Update the `[Unreleased]` section of `CHANGELOG.md` with every user-visible change.
- Group entries under **Added**, **Changed**, **Fixed**, **Removed**.

## API Development

- All REST endpoints live under `/api/v1`.
- Public endpoints (e.g., `/api/v1/health`) require no auth.
- Protected endpoints require `Authorization: Bearer <token>` validated against the `API_TOKEN` env var.
- Use Pydantic models for request bodies and responses.
- Write corresponding tests in `tests/test_api.py` and update `docs/API.md`.

## Documentation

- User-facing quick-start stays in `README.md`.
- Detailed docs go in `docs/` as Markdown files.
- After adding or changing an API endpoint, update `docs/API.md`.

## AI-Agent-Specific Rules

1. **Read before you edit** — always read a file before modifying it.
2. **Minimal diffs** — change only what is needed; do not rewrite unrelated code.
3. **Run tests** after non-trivial changes (`pytest`).
4. **Check lints** after editing Python files.
5. **Never commit secrets** (`.env`, tokens, credentials).
6. **Update CHANGELOG.md** when completing a user-visible task.
