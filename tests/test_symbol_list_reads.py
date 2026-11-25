"""Monday list item 16 (0.2.3, on liminal-libs 1.84.0): the symbol list's rows (activity.symbol_rows, which
ChartApp.symbols and the Fetcher view build on) read each kind of fact once for all of a feed's symbols. They were
about seven statements a symbol, some 230,000 for 33,145 symbols: 11 s on Windows, two minutes from Linux, where
each statement crosses the link. The rows stay exactly what the one-at-a-time reads gave, on a stand-in store of
varied symbols (one feed or two, the feed's own bars and bars built here, the other feed newer, nothing stored yet,
coverage, probes, synced, retired, closed, abroad, a crypto pair, a continuous future, series nobody watches)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from liminal_libs import collector
from liminal_libs.collector import ET
from liminal_libs.config import load_settings
from liminal_libs.live import WATCHLISTS_KEY
from liminal_libs.storage import Store
from liminal_libs.symbols import resolve

from liminal_charts.gui import activity

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=ET)
FEEDS = ("alpaca", "massive")


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _standin(path, n: int) -> Store:
    """A store of `n` varied stand-in symbols (no real data)."""
    s = Store(path, check_same_thread=False)
    day0 = NOW - timedelta(days=400)
    own: dict[str, list] = {f: [] for f in FEEDS}
    built: dict[str, list] = {f: [] for f in FEEDS}
    for i in range(n):
        t = f"S{i:05d}"
        kind = i % 11
        feeds = ("massive",) if kind < 5 else ("alpaca",) if kind == 5 else ("massive", "alpaca")
        for f in feeds:
            s.watch(f, [t], "minute", exchange="XLON" if i % 13 == 0 else "XNAS" if i % 3 else None)
        first = day0 + timedelta(days=i % 300, minutes=i % 97)
        last = NOW - timedelta(days=i % 9, hours=i % 5)
        if kind not in (3, 9):  # the others have nothing stored yet
            for f in feeds:
                rows = built if (i + len(f)) % 7 == 0 else own
                rows[f] += [(t, "minute", 1, _ms(first)), (t, "minute", 1, _ms(last))]
                if i % 4 == 0:  # bars built here beside the feed's own, reaching further back
                    built[f].append((t, "minute", 1, _ms(first - timedelta(days=3))))
                own[f] += [(t, "day", 1, _ms(last - timedelta(days=1))), (t, "day", 1, _ms(last))]
        if kind == 7:  # the other feed holds newer bars (newest_any)
            own["alpaca"].append((t, "minute", 1, _ms(NOW - timedelta(hours=1))))
        for f in feeds:
            if i % 5 == 0:
                s.set_backfilled(f, t, date(2020, 1, 1))
            elif i % 5 == 1:
                s.set_backfilled(f, t, (day0 + timedelta(days=i % 200)).date())
            if i % 3 == 0:
                s.set_synced(f, t, NOW - timedelta(days=i % 4, hours=i % 7))
            if i % 6 == 0:
                s.set_probe(f, t, NOW - timedelta(hours=i % 30), _ms(last + timedelta(minutes=i % 3)) if i % 12 else None)
            if i % 8 == 0:
                s.add_coverage(f, t, "minute", _ms(first), _ms(last))
        if i % 17 == 0:
            s.set_listing(feeds[0], t, (day0 + timedelta(days=50)).date(), None, NOW.date(), name=f"Company {i}")
        if i % 29 == 0:  # history in, no longer listed
            for f in feeds:
                s.set_backfilled(f, t, date(2020, 1, 1))
            s.set_retired(t, (NOW - timedelta(days=20)).date())
        if i % 31 == 0:
            s.set_closed(feeds[-1], t, NOW - timedelta(days=2))
    for i in range(max(1, n // 10)):  # stored, not watched
        own["alpaca"] += [(f"U{i:04d}", "minute", 1, _ms(day0)), (f"U{i:04d}", "minute", 1, _ms(NOW))]
    for f in FEEDS:
        s.watch_spec(f, resolve("BTCUSD"), "minute")
        own[f] += [("BTCUSD", "minute", 1, _ms(day0)), ("BTCUSD", "minute", 1, _ms(NOW - timedelta(hours=2)))]
    s.watch_spec("massive", resolve("NQ1!"), "minute")
    s.set_rolls("massive", "NQ", 1, [((NOW - timedelta(days=60)).date(), "NQU6"), ((NOW - timedelta(days=5)).date(), "NQZ6")])
    own["massive"] += [("NQU6", "minute", 1, _ms(NOW - timedelta(days=70))), ("NQU6", "minute", 1, _ms(NOW - timedelta(days=5))),
                       ("NQZ6", "minute", 1, _ms(NOW - timedelta(days=20))), ("NQZ6", "minute", 1, _ms(NOW - timedelta(hours=3)))]
    for f in FEEDS:
        conn = s._feed(f, create=True).conn
        with conn:
            conn.executemany("INSERT OR REPLACE INTO bars (ticker, timespan, multiplier, ts, open, high, low, close, volume) "
                             "VALUES (?, ?, ?, ?, 10, 11, 9, 10.5, 100)", own[f])
            conn.executemany("INSERT OR REPLACE INTO built_bars (ticker, timespan, multiplier, ts, open, high, low, close, "
                             "volume, derivation) VALUES (?, ?, ?, ?, 10, 11, 9, 10.5, 100, 1)", built[f])
        s._wrote()
    collector.request_priority(s, "S00002", NOW - timedelta(minutes=5))  # opened in the chart: next
    s.kv_set(WATCHLISTS_KEY, json.dumps([{"name": "Mine", "tickers": ["S00004", "S00010"]}]))
    return s


@pytest.fixture
def settings():
    return load_settings(env={"MASSIVE_API_KEY": "x"}, env_file=None)


def _one_at_a_time(monkeypatch, store: Store) -> None:
    """The store's set-based reads answered by the one-at-a-time reads they stand for (bar_edges, newest_any,
    probe, rolls): what the rows were built from before."""
    monkeypatch.setattr(store, "bar_edges_many", lambda source, tickers, timespan="minute", multiplier=1: {
        t: e for t in tickers if (e := store.bar_edges(source, t, timespan, multiplier)) is not None})
    monkeypatch.setattr(store, "newest_many", lambda tickers, timespan, holders=None: {
        t: n for t in tickers if (n := store.newest_any(t, timespan, holders)) is not None})
    monkeypatch.setattr(store, "probes", lambda source: {
        w["ticker"]: p for w in store.watchlist(source) if (p := store.probe(source, w["ticker"])) is not None})
    monkeypatch.setattr(store, "rolls_of", lambda source, rank=1: {
        w["source_ticker"]: store.rolls(source, w["source_ticker"], rank) for w in store.watchlist(source)
        if w["asset"] == "future"})


def test_the_rows_are_the_ones_the_one_at_a_time_reads_gave(tmp_path, settings, monkeypatch):
    store = _standin(tmp_path / "s.db", 120)
    rows = {(f, d): activity.symbol_rows(store, settings, f, NOW, detail=d) for f in FEEDS for d in (0, None)}
    states = {r["state"] for got in rows.values() for r in got}
    assert {"backfilling", "queued", "current", "new", "closed", "retired", "next", "blocked"} <= states
    assert any(r["sourceThrough"] for r in rows["massive", 0]) and any(r["gapDays"] for r in rows["massive", None])
    with monkeypatch.context() as m:
        _one_at_a_time(m, store)
        before = {(f, d): activity.symbol_rows(store, settings, f, NOW, detail=d) for f in FEEDS for d in (0, None)}
    assert rows == before
    nq = next(r for r in rows["massive", 0] if r["ticker"] == "NQ1!")
    assert nq["first"] == (NOW - timedelta(days=70)).date().isoformat()  # the contracts' edges, the oldest's first


def _statements(store: Store, settings) -> int:
    # The stored series listed first, as the app's are (once a STORED_SERIES_S; that listing's own statements
    # are liminal-libs' test_symbol_list_reads).
    store.stored_series()
    seen: list[str] = []
    for conn in [store.conn] + [f.conn for f in store._open_feeds()]:
        conn.set_trace_callback(seen.append)
    try:
        for f in FEEDS:
            activity.symbol_rows(store, settings, f, NOW, detail=0)
    finally:
        for conn in [store.conn] + [f.conn for f in store._open_feeds()]:
            conn.set_trace_callback(None)
    return len(seen)


def test_the_statements_do_not_grow_with_the_symbols(tmp_path, settings):
    few, many = _standin(tmp_path / "few.db", 30), _standin(tmp_path / "many.db", 300)
    assert len(many.watchlist()) > 9 * len(few.watchlist())
    assert _statements(many, settings) == _statements(few, settings)
