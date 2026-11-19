"""The auditors' plugin in the Plugins view (liminal-libs 1.64.0, plugin API 2.4; the user, 2026-11-18: "an auditor
plugin, for auditing sources and maintaining the integrity of the feeds; for the integrator"): its entry (its options
with their words and choices, where the auditors stand), an option saved like a plan, its two actions handed to the
app's frame, the request box keeping to its own kinds, and the view's script held by its text. No process, no
network; the plugin is a stand-in class as its package has it."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from liminal_charts.gui import plugins as plugins_view
from liminal_charts.gui import server as server_mod
from liminal_libs import auditing, config, layout
from liminal_libs.apis import Api, Link
from liminal_libs.coverage import day_start_ms
from liminal_libs.sources import registry
from liminal_libs.storage import Store, pulls

STATIC = Path(server_mod.__file__).parent / "static"
MON, TUE, WED, THU, FRI = (day_start_ms(date(2026, 10, d)) for d in (12, 13, 14, 15, 16))
SAT = day_start_ms(date(2026, 10, 17))


class Auditor(auditing.AuditorPlugin):
    name, title, about, api, version = "auditor", "Auditor", "How what is fetched is audited", "2.4", "0.1.0"
    apis = (Api(name="SQLite", maker="The SQLite project", about="The comparison runs inside it.", kind="sdk",
                links=(Link("SQL Language Expressions", "https://www.sqlite.org/lang_expr.html", "docs"),),
                verified="2026-11-18"),)

    def settings(self, variables):
        return self.policy(variables)


@pytest.fixture
def auditor(monkeypatch):
    """The auditors' plugin installed (as a package's entry point), beside whatever this app's workspace has."""
    plugin = Auditor().with_(origin="package: plugin-integrator-auditor")
    monkeypatch.setattr(registry, "auditors", lambda: {"auditor": plugin})
    monkeypatch.setattr(plugins_view, "auditors", lambda: {"auditor": plugin})
    auditing.set_policy(None)
    yield plugin
    auditing.set_policy(None)


def _minute(day_ms: int) -> dict:
    return {"t": day_ms + 10 * 3_600_000, "o": 10.0, "h": 11.0, "l": 9.0, "c": 10.0, "v": 100, "vw": 10.0, "n": 5}


def _settings(**variables):
    """Settings as the entry reads them: the user's variables, and the auditor's own (none: the defaults)."""
    policy = auditing.Policy.of(variables)
    return SimpleNamespace(variables=variables, plugin=lambda name: policy if name == "auditor" else None)


def test_the_auditors_entry_says_its_options_and_where_the_auditors_stand(tmp_path, auditor):
    with Store(tmp_path / "liminal.db") as store:
        store.watch("massive", ["AAA"], "minute")
        store.upsert_bars("massive", "AAA", "minute", 1, [_minute(d) for d in (MON, TUE, THU, FRI)])
        store.upsert_bars("massive", "AAA", "day", 1, [{**_minute(d), "t": d, "v": 5000} for d in (MON, TUE, WED, THU, FRI)])
        store.add_coverage("massive", "AAA", "minute", MON, SAT)
        store.add_coverage("massive", "AAA", "day", MON, SAT)
        auditing.Sweep(store, now_ms=SAT + 14 * 86_400_000).run()
        with pulls.pulling(store, "massive", "bars", ticker="AAA", asset="stock", timespan="minute") as p:
            store.upsert_bars("massive", "AAA", "minute", 1, [_minute(MON)])
        store.request_put("*", ("audit",), by="page")
        entry = plugins_view._auditor_entry(auditor, store, _settings(AUDITOR_KEEP_DAYS="7"))
    assert (entry["name"], entry["group"], entry["kind"], entry["state"]) == ("auditor", "Auditing", "auditor", "ready")
    assert (entry["version"], entry["api"], entry["origin"]) == ("0.1.0", "2.4", "package: plugin-integrator-auditor")
    assert entry["plan"]["tier"] == "1 findings open" and entry["keys"] == [] and entry["apis"][0]["name"] == "SQLite"
    options = {o["name"]: o for o in entry["options"]}
    assert list(options) == list(auditing.CHOICES) and options["AUDITOR_SWEEP_EVERY"]["label"] == "Sweep every"
    assert options["AUDITOR_KEEP_DAYS"] == {"name": "AUDITOR_KEEP_DAYS", "value": "7", "origin": "you",
                                            "choices": ["0", "1", "7", "30"], "label": "Keep verified payloads (days)"}
    assert options["AUDITOR_REPULLS"]["value"] == "5" and options["AUDITOR_REPULLS"]["origin"] == "default"
    audit = entry["audit"]
    assert audit["policy"]["keepDays"] == 7 and audit["cache"]["on"] is True and audit["cache"]["unverifiedBytes"] > 0
    assert [(s["state"], s["audit"], s["pulls"]) for s in audit["cache"]["states"]] == [("complete", None, 1)]
    assert audit["cache"]["verified"]["pulls"] == 0 and audit["cache"]["verified"]["today"]["rows"] == 0  # none audited yet
    assert audit["sweep"]["last"]["series"] == 1 and audit["findings"]["open"] == 1
    assert [(f["kind"], f["ticker"], f["state"]) for f in audit["findings"]["latest"]] == [("hole", "AAA", "open")]
    assert audit["asked"] == [{"id": 1, "symbol": "*", "state": "waiting", "by": "page", "detail": None}] and p.id
    json.dumps(entry)  # the page's


def test_a_main_file_whose_audit_tables_cannot_be_read_is_said_not_raised(auditor):
    class Broken:
        def cache_summary(self):
            raise RuntimeError("no such table: pulls")

        def __getattr__(self, name):
            raise RuntimeError("no such table: pulls")

    entry = plugins_view._auditor_entry(auditor, Broken(), _settings())
    assert entry["audit"] == {"error": "RuntimeError: no such table: pulls"} and entry["plan"]["tier"] == "nothing open"
    assert len(entry["options"]) == 7  # the options can still be chosen


def test_the_view_has_the_auditing_group_and_lists_the_auditor_last(auditor):
    assert plugins_view.GROUPS == ("Feeds with keys", "Public feeds", "AI assistants", "Auditing")
    assert server_mod.REQUEST_KINDS == ("bars", "live")  # the request box: an audit is asked on the auditor's page


def test_an_option_of_the_auditors_is_saved_like_a_plan(auditor, monkeypatch):
    saved: list[tuple] = []
    app = SimpleNamespace(settings=SimpleNamespace(offline=False), _settings_lock=__import__("threading").Lock(),
                          _settings_at=5.0, _plugins_cache={"kept": True}, _save_variable=lambda n, v: saved.append((n, v)))
    set_plan = server_mod.ChartApp.set_plan
    assert set_plan(app, "auditor", "AUDITOR_KEEP_DAYS", " 30 ") == {"plugin": "auditor", "variable": "AUDITOR_KEEP_DAYS",
                                                                    "value": "30"}
    assert saved == [("AUDITOR_KEEP_DAYS", "30")] and app._settings_at == 0.0 and app._plugins_cache is None
    assert set_plan(app, "auditor", "AUDITOR_KEEP_DAYS", None)["value"] is None and saved[-1] == ("AUDITOR_KEEP_DAYS", None)
    with pytest.raises(ValueError, match="AUDITOR_KEEP_DAYS is one of 0, 1, 7, 30"):
        set_plan(app, "auditor", "AUDITOR_KEEP_DAYS", "3")
    with pytest.raises(LookupError, match="auditor has no plan variable MASSIVE_API_KEY"):
        set_plan(app, "auditor", "MASSIVE_API_KEY", "x")
    app.settings.offline = True
    with pytest.raises(RuntimeError, match="offline"):
        set_plan(app, "auditor", "AUDITOR_KEEP_DAYS", "1")


def test_the_data_api_hands_the_auditors_asks_to_its_frame():
    src = (Path(server_mod.__file__)).read_text(encoding="utf-8")
    assert 'if parts[:3] in (["", "api", "databases"], ["", "api", "auditor"]):' in src
    assert "return self._frame_write(method, urlparse(self.path).path, body)" in src
    assert 'p = providers().get(plugin) or assistants().get(plugin) or auditors().get(plugin)' in src


def test_the_views_script_draws_the_auditors_page():
    js = (STATIC / "plugins.js").read_text(encoding="utf-8")
    for line in ("if (p.audit) return auditMainHtml(p);", "if (p.audit) return auditSideHtml(p);",
                 "fetch('/api/auditor/run', { method: 'POST', headers: WRITE_HEADERS, body: JSON.stringify({ what }) })",
                 'data-audit-run="audit"', 'data-audit-run="sweep"', "auditRun(t.dataset.auditRun);",
                 "[data-ai-ask],[data-audit-run]'", "!x.ai && !x.audit", "const v = c.verified || {}, day = v.today || {};"):
        assert line in js, line
    body = js[js.index("function auditFindingsHtml"):js.index("function auditMainHtml")]
    assert "esc(why(x))" in body and "esc(x.feed)" in body and "esc(x.ticker)" in body  # what a finding says is text
    assert "window.confirm" not in js[js.index("async function auditRun"):js.index("function auditOptionsHtml")]
    for state in ("open", "repaired", "asked", "absent", "closed"):  # every state a finding has its words
        assert f"{state}: '" in js[js.index("const AUDIT_STATE"):js.index("const AUDIT_KIND")]
    for kind in ("copy-missing", "copy-differs", "cache-file", "cut-pull", "hole", "empty-span", "integrity"):
        assert kind in js[js.index("const AUDIT_KIND"):js.index("const PULL_STATE")]


def test_the_command_line_sets_the_auditors_policy_once_it_knows_its_settings():
    import liminal_charts.cli as cli

    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert "auditing.set_policy(auditing.policy_of(settings))" in src
    assert src.index("set_read_only(settings.read_only)") < src.index("auditing.set_policy(auditing.policy_of(settings))")
    assert layout and config  # (the library's, as this app reads them)
