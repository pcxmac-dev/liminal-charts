"""The MongoDB server under a keeper host (mongo.py, `liminal-charts host mongo`): a process attaches to the
keeper when its documents live in MongoDB, the keeper starts the server for the first client and
shuts it down cleanly once the last one is gone (the user, 2026-11-01: "attached software like
duckdb or what ever should be controlled by the plugin"; no processes when the app is not in use).
Nothing here starts a process: the host, the keeper and the server are stand-ins."""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from liminal_charts import cli
from liminal_libs import host as hosting, mongo
from liminal_libs.config import load_settings


class FakeAttachment:
    def __init__(self):
        self.detached = False

    def alive(self):
        return not self.detached

    def detach(self):
        self.detached = True


@pytest.fixture
def keeper(monkeypatch, tmp_path):
    """A keeper to attach to: who attached, and a server that answers once attached."""
    made: list[tuple[str, Path]] = []
    attachments: list[FakeAttachment] = []

    def attach_or_start(name, db_path, log=print):
        made.append((name, Path(db_path)))
        attachments.append(FakeAttachment())
        return attachments[-1]
    monkeypatch.setattr(hosting, "attach_or_start", attach_or_start)
    monkeypatch.setattr(mongo, "binary", lambda: tmp_path / "mongod.exe")
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: bool(attachments) and not attachments[-1].detached)
    monkeypatch.setattr(mongo, "_attachment", None)
    return SimpleNamespace(made=made, attachments=attachments)


def test_a_process_attaches_to_the_keeper_once_and_the_server_answers(tmp_path, keeper):
    assert mongo.ensure(tmp_path) is True
    assert mongo.attach(tmp_path) is True  # attached already: kept
    assert keeper.made == [(mongo.KEEPER, tmp_path / "liminal.db")]  # the keeper of this data folder
    assert mongo.alive(0)
    mongo.detach()
    assert keeper.attachments[0].detached and mongo._attachment is None
    assert not mongo.alive(0)  # the keeper will stop the server once every client is gone
    mongo.detach()  # again: nothing
    assert mongo.ensure(tmp_path) is True and len(keeper.made) == 2  # attached anew when wanted again


def test_an_attachment_the_keeper_dropped_is_made_again(tmp_path, keeper):
    assert mongo.ensure(tmp_path)
    keeper.attachments[0].detached = True  # the keeper went away (ended by hand, say)
    assert mongo.ensure(tmp_path) and len(keeper.made) == 2


def test_nothing_is_attached_without_a_server_installed_or_from_a_reading_copy(tmp_path, monkeypatch, keeper):
    monkeypatch.setattr(mongo, "binary", lambda: None)
    assert mongo.ensure(tmp_path) is False and keeper.made == []
    with pytest.raises(RuntimeError, match="not installed"):
        mongo.attach(tmp_path)
    monkeypatch.setattr(mongo, "binary", lambda: tmp_path / "mongod.exe")
    from liminal_libs.storage import common

    monkeypatch.setattr(common, "read_only", lambda: True)
    with pytest.raises(RuntimeError, match="read-only"):
        mongo.attach(tmp_path)
    assert mongo.ensure(tmp_path) is False and keeper.made == []


def test_the_keeper_starts_the_server_for_its_clients_and_shuts_it_down_after_them(tmp_path, monkeypatch):
    settings = replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")
    events: list[str] = []
    monkeypatch.setattr(cli, "Logger", lambda path: events.append)
    monkeypatch.setattr(mongo, "binary", lambda: tmp_path / "mongod.exe")
    monkeypatch.setattr(mongo, "start", lambda data_dir, wait_s=20.0: events.append(f"started in {data_dir.name}") or 4242)
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: True)
    monkeypatch.setattr(mongo, "stop", lambda: events.append("shut down") or True)

    class FakeHost:
        def __init__(self, name, db_path, log=print):
            events.append(f"host {name} for {Path(db_path).parent.name}")
            self.stop = threading.Event()

        def serve(self, work, secure=None, handle=None):
            threading.Timer(0.05, self.stop.set).start()  # the clients went
            work(self.stop)
            secure()
            return "no client attached"
    monkeypatch.setattr(hosting, "Host", FakeHost)
    assert cli.cmd_host(SimpleNamespace(name="mongo"), settings) == 0
    marks = [e for e in events if not e.startswith("host mongo:")]  # the keeper's own log lines aside
    assert marks == [f"host mongo for {tmp_path.name}", f"started in {tmp_path.name}", "shut down"]
    assert any("server shut down" in e for e in events)


def test_the_keeper_ends_when_the_server_is_gone_and_runs_nothing_it_cannot_start(tmp_path, monkeypatch):
    settings = replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")
    events: list[str] = []
    monkeypatch.setattr(cli, "Logger", lambda path: events.append)
    monkeypatch.setattr(mongo, "binary", lambda: tmp_path / "mongod.exe")
    monkeypatch.setattr(mongo, "start", lambda data_dir, wait_s=20.0: None)  # one was running already: adopted
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: False)  # ... and then it went (mongo stop)
    monkeypatch.setattr(mongo, "stop", lambda: events.append("stop asked") or False)
    # no real time (CI windows-3.12 missed a real 0.05 s look against a real 0.1 s grace): the keeper's clock is a
    # stand-in that its stop's wait moves on by each look, so it pings GONE_PINGS times over GONE_GRACE_S in order
    monkeypatch.setattr(mongo, "GONE_GRACE_S", 2.0)  # 1.87.0: gone after 3 failed pings over this, not one
    now = [0.0]  # a look each 1.0: misses at 1, 2 and 3, the third 2.0 after the first

    class StandInStop:
        def __init__(self):
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            assert self.waits < 1000, "the keeper never took the server for gone"
            now[0] += timeout
            return False  # the clients stay: only the server's going ends the keeper

        def is_set(self):
            return False

        def set(self):
            raise AssertionError("the keeper's work set its own stop")

    real_keep = mongo.keep
    monkeypatch.setattr(mongo, "keep", lambda *a, **kw: real_keep(*a, clock=lambda: now[0], every=1.0, **kw))

    class FakeHost:
        def __init__(self, name, db_path, log=print):
            self.stop = StandInStop()

        def serve(self, work, secure=None, handle=None):
            work(self.stop)  # returns by itself: the server is gone
            assert not self.stop.is_set()
            secure()
            return "its work ended"
    monkeypatch.setattr(hosting, "Host", FakeHost)
    assert cli.cmd_host(SimpleNamespace(name="mongo"), settings) == 0
    assert [e for e in events if not e.startswith("host mongo:")] == ["stop asked"]
    assert any("the server has not answered 3 pings" in e and "the keeper ends" in e for e in events)
    # not installed: said, and nothing served
    monkeypatch.setattr(mongo, "binary", lambda: None)
    served: list = []
    monkeypatch.setattr(hosting, "Host", lambda name, db_path, log=print: served.append(name))
    assert cli.cmd_host(SimpleNamespace(name="mongo"), settings) == 0
    assert served == [] and "not installed" in events[-1]


def test_mongo_start_holds_the_server_up_only_while_the_command_runs(tmp_path, monkeypatch, keeper, capsys):
    settings = replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")
    monkeypatch.setattr(cli, "hold_until_interrupted", lambda: None)
    monkeypatch.setattr(mongo, "status", lambda data_dir: {"version": "9.0.4", "running": True, "pid": 4242, "residentMB": 100,
                                                            "uptimeS": 1, "host": "127.0.0.1", "port": 27017, "dataBytes": 0,
                                                            "storageBytes": 0, "collections": 0, "installed": True, "cacheGB": 0.5,
                                                            "binary": None})
    assert cli.cmd_mongo(SimpleNamespace(action="start"), settings) == 0
    assert keeper.made == [(mongo.KEEPER, tmp_path / "liminal.db")] and keeper.attachments[0].detached  # let go at the end
    assert "while this command runs" in capsys.readouterr().out
