#!/usr/bin/env python3
"""Ask the user before Claude registers, stops or restarts the real background watcher.

``lw start`` / ``stop`` / ``restart``, and ``lw setup`` without ``--no-service``,
talk to launchd, the systemd user session or Task Scheduler. None of those live
under ``$HOME``, so pointing ``HOME`` or ``LAMBDA_WATCHER_HOME`` at a sandbox does
not contain them: running one while developing replaces or stops the watcher this
machine actually relies on. Calling the service manager directly does the same, so
``systemctl --user stop lambda-watcher`` and friends are caught too.

Claude Code runs this before every ``Bash`` call (see ``.claude/settings.json``).
A match answers "ask", so the user decides. It does not deny, because sometimes
this really is what the user wanted. Read-only queries (``lw status``,
``systemctl --user status``) pass straight through. Like ``lint_edited.py`` it
fails open: an unreadable payload allows the call.
"""

from __future__ import annotations

import json
import re
import sys

#: ``lw start`` and its spellings: ``.venv/bin/lw``, ``lambda-watcher``, ``python -m lambda_watcher``.
CLI = r"(?:^|[\s;&|(/])(?:lw|lambda-watcher|lambda_watcher)\s+"

#: Each pattern, and what to tell the user when it matches.
GUARDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(CLI + r"(?:start|stop|restart)\b"),
     "registers, stops or restarts the real background watcher"),
    (re.compile(CLI + r"setup\b(?!.*--no-service)"),
     "`lw setup` without --no-service installs the real background watcher"),
    (re.compile(r"\bsystemctl\b.*\s--user\b.*\b(?:start|stop|restart|enable|disable|kill|mask)\b"
                r".*\blambda-watcher\b"),
     "changes the lambda-watcher systemd user service"),
    (re.compile(r"\blaunchctl\b.*\b(?:load|unload|bootstrap|bootout|kickstart|start|stop|remove)\b"
                r".*\bcom\.lambdawatcher\b"),
     "changes the lambda-watcher launchd agent"),
    (re.compile(r"\bschtasks\b.*/(?:create|delete|run|end|change)\b.*lambda", re.I),
     "changes the lambda-watcher scheduled task"),
]


def command_of(stdin: str) -> str:
    """The shell command Claude is about to run, or ``""`` if the payload has none."""
    try:
        payload = json.loads(stdin)
    except ValueError:
        return ""
    return str((payload.get("tool_input") or {}).get("command") or "")


def main() -> int:
    """Answer "ask" for a service-changing command; stay silent for anything else."""
    command = command_of(sys.stdin.read())
    for pattern, what in GUARDS:
        if pattern.search(command):
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"This {what} on this machine, outside any sandbox. "
                        "Develop with /try instead unless the user asked for this."
                    ),
                },
            }))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
