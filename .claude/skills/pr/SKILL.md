---
name: pr
description: Put the current work on a branch, run the gates, commit in the project's style, push, and open a pull request against main with the house-style description. Use when asked to open, ship or send a PR.
argument-hint: "[short description of the change]"
---

# Open a pull request

The user invoking this is the go-ahead to push a branch and open a PR. It is not the go-ahead
to push to `main` or to force-push.

## 1. Branch

Changes merge into `main` through PRs from `claude/<slug>` branches, for example
`claude/ai-deploy-brief`. If you are on `main`, create one, with a slug of two to four words
describing the change. If you are already on a feature branch, stay on it.

## 2. Gates, before committing

- `/check`, all three gates. Run the full suite before opening the PR, not just the affected
  tests.
- If the change touches `ingest.py`, `store.py`, `db.py`, `reindex.py`, `analysis/`,
  `config.py` or the AI explanation records, run `/compat-review` and fix what it finds.
- If what a command prints, or what a report renders, changed, run `/regen-docs` so the
  captures match.
- A new command needs everything `/new-command` lists, or `tests/test_docs.py` fails.

## 3. Commit

Match the existing history (`git log --oneline -20`):

- The subject is imperative, sentence case, and says what changed for the user, with no
  `feat:` prefix and no trailing period: "Show the report's file list as a folder tree" or
  "Open a file's diff in a side sheet instead of under its row".
- The body explains why: what was wrong before, and what this changes.
- Several independent changes go in several commits.
- End with the Co-Authored-By attribution line your instructions give for commits.

Stage specific files. Never add `.venv/`, scratch archives, or anything with a real
credential in it.

## 4. Push and open

```bash
git push -u origin <branch>
gh pr create --base main --title "<title>" --body-file <scratchpad>/pr-body.md
```

The title follows the commit-subject style. The body uses the shape of recent PRs
(`gh pr view 26` is a good example):

```markdown
## Summary

<One paragraph: what was wrong or missing, then what this PR does about it.>

### <Area or file, e.g. "The new card (`render_html.py`)">
- **<Point>:** <detail>
- ...

## Notes
- <Anything a reviewer needs: follow-ups, links that only work after merge, what was checked by hand.>

## Test plan
- [x] `ruff check .`
- [x] `python tools/check_docstrings.py`
- [x] Full `pytest` suite passes locally
- [ ] CI matrix on this PR
```

Tick only the boxes you actually ran in this session. End the body with the attribution line
your instructions give for PR descriptions.

## 5. Report

Give the PR URL, and mention anything you left unticked or unfinished.
