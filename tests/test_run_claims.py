"""A feed's run is a claim in the database, held against every process on it whichever system it runs on (the user,
2026-11-15: "If i run integrator on both sides, there cannot be any conflicts"; liminal-libs claims.py). The run lock
(cli.run_lock) takes the claim and, for the processes started before claims, the lock file too; the whole database's
lock stays a file and is the files' own system's to take; the feeds being fetched are every system's. The other
installation here is a second Claims object on the same file: no process is started."""
from __future__ import annotations

import os

import pytest

from liminal_charts import cli
from liminal_charts.gui import activity
from liminal_libs import claims, mounts, proc
from liminal_libs.claims import Claims


@pytest.fixture(autouse=True)
def nothing_left_held():
    yield
    claims._give_back()
    cli._HELD.clear()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "liminal.db"


def _linux(path, pid: int = 4321) -> Claims:
    """The suite inside WSL on the same database (its processes cannot be looked up from here)."""
    return Claims.open(path, me="linux::Ubuntu", pid=pid, alive=lambda p, s: True)


def test_a_feeds_run_takes_its_claim_and_its_lock_file(db):
    lock = proc.run_lock_path(db, "massive")
    with cli.run_lock(db, "massive"):
        held = claims.held(db)
        assert [(h.key, h.own, h.what) for h in held] == [("feed:massive", True, "the massive feed's run")]
        assert proc.owns(lock)  # a run started before claims still sees it
        with cli.run_lock(db, "massive"):  # this process again
            assert proc.owns(lock)
        assert proc.owns(lock) and [h.key for h in claims.held(db)] == ["feed:massive"]
        with cli.run_lock(db, "alpaca"):  # another feed beside it
            assert sorted(activity.fetching_feeds(db)) == ["alpaca", "massive"]
    assert not lock.exists() and claims.held(db) == [] and activity.fetching_feeds(db) == {} and activity.lock_owner(db) is None


def test_a_feed_the_other_system_is_fetching_is_busy_here(db):
    linux = _linux(db)
    try:
        linux.take("feed:massive", what="the massive feed's run")  # no lock file: its process id would mean nothing here
        with pytest.raises(cli.Busy) as e:
            with cli.run_lock(db, "massive"):
                pytest.fail("the other system holds this feed")
        assert "another run is in progress (linux::Ubuntu (process 4321): the massive feed's run)" in str(e.value)
        assert not proc.run_lock_path(db, "massive").exists()  # nothing left behind
        running = activity.fetching_feeds(db)
        assert list(running) == ["massive"] and running["massive"].instance == "linux::Ubuntu" and activity.lock_owner(db)
        with cli.run_lock(db, "alpaca"):  # another feed is free: each side works on its own
            assert sorted(activity.fetching_feeds(db)) == ["alpaca", "massive"]
        # no move of the data while the other system fetches, though no lock file says so
        with pytest.raises(cli.Busy, match=r"feeds are fetching \(massive\)"):
            with cli.run_lock(db):
                pytest.fail("a feed is being fetched")
        assert not proc.run_lock_path(db).exists()
        linux.release("feed:massive")
        with cli.run_lock(db, "massive"):
            assert linux.take("feed:massive") is None  # and the other way round
    finally:
        linux.close()


def test_a_run_from_before_claims_still_keeps_its_feed(db):
    lock = proc.run_lock_path(db, "massive")
    lock.write_text(f"{os.getppid()} 0")  # a live process that knows lock files alone
    with pytest.raises(cli.Busy) as e:
        with cli.run_lock(db, "massive"):
            pytest.fail("a process from before claims holds it")
    assert f"process {os.getppid()}" in str(e.value) and "lock file" in str(e.value)
    assert claims.held(db) == [] and lock.exists()  # the claim taken for it given back; its lock left alone
    assert list(activity.fetching_feeds(db)) == ["massive"]


def test_the_whole_database_is_locked_only_from_the_system_whose_drive_it_is_on(db, monkeypatch):
    with cli.run_lock(db):  # here: as before, a lock file; its holder's own feeds run under it
        assert proc.owns(proc.run_lock_path(db)) and None in activity.fetching_feeds(db)
        with cli.run_lock(db, "massive"):
            assert [h.key for h in claims.held(db)] == ["feed:massive"]
    assert not proc.run_lock_path(db).exists() and claims.held(db) == []
    proc.run_lock_path(db).write_text(f"{os.getppid()} 0")  # another process is moving the data
    with pytest.raises(cli.Busy, match="the whole database is locked"):
        with cli.run_lock(db, "massive"):
            pytest.fail("the data is being moved")
    assert claims.held(db) == [] and not proc.run_lock_path(db, "massive").exists()  # taken, then given back at once
    proc.run_lock_path(db).unlink()
    monkeypatch.setattr(mounts, "foreign", lambda p, mounts=None: True)  # the same database, seen through a mount
    with pytest.raises(cli.Busy, match="another system's drive"):
        with cli.run_lock(db):
            pytest.fail("not moved from here")
    assert not proc.run_lock_path(db).exists()
    with cli.run_lock(db, "massive"):  # a feed's run there: the claim alone, no lock file written
        assert [h.key for h in claims.held(db)] == ["feed:massive"] and not proc.run_lock_path(db, "massive").exists()
    proc.run_lock_path(db).write_text("77 1")  # the files' own system is moving the data
    with pytest.raises(cli.Busy, match="the whole database is locked"):
        with cli.run_lock(db, "massive"):
            pytest.fail("the data is being moved by its own system")
