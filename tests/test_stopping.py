"""0.2.5 (Monday list item 38; the shutdown audit of 2026-11-24: the derived-bar build ran on 20-60 minutes past a
stop, holding MongoDB): the extras' pass takes its stop and passes it into the composites, the derive pass, the
datasets and the publications; MongoDB's keeper is let go after the stores pass; `sync` and the hosts hear their stop
event; the page's commands start through lifecycle.spawn; the front ends in good order on its stop event. Stand-ins
only: no event of this machine is opened or set (a stand-in kernel32), no process started, no port opened."""
from __future__ import annotations

import signal
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from liminal_charts import cli
from liminal_charts.gui import front as front_mod
from liminal_charts.gui import server
from liminal_libs import lifecycle
from liminal_libs.config import load_settings
from liminal_libs.lifecycle import StopEvent as RealStopEvent  # the class itself: conftest stands one in for tests

@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")


class K32:
    """A stand-in kernel32 for named events: the names opened, one set by the test."""

    def __init__(self):
        self.opened, self.set, self.closed = [], False, 0

    def CreateEventW(self, sa, manual, initial, name):
        self.opened.append(name)
        return len(self.opened)

    def WaitForSingleObject(self, handle, ms):
        return 0 if self.set else 0x102

    def CloseHandle(self, handle):
        self.closed += 1
        return 1


def _until(cond, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def test_the_extras_stop_between_symbols_and_pass_the_stop_to_every_step(settings, monkeypatch):
    from liminal_libs import composite, datasets, derive

    event = threading.Event()
    seen: dict = {}
    built: list[str] = []

    def refresh(store, feeds, now, log=print, rebuild_days=2, stop=None):
        seen["composite"] = stop
        return {}

    def run(store, sources=None, tickers=None, log=print, stop=None):  # the derive pass, a symbol at a time
        seen["derive"] = asked = stop
        for symbol in ("AAA", "BBB", "CCC"):
            if asked is not None and asked():
                break
            built.append(symbol)
            if symbol == "AAA":
                event.set()  # asked to stop while the first symbol was built (liminal-signal, the fetcher letting go)
        return len(built)

    monkeypatch.setattr(composite, "refresh", refresh)
    monkeypatch.setattr(derive, "run", run)
    monkeypatch.setattr(datasets, "fetch", lambda *a, **k: seen.setdefault("datasets", k.get("stop")))
    assert cli.after_sync(SimpleNamespace(tickers=None, all_sources=True, _stop=event), settings) is True
    assert callable(seen["composite"]) and seen["derive"] is seen["composite"]  # the stop, asked as each goes
    assert built == ["AAA"]  # stopped between symbols: the rest at the next pass
    assert "datasets" not in seen  # not begun once asked to stop
    # not asked to stop: every step runs, the datasets with the stop too
    built.clear()
    seen.clear()
    monkeypatch.setattr(derive, "run", lambda store, sources=None, tickers=None, log=print, stop=None: 0)
    assert cli.after_sync(SimpleNamespace(tickers=None, all_sources=True, _stop=threading.Event()), settings) is True
    assert callable(seen["datasets"])


def test_the_extras_let_mongodb_go_after_the_stores_pass_and_end_at_a_stop(settings, monkeypatch):
    from liminal_libs import mongo, stores

    order: list = []
    monkeypatch.setattr(cli, "_fetch_gov", lambda s, log, force=False, stop=None: order.append(("gov", stop)))
    monkeypatch.setattr(stores, "settle", lambda store, gconn, folder, log: order.append("stores"))
    monkeypatch.setattr(mongo, "detach", lambda: order.append("mongo let go"))
    monkeypatch.setattr(cli, "after_sync", lambda args, s: order.append("after"))
    monkeypatch.setattr(cli, "Logger", lambda path: (lambda line: order.append(line)))
    args = SimpleNamespace(extras_only=True, _stop=threading.Event())
    assert cli.cmd_sync(args, settings) == 0
    assert order == [("gov", args._stop), "stores", "mongo let go", "after"]
    # asked to stop during the publications: nothing more begun, said
    order.clear()
    stop = threading.Event()
    monkeypatch.setattr(cli, "_fetch_gov", lambda s, log, force=False, stop=None: stop.set())
    assert cli.cmd_sync(SimpleNamespace(extras_only=True, _stop=stop), settings) == 0
    assert order == ["sync: the extras were asked to stop; the rest at the next pass"]


def test_sync_hears_its_own_stop_event_else_the_charts_one_and_sigterm(monkeypatch):
    monkeypatch.setattr(lifecycle, "StopEvent", RealStopEvent)
    k32 = K32()
    env = {cli.STOP_VAR: "Local\\liminal-stop-integrator-extras-7-1", "OTHER": "x"}
    stop = cli.listen_for_stop(env=env, k32=k32, every=0.01)
    assert k32.opened == ["Local\\liminal-stop-integrator-extras-7-1"] and cli.STOP_VAR not in env  # not inherited
    assert not stop.is_set()
    k32.set = True
    assert stop.wait(3) and _until(lambda: k32.closed == 1)
    # none named: the charts app's own
    k32 = K32()
    stop = cli.listen_for_stop(env={}, k32=k32, every=0.01)
    assert k32.opened == ["Local\\liminal-stop-charts"]
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # its handler, as the system would call it
    assert stop.is_set()


def test_a_host_answers_its_apps_stop_event_once_its_handler_is_in_place(monkeypatch):
    monkeypatch.setattr(lifecycle, "StopEvent", RealStopEvent)
    k32, raised, lines = K32(), [], []
    handler = {"now": signal.SIG_DFL}  # Host.serve has not put its handler in place yet
    monkeypatch.setattr(cli, "STOP_LOOK_S", 0.01)
    cli._host_answers_its_stop_event("feed-massive", "integrator", lines.append, k32=k32, every=0.01,
                                     raise_signal=raised.append, getsignal=lambda s: handler["now"])
    assert k32.opened == ["Local\\liminal-stop-integrator"]
    k32.set = True
    time.sleep(0.1)
    assert raised == []  # SIGTERM's default would end the process outright: not raised yet
    handler["now"] = lambda signum, frame: None  # Host.serve's
    assert _until(lambda: raised == [signal.SIGTERM])
    assert lines == ["host feed-massive: asked to stop (Local\\liminal-stop-integrator): it ends, its data secured"]
    assert cli.HOST_APPS == {"fetcher": "integrator", "history": "integrator", "live": "charts"}


def test_the_pages_commands_start_through_lifecycle_spawn(monkeypatch):
    seen: list = []
    monkeypatch.setattr(lifecycle, "spawn", lambda args, **kw: seen.append((args, kw)) or SimpleNamespace(pid=None))
    server.ChartApp._spawn(None, ["py", "-m", "liminal_charts", "capabilities", "massive"])
    (args, kw), = seen
    assert (kw["owner"], kw["kind"], kw["tied"]) == ("charts", "capabilities", True)
    assert kw["popen"] is server.subprocess.Popen  # this module's Popen: conftest's stand-in refuses every start


class Httpd:
    """A stand-in for the front's server: no port is opened; it serves until shut down."""

    def __init__(self, address, handler):
        self.server_address = ("127.0.0.1", 8999)
        self.down = threading.Event()
        self.closed = False

    def serve_forever(self):
        self.down.wait(10)

    def shutdown(self):
        self.down.set()

    def server_close(self):
        self.closed = True


class Event:
    def __init__(self):
        self.asked, self.closed = threading.Event(), False

    def is_set(self):
        return self.asked.is_set()

    def close(self):
        self.closed = True


def test_the_front_ends_in_good_order_on_its_stop_event(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    made: list = []
    monkeypatch.setattr(front_mod, "ThreadingHTTPServer", lambda address, handler: made.append(Httpd(address, handler))
                        or made[-1])
    event = Event()
    monkeypatch.setattr(front_mod, "stop_event", lambda: event)
    monkeypatch.setattr(front_mod, "STOP_LOOK_S", 0.01)
    stopped: list = []
    monkeypatch.setattr(front_mod.Front, "close", lambda self: stopped.append("the charts app stopped"))
    lines: list[str] = []
    done = threading.Event()
    threading.Thread(target=lambda: (front_mod.serve_front(tmp_path / "liminal.db", port=0, open_browser=False,
                                                           log=lines.append), done.set()), daemon=True).start()
    assert _until(lambda: bool(made))
    event.asked.set()  # liminal-signal sets the charts' stop event
    assert done.wait(5)  # ended by its event alone
    assert made[0].closed and stopped == ["the charts app stopped"] and event.closed
    assert "front: asked to stop (its stop event): the page goes down, its login closed" in lines
    assert lines[-1] == "front: ended"
