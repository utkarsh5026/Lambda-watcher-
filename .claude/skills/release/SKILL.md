---
name: release
description: Cut a lambda-watcher release by bumping the version, writing the release commit, and tagging it. Pushing the tag publishes to PyPI, which cannot be undone.
argument-hint: "<patch | minor | major | X.Y.Z>"
disable-model-invocation: true
---

# Release lambda-watcher

Pushing a `v*` tag runs `.github/workflows/release.yml`. That workflow refuses a tag that does
not equal `v<pyproject version>`, re-runs the suite on the tagged commit, publishes to PyPI
through trusted publishing, and then cuts a GitHub release with generated notes. **PyPI never
lets a version be re-uploaded.** Everything up to the push is reversible; the push is not.

## 1. Preconditions

```bash
git fetch origin
git status                       # on main, clean, not behind origin/main
git describe --tags --abbrev=0   # the last release, e.g. v0.6.0
```

If you are not on `main`, the tree is dirty, or the branch is behind, stop and say so.

## 2. Decide the version

The bump requested is `$ARGUMENTS`. If nothing was given, propose one and wait for the user to
confirm it:

```bash
git log --first-parent --oneline <last tag>..HEAD
```

The project is pre-1.0 and has used:

- **minor**: new commands, or a user-visible change (a redesigned report, new output), as
  long as existing archives stay readable.
- **patch**: fixes, and test-only or docs-only changes.

A change that leaves an older archive unreadable is a bug to fix before releasing, not a
reason for a major bump. See CLAUDE.md, "The archive outlives the code that wrote it".

## 3. Bump it in both places

- `pyproject.toml`, `[project] version = "X.Y.Z"`
- `src/lambda_watcher/__init__.py`, `__version__ = "X.Y.Z"`

Nothing else carries the version. In particular, the docs banners are normalised by
`tests/test_docs.py::test_a_version_bump_is_not_a_documentation_edit`, so don't edit the
captures.

## 4. Gate it

Run the whole of `/check`: ruff, the docstring checker, and the **full** pytest suite. A
release must not ship what CI would reject, and the tag re-runs the suite anyway. Running it
here is how you find out first.

## 5. The release commit

Stage only the two files. The message follows the existing releases (`git show v0.6.0`):

```
Release X.Y.Z

Since A.B.C: <what a user gets, in a sentence or two per change, citing
PRs as (#NN) or commits by short hash>. <Whether existing archives are
affected>, so a <minor|patch> bump.
```

End the message with the Co-Authored-By attribution line your instructions give for commits.

## 6. Tag, then confirm before pushing

```bash
git tag -a vX.Y.Z -m "lambda-watcher X.Y.Z"      # annotated, like every earlier tag
```

**Stop here and ask the user before pushing.** Show them the commit, the tag, and the
`git log --first-parent` summary. Pushing publishes to PyPI. Only after they say yes:

```bash
git push origin main
git push origin vX.Y.Z
```

Then give them the Actions run, `gh run list --workflow=release.yml --limit 1`, and
`https://pypi.org/project/lambda-watcher/X.Y.Z/` for when it finishes.

If anything goes wrong before the tag is pushed, delete the local tag (`git tag -d vX.Y.Z`)
and fix the problem. Once the tag is pushed and PyPI has the files, the only fix is a new
patch release.
