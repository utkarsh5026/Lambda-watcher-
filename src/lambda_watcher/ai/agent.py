"""An agent that investigates a change before it explains it, built on ``deepagents``.

A one-request explanation (:func:`~.run.explain_diff`) sees the diff and only
the diff. That is enough to say *what* changed, and often not enough to say
whether it is safe: a renamed parameter looks harmless until you find the three
callers still passing the old one, and a new environment variable reads as a
detail until you see what the code does when it is missing. The agent can go and
look. It is given both versions as files (:mod:`.workspace`) plus a diff tool,
reads what it decides matters, follows a change to its callers, hands a large
self-contained part to a subagent so its own context stays on the whole, and
answers in exactly the shape a one-request explanation does — so everything
downstream, from the saved record to both renderers, is shared.

It is optional on purpose. ``deepagents`` brings LangChain, LangGraph and two
vendor SDKs with it and needs Python 3.11, where the rest of lambda-watcher
installs into a bare 3.10 with four small dependencies; so it lives behind the
``agents`` extra, every import of it happens inside a function here, and
:func:`agent_problem` answers "can it run?" without importing anything. Nothing
else in the package imports this module at load time.

What leaves the machine is still decided in one place. The agent only ever sees
the :class:`~.workspace.Workspace` — vendored files left out, credential files
reduced to a note, every line redacted — and never the archive on disk, so
nothing it writes can reach an archived version either. LangSmith tracing, which
LangChain switches on from an environment variable, is forced off for the run:
it would ship every file the agent read to a third service nobody here asked for.

Cost is bounded three ways, because an agent left to itself can loop: a cap on
tool calls after which it is told to answer (:data:`MAX_TOOL_CALLS`), a cap on
model calls after which the run stops (:data:`MAX_MODEL_CALLS`), and the same
two, smaller, on each subagent. A typical change takes ten to twenty calls.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

from ..utils import LOG, utc_now_iso
from .explanation import Explanation, parse_answer
from .prompt import ANSWER_FORMAT
from .providers import (
    DEFAULT_MAX_TOKENS,
    AIError,
    AnthropicClient,
    OpenAIClient,
    _Response,
    _timeout,
    _unreachable,
    classify,
    parse_azure_endpoint,
)
from .report import explain_command
from .workspace import NEW, OLD, Workspace, agent_brief, build_workspace, project_path

if TYPE_CHECKING:
    from ..diffing.compare import VersionDiff
    from .settings import AISettings, ModelEntry

#: Bumped whenever the agent's instructions change enough that an explanation
#: written under older ones is worth regenerating. Saved as the explanation's
#: ``prompt_version``, beside ``engine="agent"`` saying whose numbering it is.
AGENT_PROMPT_VERSION = 1

#: Tool calls the agent may make before every further one is refused with
#: "do not make additional tool calls", which is its cue to answer. Enough to
#: read a dozen diffs, chase their callers and delegate twice.
MAX_TOOL_CALLS = 40

#: Of those, how many may hand work to a subagent. Each one is a whole
#: investigation of its own, so this is the cap that matters most for cost.
MAX_DELEGATIONS = 4

#: The same two caps for one subagent's run.
SUBAGENT_TOOL_CALLS = 15

#: Model calls after which a run stops outright, answer or not. A few past the
#: tool-call cap, which leaves room for the refused calls and the answer itself.
MAX_MODEL_CALLS = MAX_TOOL_CALLS + 8
SUBAGENT_MODEL_CALLS = SUBAGENT_TOOL_CALLS + 4

#: LangGraph's own step limit, the last line of defence if a cap above were
#: ever bypassed. Each model call and each round of tools is a step, plus the
#: middleware around them; ``deepagents`` itself defaults to 9,999.
RECURSION_LIMIT = 6 * MAX_MODEL_CALLS

#: What to type to get the agent. uv is the README's first suggestion;
#: ``--python`` because a uv-managed 3.10 cannot install the extra at all.
#: Anything printing this through Rich has to escape it: ``[agents]`` is
#: markup to Rich, and would silently vanish from the command.
INSTALL_COMMAND = "uv tool install --force --python 3.12 'lambda-watcher[agents]'"

#: The same, with the pipx spelling, as an :class:`AIError` hint.
INSTALL_HINT = (f"`{INSTALL_COMMAND}` adds it "
                "(or `pipx install --force 'lambda-watcher[agents]'` on Python 3.11 or newer)")

#: The subagent's name. ``deepagents`` adds a general-purpose subagent of its
#: own unless one with this name is supplied; supplying ours under it replaces
#: that one, so there is exactly one subagent, and it carries the caps above.
SUBAGENT_NAME = "general-purpose"

AGENT_SYSTEM_PROMPT = f"""\
You review a change between two versions of a software project, for the engineer \
who is about to deploy or merge the newer one, or who is trying to understand what \
a colleague shipped. The versions were archived from zip downloads: an AWS Lambda \
deployment package, a source archive of a repository, or any other bundle of code; \
the brief says what is known about this one.

Both versions are mounted as read-only files: /old/ holds the older version and \
/new/ the newer, each laid out exactly as in the project. Explore them with ls, \
glob, grep and read_file; file_diff shows one changed file's diff, and \
list_changes names every changed file. You may keep notes under /notes/. \
Third-party vendored packages are not mounted (the dependency changes in the brief \
account for them), files that usually hold credentials are mounted as a note \
instead of their contents, and credential-like values everywhere have been \
replaced with «redacted …» markers.

How to investigate:
1. Read the brief. It gives the shape of the change and lists every changed file.
2. Read the diffs that matter most first: entry points, request handling, data \
access, configuration, and anything the security scanner flagged.
3. Follow the change past the diff. When a function's signature, return value, \
errors or side effects change, grep /new/ for its callers and check they still \
agree. When code reads a new setting or calls a new service, find out what happens \
when it is missing or fails. When code is removed, check whether anything still \
relies on it.
4. For a large change, hand a self-contained part to the subagent with the task \
tool ("everything under src/billing/: what behaviour changed, and what could \
break?"), so your own context stays on the whole.
5. Stop when you can explain the change. You have about {MAX_TOOL_CALLS} tool \
calls: spend them on what decides the answer, not on reading every file.

Write for a busy reader:
- Lead with behaviour: what the code now does differently when it runs, not which \
lines moved.
- Be concrete: name the functions, endpoints, tables, queues, settings and status \
codes involved, exactly as they appear in the code.
- Say what could break or needs doing first: new environment variables or \
permissions, changed request or response shapes, callers left behind by a changed \
signature, removed error handling, new external calls, migrations, credentials \
committed to the code.
- Only state what you saw. When something that might matter was not mounted, or \
you did not get to it, say so rather than guessing.
- No filler, no praise, and no restating counts the reader can already see.

Cite files by their path in the project, without /old/ or /new/: "src/app.py", \
not "/new/src/app.py".

{ANSWER_FORMAT}"""

SUBAGENT_DESCRIPTION = (
    "Investigates one self-contained part of the change in depth — a file, a directory, or a "
    "question such as 'does anything still call parse_order the old way?' — and reports back what "
    "changed in behaviour and what could break. It starts knowing nothing but the task, so name the "
    "files or the question in it."
)

SUBAGENT_PROMPT = f"""\
You investigate one part of a change between two versions of a software project, \
for a colleague who is writing up the whole change. The older version is mounted \
read-only at /old/ and the newer at /new/. file_diff shows a changed file's diff, \
list_changes names every changed file, and ls, glob, grep and read_file explore \
either version. Credential-like values appear as «redacted …» markers.

Answer the task you are given, following the change past the diff where it \
matters: the callers of a changed function, what happens when a new setting is \
missing, whether anything relied on removed code. You have about \
{SUBAGENT_TOOL_CALLS} tool calls.

Report back in at most 200 words of plain prose, not JSON: what behaviour changed, \
what could break (naming files by their project path, and the functions and \
settings involved), and anything you could not determine."""

#: What the model is told each of the two tools of our own does. Kept apart
#: from the methods' docstrings, which are written for whoever maintains them.
LIST_CHANGES_DESCRIPTION = (
    "Every first-party file that changed between the two versions, one per line: how it changed "
    "(added, removed, modified, renamed), its path in the project, and lines added and removed."
)
FILE_DIFF_DESCRIPTION = (
    "The unified diff of one changed file between /old/ and /new/, with credential-like values "
    "redacted. Takes the file's path in the project, e.g. 'src/app.py'. For a file that did not "
    "change, read it with read_file instead."
)


def agent_problem() -> str | None:
    """Why the agent cannot run in this Python, or ``None`` when it can.

    Asked without importing anything, so ``lw ai``, ``lw doctor`` and the
    background watcher can check on every call for the price of a directory
    lookup. Only ``deepagents`` itself is checked: the OpenAI-shaped services
    also need ``langchain-openai``, which the extra installs beside it, and
    :func:`chat_model` says so in the rare install that has one and not the other.
    """
    if sys.version_info < (3, 11):
        return ("the agent needs Python 3.11 or newer, and lambda-watcher is running on "
                f"{sys.version_info.major}.{sys.version_info.minor}")
    if find_spec("deepagents") is None:
        return "the agent's libraries are not installed"
    return None


def _service_url(entry: ModelEntry, timeout: float) -> str:
    """The URL this entry's requests go to, for error messages: ``https://api.anthropic.com``."""
    if entry.provider == "anthropic":
        return AnthropicClient(entry, timeout).base
    if entry.provider == "azure":
        return parse_azure_endpoint(entry.endpoint)[0]
    return OpenAIClient(entry, timeout).base


def chat_model(entry: ModelEntry, settings: AISettings) -> Any:
    """The LangChain chat model that talks to ``entry``, configured as a one-request call would be.

    Same key, endpoint, timeout and retry count as :mod:`.providers` uses, so a
    model that passes ``lw ai test`` works here with nothing further set up.
    The SDKs underneath do their own retrying with backoff; what they cannot
    do is print "retrying in 8s", so a slow agent run is shown by its steps instead.

    OpenAI is addressed through Chat Completions rather than the Responses API
    LangChain prefers, because Responses stores conversations on OpenAI's side
    by default, and a one-request explanation never asked for that. Azure is
    addressed by deployment, through its ``/openai/v1`` API unless an API
    version is set, mirroring :class:`~.providers.AzureClient` — but without
    its fall-back between the two, so a resource that serves only the classic
    API needs an API version saved with it.
    """
    timeout = settings.timeout(entry)
    key = entry.resolved_key()
    common: dict[str, Any] = {"timeout": timeout, "max_retries": settings.max_retries}
    try:
        if entry.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic  # type: ignore[import-not-found]

            return ChatAnthropic(model=entry.model, api_key=key, base_url=_service_url(entry, timeout),
                                 max_tokens=DEFAULT_MAX_TOKENS, **common)
        from langchain_openai import AzureChatOpenAI, ChatOpenAI  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AIError("setup", f"the agent's libraries for {entry.info.label} are not installed ({exc.name})",
                      hint=INSTALL_HINT) from exc
    if entry.provider == "azure":
        base, _deployment, version = parse_azure_endpoint(entry.endpoint)
        if "://" not in base or base.endswith("://"):
            raise AIError("setup", "no Azure OpenAI endpoint is set for this model",
                          hint=f"`lw ai add azure --name {entry.name} --endpoint "
                               "https://<resource>.openai.azure.com`")
        version = entry.api_version or version
        if version:
            return AzureChatOpenAI(azure_endpoint=base, azure_deployment=entry.model, api_version=version,
                                   api_key=key, **common)
        return ChatOpenAI(model=entry.model, base_url=f"{base}/openai/v1", api_key=key,
                          use_responses_api=False, **common)
    # A local server usually wants no key, but the client refuses to start without one.
    return ChatOpenAI(model=entry.model, base_url=_service_url(entry, timeout), api_key=key or "not-needed",
                      use_responses_api=False, **common)


def as_ai_error(exc: BaseException, entry: ModelEntry, settings: AISettings) -> AIError:
    """Whatever the agent's libraries raised, as the :class:`AIError` a one-request call would have.

    Both vendor SDKs raise exceptions carrying the HTTP status, the parsed
    body and the response headers; those go through the same
    :func:`~.providers.classify` a one-request failure does, so a rejected key
    or an empty account reads the same, with the same next command, however
    the explanation was being written. Timeouts and refused connections are
    recognised by name, since each SDK has its own class for them and this
    module imports neither.
    """
    if isinstance(exc, AIError):
        return exc
    timeout = settings.timeout(entry)
    url = _service_url(entry, timeout)
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        body = getattr(exc, "body", None)
        return classify(_Response(status, {str(k).lower(): str(v) for k, v in headers.items()},
                                  body if body is not None else str(exc)), entry, url)
    name = type(exc).__name__
    if "Timeout" in name:
        return _timeout(url, timeout, entry.provider == "local")
    if "Connection" in name:
        return _unreachable(url, entry.provider == "local", str(exc))
    return AIError("bad-response", f"the agent failed: {exc}", hint="`lw logs` has the details")


def _text(content: Any) -> str:
    """A message's text, whether it came as a string or as a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block if isinstance(block, str) else str(block.get("text") or "")
                       for block in content if isinstance(block, (str, dict))
                       and (isinstance(block, str) or block.get("type") == "text"))
    return str(content or "")


def describe_step(name: str, args: dict[str, Any]) -> str:
    """One tool call as a few words for a status line: ``reading src/app.py (new)``."""
    def where(path: Any) -> str:
        """A virtual path as the project path plus which version: ``src/app.py (old)``."""
        text = str(path or "/")
        for prefix, label in ((NEW, "new"), (OLD, "old")):
            if text.startswith(prefix):
                return f"{text[len(prefix):]} ({label})"
        return text

    if name == "read_file":
        return f"reading {where(args.get('file_path'))}"
    if name == "file_diff":
        return f"reading the diff of {project_path(args.get('path') or '')}"
    if name == "grep":
        return f"searching for {str(args.get('pattern') or '')[:40]!r}"
    if name in {"ls", "glob"}:
        return f"looking through {where(args.get('path') or args.get('pattern'))}"
    if name == "list_changes":
        return "listing the changed files"
    if name == "task":
        task = " ".join(str(args.get("description") or "").split())
        return "asking a subagent: " + (task if len(task) <= 60 else task[:59] + "…")
    if name in {"write_file", "edit_file"}:
        return "taking notes"
    return name.replace("_", " ")


@dataclass
class _Run:
    """One investigation's tools, and what it is seen doing through the stream.

    The two tools of our own are methods here, so the workspace they read and
    the tally they keep travel together. LangGraph runs a turn's tool calls on
    a thread pool, so the tally is updated under a lock.
    """

    workspace: Workspace
    on_step: Callable[[int, str], None] | None = None
    steps: int = 0
    #: Project paths the agent read in full or as a diff.
    opened: set[str] = field(default_factory=set)
    #: Virtual paths read in full, and changed paths diffed, already counted
    #: towards ``redactions`` — each counted once, however often it was read.
    counted: set[str] = field(default_factory=set)
    redactions: int = 0
    answer: str = ""
    #: False when the last word came from a limit rather than the model.
    answered_by_model: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def list_changes(self) -> str:
        """The ``list_changes`` tool: every changed first-party file, uncut."""
        return "\n".join(self.workspace.changes_listing())

    def file_diff(self, path: str) -> str:
        """The ``file_diff`` tool: one changed file's diff, counting what was redacted in it once."""
        text, found = self.workspace.file_diff(path)
        change = self.workspace.change_for(path)
        if change is not None:
            with self._lock:
                self.opened.add(change.path)
                if "diff:" + change.path not in self.counted:
                    self.counted.add("diff:" + change.path)
                    self.redactions += found
        return text

    def _record(self, call: dict[str, Any]) -> None:
        """Count one tool call, note any file it opened, and say what it is doing."""
        name = str(call.get("name") or "")
        raw = call.get("args")
        args: dict[str, Any] = raw if isinstance(raw, dict) else {}
        with self._lock:
            self.steps += 1
            step = self.steps
            if name == "read_file":
                virtual = str(args.get("file_path") or "")
                if virtual in self.workspace.files:
                    self.opened.add(project_path(virtual))
                    if virtual not in self.counted:
                        self.counted.add(virtual)
                        self.redactions += self.workspace.redactions.get(virtual, 0)
        if self.on_step is not None:
            self.on_step(step, describe_step(name, args))

    def observe(self, namespace: tuple[str, ...], update: Any) -> None:
        """Take in one streamed update: tool calls from anywhere, the answer from the main agent only.

        A subagent's updates arrive under a namespace of their own; its tool
        calls count and show, but its final words are a report to the main
        agent, not the answer. An AI message the main agent did not get from
        the model — the note a model-call limit leaves when it stops the run —
        is remembered as the last word, but marked as not an answer.
        """
        if not isinstance(update, dict):
            return
        for node, data in update.items():
            messages = data.get("messages") if isinstance(data, dict) else None
            if not isinstance(messages, list):
                continue
            for message in messages:
                if getattr(message, "type", "") != "ai":
                    continue
                calls = getattr(message, "tool_calls", None) or []
                for call in calls:
                    self._record(call)
                if not calls and not namespace:
                    self.answer = _text(message.content)
                    self.answered_by_model = node == "model"


def investigate_diff(
    diff: VersionDiff,
    entry: ModelEntry,
    settings: AISettings,
    *,
    on_step: Callable[[int, str], None] | None = None,
    model: Any = None,
) -> Explanation:
    """Let an agent investigate ``diff`` with ``entry``, and return its explanation.

    ``on_step(number, what)`` is called before each tool call anywhere in the
    run — "reading src/app.py (new)" — which is how a terminal shows an agent
    that is working rather than hung, and how the background explainer stops
    one between steps: an exception raised from it ends the run. ``model``
    replaces the chat model :func:`chat_model` would build, for tests and for
    callers with a model of their own.

    Raises :class:`AIError` for every failure, including the agent's libraries
    being absent (``setup``, naming the install command), sending code being
    switched off, and a run that used every step without answering.
    """
    problem = agent_problem()
    if problem:
        raise AIError("setup", problem, hint=INSTALL_HINT)
    retry_alone = f"`{explain_command(diff.function_name, diff.a_seq, diff.b_seq)} --no-agent`"
    if not settings.send_code:
        raise AIError("setup", "the agent reads your code to investigate, and sending code is switched off",
                      hint=f"`lw ai settings --send-code` allows it, or {retry_alone} explains from the "
                           "structure alone")

    # The extra's imports, here rather than at the top: see the module docstring.
    import langsmith  # type: ignore[import-not-found]
    from deepagents import FilesystemPermission, create_deep_agent  # type: ignore[import-not-found]
    from deepagents.backends.utils import create_file_data  # type: ignore[import-not-found]
    from langchain.agents.middleware import (  # type: ignore[import-not-found]
        ModelCallLimitMiddleware,
        ToolCallLimitMiddleware,
    )
    from langchain_core.callbacks import get_usage_metadata_callback  # type: ignore[import-not-found]
    from langchain_core.tools import StructuredTool  # type: ignore[import-not-found]
    from langgraph.errors import GraphRecursionError  # type: ignore[import-not-found]

    workspace = build_workspace(diff, send_code=True)
    run = _Run(workspace, on_step=on_step)
    llm = model if model is not None else chat_model(entry, settings)
    tools = [
        StructuredTool.from_function(run.list_changes, name="list_changes",
                                     description=LIST_CHANGES_DESCRIPTION),
        StructuredTool.from_function(run.file_diff, name="file_diff", description=FILE_DIFF_DESCRIPTION),
    ]
    subagent = {
        "name": SUBAGENT_NAME,
        "description": SUBAGENT_DESCRIPTION,
        "system_prompt": SUBAGENT_PROMPT,
        "tools": tools,
        "middleware": [ToolCallLimitMiddleware(run_limit=SUBAGENT_TOOL_CALLS, exit_behavior="continue"),
                       ModelCallLimitMiddleware(run_limit=SUBAGENT_MODEL_CALLS, exit_behavior="end")],
    }
    agent = create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=AGENT_SYSTEM_PROMPT,
        subagents=[subagent],  # type: ignore[list-item]
        # The mounted versions are evidence. The run's copies are in memory and
        # vanish with it, so a write could never reach the archive — but an
        # agent that edits /new/ and then reads its own edit is explaining a
        # change nobody made.
        permissions=[FilesystemPermission(operations=["write"], paths=[f"{OLD}**", f"{NEW}**"],
                                          mode="deny")],
        middleware=[
            ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS, exit_behavior="continue"),
            ToolCallLimitMiddleware(tool_name="task", run_limit=MAX_DELEGATIONS, exit_behavior="continue"),
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
        ],
    )
    inputs = {
        "messages": [{"role": "user", "content": agent_brief(workspace)}],
        "files": {path: create_file_data(text) for path, text in workspace.files.items()},
    }

    started = time.monotonic()
    with langsmith.tracing_context(enabled=False), get_usage_metadata_callback() as usage:
        stream = agent.stream(inputs, config={"recursion_limit": RECURSION_LIMIT},
                              stream_mode="updates", subgraphs=True)
        try:
            while True:
                # Only the step itself is inside the try: an exception from
                # `observe` — the explainer stopping, from inside `on_step` —
                # is ours and must reach the caller as it was raised.
                try:
                    namespace, update = next(stream)
                except StopIteration:
                    break
                except GraphRecursionError as exc:
                    raise AIError("bad-response", "the agent took more steps than it is allowed without "
                                  "finishing", hint=f"try again, or {retry_alone} explains it in one "
                                  "request") from exc
                except AIError:
                    raise
                except Exception as exc:
                    LOG.debug("the agent failed", exc_info=True)
                    raise as_ai_error(exc, entry, settings) from exc
                run.observe(namespace, update)
        finally:
            stream.close()

    if not run.answered_by_model:
        raise AIError("bad-response", f"the agent used all {MAX_MODEL_CALLS} of its steps without answering",
                      hint=f"try again, or {retry_alone} explains it in one request")
    explanation = parse_answer(run.answer, workspace.paths)
    if explanation.is_empty:
        raise AIError("bad-response", f"{entry.label} finished its investigation with an empty answer",
                      hint=f"try again, or {retry_alone} explains it in one request")

    totals = list(usage.usage_metadata.values())
    changed = {c.path for c in workspace.changes} | {c.old_path for c in workspace.changes if c.old_path}
    explanation.provider = entry.provider
    explanation.model = entry.model
    explanation.model_name = entry.name
    explanation.created_at = utc_now_iso()
    explanation.prompt_version = AGENT_PROMPT_VERSION
    explanation.engine = "agent"
    explanation.steps = run.steps
    explanation.files_read = sorted(run.opened)
    explanation.files_total = len(workspace.changes)
    explanation.files_sent = sum(1 for c in workspace.changes
                                 if c.path in run.opened or (c.old_path and c.old_path in run.opened))
    explanation.withheld = list(workspace.withheld)
    explanation.redactions = run.redactions
    explanation.send_code = True
    explanation.input_tokens = sum(int(u.get("input_tokens") or 0) for u in totals) if totals else None
    explanation.output_tokens = sum(int(u.get("output_tokens") or 0) for u in totals) if totals else None
    explanation.seconds = round(time.monotonic() - started, 1)
    explanation.attempts = 1
    LOG.info("the agent explained %s v%04d → v%04d in %d steps, opening %d of %d changed files",
             diff.function_name, diff.a_seq, diff.b_seq, run.steps,
             len(changed & run.opened), len(workspace.changes))
    return explanation
