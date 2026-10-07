---
name: compat-review
description: Review the current branch's changes for anything that would stop an archive written by an older release from reading correctly. That covers manifest and index shape, on-disk layout, edits that skip the manifest, and removed back-compat code. Use before opening a PR that touches ingest, store, db, reindex, analysis, config or the AI explanation records, or when asked whether a change is safe for existing users.
argument-hint: "[base ref, default main]"
---

# Will an existing archive still read correctly?

People install this tool, forget about it, and come back months later. Whatever is on their
disk has to keep working under whichever release is installed now, with no migration step
(CLAUDE.md, "The archive outlives the code that wrote it"). These bugs never raise. They show
up as a diff with no lines in it, a label that vanished, or a file blamed for its encoding.
So read the change; don't just trust a passing suite.

This is a review: report what you find, and don't edit unless asked.

## Gather the change

The base ref is `$ARGUMENTS`. If that is empty, use `main`.

```bash
BASE=<base ref>
git diff "$BASE"...HEAD --stat
git diff "$BASE"...HEAD
git diff; git status --short          # uncommitted work counts too
```

## What to check

1. **Manifest shape**: `Analysis.to_manifest`, `Ingestor` writing `manifest.json`,
   `Store.patch_manifest`, and `reindex._insert`.
   - Is a key renamed, retyped or restructured? Then old manifests must still reindex. Look
     for `manifest.get(key, default)` and a branch that reads the old shape.
   - Is a key new? Then a manifest without it must rebuild as "empty", or better as "not
     analysed". It must never produce a false "added" in a diff.
   - Does the shape genuinely break? Then `analysis.MANIFEST_SCHEMA` needs a bump, and the
     reader still handles the old number.
2. **Index shape**: `db.SCHEMA`, `db.SCHEMA_VERSION`.
   - New tables and columns have to work on an `index.db` that already exists:
     `CREATE TABLE IF NOT EXISTS`, or a guarded `ALTER`.
   - Does everything new in SQLite also come from a manifest? `reindex` has to be able to
     rebuild it. Nothing may live only in the index.
3. **Disk layout**: `store.py` paths, directory names, `aliases.json`, `reports/`, and
   `versions/<dir>/explanations/`.
   - When something moves, the old location must still be found and relocated on first
     access. Follow the pattern of `Store.repo_dir` and `LEGACY_REPO_DIRNAMES`.
   - Stored paths read from the index go through `store.posix_stored_dir`, because older
     Windows releases wrote backslashes.
4. **Edits**: `rename`, `label`, `--alias`, `merge`, and any new command that changes what
   the index records.
   - The disk must be written first and the index second. Otherwise `lw reindex` reverts the
     edit.
   - Is there a test that deletes `index.db`, reindexes, and asserts the edit survived?
5. **Config**: renaming or removing a `config.yaml` field.
   - Unknown keys are ignored and `lw doctor` reports them (`config.unknown_keys`). So a
     rename silently drops the user's old setting. Is the old name still read?
   - Nothing new may be required before the tool works. Every field needs a working default.
6. **Removed accommodations**: `git diff "$BASE"...HEAD | grep -n '^-.*back-compat:'`.
   Anything deleted needs a stated reason why no archive in the wild can still have that
   shape. "Looks dead" is not a reason.
7. **New accommodations**: each one carries a `back-compat:` comment on the block itself.
   The comment and the docstring together say three things: what old shape it handles, what
   breaks without it, and what would let it go.
8. **Tests build the old shape**: write the backslashes, create the legacy directory, strip
   the key from the manifest, then assert on the answer. A test whose only assertion is
   "it did not crash" tests nothing here.

## Report

List findings most severe first. For each one give `file:line`, what an older archive
contains, and what the new code does with it. Say concretely what the user would see (an
empty diff, a missing label, a lost alias), and give the test that would catch it. If
nothing is wrong, say which of the eight areas the change touches and why each one is safe.
