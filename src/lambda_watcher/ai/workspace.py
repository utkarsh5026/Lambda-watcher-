"""Both versions of a change as files an agent can open, under the same rules as a prompt.

A one-request explanation decides up front which lines a model sees
(:mod:`.prompt`). An agent decides for itself as it goes, so the decision moves
one level down, to which *files* exist for it at all. :func:`build_workspace`
lays both versions out as virtual files — ``/old/…`` and ``/new/…`` — and that
set is everything the agent can read. It is handed the text, never the archive
on disk, so nothing it does can change an archived version, and nothing it
reads has skipped the rules below.

The rules are :mod:`.prompt`'s, applied file by file:

* vendored files are not mounted — the brief's dependency section accounts for
  them, exactly as it does in a prompt
* files shaped like credentials (``.env``, ``*.pem``) are mounted as a one-line
  note, never their contents
* every line of every mounted file goes through :func:`~.prompt.redact`
* with ``send_code`` off nothing is mounted at all, and :mod:`.agent` refuses
  to run rather than investigate an empty tree

Plus two limits a prompt has no need of, because a repository's source archive
can be far larger than any Lambda package: binary files and files over
:data:`MAX_FILE_BYTES` are not mounted, and mounting stops at
:data:`MAX_TOTAL_CHARS`, changed files first. What was left out is listed in the
brief, so the agent can say it did not look rather than imply that it did.

Nothing here imports the agent library, so these rules are tested on every
Python the package supports, not only on the ones its ``agents`` extra installs on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ..utils import LOG, human_size, matches_any
from .prompt import _WITHHELD, _file_body, _file_title, _handler_module, _overview, _structure, redact

if TYPE_CHECKING:
    from ..diffing.compare import FileChange, VersionDiff

#: Where the older and the newer version are mounted. The trailing slash is
#: part of the prefix, so ``/old/`` never matches a project path ``/older/…``.
OLD = "/old/"
NEW = "/new/"

#: The largest single file mounted. Past this a file is generated, minified or
#: data, and reading it would spend the agent's context on noise.
MAX_FILE_BYTES = 256 * 1024

#: Characters mounted across both versions before mounting stops. About a
#: million tokens of source — every first-party file of any Lambda package and
#: of most repositories, and a bound on memory for the ones it is not.
MAX_TOTAL_CHARS = 4_000_000

#: The most of one file's diff :meth:`Workspace.file_diff` returns. A third of
#: what a whole one-request prompt may carry, the same share
#: :func:`~.prompt.build_prompt` gives its largest file.
MAX_DIFF_CHARS = 50_000

#: Changed files listed by name in the brief. The ``list_changes`` tool has
#: room for the rest; the brief only needs enough to plan from.
BRIEF_CHANGES = 200

#: The note a credential-shaped file is mounted as, in place of its contents.
WITHHELD_NOTE = "(contents withheld: this kind of file usually holds credentials)\n"

#: Vendored-path patterns for an archive whose manifest cannot be read. The
#: defaults from :class:`~lambda_watcher.config.AnalysisConfig`, repeated rather
#: than imported so this module needs no configuration to be handed to it.
_DEFAULT_VENDOR_GLOBS = [
    "node_modules/**", "**/node_modules/**", "**/site-packages/**", "**/dist-info/**",
    "**/*.dist-info/**", "**/*.egg-info/**", "vendor/**", "**/__pycache__/**", ".venv/**", "venv/**",
]


@dataclass
class Workspace:
    """Both versions as virtual files, and an account of what was left out of them.

    ``files`` maps a virtual path (``/new/src/app.py``) to its text, already
    redacted, in the order it was mounted. ``redactions`` counts what was
    replaced in each one, so an explanation can report the values redacted in
    what the agent actually opened rather than in everything it could have.
    ``paths`` is every project path the answer may cite — the changed
    first-party files, under their old names too — which is what
    :func:`~.explanation.parse_answer` checks the answer's file references against.
    """

    diff: VersionDiff
    send_code: bool = True
    files: dict[str, str] = field(default_factory=dict)
    redactions: dict[str, int] = field(default_factory=dict)
    changes: list[FileChange] = field(default_factory=list)
    paths: set[str] = field(default_factory=set)
    withheld: list[str] = field(default_factory=list)
    #: ``(virtual path, why)`` for every first-party file that was not mounted.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Vendored files left out, across both versions.
    vendored: int = 0

    @property
    def mounted_chars(self) -> int:
        """How much text is mounted, across both versions."""
        return sum(len(text) for text in self.files.values())

    def count(self, prefix: str) -> int:
        """How many files are mounted under ``/old/`` or ``/new/``."""
        return sum(1 for path in self.files if path.startswith(prefix))

    def change_for(self, cited: str) -> FileChange | None:
        """The first-party change a path refers to, however the agent spelled it.

        ``src/app.py``, ``/new/src/app.py`` and ``b/src/app.py`` all find the
        change to ``src/app.py``; a rename is found by its old name as well as
        its new one. ``None`` for a file that did not change, or is vendored.
        """
        wanted = project_path(cited)
        for change in self.changes:
            if wanted in (change.path, change.old_path):
                return change
        return None

    def file_diff(self, cited: str) -> tuple[str, int]:
        """One changed file's diff as the agent's ``file_diff`` tool returns it, and the values redacted.

        The same body a one-request prompt would quote for that file
        (:func:`~.prompt._file_body`), under the same heading, so the two ways
        of explaining a change are shown a file identically. A path that did
        not change gets an answer that says so and how to look at it instead,
        rather than an error the agent has to interpret.
        """
        change = self.change_for(cited)
        if change is None:
            return (f"{project_path(cited)!r} did not change between the two versions, or is a vendored "
                    "file. list_changes names every changed file; read_file shows any file in full."), 0
        module = _handler_module(self.diff)
        title = _file_title(change, module)
        if _WITHHELD.search(change.path):
            return f"{title}\n{WITHHELD_NOTE}", 0
        body, found = _file_body(change, MAX_DIFF_CHARS)
        if not body:
            reason = change.skipped_reason or change.line_count_note or "there are no lines to show"
            body = f"(no line diff: {reason})"
        return f"{title}\n{body}", found

    def changes_listing(self, limit: int | None = None) -> list[str]:
        """The changed first-party files, one line each: ``- modified: app.py (+12 −3)``."""
        module = _handler_module(self.diff)
        shown = self.changes if limit is None else self.changes[:limit]
        lines = [f"- {_file_title(change, module)}" for change in shown]
        if limit is not None and len(self.changes) > limit:
            lines.append(f"- … and {len(self.changes) - limit} more; list_changes names them all")
        return lines or ["- none: only the package structure above differs"]


def project_path(virtual: str) -> str:
    """A path as the project knows it: ``/new/src/app.py`` → ``src/app.py``.

    Also strips the ``a/`` and ``b/`` a diff prints and a leading ``./`` or
    ``/``, so whatever spelling the agent copies out of a tool result comes back
    to the one the diff uses. ``/old/x.py`` and ``/new/x.py`` are the same
    project path; which version is meant is the caller's business.
    """
    text = str(virtual).strip().strip("`'\"")
    for prefix in (OLD, NEW):
        if text.startswith(prefix):
            return text[len(prefix):]
    text = text.lstrip("/")
    for prefix in ("a/", "b/", "./"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _version_files(root: Path) -> tuple[list[str], int]:
    """The first-party files of one version as project paths, and how many vendored files it has.

    Classified by the version's own manifest, which recorded each file's
    ``is_vendor`` with the vendor patterns that were configured when it was
    archived — so the agent sees what the diff counted as first-party, not
    what today's configuration would say. A version whose manifest cannot be
    read is walked instead and classified with the default patterns: an
    archive damaged that far still deserves an explanation.
    """
    manifest_path = root.parent / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = [e for e in manifest.get("files") or [] if isinstance(e, dict) and e.get("path")]
    except (OSError, ValueError, AttributeError) as exc:
        LOG.debug("no usable manifest at %s (%s); walking the tree instead", manifest_path, exc)
        entries = []
    if entries:
        first_party = sorted(str(e["path"]) for e in entries if not e.get("is_vendor"))
        return first_party, sum(1 for e in entries if e.get("is_vendor"))
    first_party, vendored = [], 0
    if root.is_dir():
        for file in sorted(root.rglob("*")):
            if not file.is_file() or file.is_symlink():
                continue
            rel = file.relative_to(root).as_posix()
            if matches_any(rel, _DEFAULT_VENDOR_GLOBS):
                vendored += 1
            else:
                first_party.append(rel)
    return first_party, vendored


def _read_text(path: Path) -> str | None:
    """A file's text, or ``None`` when it is binary or cannot be read.

    Binary means a NUL byte anywhere in it — cheaper than a full decode, and
    the test git itself uses. Text that is not valid UTF-8 is decoded with
    replacement characters rather than refused: a Latin-1 comment should not
    cost the agent the whole file.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def build_workspace(diff: VersionDiff, *, send_code: bool = True,
                    max_total_chars: int = MAX_TOTAL_CHARS) -> Workspace:
    """Mount both versions of ``diff`` as virtual files under :data:`OLD` and :data:`NEW`.

    Files are mounted in the order an investigation needs them, so that when
    :data:`MAX_TOTAL_CHARS` runs out it is the least useful ones left behind:
    the changed files first, then the unchanged files beside them (the callers
    and helpers a change most often reaches), then everything else, each tier
    by path and with the newer version's copy before the older one's.

    With ``send_code`` off the result has the change list and nothing mounted;
    :func:`~.agent.investigate_diff` refuses to run on it.
    """
    workspace = Workspace(diff=diff, send_code=send_code)
    workspace.changes = [c for c in diff.files if not c.is_vendor]
    for change in workspace.changes:
        workspace.paths.add(change.path)
        if change.old_path:
            workspace.paths.add(change.old_path)
    if not send_code:
        return workspace

    changed_new = {c.path for c in workspace.changes if c.new is not None}
    changed_old = {(c.old_path or c.path) for c in workspace.changes if c.old is not None}
    near = {str(PurePosixPath(p).parent) for p in changed_new | changed_old}

    # (tier, project path, side, prefix, root): sorting the tuples is the mount order.
    candidates: list[tuple[int, str, int, str, Path]] = []
    sides = ((0, NEW, diff.b_root, changed_new), (1, OLD, diff.a_root, changed_old))
    for side, prefix, root, changed in sides:
        if root is None:
            continue
        paths, vendored = _version_files(root)
        workspace.vendored += vendored
        for rel in paths:
            tier = 0 if rel in changed else (1 if str(PurePosixPath(rel).parent) in near else 2)
            candidates.append((tier, rel, side, prefix, root))
    candidates.sort()

    used = 0
    for _tier, rel, _side, prefix, root in candidates:
        virtual = prefix + rel
        if _WITHHELD.search(rel):
            workspace.files[virtual] = WITHHELD_NOTE
            if rel not in workspace.withheld:
                workspace.withheld.append(rel)
            continue
        source = root / rel
        try:
            size = source.stat().st_size
        except OSError:
            workspace.skipped.append((virtual, "missing from the archive"))
            continue
        if size > MAX_FILE_BYTES:
            workspace.skipped.append((virtual, f"too large to read ({human_size(size)})"))
            continue
        if used + size > max_total_chars:
            workspace.skipped.append((virtual, "past the size limit for one explanation"))
            continue
        text = _read_text(source)
        if text is None:
            workspace.skipped.append((virtual, "binary"))
            continue
        lines, found = [], 0
        for line in text.split("\n"):
            clean, hits = redact(line)
            lines.append(clean)
            found += hits
        workspace.files[virtual] = "\n".join(lines)
        if found:
            workspace.redactions[virtual] = found
        used += len(text)
    return workspace


def agent_brief(workspace: Workspace) -> str:
    """The task the agent is given: the change's shape, every changed file, and where to find them.

    Opens with what a one-request prompt opens with — :func:`~.prompt._overview`
    under a heading that does not assume a Lambda function, then
    :func:`~.prompt._structure` — and lists the changed files by name and size
    of change, but quotes no lines: reading them is the agent's job, and a
    brief that already held the diffs would spend its context before the
    investigation began.
    """
    diff = workspace.diff
    heading = f"# {diff.function_name!r}: version {diff.a_seq} → version {diff.b_seq}"
    lines = _overview(diff, heading) + _structure(diff)
    lines += ["", "## Changed files (first-party)", *workspace.changes_listing(BRIEF_CHANGES)]
    lines += [
        "", "## Where things are",
        f"- /old/ holds version {diff.a_seq} ({workspace.count(OLD)} files) and /new/ holds "
        f"version {diff.b_seq} ({workspace.count(NEW)} files).",
    ]
    if workspace.vendored:
        lines.append(f"- {workspace.vendored} vendored third-party files are not mounted; the dependency "
                     "changes above account for them.")
    if workspace.withheld:
        lines.append("- Mounted as a note instead of their contents, because they usually hold "
                     "credentials: " + ", ".join(workspace.withheld[:20]))
    if workspace.skipped:
        shown = "; ".join(f"{path} ({why})" for path, why in workspace.skipped[:15])
        more = f"; and {len(workspace.skipped) - 15} more" if len(workspace.skipped) > 15 else ""
        lines.append(f"- Not mounted: {shown}{more}. Say so if any of them matter to your answer.")
    lines += ["", "Investigate the change as instructed, then reply with the JSON object only."]
    return "\n".join(lines)
