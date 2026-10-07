<div align="center">

# lambda-watcher

**Every Lambda zip you download becomes a version you can diff.**

[![PyPI](https://img.shields.io/pypi/v/lambda-watcher?color=4f46e5&label=pypi)](https://pypi.org/project/lambda-watcher/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://pypi.org/project/lambda-watcher/)
[![CI](https://github.com/utkarsh5026/lambwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarsh5026/lambwatch/actions/workflows/ci.yml)
[![Licence: Apache 2.0](https://img.shields.io/badge/licence-Apache%202.0-blue)](https://github.com/utkarsh5026/lambwatch/blob/main/LICENSE)

[**How it works**](https://utkarsh5026.github.io/lambwatch/) ·
[**Live example report**](https://utkarsh5026.github.io/lambwatch/examples/report/v0001-v0002.html) ·
[**Install**](#install) ·
[**Commands**](#commands)

<a href="https://utkarsh5026.github.io/lambwatch/examples/report/v0001-v0002.html"><img src="https://raw.githubusercontent.com/utkarsh5026/lambwatch/main/docs/images/report-summary.png" alt="The HTML report lambda-watcher writes for a new version: files changed, lines, dependencies, package size, new secret findings, and the environment variables and AWS services the new code needs before it is deployed" width="860"></a>

</div>

If your deploy ritual is *"download the current function zip as a backup, then push the new one"*,
you end up with a folder of near-identical archives and no practical way to answer
**"what changed between the 2nd one and the 10th?"**

lambda-watcher watches your Downloads folder, files every Lambda zip that lands there as a
numbered version, and shows you what actually changed. You keep downloading zips; it does the rest.

- **One command to set up.** `lw setup` and it runs in the background from then on, on macOS,
  Linux and Windows. No config file required.
- **Diffs a person can read.** Your three changed files, not the 3,000 vendored ones around
  them — and `boto3 1.34.0 → 1.35.20` instead of 400 changed `site-packages/` files.
- **Catches what breaks a deploy.** New environment variables, new AWS services (and so new IAM
  permissions), and hardcoded credentials are called out before you ship.
- **Never touches AWS.** No credentials, no API calls. It reads the zips already on your disk
  and works offline.

---

## Install

```bash
uv tool install lambda-watcher      # or: pipx install lambda-watcher
lw setup
```

That is the whole thing. `setup` finds your downloads folder, offers to import any zips already
sitting in it, and starts the watcher in the background — a launchd agent on macOS, a systemd
user service on Linux, a scheduled task on Windows — so it comes back after a reboot without you
thinking about it. Then just keep downloading zips.

Needs Python 3.10+. Either installer puts `lw` (and its long name, `lambda-watcher`) on your
`PATH` in its own environment; plain `pip install lambda-watcher` works too.

**Nothing to download yet?** `lw demo` runs three downloads of a sample Lambda through the real
pipeline — in a scratch archive of its own, so nothing appears in yours — and shows you the diff
and the HTML report it produces.

<details>
<summary>Install from a checkout instead</summary>

```bash
git clone https://github.com/utkarsh5026/lambwatch.git
cd lambwatch
python3 -m venv .venv
.venv/bin/pip install -e .
```

</details>

## Everyday use

```bash
lw                                         # is it running, and what has it caught?
lw diff order-processor                    # what changed in the last version
lw diff order-processor --from 2 --to 10   # any two versions
lw report                                  # every function on one page, in your browser
lw open order-processor                    # the whole history, in your editor
```

Here is the watcher picking up three downloads of the same function:

```
$ lw watch
lambda-watcher 0.6.0 — archiving into ~/.lambda-watcher
watching ~/Downloads. Press Ctrl-C to stop.
               new  order-processor v0001  order-processor.zip — archived a new version
               new  order-processor v0002  order-processor (1).zip — 2 added, 2 modified, 9 renamed, 55 vendored
                    +24/-5 lines · new: 1 env var, 1 AWS service, 3 secrets
                    report: ~/.lambda-watcher/reports/order-processor/v0001-v0002.html
         unchanged  order-processor v0002  order-processor (2).zip — identical to version 2
done
```

The last line is the one that matters: the same code, downloaded again, is recorded as
`unchanged` rather than becoming a bogus version 3.

<details>
<summary>Running it by hand, or setting it up piece by piece</summary>

```bash
lw watch      # run in the foreground instead; Ctrl-C stops it
lw start      # install and start the background watcher
lw stop       # stop it (--remove also unregisters it)
lw restart    # after editing the config
lw doctor     # check everything, name the fix for each problem, exit 1 if there is one
lw logs -f    # follow what the watcher is doing right now
```

Already have a folder of old backups? Import them oldest-first so the version numbers match real
history:

```bash
lw backfill ~/Downloads/lambda-backups --dry-run   # check the names first
lw backfill ~/Downloads/lambda-backups
```

If your platform's service manager is unavailable — WSL without a systemd user session, say —
`lw start` falls back to a plain background process and tells you it will not survive a reboot.
[docs/autostart.md](https://github.com/utkarsh5026/lambwatch/blob/main/docs/autostart.md) has the
manual recipes. `lw --install-completion` teaches your shell to tab-complete function names.

</details>

## How it works

Every time a `.zip` lands in your Downloads folder, lambda-watcher:

| | Step | What happens |
|:-:|---|---|
| 1 | **Waits** | until the download has actually finished — no half-written files. |
| 2 | **Names it** | from the filename, a sidecar `get-function` JSON, or the archive's contents. `order-processor.zip`, `order-processor (1).zip` and `order-processor-2026-03-01.zip` all land under one function. |
| 3 | **Extracts safely** | path traversal, zip bombs and encrypted archives are refused, not trusted. |
| 4 | **Hashes the content** | not the file, so re-downloading unchanged code is `unchanged` rather than a new version. |
| 5 | **Analyses** | runtime, handler, dependencies (declared *and* the versions actually vendored in the zip), the environment variables the code reads, the AWS services it calls, and hardcoded credentials. |
| 6 | **Archives** | as version *N* with a `manifest.json`, indexed in SQLite and committed to a per-function git repo tagged `v0004`. |

## Why the diffs are readable

A raw `diff -r` between two Lambda zips is unusable: thousands of vendored dependency files drown
three lines of real change. lambda-watcher answers the questions you actually have, in order:

| What you get | In practice |
|---|---|
| **Your code, separated from theirs** | `node_modules/`, `site-packages/` and friends are classified as vendored and hidden by default. Three changed files, not 3,000. |
| **Dependencies as versions, not files** | A `boto3` upgrade shows as `boto3 1.34.0 → 1.35.20`, parsed from the `dist-info` actually shipped in the zip. |
| **Config impact, called out** | A new `os.environ["QUEUE_URL"]` is flagged as *"this must exist in the function's environment before you deploy"*; a new `boto3.client("sqs")` as *"the execution role may need new IAM permissions"*. These break deploys and never show up in a file diff. |
| **A reindent is not a rewrite** | A file whose only change is whitespace or line endings is labelled *whitespace only* instead of reprinted line by line. `lw diff --whitespace` shows it anyway. |
| **Minified bundles are diffed by word** | A changed digit in an 8 KB one-line bundle shows as `@ 19  …var t=1 → 2;a=1;a=1…`, not the whole file quoted twice. Lock files are skipped — the dependency row already says what moved. |
| **Renames survive edits** | A file that moved *and* changed is one rename with a diff, not an unrelated add plus delete. |
| **Secrets are diffed too** | An AWS key or Stripe token that appears between v7 and v8 gets its own section. Values are stored redacted; the secret never enters the index. |

Concretely — the two versions above, where a `diff -rq` reports 61 changed files and 56 of them
are `site-packages/`:

```
$ lw diff order-processor
╭──────────────────────────────────────────────────────────────────────╮
│ order-processor   v0001 → v0002                                      │
│ 2 added  2 modified  9 renamed  55 vendored (hidden)  +24 / -5 lines │
│ to see the 55 vendored files: lw diff order-processor --vendor       │
╰──────────────────────────────────────────────────────────────────────╯
  size     8.3 KB → 9.2 KB (+886 B)

Dependencies                                      
   manager  package   from    to       origin     
+  pip      pydantic  —       2.9.0    installed  
~  pip      boto3     1.34.0  1.35.20  installed  
~  pip      botocore  1.34.0  1.35.20  installed  

  Env vars added: QUEUE_URL
  AWS services added: sqs
  ↑ these need to exist in the function's environment configuration

New findings                                              
high  aws-access-key-id  config.py:6  AKIA…LE (20 chars)  
high  stripe-key         config.py:7  sk_l…dc (32 chars)  
 low  debug-flag         config.py:8  DEBUG = True        

Files                                                                               
   path                                                                 +  −  size  
~  lambda_function.py                                                   9  2  +322  
~  requirements.txt                                                     2  1   +17  
+  config.py                                                           10     +235  
+  helpers/__init__.py                                                              
→  {db → helpers/db}.py                                                 1      +33  
→  site-packages/boto3-1.{34.0 → 35.20}.dist-info/ · 4 files, 1         1  1    +1  
   edited                                                                           
→  site-packages/botocore-1.{34.0 → 35.20}.dist-info/ · 4 files, 1      1  1    +1  
   edited
```

The 55 vendored files became three version numbers, `db.py` moving into a package is one rename,
and the new environment variable, AWS service and secret findings are changes a file diff cannot
express at all. That capture is real output: [`docs/examples/build_demo.py`](https://github.com/utkarsh5026/lambwatch/blob/main/docs/examples/build_demo.py)
builds the two zips and runs the real pipeline over them, and the test suite fails if this README
drifts from it.

<details>
<summary>Why <code>lw diff</code> and <code>git diff</code> report different totals</summary>

`lw diff` hides vendored dependency files and the git mirror keeps them, so the two report
different totals for the same pair of versions — the demo above is *2 added, 2 modified, 9
renamed* from `lw diff` and *68 files changed* from
`lw git order-processor diff --stat v0001 v0002`. Neither is wrong, so each one says the other
exists: `lw diff --vendor` shows the hidden files, and `lw diff --mirror` prints the mirror's own
patch for whichever two versions you asked for.

</details>

## The HTML report

`lw diff --html --open` turns any comparison into a shareable page, and `lw report` builds the
whole history. Every file opens its own diff, with syntax highlighting, in a panel beside the
summary:

<p align="center">
<img src="https://raw.githubusercontent.com/utkarsh5026/lambwatch/main/docs/images/report-file-diff.png" alt="A file's diff in the HTML report: lambda_function.py, with removed and added lines, word-level highlights and syntax colouring" width="720">
</p>

…and each function's history reads as a timeline of what every release did:

<p align="center">
<img src="https://raw.githubusercontent.com/utkarsh5026/lambwatch/main/docs/images/report-history.png" alt="The version history page: a timeline of releases, each with its line counts, new findings, dependency and environment changes" width="860">
</p>

**The answer is written before you ask for it.** Every new version writes its own comparison to
`~/.lambda-watcher/reports/<function>/latest.html` as it is archived, and rewrites
`~/.lambda-watcher/reports/index.html`, which links every function's latest change and counts the
secrets each one ships — a bookmark rather than a command. Each page is self-contained, so it
looks the same opened offline or from an email.
**[Open the live example →](https://utkarsh5026.github.io/lambwatch/examples/report/v0001-v0002.html)**

## Explained in plain English (optional)

Add an AI model once and every change is also explained for you — what the function now does
differently, what is worth checking before it ships, and a deploy checklist:

```bash
lw ai add                  # pick a service, paste a key, pick a model; it checks the model answers
lw explain order-processor # explain the latest change, here and in the report
```

Works with **Anthropic**, **OpenAI**, **Azure OpenAI** (paste the endpoint or the whole Target URI
from the portal) and **any model on your own machine** — Ollama, LM Studio, vLLM. There is nothing
extra to install, and a key you already export as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is picked
up without asking.

From then on the watcher explains each new version in the background and the report opens on the
answer: every file it mentions links to that file's diff, each changed file gets a one-line note,
the deploy checklist remembers what you ticked, and the history page lists what each release
*did*. Older versions get one with `lw explain`; `--all` fills in a function's whole history.

**What leaves your machine, and when, is yours to decide:**

```bash
lw explain order-processor --dry-run   # print exactly what would be sent; send nothing
lw ai settings --no-auto               # explain only when asked, not every new version
lw ai settings --no-send-code          # send the shape of a change, never a line of code
lw ai use quick                        # switch between saved models
lw ai off                              # stop entirely, keeping your keys (lw ai on undoes it)
```

Vendored packages are never sent, files like `.env` and `*.pem` are named but never quoted, and
anything credential-shaped is replaced before sending — a mitigation, not a guarantee, so for code
that must not leave your machine use `--no-send-code` or a local model. Keys live in
`~/.lambda-watcher/ai.json`, readable only by you, never in `config.yaml`.

<details>
<summary>When a request fails</summary>

Rate limits, overloads, timeouts and dropped connections are retried with backoff, honouring the
service's own `Retry-After`; an account out of credit is told apart from a rate limit and not
retried pointlessly; a change too large for the model is sent again smaller. If a request still
fails, the report says why and gives the command that tries again. A report opened while an
explanation is being written reloads itself when it lands. `lw ai test` checks a model still
answers, and `lw doctor` flags a saved model whose key has gone missing.

</details>

## Commands

Run `lw <command> --help` for the options and worked examples of any of these.

### Everyday

| Command | What it does |
|---|---|
| `setup` | Config, background watcher and any history already on disk, in one go. `--no-service` skips the background watcher, `--yes` takes every default. |
| `status` | Is it running, and what has it archived? Also what bare `lw` prints. |
| `demo` | See it work on a sample Lambda, in a scratch archive of its own. `--open` opens the report; `--clean` removes it. |
| `doctor` | Check the config, watch folders, the watcher and its heartbeat, git and disk space. Every problem names its fix; exits 1 if there is one, so it works from a cron job. |
| `ls` | Every function archived so far. |
| `diff FN` | Compare two versions. Defaults to the last two. `--from`/`--to`, `--html`, `--open`, `--vendor`, `--whitespace`, `--no-patch`, `--json`. |
| `report FN` | Build a browsable HTML history: an index plus a diff for every step, opened in your browser. `--no-open` just writes it. |
| `explain FN` | Explain a change in plain English with an AI model, here and in the report. `--from`/`--to`, `--model`, `--refresh`, `--all`, `--dry-run`, `--json`, `--open`. |
| `ai` | Set up and manage the AI models: `ai add`, `ai remove`, `ai use`, `ai test`, `ai settings`, `ai on` / `ai off`. On its own, shows what is set up. |

### Watching

| Command | What it does |
|---|---|
| `start` / `stop` | Register the background watcher with the OS, or stop it. `stop --remove` unregisters it too. |
| `restart` | Stop and start it — use after editing the config. |
| `watch` | Watch the download folders in the foreground. `--once` processes what is already there and exits. |
| `ingest FILE...` | Archive specific zips by hand. `--as NAME` overrides the detected function, `--label` annotates the version. |
| `backfill DIR` | Import a folder of old downloads, oldest first. `--dry-run` shows the names it would assign. |

### Reading the archive

| Command | What it does |
|---|---|
| `versions FN` | Every archived version of one function. |
| `show FN [V]` | Runtime, handler, dependencies, env vars, services and findings for one version. `--files`, `--json`. |
| `export FN [V]` | Get a version back out as a deployable zip (`--zip`) or a plain folder (`--tree`). |
| `open FN [V]` | Open the function's mirror in your editor — every version in one folder, with history. Name a version to open just its files. |
| `git FN ...` | Run git inside that function's mirror repo: `lw git order-processor log --oneline`. |
| `search TERM` | Search filenames and dependencies across everything archived. |

### Housekeeping

| Command | What it does |
|---|---|
| `rename OLD NEW` | Fix a misidentified name. `--alias FRAGMENT` remembers the mapping for next time. |
| `merge SRC DST` | Combine two entries that are really the same Lambda, renumbering by archive time. |
| `label FN V TEXT` | Annotate a version, e.g. `label order-processor 7 "prod deploy 2026-03-01"`. |
| `rm FN` | Delete a function and everything archived for it. |
| `path FN [V]` | Print a path, for `cd "$(lw path order-processor 7)"`. |
| `log` | Recent activity, including downloads that were skipped and why, and when the watcher started and stopped. |
| `logs` | The watcher's own log file — what it noticed and what it made of it. `-f` follows it, `--service` shows the service manager's output instead. |
| `init` | Write an annotated config file you can edit. `setup` does this for you; `--force` overwrites. |
| `reindex` | Rebuild the SQLite index from the manifests on disk. |

Version arguments accept `7`, `v7`, `latest`, `first`, or `-1` / `-2` counting back from the
newest.

## Where things are kept

```
~/.lambda-watcher/
├── config.yaml
├── index.db                        # rebuildable index (see `reindex`)
├── logs/watcher.log
├── reports/                        # generated HTML; index.html links it all
├── quarantine/                     # archives that failed, with a reason file
├── repos/
│   └── order-processor/            # git mirror: one commit per version, tagged v0001…
└── functions/
    └── order-processor/
        └── versions/
            ├── 0001-7fc98e0e/
            │   ├── code/           # the extracted tree
            │   ├── manifest.json   # the full analysis
            │   └── package.zip     # the original download
            └── 0002-7f887035/
```

The directories are the source of truth. `index.db` is a cache you can delete and rebuild with
`lw reindex`, and the whole store is portable — copy it to another machine and reindex.

### The git mirror

Each function gets its own git repository at `repos/<name>/`: the working tree is the latest
version, with one commit per archived version tagged `v0001`, `v0002`, … Every tool you already
know works on it:

```bash
lw open order-processor  # VS Code, on the whole repo

cd "$(lw path order-processor --repo)"
git diff v0002 v0010                 # the diff you originally wanted
git log --oneline --stat
```

One repo per function is the point: your 2nd and 10th version of *one* Lambda sit next to each
other, with no other function's history in the way.

<details>
<summary>Which editor <code>lw open</code> uses</summary>

`open` finds VS Code, Cursor, Windsurf, VSCodium, Zed or Sublime on your `PATH` — set `editor:`
in the config (or `LAMBDA_WATCHER_EDITOR`, or `--editor`) to name a different one. What you get is
a folder of real files, not a diff: the sidebar reads `order-processor` (the folder is named after
the function on purpose), and the editor's own file tree, search, Source Control panel and
timeline all work, with every earlier version a tag away. Name a version —
`lw open order-processor 3` — to open that version's files on their own instead.

</details>

## Configuration

Everything is optional — it works with no config file at all. `lw init` writes an annotated
`~/.lambda-watcher/config.yaml` when you want to change something.

<details>
<summary>The settings worth knowing</summary>

```yaml
watch:
  dirs: ["~/Downloads"]          # add more if you download from several places
  stable_seconds: 2.0            # how long a file must stop changing before it is read
  force_polling: false           # turn on for network shares and VM mounts
                                 # (WSL is detected and polled without this)
  arrival_max_age_seconds: 300   # ignore "modified" events for files older than this

store:
  on_ingest: copy            # copy | move | leave
                             # `move` takes the zip out of Downloads once archived
  strip_wrapper_dir: true    # lift a lone `myrepo-1.2.3/` wrapper to the root, so a
                             # source archive's ref does not read as a full rewrite
  max_versions_per_function: 0   # 0 keeps everything

naming:
  rules:                     # explicit filename → function name mappings
    - pattern: "^prod[-_](.+?)[-_]deploy"
      name: '\1'

diff:
  ignore_vendor: true        # hide vendored dependency files in diffs
  context_lines: 3
```

Set `LAMBDA_WATCHER_HOME` to relocate the whole archive, or `LAMBDA_WATCHER_CONFIG` to point at a
different config file — useful for keeping work and personal archives separate.

</details>

### When it guesses the wrong name

The filename is a guess, and `lw log` records which strategy was used and how confident it was.
Two commands fix any mistake:

```bash
lw rename unknown-a1b2c3d4 order-processor --alias "a1b2c3d4"
lw merge order-processor-old order-processor
```

`--alias` teaches it permanently: any future download whose filename contains that fragment maps
straight to the right function.

## Notes and limits

- **Only the code is archived.** A deployment package does not contain the function's
  configuration — memory, timeout, environment variable *values*, IAM role, layers or triggers.
  lambda-watcher infers what it can from the code and flags it. To archive the real configuration
  too, save `aws lambda get-function --function-name X > X.json` next to the zip: it is picked up
  as a naming hint, and it is a genuinely useful thing to keep.
- **Secret scanning is a tripwire, not a security tool.** It catches the obvious cases — an AWS
  key, a private key block, a live Stripe token — and skips placeholders. Treat a finding as a
  prompt to look, not a verdict.
- **Layers are separate functions in AWS**, downloaded separately, so they are archived as their
  own entries.
- **Large vendored packages make for large archives.** `store.on_ingest: leave` and
  `store.keep_zip: false` trade the original zips for disk space, and
  `store.max_versions_per_function` caps history.

<details>
<summary>Source archives, and Windows antivirus noise</summary>

- **Source archives work too.** A zip from GitHub (or npm, or `git archive`) wraps everything in a
  directory named after the ref — `myrepo-1.2.3/` — and names the file the same way. Both are
  handled: the wrapper is lifted to the root so a re-download diffs as an edit rather than a total
  rewrite, the ref becomes the version's label, and the ref is stripped from the name so
  `myrepo-1.2.3.zip`, `myrepo-main.zip` and `myrepo-a1b2c3d.zip` all land as versions of one
  `myrepo`. A trailing `-v2` is still left alone: it is part of a name far more often than it is a
  tag.
- **A filesystem event is not proof that a file was written.** Windows reports a zip as modified
  when an antivirus scan, the search indexer or OneDrive so much as touches it — watchdog asks the
  OS for attribute and last-access changes too — so a background sweep re-announces every zip in
  the folder at once. Events claiming a write to a file nothing has written to are ignored
  (`watch.arrival_max_age_seconds`), and `store.on_ingest: move` only clears out a download the
  watcher saw arrive: a zip that a startup scan or a `backfill` merely found is archived where it
  lies, never deleted.

</details>

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check . && python tools/check_docstrings.py   # the two lint gates CI runs
```

The design decisions behind the analysis and diff layers are written up in
[docs/design.md](https://github.com/utkarsh5026/lambwatch/blob/main/docs/design.md), and
[CLAUDE.md](https://github.com/utkarsh5026/lambwatch/blob/main/CLAUDE.md) covers the architecture
and the conventions a change is expected to follow.

## Licence

Apache 2.0. See [LICENSE](https://github.com/utkarsh5026/lambwatch/blob/main/LICENSE).
