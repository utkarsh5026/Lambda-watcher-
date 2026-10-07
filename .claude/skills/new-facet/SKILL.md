---
name: new-facet
description: Add a new analysis facet (something detected in every archived package, like env vars, AWS services or secrets) and wire it through manifest, index, rebuild, diff and both renderers, keeping older archives readable. Use whenever a new kind of thing is being detected in a Lambda package.
argument-hint: "<facet name> [what it detects]"
---

# Add the `$ARGUMENTS` facet

This is the main cross-cutting change in this codebase. Skip one place and the failure is
silent: `lw reindex` drops the facet, or a diff quietly shows nothing. Use `services` as the
template, since it is the simplest list-shaped facet. Running
`grep -rn "services_for\|services_added\|\"services\"" src` lists every place it touches,
and the new facet needs the same places.

## The places, in pipeline order

1. **`src/lambda_watcher/analysis/<name>.py`**: a dataclass with `as_dict()`, and a
   `detect_<name>(root, inventory)` function. Scan `inventory.code_files` (first-party code
   only) unless the facet is about vendored code. Bound the work the way
   `detect_services` does, with a file cap and `read_text(..., max_bytes=...)`, because some
   packages hold thousands of files.
2. **`analysis/__init__.py`**: import it, add a field to `Analysis`
   (`field(default_factory=list)`), call it in `analyse()`, and add its key to
   `to_manifest()`. If it can be turned off, add a flag to `config.AnalysisConfig` with a
   working default, and document the flag in the commented template in `templates.py`.
3. **`db.py` `SCHEMA`**: `CREATE TABLE IF NOT EXISTS <name> (... version_id INTEGER NOT NULL
   REFERENCES versions(id) ON DELETE CASCADE ...)`. `IF NOT EXISTS` lets an existing
   `index.db` gain the table on open. `ON DELETE CASCADE` keeps `rm` and pruning correct.
   Add a `<name>_for(version_id)` accessor beside `services_for`.
4. **`ingest.py`, `Ingestor._index_version`** (the write path): one `bulk_insert` for the new
   table.
5. **`reindex.py`, `_insert`** (the rebuild path): it must produce exactly the same rows as
   step 4. Read the section with `manifest.get("<name>", [])`, never `manifest["<name>"]`,
   because every manifest written before this release lacks the key. Keep the existing
   `back-compat:` comment there accurate.
6. **`diffing/compare.py`**: add fields to `VersionDiff` (`<name>_added` / `<name>_removed`,
   or richer changes), and account for them in `is_empty` (or a version whose only change
   is this facet reads as "nothing changed"), in `counts()` / `headline()`, and in
   `as_dict()`, which is what `--json` prints. Compute them in `compare_versions`. Then pass
   `db.<name>_for(...)` for both sides in `diffing/build.py::diff_from_index`, which is the
   single place a diff is assembled from the index.
7. **Both renderers**: `diffing/render_text.py` and `diffing/render_html.py`.

Then decide whether these also need it: `lw show` (`cli.py`, where it reads
`db.services_for`) and the AI prompt (`ai/prompt.py`). In the prompt, everything sent goes
through `redact()`, and vendored files are never sent.

## The archive outlives the code (CLAUDE.md)

- **Adding a key to the manifest is additive. Don't bump `MANIFEST_SCHEMA`** unless an
  existing key changes shape.
- **"Absent" is not the same as "found nothing".** A version archived before this release
  has no rows for the facet. If it is diffed against a new version, everything the new
  version has will look *added*. Decide how to handle that and write the decision into a
  docstring. One option is to treat a manifest without the key as "not analysed" and report
  no change for that side. Another is to re-derive the facet from the version's `code/`
  directory on disk. Whatever you choose, never report a false "added".
- Mark every accommodation of the old shape with a `back-compat:` comment that says what old
  shape it handles, what breaks without it, and what would let it go.

## Tests

- A unit test for the detector in `tests/test_analysis.py`. Build the package with the
  `make_zip` fixture. Credential-shaped strings come from `conftest.fake_secret()`, never
  literals.
- A diff test in `tests/test_diff.py` covering added, removed and unchanged.
- A rebuild test in `tests/test_reindex.py`: ingest, **delete** `index.db`, reindex, and
  assert the facet's rows are identical.
- An old-archive test: write a manifest *without* the new key (or strip it from one that has
  it), reindex, then diff old against new, and assert on the actual output. A test that only
  checks nothing raised tests nothing here.

## Finish

If the demo `order-processor` exercises the facet, the documented captures will change, so
run `/regen-docs`. Then run `/check`.
