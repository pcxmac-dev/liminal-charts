"""The AI assistants on the app (plugin API 2.0; the user, 2026-11-03: three AI plugins "compliant
with the rest of the plugin structure (setup / connecting methods / etc)"): the "AI assistants" group
on the Plugins view with a fake assistant plugin, its key and account set up (the account never
echoed), its plan and model chosen, an evaluation end to end through the app on a fake session and a
judge over it, the evaluations listed, nothing sent offline, the `ai` and `plugins` commands, and
the key script listing the assistants."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from liminal_libs import sourcing
from liminal_libs.assistants import AssistantPlugin, Caps, Model, Plan, ResponsesAssistant, Source
from liminal_libs.config import load_settings
from liminal_libs.ratelimit import fingerprint
from liminal_libs.sources import registry
from liminal_libs.storage import Store

from liminal_charts import cli
from liminal_charts.gui.server import ChartApp

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
KEY, EMAIL = "sk-acme-secret-000", "me@example.com"
SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "set-key.ps1"


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body if body is not None else {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, None, headers))
        return self.responses.pop(0)

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, params, json, headers))
        return self.responses.pop(0)


def answer(text, in_=100, out=20):
    return FakeResponse(200, {"id": "resp_1", "model": "acme-nano", "status": "completed",
                              "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                              "usage": {"input_tokens": in_, "output_tokens": out, "input_tokens_details": {"cached_tokens": 0},
                                        "output_tokens_details": {"reasoning_tokens": 0}}}, {"x-request-id": "req_1"})


class AcmeAssistant(ResponsesAssistant):
    base_url = "https://api.acme-ai.example"


class AcmeAI(AssistantPlugin):
    name = "acme-ai"
    title = "Acme AI"
    about = "a test assistant"
    vendor = "Acme"
    signup = "https://acme-ai.example/"
    assistant = AcmeAssistant
    verified = "2026-11-03"
    plans = (Plan("free", "Free", api_access=False, note="no credits: buy some"),
             Plan("build", "Build", rpm=5000, tpm=1_000_000, price="$5 bought", spend_cap="$500 a month"))
    models = (Model("acme-nano", "Acme Nano", 0.05, 0.40, 0.005, 400_000), Model("acme-pro", "Acme Pro", 2.0, 10.0, 0.5, 1_000_000))
    sources = (Source("https://acme-ai.example/pricing", "the prices", "Prices", ("acme-nano",)),)
    efforts = ("", "low", "high")


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """The fake assistant beside the real plugins (their entry points), the machine ledgers in a scratch file."""
    monkeypatch.setenv("LIMINAL_LIVE_LOCK", str(tmp_path / "machine" / "live.lock"))
    real = list(registry._entry_points())
    eps = [*real, SimpleNamespace(name="acme-ai", value="fake_acme_ai:ASSISTANT", load=lambda: AcmeAI())]
    monkeypatch.setattr(registry, "_entry_points", lambda: eps)
    registry.reload()
    yield registry.assistants()["acme-ai"]
    registry._providers = None


def _app(tmp_path, monkeypatch, **env):
    from liminal_libs import config

    saved: dict[str, str] = {}
    base = {"LIMINAL_DB": str(tmp_path / "m.db"), **env}
    monkeypatch.setattr(config, "saved_user_keys", lambda: dict(saved))  # the app notices a saved variable (never the real registry)
    store = Store(tmp_path / "m.db", check_same_thread=False)
    app = ChartApp(store, settings=load_settings(env=base, env_file=None),
                   reload_settings=lambda: load_settings(env={**base, **saved}, env_file=None),
                   fetcher=SimpleNamespace(wake_up=lambda: None, ask_pass=lambda: None, status=lambda: {}))
    app._save_variable = lambda name, value: saved.pop(name, None) if value is None else saved.__setitem__(name, value)
    return app, store, saved


def test_the_plugins_view_lists_the_assistants_in_their_own_group(installed, tmp_path, monkeypatch):
    app, store, saved = _app(tmp_path, monkeypatch)
    try:
        out = app.plugins()
        assert out["groups"] == ["Feeds with keys", "Public feeds", "AI assistants"]
        e = next(p for p in out["plugins"] if p["name"] == "acme-ai")
        assert e["group"] == "AI assistants" and e["state"] == "no key" and e["kind"] == "assistant" and e["api"] == "2.0"
        assert [k["name"] for k in e["keys"]] == ["ACME_AI_API_KEY", "ACME_AI_ACCOUNT"] and not e["keys"][1]["secret"]
        assert e["plan"]["vars"][0]["name"] == "ACME_AI_PLAN" and e["plan"]["vars"][0]["choices"] == ["free", "build"]
        assert e["ai"]["vendor"] == "Acme" and e["ai"]["plan"] == "free" and e["ai"]["model"] == "acme-nano" and not e["ai"]["planAccess"]
        assert e["ai"]["caps"] == {"requestsPerDay": 50, "tokensPerDay": 200000, "maxOutputTokens": 1024}
        assert e["ai"]["plans"][0]["name"] == "free" and e["ai"]["models"][0]["id"] == "acme-nano" and e["ai"]["recent"] == []
        assert e["scan"]["has"] and e["options"][0]["name"] == "ACME_AI_MAX_OUTPUT_TOKENS"
        assert e["limits"]["published"] == "Acme publishes no limits for the Free plan"
        assert e["databases"][0]["where"] == "liminal.db" and e["notes"] == ["the Free plan has no API access: no credits: buy some"]
        # the key and the account set up here (the account in the open), the plan and the model chosen
        app.set_key("acme-ai", "ACME_AI_API_KEY", KEY)
        app.set_key("acme-ai", "ACME_AI_ACCOUNT", EMAIL)
        assert saved == {"ACME_AI_API_KEY": KEY, "ACME_AI_ACCOUNT": EMAIL}
        app.set_plan("acme-ai", "ACME_AI_PLAN", "build")
        app.set_plan("acme-ai", "ACME_AI_MODEL", "acme-pro")
        app.set_plan("acme-ai", "ACME_AI_REQUESTS_PER_DAY", "20")
        with pytest.raises(ValueError):
            app.set_plan("acme-ai", "ACME_AI_MODEL", "acme-max")
        with pytest.raises(LookupError):
            app.set_key("acme-ai", "ACME_AI_SECRET", "x")
        app._plugins_cache = None
        e = next(p for p in app.plugins()["plugins"] if p["name"] == "acme-ai")
        assert e["state"] == "ready" and e["keysSet"] and e["ai"]["plan"] == "build" and e["ai"]["model"] == "acme-pro"
        assert e["ai"]["caps"]["requestsPerDay"] == 20 and e["notes"] == [] and "5,000 requests a minute" in e["limits"]["published"]
        assert EMAIL not in json.dumps(e) and KEY not in json.dumps(e)
    finally:
        store.close()


def test_an_evaluation_goes_through_the_app_and_is_kept(installed, tmp_path, monkeypatch):
    app, store, saved = _app(tmp_path, monkeypatch, ACME_AI_API_KEY=KEY, ACME_AI_ACCOUNT=EMAIL, ACME_AI_PLAN="build")
    try:
        session = FakeSession(answer("Shut for the holiday.\nVerdict: no"))
        out = app.evaluate({"question": "Is the market open?", "answer": "verdict", "scale": "yes, no", "context": ["NYSE is closed"]},
                           session=session)
        body = session.calls[0][3]
        assert body["safety_identifier"] == fingerprint(EMAIL) and EMAIL not in json.dumps(body) and "NYSE is closed" in body["input"][0]["content"]
        assert out["assistant"] == "acme-ai" and out["verdict"] == "no" and out["model"] == "acme-nano" and out["costUsd"] > 0
        listed = app.evaluations({})["evaluations"]
        assert [r["id"] for r in listed] == [out["id"]] and listed[0]["argument"]["question"] == "Is the market open?"
        # a judge over it, by the argument form, its input by id
        session = FakeSession(answer('{"verdict": "A", "ranking": ["A"], "reasons": "the closure"}'))
        verdict = app.evaluate({"argument": {"kind": "judge", "evaluations": [out["id"]], "task": "compare"}}, session=session)
        assert verdict["kind"] == "judge" and verdict["inputs"] == [out["id"]] and verdict["verdict"] == "A"
        chain = app.evaluations({"id": verdict["id"]})["chain"]
        assert [c["id"] for c in chain] == [verdict["id"], out["id"]]
        e = next(p for p in app.plugins()["plugins"] if p["name"] == "acme-ai")
        assert [r["kind"] for r in e["ai"]["recent"]] == ["judge", "question"] and e["ai"]["usage"]["today"]["requests"] == 2
        assert e["ai"]["usage"]["machine"]["requests"] == 2 and e["usage"]["today"] == 2
        with pytest.raises(ValueError, match="question is required"):
            app.evaluate({}, session=session)
        with pytest.raises(LookupError):
            app.evaluate({"question": "x", "assistant": "nobody"}, session=session)
    finally:
        store.close()


def test_nothing_is_sent_offline(installed, tmp_path, monkeypatch):
    app, store, saved = _app(tmp_path, monkeypatch, ACME_AI_API_KEY=KEY, ACME_AI_PLAN="build", LIMINAL_OFFLINE="1")
    try:
        session = FakeSession()
        with pytest.raises(RuntimeError, match="offline"):
            app.evaluate({"question": "x"}, session=session)
        assert session.calls == []
        with pytest.raises(RuntimeError, match="offline"):
            app.start_rescan("acme-ai")
    finally:
        store.close()


def test_the_commands_know_the_assistants(installed, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LIMINAL_DB", str(tmp_path / "m.db"))
    monkeypatch.setenv("LIMINAL_OFFLINE", "1")
    monkeypatch.setenv("ACME_AI_API_KEY", KEY)
    assert cli.main(["ai", "ask", "is", "it", "open?", "--assistant", "acme-ai"]) == 2
    assert "offline (LIMINAL_OFFLINE): nothing sent to acme-ai" in capsys.readouterr().out
    assert cli.main(["plugins", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    row = next(r for r in out["assistants"] if r["name"] == "acme-ai")
    assert row["keys"][0] == {"name": "ACME_AI_API_KEY", "label": "Acme API key", "aliases": [], "secret": True,
                              "hint": "an API key from the vendor's console", "set": True}
    assert row["plan"] == "free" and row["tables"]["vendor"] == "Acme" and row["freeNote"].startswith("the Free plan has no API access")
    assert cli.main(["plugins"]) == 0
    text = capsys.readouterr().out
    assert "acme-ai   Acme AI    assistant" in text and KEY not in text
    assert cli.main(["ai", "evaluations", "--assistant", "acme-ai"]) == 0  # nothing kept: an empty list, no error


def test_the_key_script_offers_the_assistants_with_the_account_in_the_open():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "$listed.assistants" in src and "typed in the open" in src and 'Read-Host "$Name (typed hidden)" -AsSecureString' in src
    assert "Set-Key $k.name (-not $k.secret)" in src and "Write-Host $value" not in src


# -- Connect (the capability scan) through the app: `capabilities NAME`, `ai connect`, the view's Rescan ----

LIMITS = {"x-ratelimit-limit-requests": "5000", "x-ratelimit-limit-tokens": "1000000"}
CONNECT_ENV = {"ACME_AI_API_KEY": KEY, "ACME_AI_ACCOUNT": EMAIL, "ACME_AI_PLAN": "build"}


def _connect_session():
    """What a Connect asks of the vendor: its models list, then one short answer; its limits in the headers."""
    said = answer("OK", 12, 1)
    said.headers = {**said.headers, **LIMITS}
    return FakeSession(FakeResponse(200, {"data": [{"id": "acme-nano"}, {"id": "acme-pro"}]}, dict(LIMITS)), said)


def test_an_assistants_connect_is_run_kept_logged_and_shown(installed, tmp_path, monkeypatch):
    """cli._scan looked an assistant's name up among the feeds alone and returned at once: nothing asked,
    nothing kept, nothing said (found 2026-11-09)."""
    app, store, saved = _app(tmp_path, monkeypatch, **CONNECT_ENV)
    try:
        session, lines = _connect_session(), []
        source = installed.build(app.settings, session=session)
        cli._scan("acme-ai", source, store, lines.append, settings=app.settings)
        assert [c[:2] for c in session.calls] == [("GET", "https://api.acme-ai.example/v1/models"),
                                                  ("POST", "https://api.acme-ai.example/v1/responses")]
        kept = store.kv_json(sourcing.SCAN_KEY.format("acme-ai"))
        assert kept["results"][:2] == [["the key", True, "accepted: 2 models listed by the server"],
                                       ["the model acme-nano", True, "listed"]]
        assert kept["results"][2][:2] == ["an answer", True]
        assert kept["results"][3] == ["its plan", True, "the server allows 5,000 requests a minute and 1,000,000 tokens a minute: "
                                                        "the Build plan"]
        assert kept["plan"] == {"ACME_AI_PLAN": "build"} and kept["alike"] == {}
        assert kept["limits"] == {"seen": LIMITS, "perMinute": 5000, "kept": [5000, 60.0]}
        assert lines[0] == "acme-ai: yes the key: accepted: 2 models listed by the server" and len(lines) == 5
        assert lines[-1] == "acme-ai: plan found: ACME_AI_PLAN=build (used wherever you have not set ACME_AI_PLAN)"
        # the window its limiter keeps is its plan's (Assistant.window): never a feed's (registry.rate_window knows feeds only)
        assert not any("limiter" in line for line in lines) and source.client._limiter.max_calls == 5000
        said = json.dumps(kept) + "".join(lines)
        assert KEY not in said and EMAIL not in said
        # the Plugins view shows it: when it was asked, each answer, the plan found
        app._plugins_cache = None
        e = next(p for p in app.plugins()["plugins"] if p["name"] == "acme-ai")
        assert e["state"] == "ready" and e["scan"]["at"] == kept["at"] and not e["scan"]["running"]
        assert e["scan"]["results"][0] == {"what": "the key", "available": True, "feed": "acme-ai",
                                           "detail": "accepted: 2 models listed by the server"}
        assert [r["what"] for r in e["scan"]["results"]] == ["the key", "the model acme-nano", "an answer", "its plan"]
        assert e["plan"]["found"] == {"ACME_AI_PLAN": "build"} and e["plan"]["differs"] == []
    finally:
        store.close()


def test_a_connect_that_fails_is_said_and_nothing_is_kept(installed, tmp_path, monkeypatch):
    app, store, saved = _app(tmp_path, monkeypatch, **CONNECT_ENV)
    try:
        lines = []
        monkeypatch.setattr(AcmeAI, "scan", lambda self, source: (_ for _ in ()).throw(ValueError("an answer it could not read")))
        cli._scan("acme-ai", object(), store, lines.append, settings=app.settings)
        assert lines == ["acme-ai: capability scan failed (ValueError: an answer it could not read)"]
        assert sourcing.Scans.last(store, "acme-ai") is None
    finally:
        store.close()


@pytest.mark.parametrize("argv", [["capabilities", "acme-ai"], ["ai", "connect", "--assistant", "acme-ai"]])
def test_the_commands_connect_an_assistant(installed, tmp_path, monkeypatch, capsys, argv):
    """What the Plugins view's Rescan starts (`capabilities NAME`, in its own process) and `ai connect`."""
    s = load_settings(env={"LIMINAL_DB": str(tmp_path / "m.db"), **CONNECT_ENV}, env_file=None)
    session, build = _connect_session(), AcmeAI.build
    monkeypatch.setattr(AcmeAI, "build", lambda self, settings, session_=None, now=None: build(self, settings, session=session, now=now))
    args = cli.build_parser().parse_args(argv)
    assert (cli.cmd_capabilities if argv[0] == "capabilities" else cli.cmd_ai)(args, s) == 0
    out = capsys.readouterr().out
    assert "acme-ai: yes the key: accepted: 2 models listed by the server" in out
    assert "acme-ai: plan found: ACME_AI_PLAN=build" in out and KEY not in out and EMAIL not in out
    assert len(session.calls) == 2
    with Store(s.db_path) as store:
        assert sourcing.Scans.last(store, "acme-ai")["plan"] == {"ACME_AI_PLAN": "build"}
