"""The agent behind ``lw explain --agent``: what it may read, how it is chosen, and a whole run.

The first half needs nothing beyond the core install, and runs on every leg of
the matrix: the workspace (the only thing the agent can see, so the place the
privacy rules have to hold), the setting, the fall-back when the agent cannot
run, the error mapping, and the dry run. The second half drives a real
``deepagents`` agent with a scripted chat model — no network, every reply
written out in the order the agent will ask for it — and is skipped where the
``agents`` extra is not installed (Python 3.10, or a plain ``.[dev]`` install).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lambda_watcher.ai import agent as agent_module
from lambda_watcher.ai import run as run_module
from lambda_watcher.ai.agent import as_ai_error, describe_step
from lambda_watcher.ai.explanation import Explanation, load_record
from lambda_watcher.ai.providers import AIError
from lambda_watcher.ai.run import Explainer, explain_diff
from lambda_watcher.ai.settings import AISettings, ModelEntry
from lambda_watcher.ai.workspace import NEW, OLD, WITHHELD_NOTE, agent_brief, build_workspace, project_path
from lambda_watcher.cli import app
from lambda_watcher.diffing import diff_from_index
from lambda_watcher.ingest import Ingestor
from tests.conftest import PY_V1, PY_V2, fake_secret
from tests.test_ai import FakeService, claude_at

runner = CliRunner()

ANSWER = {
    "headline": "Saved orders are now also published to SQS",
    "summary": "The handler sends each order to QUEUE_URL after saving it and returns 201.",
    "risk": "medium",
    "risk_reason": "A new environment variable and IAM permission are needed.",
    "changes": [{"kind": "feature", "title": "Orders go to SQS", "detail": "send_message after put_item.",
                 "files": ["/new/lambda_function.py"]}],
    "risks": [{"level": "high", "title": "Role needs sqs:SendMessage", "detail": "or every call fails",
               "files": ["lambda_function.py"]}],
    "checklist": ["Add QUEUE_URL to the environment"],
    "files": {"/new/lambda_function.py": "publishes to SQS"},
}


@pytest.fixture(autouse=True)
def _no_ai_environment(monkeypatch):
    """No key or tracing switch from the machine running the suite."""
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "LANGSMITH_TRACING",
                 "LANGCHAIN_TRACING_V2"):
        monkeypatch.delenv(name, raising=False)


def _pair(cfg, db, ingestor: Ingestor, make_zip, v1: dict, v2: dict, name: str = "fn"):
    """Archive two versions and return the diff between them."""
    ingestor.ingest(make_zip(f"{name}.zip", v1))
    ingestor.ingest(make_zip(f"{name}.zip", v2, built=(2026, 2, 1, 0, 0, 0)))
    function = db.get_function(name)
    a, b = db.get_version(function["id"], 1), db.get_version(function["id"], 2)
    return diff_from_index(db, ingestor.store, cfg.diff, name, a, b)


def _claude() -> ModelEntry:
    """A saved model the tests never actually reach."""
    return ModelEntry(name="claude-sonnet-5", provider="anthropic", model="claude-sonnet-5",
                      api_key="sk-ant-test-0000000000")


# --------------------------------------------------------------------------- #
# The workspace: everything the agent can see
# --------------------------------------------------------------------------- #
def test_the_workspace_follows_the_same_rules_as_a_prompt(cfg, db, ingestor, make_zip):
    secret = fake_secret("stripe")
    diff = _pair(cfg, db, ingestor, make_zip,
                 {"lambda_function.py": PY_V1, "helpers.py": "def fmt(x):\n    return x\n",
                  "site-packages/boto3/__init__.py": "V = '1.34.0'\n"},
                 {"lambda_function.py": PY_V2, "helpers.py": "def fmt(x):\n    return x\n",
                  "site-packages/boto3/__init__.py": "V = '1.35.20'\n",
                  "config.py": f'STRIPE = "{secret}"\n', ".env": "DB_PASSWORD=hunter2hunter2\n"})
    workspace = build_workspace(diff)
    mounted = "\n".join(workspace.files.values())

    assert secret not in mounted and "hunter2" not in mounted
    assert "«redacted stripe-key»" in workspace.files[NEW + "config.py"]
    assert workspace.redactions[NEW + "config.py"] >= 1
    assert workspace.files[NEW + ".env"] == WITHHELD_NOTE and workspace.withheld == [".env"]
    assert not any("site-packages" in path for path in workspace.files)
    assert workspace.vendored == 2
    # Both versions, unchanged files included: callers live in files that did not change.
    assert {NEW + "lambda_function.py", OLD + "lambda_function.py", NEW + "helpers.py",
            OLD + "helpers.py"} <= set(workspace.files)
    assert "QUEUE_URL" in workspace.files[NEW + "lambda_function.py"]
    assert "QUEUE_URL" not in workspace.files[OLD + "lambda_function.py"]


def test_without_send_code_nothing_is_mounted(cfg, db, ingestor, make_zip):
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    workspace = build_workspace(diff, send_code=False)
    assert workspace.files == {}
    assert workspace.paths == {"lambda_function.py"}       # the change list is still known


def test_changed_files_are_mounted_first_when_the_size_limit_runs_out(cfg, db, ingestor, make_zip):
    filler = {f"zz_unchanged_{i}.py": f"N = {i}\n" * 400 for i in range(5)}
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1, **filler},
                 {"lambda_function.py": PY_V2, **filler})
    workspace = build_workspace(diff, max_total_chars=len(PY_V1) + len(PY_V2) + 3000)
    assert {NEW + "lambda_function.py", OLD + "lambda_function.py"} <= set(workspace.files)
    assert workspace.skipped and all(why == "past the size limit for one explanation"
                                     for _path, why in workspace.skipped)
    # What was left out is in the brief, so the agent can say it did not look.
    assert "Not mounted:" in agent_brief(workspace)


def test_binary_and_oversized_files_are_listed_rather_than_mounted(cfg, db, ingestor, make_zip):
    big = "x = 1\n" * 50_000                                # about 300 KB
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1},
                 {"lambda_function.py": PY_V2, "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00", "generated.py": big})
    workspace = build_workspace(diff)
    reasons = dict(workspace.skipped)
    assert reasons[NEW + "logo.png"] == "binary"
    assert reasons[NEW + "generated.py"].startswith("too large to read")
    assert NEW + "lambda_function.py" in workspace.files


def test_a_version_whose_manifest_cannot_be_read_is_walked_instead(cfg, db, ingestor, make_zip):
    diff = _pair(cfg, db, ingestor, make_zip,
                 {"lambda_function.py": PY_V1, "node_modules/left-pad/index.js": "module.exports = 1\n"},
                 {"lambda_function.py": PY_V2, "node_modules/left-pad/index.js": "module.exports = 2\n"})
    assert diff.b_root is not None
    (diff.b_root.parent / "manifest.json").write_text("{ not json", encoding="utf-8")
    workspace = build_workspace(diff)
    assert NEW + "lambda_function.py" in workspace.files
    assert not any("node_modules" in path for path in workspace.files)


def test_file_diff_finds_a_change_however_its_path_is_spelled(cfg, db, ingestor, make_zip):
    diff = _pair(cfg, db, ingestor, make_zip,
                 {"lambda_function.py": PY_V1, "same.py": "A = 1\n"},
                 {"lambda_function.py": PY_V2, "same.py": "A = 1\n", ".env": "TOKEN=abc\n"})
    workspace = build_workspace(diff)
    for spelling in ("lambda_function.py", "/new/lambda_function.py", "b/lambda_function.py",
                     "/old/lambda_function.py"):
        text, _found = workspace.file_diff(spelling)
        assert text.startswith("modified: lambda_function.py") and "+QUEUE = " in text, spelling
    assert "did not change" in workspace.file_diff("same.py")[0]
    assert WITHHELD_NOTE in workspace.file_diff(".env")[0]
    assert project_path("/new/src/app.py") == project_path("b/src/app.py") == "src/app.py"


def test_the_brief_does_not_assume_a_lambda_function(cfg, db, ingestor, make_zip):
    diff = _pair(cfg, db, ingestor, make_zip, {"README.md": "# lib\n", "lib/core.py": "X = 1\n"},
                 {"README.md": "# lib\n\nNow with Y.\n", "lib/core.py": "X = 1\nY = 2\n"}, name="mylib")
    brief = agent_brief(build_workspace(diff))
    assert brief.startswith("# 'mylib': version 1 → version 2")
    assert "Lambda" not in brief.splitlines()[0]
    assert "- modified: lib/core.py (+1 −0)" in brief
    assert "/old/ holds version 1" in brief


# --------------------------------------------------------------------------- #
# Choosing the agent, and what happens when it cannot run
# --------------------------------------------------------------------------- #
def test_the_engine_setting_is_saved_and_an_older_file_reads_as_one_request(tmp_path: Path):
    settings = AISettings.load(tmp_path)
    assert settings.engine == "prompt"
    settings.engine = "agent"
    settings.save()
    assert AISettings.load(tmp_path).engine == "agent"
    # A file from before the setting existed, and one with a value nobody wrote.
    (tmp_path / "ai.json").write_text(json.dumps({"schema": 1, "enabled": True}), encoding="utf-8")
    assert AISettings.load(tmp_path).engine == "prompt"
    (tmp_path / "ai.json").write_text(json.dumps({"engine": "swarm"}), encoding="utf-8")
    assert AISettings.load(tmp_path).engine == "prompt"


def test_an_explanation_saved_before_agents_existed_reads_as_one_request():
    old = {"headline": "h", "summary": "s", "files_sent": 2, "files_total": 2, "prompt_version": 1}
    explanation = Explanation.from_dict(old)
    assert explanation.engine == "prompt" and explanation.steps == 0 and explanation.files_read == []
    newer = Explanation.from_dict({**old, "engine": "agent", "steps": 9, "files_read": ["app.py"]})
    assert (newer.engine, newer.steps, newer.files_read) == ("agent", 9, ["app.py"])


def test_the_setting_falls_back_to_one_request_but_asking_for_the_agent_does_not(
        cfg, db, ingestor, make_zip, monkeypatch):
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    monkeypatch.setattr(agent_module, "agent_problem", lambda: "the agent's libraries are not installed")
    one_request = Explanation(headline="explained in one request")
    monkeypatch.setattr(run_module, "_explain_in_one_request", lambda *a, **k: one_request)
    settings = AISettings(engine="agent")

    assert explain_diff(diff, _claude(), settings) is one_request
    with pytest.raises(AIError) as raised:
        explain_diff(diff, _claude(), settings, engine="agent")
    assert raised.value.kind == "setup" and "lambda-watcher[agents]" in raised.value.hint


def test_with_send_code_off_the_agent_is_refused_and_the_setting_falls_back(
        cfg, db, ingestor, make_zip, monkeypatch):
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    monkeypatch.setattr(agent_module, "agent_problem", lambda: None)
    one_request = Explanation(headline="structure only")
    monkeypatch.setattr(run_module, "_explain_in_one_request", lambda *a, **k: one_request)
    settings = AISettings(engine="agent", send_code=False)

    assert explain_diff(diff, _claude(), settings) is one_request
    with pytest.raises(AIError) as raised:
        explain_diff(diff, _claude(), settings, engine="agent")
    assert "sending code is switched off" in str(raised.value)
    assert "--no-agent" in raised.value.hint


class _StatusError(Exception):
    """Shaped like the vendor SDKs' HTTP errors: a status, a parsed body and a response."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.body = body
        self.response = type("Response", (), {"headers": {"Retry-After": "3"}})()


class APITimeoutError(Exception):
    """Named like both SDKs' timeout errors."""


class APIConnectionError(Exception):
    """Named like both SDKs' connection errors."""


def test_the_libraries_errors_read_like_a_one_request_failure():
    entry, settings = _claude(), AISettings()
    rejected = as_ai_error(_StatusError(401, {"error": {"type": "authentication_error",
                                                        "message": "invalid x-api-key"}}), entry, settings)
    assert rejected.kind == "auth" and "lw ai add anthropic" in rejected.hint
    limited = as_ai_error(_StatusError(429, {"message": "slow down", "type": "rate_limit"}), entry, settings)
    assert limited.kind == "rate-limit" and limited.retry_after == 3
    assert as_ai_error(APITimeoutError("read timed out"), entry, settings).kind == "timeout"
    assert as_ai_error(APIConnectionError("refused"), entry, settings).kind == "network"


def test_each_step_is_said_in_a_few_words():
    assert describe_step("read_file", {"file_path": "/new/src/app.py"}) == "reading src/app.py (new)"
    assert describe_step("file_diff", {"path": "b/src/app.py"}) == "reading the diff of src/app.py"
    assert describe_step("grep", {"pattern": "parse_order"}) == "searching for 'parse_order'"
    assert describe_step("task", {"description": "everything under src/billing/"}) == \
        "asking a subagent: everything under src/billing/"


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #
@pytest.fixture
def archived(tmp_path: Path, monkeypatch, make_zip) -> Path:
    """An archive with two versions of order-processor, in a home of its own."""
    store = tmp_path / "store"
    monkeypatch.setenv("LAMBDA_WATCHER_HOME", str(store))
    monkeypatch.setenv("COLUMNS", "200")
    for path in (make_zip("order-processor.zip", {"lambda_function.py": PY_V1}),
                 make_zip("order-processor-2.zip", {"lambda_function.py": PY_V2},
                          built=(2026, 2, 1, 0, 0, 0))):
        result = runner.invoke(app, ["ingest", str(path), "--as", "order-processor"])
        assert result.exit_code == 0, result.output
    return store


def test_an_agent_dry_run_lists_everything_it_could_read_and_sends_nothing(archived: Path):
    result = runner.invoke(app, ["explain", "order-processor", "--agent", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would give an agent" in result.output and "Nothing was sent" in result.output
    assert "----- the task -----" in result.output and "/old/ holds version 1" in result.output
    assert "/new/lambda_function.py" in result.output and "/old/lambda_function.py" in result.output
    assert not list(archived.rglob("explanations"))


def test_the_agent_can_be_made_the_default_and_says_so_when_it_cannot_run(archived: Path, monkeypatch):
    monkeypatch.setattr("lambda_watcher.cli.agent_problem", lambda: "the agent's libraries are not installed")
    result = runner.invoke(app, ["ai", "settings", "--agent"])
    assert result.exit_code == 0, result.output
    assert AISettings.load(archived).engine == "agent"
    assert "cannot run here, so one request is used" in result.output
    assert "lambda-watcher[agents]" in result.output       # not eaten as Rich markup
    assert "lw ai settings --no-agent" in result.output
    result = runner.invoke(app, ["ai", "settings", "--no-agent"])
    assert AISettings.load(archived).engine == "prompt"


# --------------------------------------------------------------------------- #
# A whole run, with a scripted model (needs the agents extra)
# --------------------------------------------------------------------------- #
def _scripted(replies):
    """A chat model that answers with ``replies`` in order, whatever it is asked.

    Built on demand so the module imports without the extra installed. The
    main agent and its subagent share it, so the script interleaves their
    turns in the order the agent will take them.
    """
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class Scripted(GenericFakeChatModel):
        """Ignores the tools it is offered; the script already knows what to call."""

        def bind_tools(self, tools, **kwargs):
            """The same model: the tool calls are in the script."""
            return self

    def message(reply):
        """A scripted reply as the message the model sends."""
        if isinstance(reply, str):
            return AIMessage(content=reply)
        name, args = reply
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{id(reply)}"}])

    return Scripted(messages=(message(r) for r in replies))


def test_an_agent_investigates_then_answers_in_the_shape_the_report_reads(cfg, db, ingestor, make_zip):
    secret = fake_secret("stripe")
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1, "util.py": "X = 1\n"},
                 {"lambda_function.py": PY_V2, "util.py": "X = 1\n", "config.py": f'KEY = "{secret}"\n'})
    model = _scripted([
        ("list_changes", {}),
        ("file_diff", {"path": "lambda_function.py"}),
        ("read_file", {"file_path": "/new/config.py"}),
        ("task", {"description": "does util.py still agree with the handler?",
                  "subagent_type": "general-purpose"}),
        ("read_file", {"file_path": "/new/util.py"}),         # the subagent's turn
        "util.py is unchanged and nothing in it calls the handler.",
        ("write_file", {"file_path": "/new/lambda_function.py", "content": "tampered"}),
        json.dumps(ANSWER),
    ])
    steps: list[tuple[int, str]] = []
    explanation = agent_module.investigate_diff(diff, _claude(), AISettings(), model=model,
                                                on_step=lambda n, what: steps.append((n, what)))

    assert explanation.engine == "agent" and explanation.headline == ANSWER["headline"]
    assert explanation.changes[0].files == ["lambda_function.py"]     # /new/ prefix undone
    assert explanation.file_notes == {"lambda_function.py": "publishes to SQS"}
    assert explanation.steps == 6 and [n for n, _ in steps] == [1, 2, 3, 4, 5, 6]
    assert ("reading util.py (new)" in [what for _, what in steps])     # the subagent's step shows
    assert explanation.files_read == ["config.py", "lambda_function.py", "util.py"]
    assert (explanation.files_sent, explanation.files_total) == (2, 2)
    assert explanation.redactions >= 1                                  # counted from what it opened
    assert explanation.model_name == "claude-sonnet-5" and explanation.seconds >= 0


def test_an_agent_that_never_answers_is_stopped_and_says_how_to_retry(cfg, db, ingestor, make_zip,
                                                                      monkeypatch):
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    monkeypatch.setattr(agent_module, "MAX_TOOL_CALLS", 3)
    monkeypatch.setattr(agent_module, "MAX_MODEL_CALLS", 5)
    model = _scripted(("ls", {"path": "/new"}) for _ in itertools.count())
    with pytest.raises(AIError) as raised:
        agent_module.investigate_diff(diff, _claude(), AISettings(), model=model)
    assert raised.value.kind == "bad-response" and "without answering" in str(raised.value)
    assert "--no-agent" in raised.value.hint


def test_a_failure_inside_the_agent_is_an_ai_error_and_our_own_exceptions_pass_through(
        cfg, db, ingestor, make_zip):
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    model = _scripted([("ls", {"path": "/new"}), json.dumps(ANSWER)])

    class Stop(Exception):
        """Raised from on_step, the way the background explainer stops a run."""

    def stop(_n: int, _what: str) -> None:
        """Stop at the first step."""
        raise Stop()

    with pytest.raises(Stop):
        agent_module.investigate_diff(diff, _claude(), AISettings(), model=model, on_step=stop)

    class Refusing(type(_scripted([]))):                 # type: ignore[misc]
        """A model whose service rejects the key."""

        def _generate(self, *args, **kwargs):
            """Fail the way the Anthropic SDK does on a 401."""
            raise _StatusError(401, {"error": {"type": "authentication_error", "message": "bad key"}})

        def _stream(self, *args, **kwargs):
            """Fail the same way when asked to stream."""
            raise _StatusError(401, {"error": {"type": "authentication_error", "message": "bad key"}})

    with pytest.raises(AIError) as raised:
        agent_module.investigate_diff(diff, _claude(), AISettings(), model=Refusing(messages=iter([])))
    assert raised.value.kind == "auth"


def test_the_watcher_explains_with_the_agent_when_it_is_the_setting(cfg, db, make_zip, monkeypatch):
    pytest.importorskip("deepagents")
    cfg.report.auto_diff = True
    settings = AISettings.load(cfg.root)
    settings.add(_claude())
    settings.engine = "agent"
    settings.save()
    monkeypatch.setattr(agent_module, "chat_model",
                        lambda entry, s: _scripted([("file_diff", {"path": "lambda_function.py"}),
                                                    json.dumps(ANSWER)]))
    explainer = Explainer(cfg, db, Ingestor(cfg, db).store)
    ingestor = Ingestor(cfg, db, explainer=explainer)
    ingestor.ingest(make_zip("fn.zip", {"lambda_function.py": PY_V1}))
    ingestor.ingest(make_zip("fn.zip", {"lambda_function.py": PY_V2}, built=(2026, 2, 1, 0, 0, 0)))
    assert explainer.drain(timeout=60)
    explainer.stop()

    function = db.get_function("fn")
    a, b = db.get_version(function["id"], 1), db.get_version(function["id"], 2)
    record = load_record(ingestor.store, dict(a), dict(b))
    assert record is not None and record.status == "done" and record.explanation is not None
    assert record.explanation.engine == "agent" and record.explanation.steps == 1
    page = (cfg.reports_dir / "fn" / "latest.html").read_text(encoding="utf-8")
    assert "as an agent, in 1 step, having opened 1 of 1 changed file" in page


@pytest.fixture
def service():
    """A fresh scripted Messages API on localhost, as in test_ai."""
    fake = FakeService()
    yield fake
    fake.close()


def _message(content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    """A whole Messages API response, as the Anthropic SDK expects to parse one."""
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
            "content": content, "stop_reason": stop, "usage": {"input_tokens": 100, "output_tokens": 20}}


def test_the_real_client_offers_the_tools_and_reads_the_answer(cfg, db, ingestor, make_zip, service):
    """Through ChatAnthropic and the Anthropic SDK, to a scripted service: no fake model in the way."""
    pytest.importorskip("deepagents")
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    service.respond(200, _message([{"type": "tool_use", "id": "toolu_1", "name": "file_diff",
                                    "input": {"path": "lambda_function.py"}}], "tool_use"))
    service.respond(200, _message([{"type": "text", "text": json.dumps(ANSWER)}], "end_turn"))

    explanation = agent_module.investigate_diff(diff, claude_at(service), AISettings(max_retries=0))

    first, second = (request["body"] for request in service.requests)
    assert service.requests[0]["headers"]["x-api-key"] == "sk-ant-test-0000000000"
    assert {"list_changes", "file_diff", "read_file", "grep", "task"} <= {t["name"] for t in first["tools"]}
    assert "/old/ holds version 1" in json.dumps(first["messages"])
    results = [block for message in second["messages"] if isinstance(message["content"], list)
               for block in message["content"] if block.get("type") == "tool_result"]
    assert "+QUEUE = " in json.dumps(results)
    assert explanation.headline == ANSWER["headline"] and explanation.steps == 1
    assert (explanation.input_tokens, explanation.output_tokens) == (200, 40)


def test_the_real_clients_errors_are_sorted_like_a_one_request_failure(cfg, db, ingestor, make_zip, service):
    pytest.importorskip("deepagents")
    diff = _pair(cfg, db, ingestor, make_zip, {"lambda_function.py": PY_V1}, {"lambda_function.py": PY_V2})
    service.respond(401, {"type": "error", "error": {"type": "authentication_error",
                                                    "message": "invalid x-api-key"}})
    with pytest.raises(AIError) as raised:
        agent_module.investigate_diff(diff, claude_at(service), AISettings(max_retries=0))
    assert raised.value.kind == "auth" and "lw ai add anthropic" in raised.value.hint
    service.respond(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    with pytest.raises(AIError) as raised:
        agent_module.investigate_diff(diff, claude_at(service), AISettings(max_retries=0))
    assert raised.value.kind == "overloaded"
