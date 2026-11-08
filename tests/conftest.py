from __future__ import annotations

from datetime import date
from typing import Iterator

import pytest

from liminal_libs import live
from liminal_libs.errors import NotEntitled
from liminal_libs.models import Exchange, Instrument, country
from liminal_libs.sources.base import DataSource
from liminal_libs.storage import Store


@pytest.fixture(autouse=True, scope="session")
def plugins_as_installed_packages():
    """The suite sees the plugins as installed packages (their entry points), which a test's fakes
    stand in for (test_plugins.py: `installed`), not through the workspace's plugin directory beside
    this checkout (liminal_libs.layout.plugins_dir: the real plugins would load next to the fakes).
    tests/test_workspace.py covers that loading on its own."""
    import os
    import tempfile
    from pathlib import Path

    from liminal_libs import layout
    from liminal_libs.sources import registry

    before = os.environ.get(layout.PLUGINS_VAR)
    os.environ[layout.PLUGINS_VAR] = str(Path(tempfile.gettempdir()) / "liminal-tests-no-plugin-directory")
    registry._providers = None
    yield
    if before is None:
        os.environ.pop(layout.PLUGINS_VAR, None)
    else:
        os.environ[layout.PLUGINS_VAR] = before
    registry._providers = None


@pytest.fixture(autouse=True)
def no_saved_keys(monkeypatch):
    """Tests never read the user's real API keys: neither the saved user environment variables nor
    the process's own (a shell started after the keys were saved has them), for every provider
    plugin's keys and plans. A test that wants one sets it."""
    from liminal_libs import config
    from liminal_libs.sources.registry import variables

    monkeypatch.setattr(config, "saved_user_keys", lambda: {})
    for name in variables():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_found_limits(monkeypatch):
    """A feed's limiter keeps inside the limits its servers' headers gave on its account's last
    scans, read from the database the settings name (registry.found_limits): tests never read the
    real one's; test_plugins.py tests the reading on its own."""
    from liminal_libs.sources import registry

    monkeypatch.setattr(registry, "found_limits", lambda name, settings: [])


@pytest.fixture(autouse=True)
def installed_plugins_seen_fakes_forgotten():
    """The suite runs with the plugin packages released on their own installed beside the app
    (pyproject.toml's `plugins` extra; CI installs `.[dev,plugins]`), as the user's app does: with
    every plugin leaving sources/plugins/ (2026-11-01), their records are the fixtures where a test
    names a feed. A plugin's own tests are in its repository. A test's fake entry points
    (test_plugins.py: `installed`) are forgotten before the next test: the plugins are found again."""
    from liminal_libs.sources import registry

    if registry._providers is not None and any(
            not p.origin.startswith(("built in", "package: plugin-", "plugins/plugin-"))
            for p in (*registry._providers.values(), *registry._publishers.values())):
        registry._providers = None


@pytest.fixture(autouse=True)
def no_agency_requests(monkeypatch):
    """Tests never read the publishers' real feeds: the hourly sync without a recorder reads them
    (cli._fetch_gov, publications.run_due), and the Plugins view's Recheck (recheck_now);
    test_publications.py drives publications.Reader with fake answers instead."""
    from liminal_charts import cli
    from liminal_libs import publications

    monkeypatch.setattr(cli, "_fetch_gov", lambda settings, log, force=False: None)
    monkeypatch.setattr(publications, "run_due", lambda settings, log, force=False, now=None: None)
    monkeypatch.setattr(publications, "recheck_now", lambda settings, name, log, wait_s=30.0: None)


@pytest.fixture(autouse=True)
def ui_preferences_apart(monkeypatch, tmp_path):
    """Tests never read or write the machine's UI preferences (the data folder's ui.json, which every app's page
    follows: the user's theme): each test has its own file (LIMINAL_UI_FILE), none until it saves one."""
    from liminal_libs import ui

    monkeypatch.setenv(ui.UI_VAR, str(tmp_path / "ui.json"))


@pytest.fixture(autouse=True)
def no_cached_movers():
    """The big movers are cached per process for a few minutes (collector.big_movers): each test
    starts without another test's."""
    from liminal_libs import collector

    collector._movers_cache.clear()


@pytest.fixture(autouse=True)
def read_only_restored():
    """cli.main sets the process's read-only mode from LIMINAL_READONLY: never left on for the next test."""
    from liminal_libs.storage import read_only, set_read_only

    was = read_only()
    yield
    set_read_only(was)


@pytest.fixture(autouse=True)
def tick_access_restored():
    """cli.main sets the process's tick access from BARS_ONLY (off by default): a test that runs a
    command must not leave the stores of the tests after it unable to open tick files."""
    from liminal_libs.storage import set_tick_access, tick_access

    was = tick_access()
    yield
    set_tick_access(was)


@pytest.fixture(autouse=True)
def no_recorders(monkeypatch, tmp_path):
    """Tests never start the live recorder: `sync` would start a real detached process (with real
    exchange connections) pointed at the test's temporary folder."""
    monkeypatch.setenv("LIVE_CRYPTO", "off")
    monkeypatch.setenv("LIVE_STOCKS", "off")
    monkeypatch.setenv("LIMINAL_LIVE_LOCK", str(tmp_path / "machine-live.lock"))  # never the real machine's
    monkeypatch.setattr(live, "attach", lambda db_path, log=print: None)  # nor attach to (and so start) one
    # Nor see or end the machine's processes: a test recorder holding its locks reaps "other"
    # recorders (live.reap_strays), and once ended the user's real one.
    from liminal_libs import proc

    monkeypatch.setattr(proc, "processes", lambda: [])

    def no_terminate(pid):
        raise AssertionError(f"a test tried to end process {pid}")

    monkeypatch.setattr(proc, "terminate", no_terminate)
    # Nor start one from the chart app (resuming fetching starts a sync, a scan, a build): a test
    # that resumed once started a real sync on the user's database. A test that wants to see what
    # would start replaces Popen on this stand-in.
    import subprocess
    from types import SimpleNamespace

    from liminal_charts.gui import server

    def no_spawn(args, **kw):
        raise AssertionError(f"a test tried to start a process: {args}")

    monkeypatch.setattr(server, "subprocess", SimpleNamespace(
        Popen=no_spawn, DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE, CREATE_NO_WINDOW=0))
    # Nor from the command line: the hourly sync starts each feed's run in a process of its own
    # (cli.start_feed), and a test's `sync` once started five, which sent Alpaca two requests on the
    # user's keys. A test that wants to see which would start replaces start_feed.
    from liminal_charts import cli

    monkeypatch.setattr(cli, "start_feed", lambda name, db_path: no_spawn(["host", f"feed-{name}"]))
    monkeypatch.setattr(cli, "start_history", lambda db_path: no_spawn(["host", "history"]))
    # Nor start, see or write to a MongoDB server: one may run on this machine with the user's
    # documents. Mongo is "not installed" unless a test installs its stand-in (tests/test_stores.py).
    from liminal_libs import mongo

    monkeypatch.setattr(mongo, "_spawn", no_spawn)
    monkeypatch.setattr(mongo, "binary", lambda: None)
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: False)
    monkeypatch.setattr(mongo, "_alive", [0.0, False])  # a ping's answer kept a few seconds: never a test's before

    def no_server(*a, **k):
        raise AssertionError("a test tried to reach the MongoDB server")

    monkeypatch.setattr(mongo, "client", no_server)
    monkeypatch.setattr(mongo, "database", no_server)
    # Nor start a resident host (host.py: the recorder, Mongo's keeper, a feed's run as its own
    # process), nor attach to the user's: the pipes' key and sockets live in the test's own folder.
    from liminal_libs import host

    monkeypatch.setenv("LIMINAL_IPC_DIR", str(tmp_path / "ipc"))
    monkeypatch.setattr(host, "start_host", lambda name, db_path: no_spawn(["host", name]))
    monkeypatch.setattr(host, "attach_or_start", lambda name, db_path, log=print: None)  # no host answers a test


class FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = str(body)

    def json(self) -> dict:
        return self._body


class FakeSession:
    """Returns queued responses and records every request."""

    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None, dict]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


class FakeSource(DataSource):
    """In-memory source: entitlements and data are configured per test."""

    name = "fake"

    def __init__(self, entitled=("trades", "second", "minute"), trades=None, bars=None, budget=None,
                 listing: Instrument | None = None):
        self.entitled = set(entitled)
        self.listing = listing
        self.instrument_calls = 0
        self.trades = sorted(trades or [], key=lambda t: t["sip_timestamp"])
        self.bars = bars or {}
        self.budget = budget
        self._requests = 0
        self.trade_calls: list[dict] = []

    def _spend(self):
        from liminal_libs.errors import BudgetExhausted

        if self.budget is not None and self._requests >= self.budget:
            raise BudgetExhausted("fake cap")
        self._requests += 1

    @property
    def requests_made(self) -> int:
        return self._requests

    def is_entitled(self, resolution, ticker, asset="stock"):
        self._spend()
        self.probed = getattr(self, "probed", []) + [(resolution, ticker, asset)]
        return resolution in self.entitled

    def trade_pages(self, ticker, gte_ns=None, lt_ns=None, descending=False, page_size=2) -> Iterator[list[dict]]:
        if "trades" not in self.entitled:
            self._spend()
            raise NotEntitled(403, "no trades")
        self.trade_calls.append({"gte": gte_ns, "lt": lt_ns, "desc": descending})
        rows = [
            t for t in self.trades
            if (gte_ns is None or t["sip_timestamp"] >= gte_ns) and (lt_ns is None or t["sip_timestamp"] < lt_ns)
        ]
        if descending:
            rows = rows[::-1]
        for i in range(0, max(len(rows), 1), page_size):
            self._spend()
            yield rows[i:i + page_size]

    def bar_pages(self, ticker, start, end, timespan, multiplier=1, descending=False, asset="stock"):
        self._spend()
        self.bar_calls = getattr(self, "bar_calls", []) + [(ticker, start, end, timespan, asset)]
        if timespan not in self.entitled and timespan not in ("day", "hour"):
            raise NotEntitled(403, "no " + timespan)
        key = timespan if multiplier == 1 else f"{timespan}:{multiplier}"
        src = self.bars.get((ticker, key), self.bars.get(key, []))
        yield list(src)

    frames: tuple = ()  # the time frames above a minute it sends (native_frames)

    def native_frames(self, asset):
        return self.frames

    def exchanges(self):
        self._spend()
        return [Exchange("fake", 4, "Nasdaq", country("US"), mic="XNAS"), Exchange("fake", 12, "NYSE Arca", country("US"))]

    def instrument(self, ticker, resolve_exchange=True):
        self._spend()
        self.instrument_calls += 1
        return self.listing or Instrument(ticker)

    def market_daily(self, day: date):
        self._spend()
        return []


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()
