---
name: regen-docs
description: Regenerate the captured terminal output in README.md and docs/index.html, plus the published demo reports, from a real run of docs/examples/build_demo.py. Use after any change to what a command prints or how a report renders, or when tests/test_docs.py reports lines that are "not output the tool produced".
argument-hint: "[--publish]"
---

# Regenerate the documentation captures

Every terminal block in `README.md` and on the Pages site (`docs/index.html`) is real output
from `docs/examples/build_demo.py`. `tests/test_docs.py` re-runs the builder and fails on any
documented line the tool did not print. **Never type or touch up captured output by hand.**
Copy it from a run.

## 1. Capture

```bash
.venv/bin/python docs/examples/build_demo.py > <scratchpad>/captures.txt
```

Each capture is printed as a `[title]` line, then `$ lambda-watcher ...`, a rule, and the
output. The order matches the page. Nothing touches the network, AWS or the real archive:
the builder redirects `HOME` and `LAMBDA_WATCHER_HOME` and never runs
`start`/`stop`/`restart`.

## 2. Find what is stale

```bash
.venv/bin/python -m pytest tests/test_docs.py -q
```

A failure lists up to 15 stale lines as `[<block caption>] <line>`. If there are more, fix
those and rerun.

## 3. Paste the new output in

- **README.md**: the fenced blocks whose first line is `$ lw ...`. Replace the lines under the
  prompt with the matching capture. Keep `$ lw` as the prompt, even though the capture prints
  `$ lambda-watcher`.
- **docs/index.html**: each `<div class="slab">` has a `<span class="slab-cap">` naming the
  command and a `<pre>` with the output. Inside the `<pre>`:
  - HTML-escape the text: `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`, `"` → `&quot;`,
    `'` → `&#x27;`.
  - **Keep the colour spans** (`<span class="c-add">`, `c-white`, `c-dim` and so on) on the
    same tokens they wrapped before. The test strips the tags before comparing, so it never
    complains about a lost colour. Losing them anyway makes the page worse.
- Paths come out as `~/.lambda-watcher` and `~/Downloads`, and the demo's version
  directories are `0001-7fc98e0e` and `0002-7f887035`. Different hashes mean the demo's
  contents or build stamp changed. That is a bigger change than a renderer tweak, so find
  out why.
- Only a few things may vary between runs: timestamps, "just now" / "N minutes ago", git
  commit ids, `doctor`'s free disk space, and the version in the `lambda-watcher X.Y.Z`
  banner. Leave those as they are on the page, and don't churn them just because this run
  printed different values.
- If prose claims a number the tool computes, such as "61 changed files, 56 of them
  vendored", the test names it. Update the prose in both `docs/index.html` and `README.md`.

## 4. Published reports (`--publish`, or whenever report HTML changed)

```bash
.venv/bin/python docs/examples/build_demo.py --publish > /dev/null
git status docs/examples/
```

This refreshes `docs/examples/report/` (the live report the site links to) and
`docs/examples/report-explained/` (the same report with
`docs/examples/sample-explanation.json` rendered into it). Look at the diff. The builder
masks the two credential-shaped fixtures in published copies, so a raw-looking key in
`git diff` means something went wrong. Stop, and do not commit it, because GitHub push
protection will reject the push anyway.

If the screenshots in `docs/images/` show something the change altered, say so. They are not
regenerated automatically.

## 5. Verify

```bash
.venv/bin/python -m pytest tests/test_docs.py -q
```

It must pass with nothing skipped except the Windows-only skips. Report which blocks changed
and why. The why is usually the renderer change that made them stale.
