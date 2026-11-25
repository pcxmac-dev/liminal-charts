"""Resident hosts (host.py): a process that runs only while a client is attached over a local pipe,
and ends, its data secured, once its last client is gone (the user, 2026-11-01: no processes or
open databases when the app or a plugin is not in use). The host runs in a thread here: tests never
start processes."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from liminal_libs import host as hosting
from liminal_libs.host import Attachment, Host, address, attach, key
from liminal_libs.host import start_host as real_start_host  # bound before conftest's guard replaces it


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMINAL_IPC_DIR", str(tmp_path / "ipc"))
    return tmp_path / "liminal.db"


class Served:
    """A host served in a thread: its work waits for stop; what happened, in order."""

    def __init__(self, name, db, **kw):
        self.host = Host(name, db, log=lambda m: self.events.append(m), **kw)
        self.events: list[str] = []
        self.order: list[str] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        self._wait_listening()

    def _serve(self):
        def work(stop):
            stop.wait(30)
            self.order.append("work ended")

        def secure():
            self.order.append("secured")
        self.host.serve(work, secure, handle=lambda q: {"answer": q.get("what"), "clients": self.host.clients()})

    def _wait_listening(self):
        end = time.monotonic() + 5
        while self.host._listener is None and time.monotonic() < end:
            time.sleep(0.01)
        assert self.host._listener is not None

    def ended(self, within=5.0) -> bool:
        self.thread.join(within)
        return not self.thread.is_alive()


def until(cond, within=5.0) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_a_host_ends_at_once_when_its_last_client_lets_it_go(db):
    # liminal-libs 1.87.0 (the shutdown audit of 2026-11-24): a last client that says "detach" ends its host at once,
    # not after the idle time (60 s a host, one host after another at a stop); the idle time is the next test's
    served = Served("t", db, idle=30, start=5)
    a = attach("t", db, log=print)
    assert a is not None and a.host_pid == os.getpid() and a.alive()
    assert until(lambda: served.host.clients() == [os.getpid()])
    assert not served.host.stop.is_set()
    a.detach()
    assert until(lambda: served.host.clients() == [])
    assert served.ended() and served.host.stop.is_set()
    assert served.order == ["work ended", "secured"]  # the data secured after the work, before the exit
    assert served.host.reason == "its last client let it go"
    assert not a.alive()


def gone_without_a_word(a: Attachment) -> None:
    """The client's end of the pipe closed with no "detach" said: its process killed or crashed."""
    a._conn.close()
    a.detached = True


def test_a_host_ends_once_its_last_client_has_been_gone_for_the_idle_time(db):
    # a client gone without a word is waited for (a restart's own client may come back): the idle time, at most
    # GONE_SECONDS (liminal-libs 1.87.0)
    served = Served("t", db, idle=0.3, start=5)
    a = attach("t", db, log=print)
    assert until(lambda: served.host.clients() == [os.getpid()])
    gone_without_a_word(a)
    assert until(lambda: served.host.clients() == [])
    assert served.ended() and served.host.stop.is_set()
    assert served.order == ["work ended", "secured"]
    assert "no client attached for 0.3 s" in served.host.reason


def test_a_client_attaching_again_keeps_the_host_up(db):
    served = Served("t", db, idle=0.4, start=5)
    a = attach("t", db)
    assert until(lambda: served.host.clients() == [os.getpid()])
    gone_without_a_word(a)  # 1.87.0: a "detach" would end it at once; a client gone without one is waited for
    assert until(lambda: served.host.clients() == [])
    time.sleep(0.15)
    with attach("t", db) as b:  # before the idle time ran out
        assert until(lambda: served.host.clients() == [os.getpid()])
        time.sleep(0.5)
        assert not served.host.stop.is_set() and b.alive()  # still up: a client is attached
    assert served.ended()


def test_two_clients_count_as_two_attachments(db):
    served = Served("t", db, idle=0.2, start=5)
    a, b = attach("t", db), attach("t", db)
    assert until(lambda: len(served.host.attached) == 2)
    a.detach()
    time.sleep(0.4)
    assert not served.host.stop.is_set()  # one still attached
    b.detach()
    assert served.ended()


def test_a_host_nobody_attaches_to_ends_after_the_start_grace(db):
    served = Served("t", db, idle=10, start=0.3)
    assert served.ended()
    assert "no client came within 0.3 s" in served.host.reason and served.order == ["work ended", "secured"]


def test_a_client_whose_process_is_gone_counts_as_detached(db, monkeypatch):
    monkeypatch.setattr(hosting, "CHECK_SECONDS", 0.1)
    monkeypatch.setattr(hosting, "pid_alive", lambda pid: pid != 424242)
    served = Served("t", db, idle=0.2, start=5)
    from multiprocessing.connection import Client

    conn = Client(address("t", db), hosting.family(), authkey=key())
    conn.send({"attach": "t", "pid": 424242, "api": 1})  # a client that died with its pipe end still held (a launcher's)
    assert conn.recv()["ok"]
    assert until(lambda: served.host.clients() == [424242])
    assert until(lambda: served.host.clients() == [])  # dropped at the next check
    assert served.ended()
    conn.close()


def test_a_connection_that_says_nothing_is_not_an_attachment(db, monkeypatch):
    monkeypatch.setattr(hosting, "HELLO_SECONDS", 0.1)
    served = Served("t", db, idle=10, start=0.5)
    from multiprocessing.connection import Client

    conn = Client(address("t", db), hosting.family(), authkey=key())
    time.sleep(0.3)
    assert served.host.clients() == [] and not served.host._ever
    assert served.ended()  # no client came: a probe is not one
    conn.close()


def test_a_client_asks_the_host(db):
    served = Served("t", db, idle=0.3, start=5)
    with attach("t", db) as a:
        assert a.ask(what="status") == {"answer": "status", "clients": [os.getpid()]}
        assert a.alive()
    assert served.ended()


def test_attach_starts_a_host_when_none_answers(db):
    started: list[Served] = []
    assert attach("t", db, wait=0.3) is None  # none answers, none started
    a = attach("t", db, start=lambda: started.append(Served("t", db, idle=0.3, start=5)), wait=5)
    assert a is not None and len(started) == 1
    a.detach()
    assert started[0].ended()


def test_the_key_is_made_once_and_the_pipe_is_per_database(db, tmp_path):
    k = key()
    assert len(k) >= 32 and key() == k and (Path(hosting.ipc_dir()) / "key").read_bytes().strip() == k
    assert address("live", db) != address("live", tmp_path / "other.db") and address("live", db) != address("mongo", db)
    assert address("live", db) == address("live", Path(str(db)))


def test_a_wrong_key_cannot_attach(db, monkeypatch):
    served = Served("t", db, idle=10, start=0.6)
    monkeypatch.setattr(hosting, "key", lambda: b"x" * 32)
    assert attach("t", db, wait=0.3) is None
    assert served.host.clients() == [] and not served.host._ever
    assert served.ended()


def test_a_second_host_for_the_same_name_does_not_run(db):
    served = Served("t", db, idle=0.3, start=5)
    with attach("t", db):
        other = Host("t", db, idle=1, start=1, log=lambda m: None)
        ran: list[str] = []
        reason = other.serve(lambda stop: ran.append("ran"))
        assert ran == [] and "another host answers for t" in reason
    assert served.ended()


def test_start_host_runs_the_host_command_without_a_window(db, monkeypatch):
    calls: list[tuple] = []

    class Popen:
        def __init__(self, args, **kw):
            calls.append((args, kw))
    monkeypatch.setattr(hosting.subprocess, "Popen", Popen)
    real_start_host("live", db)
    (args, kw), = calls
    assert args[1:] == ["-m", "liminal_charts", "host", "live"] and kw["env"]["LIMINAL_DB"] == str(db)
    if os.name == "nt":
        assert kw["creationflags"] & 0x08000000 and kw["creationflags"] & 0x00000008  # no window, detached
    assert kw["stdin"] == kw["stdout"] == kw["stderr"] == hosting.subprocess.DEVNULL


def test_an_attachment_is_a_context_manager_that_detaches_once(db):
    served = Served("t", db, idle=0.2, start=5)
    a = attach("t", db)
    assert isinstance(a, Attachment)
    with a:
        pass
    a.detach()  # again: nothing
    assert a.detached and not a.alive()
    assert served.ended()
