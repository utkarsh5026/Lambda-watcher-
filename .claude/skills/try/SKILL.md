---
name: try
description: Run the real lw CLI against a throwaway sandbox archive seeded with the demo order-processor function, to see a change working end to end without touching the real ~/.lambda-watcher archive or the installed background watcher. Use when asked to run, try, demo or eyeball the tool, or to confirm a CLI or report change works for real and not only in tests.
argument-hint: "[lw command and args, e.g. diff order-processor]"
---

# Try it in a sandbox

This machine probably has a real archive at `~/.lambda-watcher` and a real background watcher
registered with the OS. Neither may be touched while you develop. Every `lw` command you run
goes through the sandbox below, unless the user has explicitly asked you to look at their
real archive.

## Build the sandbox (once per session)

Put it in your scratchpad directory if you have one, otherwise use `mktemp -d`:

```bash
S=<scratchpad>/lw-sandbox
rm -rf "$S" && mkdir -p "$S/home/Downloads"
export HOME="$S/home" LAMBDA_WATCHER_HOME="$S/home/.lambda-watcher"
.venv/bin/python -c "import sys; from pathlib import Path; from lambda_watcher.demo import stage_downloads; stage_downloads(Path(sys.argv[1]))" "$HOME/Downloads"
.venv/bin/lw backfill "$HOME/Downloads"
```

Expected result: `new v0001`, `new v0002`, `unchanged v0002`.

- **Redirect both variables.** `LAMBDA_WATCHER_HOME` moves the archive. `HOME` moves
  `~/Downloads` and the per-user state that `status` and `doctor` read.
- **Use `backfill`, not `lw ingest "$HOME"/Downloads/*.zip`.** The glob sorts
  `order-processor (1).zip` ahead of `order-processor.zip`, so v2 gets archived as v0001 and
  every diff reads backwards. `backfill` replays in mtime order, and `stage_downloads` spaces
  the zips hours apart for exactly that reason.
- **Shell state does not persist between Bash calls.** Put the two `export`s at the start of
  every command, or prefix each one with
  `HOME=... LAMBDA_WATCHER_HOME=... .venv/bin/lw ...`.

## Run what was asked

With arguments, run `.venv/bin/lw $ARGUMENTS` in the sandbox. Without any, run the commands
that exercise whatever changed, for example:

```bash
.venv/bin/lw                              # the status dashboard; must exit 0
.venv/bin/lw ls
.venv/bin/lw diff order-processor
.venv/bin/lw show order-processor latest
.venv/bin/lw report order-processor --no-open
.venv/bin/lw doctor                       # exits 1 when something is wrong, by design
```

Set `COLUMNS=100` (or wider) so Rich does not wrap tables mid-row.

The HTML report goes to `$LAMBDA_WATCHER_HOME/reports/order-processor/latest.html`, and the
archive index to `reports/index.html`. Read the HTML, or open it in a browser tool if you have
one, when the change is visual.

To test your own package rather than the demo, write a zip into `$HOME/Downloads` with
`zipfile`, pinning `ZipInfo(date_time=...)` the way `tests/conftest.py::make_zip` does, then
run `lw ingest <path>`.

## Never, even in the sandbox

- `lw start`, `lw stop`, `lw restart`, and `lw setup` without `--no-service`. These register
  or stop a real OS service: launchd, the systemd user session or Task Scheduler. That
  service manager does not live under `$HOME`, so redirecting `HOME` does not contain it. A
  project hook asks the user before any of these runs.
- Anything that calls a real AI service. Use `lw explain <fn> --dry-run` to see the prompt.
  Unset `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY` and
  `AZURE_OPENAI_ENDPOINT` for the command (`env -u ...`) so no key from the environment gets
  picked up.

## Report

Paste the output that shows the change working, or failing, and give the sandbox path so the
user can look at the archive themselves.
