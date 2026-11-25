"""Fetching only while the app, or a command, is in use (the user, 2026-11-01): a feed's run is the
host feed-NAME and the closed months' copy the host history (cli.cmd_host), stopped at the next
request once let go; `sync` by hand holds the fetcher (fetcher.py) and asks it for a pass, or for
the symbols given; nothing is scheduled any more (a service is the user's own decision: service.py)."""
from __future__ import annotations

import threading
import os
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest

from liminal_charts import cli
from liminal_libs import host as hosting
from liminal_libs.config import load_settings
from liminal_libs.storage import Store


class FakeAttachment:
    def __init__(self, name, lives=1):
        self.name, self.lives, self.detached, self.host_pid = name, lives, False, 4242
        self.asked: list[dict] = []
        self.busy: list[bool] = []

    def alive(self):
        if self.detached:
            return False
        self.lives -= 1
        return self.lives >= 0

    def detach(self):
        self.detached = True

    def ask(self, **what):
        self.asked.append(what)
        if what.get("status"):
            return {"busy": self.busy.pop(0) if self.busy else False}
        return {"ok": True}


class FakeHost:
    made: list = []

    def __init__(self, name, db_path, log=print):
        self.name = name
        self.stop = threading.Event()
        FakeHost.made.append(self)

    def serve(self, work, secure=None, handle=None):
        work(self.stop)
        return "its work ended"


def _settings(tmp_path):
    return replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")


def test_a_feed_host_runs_its_pass_and_stops_at_its_next_request_once_let_go(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(hosting, "Host", FakeHost)
    monkeypatch.setattr(cli, "Logger", lambda path: (lambda m: None))
    FakeHost.made.clear()
    runs: list[Namespace] = []
    monkeypatch.setattr(cli, "_sync", lambda args, s: runs.append(args) or (0, 1))
    assert cli.cmd_host(Namespace(name="feed-alpaca"), settings) == 0
    (run,), (h,) = runs, FakeHost.made
    assert h.name == "feed-alpaca" and run.source == "alpaca" and run.fetch_only and not run.all_sources
    assert run._stop is h.stop  # the host's stop event reaches the run's requests
    assert cli.cmd_host(Namespace(name="feed-nothing"), settings) == 2
    # the run's should_stop: once the host's clients are gone, this run stops at its next request
    with Store(settings.db_path) as store:
        stop = threading.Event()
        check = cli._pause_check(store, stop=stop)
        assert check() is None
        stop.set()
        assert check() == cli.HOST_STOPPED


def test_the_history_host_ends_after_the_month_in_hand_once_let_go(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(hosting, "Host", FakeHost)
    monkeypatch.setattr(cli, "Logger", lambda path: (lambda m: None))
    FakeHost.made.clear()
    runs: list[Namespace] = []
    real_cmd_history = cli.cmd_history
    monkeypatch.setattr(cli, "cmd_history", lambda args, s: runs.append(args) or 0)
    assert cli.cmd_host(Namespace(name="history"), settings) == 0
    (run,), (h,) = runs, FakeHost.made
    assert h.name == "history" and run.action == "run" and run.budget == cli.HISTORY_BUDGET_S and run._stop is h.stop
    # cmd_history's own stop check: the host's event, as well as a halt
    from liminal_libs import proc
    from liminal_libs.storage import history

    seen: list = []

    def run_months(conns, history_dir, log, budget_s, code, stop):
        seen.append(stop())
        run._stop.set()
        seen.append(stop())
        return True
    monkeypatch.setattr(history, "run", run_months)
    monkeypatch.setattr(history, "available", lambda: True)
    monkeypatch.setattr(proc, "acquire", lambda path: True)
    monkeypatch.setattr(proc, "release", lambda path: None)
    monkeypatch.setattr(cli, "_background_priority", lambda: None)
    assert real_cmd_history(run, settings) == 0 and seen == [False, True]


def test_sync_by_hand_holds_the_fetcher_and_asks_it_for_a_pass_or_the_symbols(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    lines: list[str] = []
    att = FakeAttachment("fetcher", lives=99)
    att.busy = [True, True, False]  # its pass goes on for two looks, then it is idle
    monkeypatch.setattr(cli, "attach_fetcher", lambda db, log: att)
    monkeypatch.setattr(cli.time, "sleep", lambda s: lines.append("waited"))
    assert cli.dispatch(settings, lines.append) == 1
    assert att.asked[0] == {"pass": True} and sum(1 for a in att.asked if a.get("status")) == 3
    assert att.detached and lines.count("waited") >= 3 and any("a pass asked of the fetcher" in m for m in lines)
    # the symbols given: requests on the fetcher's stack, by the command line, waited for until each has ended
    att2 = FakeAttachment("fetcher", lives=99)
    monkeypatch.setattr(cli, "attach_fetcher", lambda db, log: att2)

    def served(s):
        lines.append("waited")
        with Store(settings.db_path) as store:
            for r in store.requests_open():
                store.request_update(r["id"], state="done", detail="served by massive")
    monkeypatch.setattr(cli.time, "sleep", served)
    assert cli.dispatch(settings, lines.append, tickers={"spcx", "aapl"}) == 1
    with Store(settings.db_path) as store:
        rows = store.request_rows()
    assert {r["symbol"] for r in rows} == {"SPCX", "AAPL"} and all(r["by"] == "cli" and r["state"] == "done" for r in rows)
    assert att2.asked == [{"wake": True}] and att2.detached
    assert sum(1 for m in lines if m.startswith("sync: ") and ": done (" in m) == 2
    # no fetcher answers (and none could be started): nothing fetched
    monkeypatch.setattr(cli, "attach_fetcher", lambda db, log: None)
    assert cli.dispatch(settings, lines.append) == 0 and "no fetcher answered" in lines[-1]
    # Ctrl+C while waiting: the fetcher let go (it ends unless something else holds it)
    att3 = FakeAttachment("fetcher", lives=99)
    att3.busy = [True] * 99

    def interrupted(s):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli.time, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.wait_for_fetcher(att3, lines.append)
    assert att3.detached and lines[-1].startswith("sync: the fetcher let go")
    # `sync --tickers` by hand goes the same way
    monkeypatch.setattr(cli, "dispatch", lambda s, log=None, tickers=None, stop=None: lines.append(f"dispatch {sorted(tickers or [])}") or 1)
    args = cli.build_parser().parse_args(["sync", "--tickers", "spcx"])
    assert cli._sync(args, settings) == (0, 1) and lines[-1] == "dispatch ['SPCX']"


def test_the_fetcher_commands_and_its_host_are_the_integrators(tmp_path, monkeypatch):
    """`liminal-charts host fetcher`, `liminal-charts fetcher ...` and `liminal-charts service ...` answer through the
    integrator (liminal-integrator), installed beside the app; without it they say where it is."""
    import liminal_integrator.cli as integrator_cli
    import liminal_integrator.fetcher as integrator_fetcher

    settings = _settings(tmp_path)
    seen: list = []
    monkeypatch.setattr(integrator_fetcher, "run_host", lambda s, log=None, port=None, rescan=None: seen.append(("host", s)) or "ended")
    monkeypatch.setattr(integrator_cli, "cmd_fetcher", lambda args, s: seen.append(("fetcher", args.action)) or 0)
    monkeypatch.setattr(integrator_cli, "cmd_service", lambda args, s: seen.append(("service", args.action)) or 0)
    assert cli.cmd_host(Namespace(name="fetcher"), settings) == 0
    assert cli.cmd_fetcher(cli.build_parser().parse_args(["fetcher", "status"]), settings) == 0
    assert cli.cmd_service(cli.build_parser().parse_args(["service", "install"]), settings) == 0
    assert seen == [("host", settings), ("fetcher", "status"), ("service", "install")]
    monkeypatch.setattr(cli, "integrator", lambda: None)  # not installed
    assert cli.cmd_fetcher(Namespace(action="serve"), settings) == 2 and cli.cmd_service(Namespace(action="run"), settings) == 2
    monkeypatch.setitem(__import__("sys").modules, "liminal_integrator", None)  # an import of it fails
    assert cli.cmd_host(Namespace(name="fetcher"), settings) == 2


def test_the_fetcher_and_service_commands_apply_an_integrators_name_database_and_plugins(tmp_path, monkeypatch):
    """`liminal-charts fetcher --name paper --plugins none` and `liminal-charts service install --name ...` apply the
    integrator's name, database and plugin selection to this process first (the integrator's
    apply_instance, 1.4.0), and load the settings again for it; a name that cannot be one is said."""
    import liminal_integrator.cli as integrator_cli
    import liminal_integrator.fetcher as integrator_fetcher
    from liminal_libs.sources import registry

    settings = _settings(tmp_path)
    for var in (integrator_fetcher.NAME_VAR, "LIMINAL_DB", registry.SELECTION_VAR):
        monkeypatch.setenv(var, "")  # restored (deleted) after
    seen: list = []
    monkeypatch.setattr(integrator_cli, "cmd_fetcher", lambda args, s: seen.append(("fetcher", s.db_path)) or 0)
    monkeypatch.setattr(integrator_cli, "cmd_service", lambda args, s: seen.append(("service", s.db_path)) or 0)
    monkeypatch.setattr(cli, "load_settings", lambda: replace(settings, db_path=Path(os.environ["LIMINAL_DB"])))
    try:
        assert cli.cmd_fetcher(cli.build_parser().parse_args(["fetcher", "status"]), settings) == 0
        assert seen[-1] == ("fetcher", settings.db_path)  # nothing applied: the settings as loaded
        db = tmp_path / "paper" / "liminal.db"
        assert cli.cmd_fetcher(cli.build_parser().parse_args(["fetcher", "serve", "--name", "paper", "--db", str(db),
                                                               "--plugins", "none"]), settings) == 0
        assert seen[-1] == ("fetcher", db) and os.environ[integrator_fetcher.NAME_VAR] == "paper"
        assert os.environ[registry.SELECTION_VAR] == "none" and registry.providers() == {}
        assert cli.cmd_service(cli.build_parser().parse_args(["service", "install", "--name", "paper", "--plugins", "all"]),
                               settings) == 0
        assert seen[-1] == ("service", integrator_fetcher.instance_db("paper")) and registry.providers()
        assert cli.cmd_fetcher(cli.build_parser().parse_args(["fetcher", "serve", "--name", "x y"]), settings) == 2
        assert cli.cmd_fetcher(Namespace(action="serve"), settings) == 0  # an integrator's parser without them
    finally:
        registry.select(None)


def test_nothing_is_scheduled_and_the_extras_run_alone(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    assert cli.cmd_schedule(Namespace(action="install", every_hours=1), settings) == 2
    assert "nothing is scheduled" in capsys.readouterr().out
    done: list[str] = []
    monkeypatch.setattr(cli, "_fetch_gov", lambda s, log, force=False, stop=None: done.append("gov"))
    monkeypatch.setattr(cli, "_stores_pass", lambda s, log, history=True: done.append(f"stores history={history}"))
    monkeypatch.setattr(cli, "after_sync", lambda args, s: done.append("after"))
    monkeypatch.setattr(cli, "_sync", lambda args, s: done.append("feeds") or (0, 0))
    assert cli.cmd_sync(Namespace(extras_only=True, all_sources=True, tickers=None, fetch_only=False), settings) == 0
    assert done == ["gov", "stores history=False", "after"]  # the feeds are the fetcher's to hold, not this process's
