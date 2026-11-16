"""The pass after a sync and the closed months' copy held against every installation on the database (liminal-libs
claims.py), the work that is the files' own system's refused from another, and the queue page beside another
installation (the user, 2026-11-15: "If i run integrator on both sides, there cannot be any conflicts"). The other
installation is a second Claims object on the same file; "another system's drive" is mounts.foreign standing in; no
process is started."""
from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from liminal_charts import cli
from liminal_charts.gui.server import ChartApp
from liminal_libs import claims, mounts
from liminal_libs.claims import Claims
from liminal_libs.config import load_settings
from liminal_libs.storage import Store

# The installation that is not the one running these tests (inside WSL this one is linux::Ubuntu itself)
OTHER = "linux::Ubuntu" if claims.os_name() == "windows" else "windows::NEPTUNE"


@pytest.fixture(autouse=True)
def nothing_left_held():
    yield
    claims._give_back()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "liminal.db"
    Store(path).close()
    return path


@pytest.fixture
def logged(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(cli, "Logger", lambda path: lines.append)
    return lines


def _other(path, pid: int = 4321) -> Claims:
    return Claims.open(path, me=OTHER, pid=pid, alive=lambda p, s: True)


def test_the_pass_after_a_sync_is_left_to_the_installation_that_is_at_it(db, logged):
    other = _other(db)
    try:
        theirs = other.take("post", what="composites and derived frames")  # no lock file here says so
        args = SimpleNamespace(tickers=None, all_sources=False)
        assert cli.after_sync(args, SimpleNamespace(db_path=db)) is False
        assert "another process is building them" in logged[0] and not db.with_name(cli.POST_LOCK).exists()
        other.release(theirs)
    finally:
        other.close()


def test_the_closed_months_copy_is_one_at_a_time_and_the_files_own_systems(db, logged, monkeypatch):
    from liminal_libs.storage import history

    monkeypatch.setattr(history, "available", lambda: True)
    monkeypatch.setattr(history, "run", lambda *a, **k: pytest.fail("no copy is made here"))
    settings = SimpleNamespace(db_path=db, read_only=False)
    run = Namespace(action="run", feed=None, budget=1)
    other = _other(db)
    try:
        other.take("history", what="the closed months' copy")
        assert cli.cmd_history(run, settings) == 0 and logged[-1] == "history: another run is copying; left to it"
        assert not db.with_name(cli.HISTORY_LOCK).exists()
    finally:
        other.close()
    monkeypatch.setenv("LIMINAL_LINK", "loopback")  # its databases through a link, as from there
    monkeypatch.setattr(mounts, "foreign", lambda p, mounts=None: True)  # Windows' data, seen from inside WSL
    assert cli.cmd_history(run, settings) == 0 and "copied by the system whose drive the data is on" in logged[-1]
    assert claims.mine(db) == {} and not db.with_name(cli.HISTORY_LOCK).exists()  # nothing taken from here


def test_a_backup_is_made_by_the_system_that_holds_the_files(db, monkeypatch, capsys):
    settings = load_settings(env={"LIMINAL_DB": str(db)}, env_file=None)
    monkeypatch.setenv("LIMINAL_LINK", "loopback")  # its databases through a link, as from there
    monkeypatch.setattr(mounts, "foreign", lambda p, mounts=None: True)
    with pytest.raises(SystemExit) as e:
        cli.cmd_admin(Namespace(action="backup", ticks=False, dest=None), settings)
    assert "a backup is made by the system that holds the files" in str(e.value) and "another system's drive" in str(e.value)
    assert not (db.parent / "backups").exists()


def test_the_queue_page_shows_the_other_installations_job_running_while_it_is_at_work(db):
    with Store(db, check_same_thread=False) as store:
        store.queue_start("massive", "massive", "bars", "AAPL")
        store.queue_start("massive", "massive", "bars", "MSFT")
        with store.conn:
            store.conn.execute("UPDATE fetch_queue SET pid=4321, instance=? WHERE key='AAPL'", (OTHER,))
            store.conn.execute("UPDATE fetch_queue SET pid=4322, instance=? WHERE key='MSFT'", (OTHER,))
        other = _other(db)
        try:
            other.take("feed:massive", what="the massive feed's run")  # process 4321 is at work; 4322 holds nothing
            app = ChartApp(store, settings=load_settings(env={"LIMINAL_DB": str(db)}, env_file=None))
            q = app.queue({})
            rows = {r["key"]: r for r in q["rows"]}
            assert rows["AAPL"]["state"] == "running" and rows["AAPL"]["instance"] == OTHER
            assert rows["MSFT"]["state"] == "stopped" and rows["MSFT"]["detail"] == "its process ended before it"
            assert q["totals"] == {"running": 1, "stopped": 1}
            assert app.plugin_fetching("massive")["state"] == "fetching"  # its run there: this page says so too
        finally:
            other.close()
