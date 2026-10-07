"""The changed files arranged as the folders they live in, for the report's file list.

A flat list of forty paths makes the reader rebuild the directory structure in
their head — which of these sit together, how much of ``src/handlers/`` moved,
whether the one change outside it matters. Grouping the rows under their
folders draws that structure instead, and lets a folder that is all noise be
folded away in one click.

This module is the shape only: which folder each row of
:meth:`~.compare.VersionDiff.file_rows` hangs under, what each folder adds up
to, and the order they are read in. :func:`~.render_html._render_tree` turns it
into markup.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..utils import rename_label
from .compare import FileChange, MoveGroup

#: Anything the file list draws as one row: a file, or a whole directory move.
Row = FileChange | MoveGroup


def rename_parts(old: str, new: str) -> tuple[str, str, str, str, str]:
    """A rename split as ``(folder, rest of the prefix, was, is now, shared suffix)``.

    :func:`~lambda_watcher.utils.rename_label` with the folder both paths share
    pulled off the front, because that folder is where the rename belongs in
    the tree: ``src/app/{old → new}.py`` hangs under ``src/app`` and is drawn
    there as ``{old → new}.py``. The folder is cut at the last ``/`` of the
    shared prefix, so it is always whole path segments that both sides start
    with — ``site-packages/boto3-{1.34.0 → 1.35.20}.dist-info`` gives
    ``site-packages`` and ``boto3-``.

    A rename across folders keeps only what the two still share:
    ``src/{handlers → services}/db.py`` hangs under ``src``, which is the one
    place it can sit and still say where it came from.
    """
    head, was, now, tail = rename_label(old, new)
    folder, slash, rest = head.rpartition("/")
    if not slash:
        return "", head, was, now, tail
    return folder, rest, was, now, tail


def folder_of(row: Row) -> str:
    """The folder a row hangs under, ``""`` for the archive root.

    ``src/app/handler.py`` → ``src/app``. A rename or a directory move goes
    under the folder its two sides share — see :func:`rename_parts` — so the
    row can show both ends of the move without repeating the part that stayed.
    """
    if isinstance(row, MoveGroup):
        return rename_parts(*row.display_dirs)[0]
    if row.kind == "renamed" and row.old_path:
        return rename_parts(row.old_path, row.path)[0]
    return row.path.rpartition("/")[0]


def _name_in(row: Row, folder: str) -> str:
    """What a row is sorted by inside its folder: its new path with the folder taken off.

    ``src/app/handler.py`` under ``src/app`` sorts as ``handler.py``; a move of
    ``handlers`` to ``lambda/handlers`` at the root sorts as ``lambda/handlers``.
    """
    path = (row.new_dir or ".") if isinstance(row, MoveGroup) else row.path
    return path[len(folder) + 1:] if folder else path


@dataclass
class Folder:
    """One folder of the tree: the folders inside it, the rows directly in it, and their totals.

    ``name`` is what its row shows — one segment, or several joined when a
    chain of folders holds nothing but each other (``src/lambda_watcher``,
    the way a code host draws it). ``path`` is the full path from the root.
    The root itself has both empty and is never drawn as a row.

    ``files``, ``added`` and ``removed`` cover everything underneath, so a
    collapsed folder still says how much it is hiding. ``files`` counts the way
    the page's "N of M files shown" counter does, which is why a directory move
    counts its members less the edited ones: those follow as rows of their own.
    """

    name: str = ""
    path: str = ""
    folders: dict[str, Folder] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)
    files: int = 0
    added: int = 0
    removed: int = 0

    def subfolders(self) -> list[Folder]:
        """The folders directly inside this one, by name, case ignored."""
        return sorted(self.folders.values(), key=lambda f: f.name.lower())

    def sorted_rows(self) -> list[Row]:
        """The rows directly in this folder, by name, case ignored."""
        return sorted(self.rows, key=lambda r: _name_in(r, self.path).lower())

    def walk(self) -> list[Row]:
        """Every row underneath, in the order the page lists them: folders first, then files."""
        out: list[Row] = []
        for sub in self.subfolders():
            out.extend(sub.walk())
        out.extend(self.sorted_rows())
        return out


def _tally(row: Row) -> tuple[int, int, int]:
    """What one row adds to its folders' totals, as ``(files, added lines, removed lines)``.

    A directory move adds no lines: every line it counts belongs to an edited
    member, and those members are rows of their own that add their lines
    themselves. Counting them on the move as well would count them twice.
    """
    if isinstance(row, MoveGroup):
        return row.moved - row.edited, 0, 0
    return 1, row.added_lines, row.removed_lines


def _compact(folder: Folder) -> Folder:
    """Merge every chain of folders that hold only one folder each into one row.

    ``a/`` holding only ``b/`` holding the files is one folder as far as the
    reader is concerned, and two rows of it is a click and an indent spent on
    nothing. The root is never merged, since it is never drawn.
    """
    for key, sub in list(folder.folders.items()):
        while not sub.rows and len(sub.folders) == 1:
            (only,) = sub.folders.values()
            only.name = f"{sub.name}/{only.name}"
            sub = only
        folder.folders[key] = _compact(sub)
    return folder


def build(rows: list[Row]) -> Folder:
    """Hang every row under its folder and return the root.

    ``rows`` is what the file list draws: :meth:`~.compare.VersionDiff.file_rows`
    with each move's edited members after it. Each row's totals are added to
    every folder above it on the way down, so no folder has to add its contents
    up again later.
    """
    root = Folder()
    for row in rows:
        files, added, removed = _tally(row)
        node = root
        parts = [part for part in folder_of(row).split("/") if part]
        while True:
            node.files += files
            node.added += added
            node.removed += removed
            if not parts:
                break
            part = parts.pop(0)
            path = f"{node.path}/{part}" if node.path else part
            node = node.folders.setdefault(part, Folder(name=part, path=path))
        node.rows.append(row)
    return _compact(root)
