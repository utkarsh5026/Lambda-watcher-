---
name: new-command
description: Add a new lw CLI command (or an lw ai subcommand) with everything the test suite requires around it, including the help panel, the helptext entry and examples, function-name completion, a README row, the docs-site reference entry and a captured example. Use whenever a command is being added or renamed.
argument-hint: "<command name> [what it should do]"
---

# Add `lw $ARGUMENTS`

A command is not just a function in `cli.py`. Three tests check what has to surround it.
`tests/test_helptext.py` checks the help and examples. `tests/test_docs.py` checks for a
README row and a docs-site entry, in both directions. Do all of the steps below in one
change, then run `/check`.

Use `label` as the template: its implementation is in `src/lambda_watcher/cli.py` (search
for `def label(`) and its help is the `"label"` entry in `src/lambda_watcher/helptext.py`.
Between them they show every convention below.

## 1. Pick its panel and place it

The panels are `Everyday`, `Watching`, `Reading the archive` and `Housekeeping`. Typer orders
them by where each panel's first command is registered, so a command's position in `cli.py`
decides where its panel appears in `lw --help`. Put the new command among the others in its
panel, and confirm with `.venv/bin/lw --help` that the panel order did not change.

## 2. Write its help in `helptext.py` first

Add an entry to `COMMANDS` (or to `SUBCOMMANDS` under `"ai <name>"`, for a subcommand):

```python
"<name>": CommandHelp(
    summary="...",       # the line `lw --help` lists it by; must make sense on its own
    about="""
        Plain prose for whoever runs it. What it does, what it changes, when to use it.
    """,
    examples=(
        ("What this one does", "lw <name> order-processor"),
    ),
),
```

- Examples are real invocations, written with `lw`. The test suite parses every one against
  the command, so a misspelled option fails it.
- The text is Rich markup: escape a literal `[` as `\[`.
- `for_command("<name>")` raises `KeyError` at import time when the entry is missing. That is
  deliberate.

## 3. The command itself, in `cli.py`

```python
@app.command(rich_help_panel="<Panel>", **for_command("<name>"))
def <name>(
    function: str = typer.Argument(..., help=FUNCTION_HELP, autocompletion=_complete_function),
    version: Optional[str] = typer.Argument(None, help=LATEST_VERSION_HELP),
) -> None:
    """For maintainers: what it does and why. This never reaches the terminal."""
```

- If the function name would shadow a builtin, pass the command's name to the decorator:
  `@app.command("rm", ...)`.
- **Take a function name?** Then add `autocompletion=_complete_function` and
  `help=FUNCTION_HELP`, and resolve it with `_resolve_function(db, ...)`. The completer runs
  in the user's shell on every TAB, so nothing you add to it may raise.
- **Take a version?** Resolve it with `_resolve_seq` and `_version_or_fail`. Never parse
  `7` / `v7` / `latest` / `-1` yourself.
- **Errors** go through `_fail(...)`. Every error message and every empty state ends in a
  command the reader can type, and that command is spelled `lw`, never `lambda-watcher`. An
  empty archive is a normal state: exit 0 and say what to do next.
- **Changes something the index records?** Write the disk first
  (`Store.patch_manifest`, `Store.write_aliases`), then the index, then call
  `_refresh_archive_index(cfg, db)`. Writing only to SQLite means `lw reindex` silently
  reverts the change. See "Disk is the source of truth" in CLAUDE.md.
- **Needs nothing configured first.** Every setting it reads must already have a working
  default.
- Docstrings go on every helper and closure you add, because CI checks for them.
- Leave the `Optional[X] = typer.Option(...)` style alone (ruff ignores `UP045`/`B008` for
  exactly this).

## 4. Tests

Add the tests to `tests/test_cli.py`. Follow its `home` fixture, which monkeypatches
`LAMBDA_WATCHER_HOME` and `COLUMNS`, and drive the command through `CliRunner`. Assert on the
output and on what is on disk afterwards, not just on the exit code. If the command edits
the archive, also rebuild with the index deleted (see `tests/test_reindex.py`) and assert the
edit survived.

## 5. Documentation, which the suite enforces

1. **README.md command table**: add a row `` | `<name> ARGS` | what it does, with its main flags. | ``
   under the right group heading. Write the name without `lw`, as the other rows do.
2. **docs/index.html command reference**: add a `<details class="cmd">` entry whose
   `cmd__name` is `lw <name> ARGS` and whose `cmd__what` is the summary, with a `slab`
   holding the captured output. Copy the shape of the neighbouring entries.
3. **docs/examples/build_demo.py**: add `capture("<name>", "lambda-watcher <name> ...",
   cli.run("<name>", ...))` where it fits the narrative. Use `wide.run` if the output prints
   an archive path. A command that touches the machine outside the sandbox, as
   `start`/`stop`/`restart` do, is never captured. Describe it in another entry's prose
   instead, and make sure that prose names `lw <name>`.
4. Run `/regen-docs` to paste the real capture into the site and the README. Never type
   output by hand.

## 6. Finish

Run `/check`. Then check by hand that `lw <name> --help` ends in the Examples panel, and that
`lw --help` lists the command in its panel with the summary you wrote.
