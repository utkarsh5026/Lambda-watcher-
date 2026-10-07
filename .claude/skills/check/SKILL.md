---
name: check
description: Run the gates CI runs on every PR (ruff check, the docstring checker, pytest), cheapest first, and fix what fails. Use before committing, before opening a PR, or when asked whether a change is done.
argument-hint: "[pytest selector, e.g. tests/test_diff.py or -k rename]"
---

# Run the CI gates locally

CI is `.github/workflows/ci.yml`: `ruff check`, then `tools/check_docstrings.py`, then pytest
across a Python × OS matrix. Run the same three here, in this order. The first two take
under a second, so never skip them to save time.

If `.venv/` is missing, create it first:
`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.

## 1. Lint

```bash
.venv/bin/ruff check .
```

`ruff check --fix` is fine for the mechanical rules (unused imports, `UP` rewrites). Two
things CI deliberately allows, so leave them alone:

- **Never run `ruff format`.** It would collapse the hand-aligned dict literals and tables
  the codebase uses on purpose (CLAUDE.md, "CI/CD").
- **Don't rewrite `Optional[X] = typer.Option(...)`** in `cli.py`. `UP045`, `UP007` and
  `B008` are ignored because Typer evaluates those defaults at import time.

## 2. Docstrings

```bash
python3 tools/check_docstrings.py            # all of src/; pass file paths to narrow it
```

Every function, method, class, property, nested closure and `__init__` under `src/` needs a
docstring, and `src/` is at 100%. Write a real one: a one-line summary that carries the
meaning, a blank line, then why it is that way. No `Args:`/`Returns:` blocks restating the
type hints. CLAUDE.md, "Write code a stranger can read", has the full rules.

## 3. Tests

- If arguments were given, run only them: `.venv/bin/python -m pytest $ARGUMENTS`
- Otherwise run the test files for what changed first, for fast feedback, then the whole
  suite before you call the change done: `.venv/bin/python -m pytest`. The full suite takes
  about three minutes of wall time, mostly I/O wait, so start it in the background and
  keep working.

Which tests cover what:

| Changed | Run first |
|---|---|
| `cli.py`, `helptext.py` | `tests/test_cli.py tests/test_helptext.py tests/test_docs.py` |
| `ingest.py`, `store.py`, `db.py`, `reindex.py` | `tests/test_ingest.py tests/test_reindex.py` |
| `analysis/` | `tests/test_analysis.py tests/test_diff.py tests/test_reindex.py` |
| `diffing/` | `tests/test_diff.py tests/test_render_html.py tests/test_docs.py` |
| `ai/` | `tests/test_ai.py` |
| `watcher.py`, `heartbeat.py` | `tests/test_watcher.py tests/test_heartbeat.py` |
| `service.py` | `tests/test_service.py tests/test_no_window.py` |
| `config.py`, `templates.py` | `tests/test_config.py` |
| `extract.py`, `identify.py`, `utils.py` | the matching `tests/test_<module>.py` |

What some failures mean:

- **`tests/test_docs.py`, a capture mismatch**: renderer output changed, so the documented
  terminal blocks are stale. Regenerate them with `/regen-docs`. Never hand-edit a capture to
  make the test pass.
- **`tests/test_docs.py`, a coverage failure** ("never names", "missing from the README's
  command table"): a command was added or renamed without its site entry or README row.
  `/new-command` lists all of them.
- **`tests/test_helptext.py`**: a command's `helptext.COMMANDS` entry and `cli.py` are out of
  step, or one of its examples names an option that no longer exists.
- **Git-mirror tests**: they commit, so they need `git config --global user.name` and
  `user.email` to be set.

## Report

List each gate as passed or failed. For a failure, quote the failing output and say what you
changed to fix it. Only say the suite passed if you watched it pass in this session. If you
ran only part of it, say which part.
