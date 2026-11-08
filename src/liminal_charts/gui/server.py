"""Local HTTP server for the chart app. Binds to 127.0.0.1 only and serves data from the local store."""
from __future__ import annotations

import functools
import json
import math
import sqlite3
import traceback
from collections import Counter, OrderedDict
from contextlib import contextmanager
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import pandas as pd

from liminal_libs import config, freshness, live, mongo, sourcing
from liminal_libs.config import Settings
from liminal_libs.storage import LayoutLimit, Store
from liminal_libs.storage.markets import mic_names
from liminal_libs.storage.requests import KINDS as REQUEST_KINDS, OPEN as OPEN_REQUEST, read_status
from . import activity, astro, web
from ..frame import ChartsApp
from .bars import CALENDAR, PRICE_BASED, TIME_TYPES, BarEngine, Style, _BoundedCache, resample
from liminal_libs.calc.derivatives import Model
from .stats import details_subset, key_stats
from .web import MAX_BODY, PROFILE_COOKIE, PROFILE_HEADER, STATIC, WRITE_HEADER, fetcher_page  # noqa: F401 (shared with the front, web.py)


def finite(data):
    """`data` with every NaN or infinity (a missing value from pandas) as None, for JSON."""
    if isinstance(data, float):
        return data if math.isfinite(data) else None
    if isinstance(data, dict):
        return {k: finite(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [finite(v) for v in data]
    return data


MAX_LIMIT = 20000
CALENDAR_MAX_DAYS = 3700  # the widest /api/calendar range (timeaxis.js asks for at most CALENDAR_DAYS)
ROW_COUNTS_TTL_S = 600  # a symbol's bar counts (the Data panel) are counted again after this
ROW_COUNTS_KEPT = 64  # the symbols whose counts are kept (the ones counted last)
COUNTING_AT_ONCE = 2  # counts made at once, each on a thread and connections of its own
PRUNED_COUNTS_TTL_S = 300  # a symbol's pruned-list counts by rule (an index scan of up to millions)
ACTIVITY_TTL_S = 2.0  # the status answer shared by polls this close together
SYMBOL_ROWS_TTL_S = 10.0  # its symbols' rows (index seeks in every feed file) kept this long
OFFICIAL_TTL_S = 600.0  # the official holidays the sync fetched, read again this often (freshness.use_official)
FEEDS_TTL_S = 60.0  # the feeds' summary (configuration and the watchlist) kept this long
DEPS_TTL_S = 60.0  # the dependency checks' answer is kept this long
FIRST_PROFILE = 1  # the profile holding what came before profiles; the default without a cookie
# Chart data built at once (bars, level 2 profiles, projections, range statistics): each holds a
# window's frames, up to MAX_LIMIT bars; the rest wait their turn, then are answered busy.
HEAVY_REQUESTS = 2
HEAVY_WAIT_S = 30.0
# A paper order's bars held only by a feed of one exchange's share (IEX) wait this long for the
# whole market's to replace them (a sync stores a session within hours), then are taken as they are.
PARTIAL_SETTLE_MS = 2 * 86_400_000


class Busy(RuntimeError):
    """More chart requests than HEAVY_REQUESTS waited past HEAVY_WAIT_S (answered 503)."""


def _heavy(method):
    """A ChartApp method that builds chart data: run under its in-flight cap (ChartApp.heavy_work)."""
    @functools.wraps(method)
    def run(self, *a, **k):
        with self.heavy_work():
            return method(self, *a, **k)
    return run


_AI_IN_FLIGHT = threading.BoundedSemaphore(2)  # answers in flight at once across every page


class ChartApp:
    """The API, independent of HTTP so it can be tested directly."""

    SETTINGS_TTL_S = 5.0  # the user's saved keys and plans are looked at this often

    def __init__(self, store: Store, priority: list[str] | None = None, settings: Settings | None = None,
                 reload_settings: Callable[[], Settings] | None = None, mode: str = "chart", fetcher=None,
                 touch: Callable[[], None] | None = None, frame=None):
        self.store = store
        # The page served at /: the chart app's ("chart") or the fetcher's own ("fetcher": the data
        # and monitoring views, without the chart and what is the user's); the API is the same.
        self.mode = mode
        # The fetcher this API speaks for (serve_integrator: the integrator's link to the fetcher host
        # over the pipe), asked for the symbols the pages open and for a pass; None alone (serve):
        # the requests wait in the file.
        self._fetcher = fetcher
        self._touch = touch  # tells the integrator a page is in touch (its link attaches): the fetcher stays up
        # The app of the frame this API speaks for (liminal_libs.apps.App: the plugins attached, with the
        # app's login as the one way to their keys; the user, 2026-11-06): the charts app's own (frame.py),
        # or the integrator's when its page is served here (serve_integrator). Its plugins are attached by
        # the server at start (frame.attach_plugins()), not here: a process holds its plugins once.
        self.frame = frame if frame is not None else ChartsApp()
        self.login = self.frame.login
        self._symbol_request: tuple[str, int] | None = None  # the last symbol a page opened, and its request
        self._settings = settings or Settings.load(env={}, env_file=None)
        # With `reload_settings` (the app started from the command line), a key or plan saved after
        # it started (tools/set-key.ps1) counts within SETTINGS_TTL_S: the Fetcher shows each feed
        # as it is now, not as it was when the app started.
        self._reload_settings = reload_settings
        self._saved_keys = config.saved_user_keys() if reload_settings else None
        self._settings_at = time.monotonic()
        self._settings_lock = threading.Lock()
        self._priority = priority
        self._heavy = threading.BoundedSemaphore(HEAVY_REQUESTS)
        self._lock = threading.RLock()  # the app's own reads and writes through `store`
        self._panel_lock = threading.Lock()
        # ticker -> (counted at, row_counts) for the ROW_COUNTS_KEPT symbols counted last, and the
        # tickers being counted (at most COUNTING_AT_ONCE): under _counts_lock (_counts)
        self._row_counts: OrderedDict[str, tuple[float, dict]] = _BoundedCache(ROW_COUNTS_KEPT)
        self._counting: set[str] = set()
        self._counts_lock = threading.Lock()
        # ticker -> (counted at, by feed and rule) for the ROW_COUNTS_KEPT symbols counted last: under _panel_lock
        self._pruned_counts: OrderedDict[str, tuple[float, dict]] = _BoundedCache(ROW_COUNTS_KEPT)
        self._status_lock = threading.Lock()  # the Fetcher view and status bar
        self._queue_lock = threading.Lock()  # the fetcher's requests and the plugins' queue
        self._activity: dict[str, tuple[float, tuple, dict]] = {}  # feed -> (made at, (lines, open symbol), answer)
        self._official_at: float | None = None  # when the official holidays were last applied (OFFICIAL_TTL_S)
        self._activity_rows: dict = {}  # activity.activity's symbol rows (SYMBOL_ROWS_TTL_S)
        self._feeds_summary: tuple[float, list] | None = None
        self._plugins_cache: tuple[float, dict, bool] | None = None  # the Plugins view (PLUGINS_TTL_S)
        self._rescan: tuple = (None, None)  # the capability rescan's process and {name, since, pid}
        self._deps_lock = threading.Lock()  # dependency checks
        self._deps: tuple[float, dict] | None = None
        self._datasets_lock = threading.Lock()  # the datasets list
        self._symbols_lock = threading.RLock()  # the symbol list
        # The profiles there are, for the cookie every request carries: checked under the shared
        # lock, every chart and status request waited behind whatever held it (profile_exists)
        self._profiles_lock = threading.Lock()
        self._profile_ids: set[int] = set()
        self._profile_ids_at = float("-inf")
        self._open_noted: dict[str, int] = {}  # ticker -> ms it was last noted open (note_open)
        # The live recorder's host (host.py), attached while a chart has a symbol open: it records
        # the open symbols' trades and ends once nothing has been open for live.OPEN_FRESH_MS
        # (attach_live, settle_live; the user, 2026-11-01: no processes when the app is not in use).
        self._live, self._live_lock, self._live_watch = None, threading.Lock(), None
        self._derive_proc: subprocess.Popen | None = None  # a build started by start_derive
        self._gov_lock = threading.Lock()  # gov/gov.db, opened on first use
        self._recheck: tuple | None = None  # a publisher's limits recheck: (thread, {name, since})
        self._recheck_lock = threading.Lock()
        self._paper_lock = threading.RLock()  # the paper account (paper), opened on first use
        self._monitor_lock = threading.Lock()  # the Resources view's sampler, made on first use
        # Quiet: with no page in touch for QUIET_SECONDS every store is closed and every host let
        # go (release); the next request opens them again (using). The user, 2026-11-01: no open
        # databases or processes when the app is not in use.
        self._use = threading.Condition()
        self._in_use = 0  # requests under way (using)
        self._quiet = False  # the stores closed, the hosts let go
        self._releasing = False
        self._last_contact = time.monotonic()  # when a page last asked for anything
        self._woken = False  # whether a page ever came (the on_wake work runs at the first)
        self._on_wake: list[Callable[[], None]] = []
        self._open_stores(store)

    def _open_stores(self, store: Store) -> None:
        """The app's stores: the one given and, on connections of their own, each a lock of its
        own, the readers of the parts that waited behind each other under one shared lock."""
        self.store = store
        # The engine reads bars through connections of its own, under its own lock: loading a
        # long history (twenty years of minutes) held the one shared lock for over a minute, and
        # the symbol list, the Fetcher's poll and the search dialog all waited behind it.
        reader = store.reader()
        self.engine = BarEngine(reader, priority=self._priority, quality=lambda feed: self._feed_quality(feed, reader),
                                cache_bytes=self.settings.chart_cache_mb * 2**20)
        from .window import WindowReader

        self.window = WindowReader(self.engine)  # the auto-ranging chart: any level, by window
        # The Data panel's inventory reads through connections of its own, under its own lock, and
        # its row counts are made in the background (counting AAPL's frames took 20 s under the
        # shared lock, and the symbol list for the picker's map, the calendar and the side panel
        # all waited behind it).
        self._panel_store = store.reader()
        self._status_store = store.reader()  # the Fetcher view and status bar
        # the fetcher's requests and the plugins' queue, never behind the status poll (seconds on
        # thousands of symbols: the fetcher's own page showed an empty stack and queue meanwhile)
        self._queue_store = store.reader()
        self._deps_store = store.reader()  # dependency checks
        self._datasets_store = store.reader()  # the datasets list
        self._symbols_store = store.reader()  # the symbol list
        self._profiles_store = store.reader()  # the profiles there are (profile_exists)
        self._gov_db = None  # gov/gov.db, opened on first use
        self._paper, self._paper_store = None, None  # the paper account (paper), opened on first use
        self._monitor = None  # the Resources view's sampler, made on first use

    def _close_stores(self) -> None:
        """Every store closed (release): the readers', the engine's, the main one's connections,
        gov.db, the paper account's, and the sampler ended."""
        for lock, name in ((self._panel_lock, "_panel_store"), (self._status_lock, "_status_store"),
                           (self._queue_lock, "_queue_store"),
                           (self._deps_lock, "_deps_store"), (self._datasets_lock, "_datasets_store"),
                           (self._symbols_lock, "_symbols_store"), (self._profiles_lock, "_profiles_store")):
            with lock:
                getattr(self, name).close()
        with self._gov_lock:
            if self._gov_db is not None:
                self._gov_db.close()
                self._gov_db = None
        with self._paper_lock:
            if self._paper_store is not None:
                self._paper_store.close()
            self._paper, self._paper_store = None, None
        with self._monitor_lock:
            if self._monitor is not None:
                self._monitor.stop()
                self._monitor = None
        with self.engine.lock:
            self.engine.store.close()
        with self._lock:
            self.store.close()

    QUIET_SECONDS = 120.0  # no page in touch for this long: released (LIMINAL_QUIET_SECONDS)
    QUIET_CHECK_SECONDS = 15.0

    @property
    def quiet(self) -> bool:
        return self._quiet

    def on_wake(self, work: Callable[[], None]) -> None:
        """Work for the first page's coming (serve: the start's rescan, the world's calendars),
        not the app's start: nothing runs for nobody."""
        self._on_wake.append(work)

    @contextmanager
    def using(self):
        """A request under way: the stores open (opened again if the app was quiet), and the app
        not released meanwhile."""
        with self._use:
            while self._releasing:
                self._use.wait()
            if self._quiet:
                self._wake_locked()
            self._in_use += 1
            self._last_contact = time.monotonic()
            first = not self._woken
            self._woken = True
        if self._touch is not None:
            self._touch()
        try:
            if first:
                self._first_wake()
            yield
        finally:
            with self._use:
                self._in_use -= 1
                self._use.notify_all()

    def _wake_locked(self) -> None:
        self._open_stores(Store(self.store.path, check_same_thread=False))
        self._quiet = False
        _log_to(self.store.path)("quiet: a page is back; the stores are open again")

    def _first_wake(self) -> None:
        self.monitor.start()  # Resources: sampled in the background from now on
        for work in self._on_wake:
            try:
                work()
            except Exception as e:  # noqa: BLE001 - the start's extras never fail a request
                _log_to(self.store.path)(f"on the first page: {type(e).__name__}: {e}")
        self._on_wake = []

    def release(self) -> bool:
        """Quiet: every store closed, the recorder and MongoDB's keeper let go, the sampler ended.
        Not while a request is under way (False), nor twice."""
        with self._use:
            if self._quiet or self._releasing or self._in_use:
                return False
            self._releasing = True
        try:
            with self._live_lock:
                if self._live is not None:
                    self._live.detach()
                    self._live = None
            mongo.detach()
            self._close_stores()
        finally:
            with self._use:
                self._quiet, self._releasing = True, False
                self._use.notify_all()
        _log_to(self.store.path)(f"quiet: no page in touch for {self.quiet_seconds():g} s; the stores are closed, "
                                 "the recorder and MongoDB's keeper let go")
        return True

    def quiet_seconds(self) -> float:
        try:
            return float(config.setting("LIMINAL_QUIET_SECONDS") or self.QUIET_SECONDS)
        except ValueError:
            return self.QUIET_SECONDS

    def watch_quiet(self, stop: threading.Event | None = None) -> None:
        """Releases the app once no page has been in touch for quiet_seconds (serve: a thread)."""
        stop = stop or threading.Event()
        while not stop.wait(self.QUIET_CHECK_SECONDS):
            with self._use:
                due = not self._quiet and self._woken and time.monotonic() - self._last_contact >= self.quiet_seconds()
            if due:
                self.release()

    def close(self) -> None:
        """The app ending (serve): the stores closed, the hosts let go."""
        if not self._quiet:
            self.release()

    # -- the fetcher ---------------------------------------------------------------------------
    # This API speaks for the fetcher (fetcher.py: the host `fetcher`, over serve_integrator's link), which takes
    # the requests (a symbol a page opened, the request box) and holds the feeds' runs; the chart
    # app's front funnels its pages' requests here (gui/front.py). Alone (serve: a copy with no
    # fetcher in it, nothing collected) the requests wait in the file for a fetcher.
    def _wake_fetcher(self) -> None:
        if self._fetcher is not None:
            self._fetcher.wake_up()

    def ask_pass(self) -> bool | None:
        """The fetcher's standing pass now (resuming fetching). None: no fetcher here to ask."""
        if self._fetcher is None:
            return None
        self._fetcher.ask_pass()
        return True

    def fetcher_status(self) -> dict | None:
        """The fetcher's status: over the link when it answers, else as it last wrote it in the file."""
        if self._fetcher is not None:
            status = self._fetcher.status()
            if status is not None:
                return status
        with self._queue_lock:
            return read_status(self._queue_store)

    def requests(self, q: dict[str, str]) -> dict:
        """The fetcher's requests (storage/requests.py) as the Fetcher view shows them, with its status."""
        limit = max(1, min(int(q.get("limit", 200)), 2000))
        with self._queue_lock:
            rows = self._queue_store.request_rows(q.get("state") or None, limit)
            counts = self._queue_store.request_counts()
        return {"rows": rows, "counts": counts, "kinds": list(REQUEST_KINDS), "status": self.fetcher_status(),
                "limit": limit}

    def request_symbol(self, body: dict) -> dict:
        """Ask the fetcher for a symbol ({"symbol": .., "kinds": [..]}): its request, taken next
        (the newest ask first)."""
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        symbol = stored_ticker(symbol)  # TWSE:2330 and 2330::XTAI are the request for 2330.TW (one request, asked anew)
        kinds = [k for k in (body.get("kinds") or ["bars"]) if k in REQUEST_KINDS] or ["bars"]
        by = body.get("by") if body.get("by") in ("chart", "page", "cli") else "page"
        if self.settings.read_only:
            raise RuntimeError("this copy of the app reads only (LIMINAL_READONLY): nothing is asked for")
        with self._lock:
            row = self.store.request_put(symbol, kinds, by=by)
        self._wake_fetcher()
        return row

    def cancel_request(self, request_id: int) -> dict:
        with self._lock:
            ok = self.store.request_cancel(request_id)
        if ok:
            self._wake_fetcher()
        return {"cancelled": ok, "id": request_id}

    @property
    def settings(self) -> Settings:
        """The app's settings, with the user's saved keys and plans as they are now (see __init__)."""
        if self._reload_settings is None or time.monotonic() - self._settings_at < self.SETTINGS_TTL_S:
            return self._settings
        with self._settings_lock:
            if time.monotonic() - self._settings_at >= self.SETTINGS_TTL_S:
                self._settings_at = time.monotonic()
                saved = config.saved_user_keys()
                settings = self._settings
                if saved != self._saved_keys:
                    self._saved_keys = saved
                    settings = self._reload_settings()
                # each plugin's plan as its last scan found it, wherever the user set none
                settings = settings.with_detected(sourcing.Scans.plans(sourcing.Scans.at(self.store.path)))
                if settings is not self._settings:
                    self._settings = settings
                    # what was worked out from the old keys and plans: the feeds, their states, the checks
                    self._activity, self._activity_rows = {}, {}
                    self._feeds_summary = self._deps = self._plugins_cache = None
        return self._settings

    @contextmanager
    def heavy_work(self):
        """One of at most HEAVY_REQUESTS requests building chart data at once; Busy after waiting
        HEAVY_WAIT_S (a page asks again), rather than a queue of requests each holding frames."""
        if not self._heavy.acquire(timeout=HEAVY_WAIT_S):
            raise Busy("the chart app is building other charts' data: ask again shortly")
        try:
            yield
        finally:
            self._heavy.release()

    def _feed_quality(self, feed: str, store: Store | None = None) -> float:
        """A feed's best quality over asset classes (display priority between feeds). Read through
        the connection of whoever asks, under its own lock: the engine its reader, the app `store`."""
        from liminal_libs.composite import quality_of

        store = store or self.store
        return max((quality_of(store, feed, a) for a in ("stock", "crypto", "index", "fx", "future")), default=1.0)

    def models(self, ticker: str, asset: str, sources: list[str], feeds: dict | None = None,
               store: Store | None = None, lock=None) -> dict | None:
        """The models of an asset traded on several exchanges: the gold-standard reference (one
        exchange), the composite across exchanges, the consolidated tape, and each exchange's quality.
        Read through `store` under `lock` (the app's own by default)."""
        from liminal_libs.composite import exchange_feeds, quality_of, reference_feed
        from liminal_libs.sources import feed_profiles

        feeds = feeds if feeds is not None else feed_profiles(self.settings)
        exch = {f: v for f, v in exchange_feeds(feeds, asset).items() if f in sources}
        if len(exch) < 2:
            return None
        consolidated = next((f for f in sources if f in feeds and feeds[f].venues.get(asset) in ("consolidated", "sip")), None)
        store, lock = store or self.store, lock or self._lock
        with lock:
            has_composite = store.bar_edges("composite", ticker, "minute") is not None
            exchanges = sorted(({"feed": f, "exchange": v, "quality": round(quality_of(store, f, asset), 3)}
                                for f, v in exch.items()), key=lambda x: -x["quality"])
        return {"reference": reference_feed(feeds, asset, set(sources)), "composite": has_composite,
                "consolidated": consolidated, "exchanges": exchanges}

    def symbols(self) -> list[dict]:
        """One entry per ticker, with every feed that has data for it. One pass over the watchlist
        and the stored series (indexed by ticker): thousands of symbols, no scan of them per symbol;
        each symbol's state without the per-symbol download plan (the Fetcher shows that)."""
        from collections import defaultdict

        from liminal_libs.composite import reference
        from liminal_libs.sources import feed_profiles

        # Its own connections and lock (a second or more for thousands of symbols): under the shared
        # lock the layouts, watchlists and the first chart waited behind it on every page load.
        store = self._symbols_store
        with self._symbols_lock:
            watch = store.watchlist()
            stored = store.stored_series()
            exchanges = store.exchanges()
            snapshot = store.daily_snapshot()
            icons = store.icon_tickers()
            profiles = store.company_profiles()
            members = store.constituents("sp500", "S&P 500")
            futures = _futures_moves(store)  # each continuous future's exchange, type and front contract's move
            now = datetime.now(timezone.utc).astimezone(activity.ET)
            by_feed: dict[str, dict[str, dict]] = {}  # ticker -> feed -> its Fetcher row
            for src in sorted({w["source"] for w in watch}):
                for r in activity.symbol_rows(store, self.settings, src, now, detail=0):
                    by_feed.setdefault(r["ticker"], {})[src] = r
        names = mic_names(exchanges)
        # Futures contracts are stored only as parts of a continuous series (NQ1!): not symbols of their own.
        with self._symbols_lock:
            parts = {c["ticker"] for w in watch if w["asset"] == "future"
                     for c in store.contracts(w["source"], w["source_ticker"])}
        watched = {w["ticker"] for w in watch}
        tickers = sorted(({t for _, t, _ in stored} - (parts - watched)) | watched)
        by_ticker: dict[str, list] = defaultdict(list)
        for w in watch:
            by_ticker[w["ticker"]].append(w)
        kinds: dict[str, dict[str, list]] = defaultdict(dict)  # ticker -> {feed: [kinds]}
        for s, t, k in stored:
            kinds[t].setdefault(s, []).append(k)
        qualities: dict[str, float] = {}

        def quality(feed: str) -> float:  # once per feed, not per symbol
            if feed not in qualities:
                with self._symbols_lock:
                    qualities[feed] = self._feed_quality(feed, store)
            return qualities[feed]
        feed_set = feed_profiles(self.settings)
        out = []
        for ticker in tickers:
            ws = by_ticker.get(ticker, [])
            w = next((x for x in ws if x.get("name")), ws[0] if ws else {})
            mic = next((x["exchange"] for x in ws if x.get("exchange")), None)
            fut = next((futures(x) for x in ws if x["asset"] == "future"), None)
            mic = mic or (fut or {}).get("exchange")
            held = kinds.get(ticker, {})
            have = set(held) | {x["source"] for x in ws if x["asset"] == "future"}
            fine = {s for s, ks in held.items() if any(k in ("trades", "second") for k in ks)}
            order = self.engine.rank_sources(have, fine, reference(w.get("asset") or "stock"), quality)
            feeds = [{"source": s, "data": sorted(held.get(s, []))} for s in order]
            state = _symbol_state(by_feed.get(ticker, {}), order)
            out.append({
                "ticker": ticker, "name": w.get("name"), "kind": w.get("kind") or w.get("asset") or "stock",
                "asset": w.get("asset") or "stock",
                "exchange": mic, "exchangeName": names.get(mic, mic) if mic else None,
                "listed": str(w["listed_from"]) if w.get("listed_from") else None,
                "feeds": feeds,
                "models": self.models(ticker, w.get("asset") or "stock", [f["source"] for f in feeds], feed_set,
                                      store, self._symbols_lock),
                "icon": ticker in icons,
                "market": _market(snapshot, ticker, w),
                **_sector(ticker, profiles.get(ticker), w.get("asset") or "stock", (snapshot.get(ticker) or {}).get("close"),
                          members.get(ticker)),
                "changePct": fut["changePct"] if fut else _change(snapshot, ticker),
                **({"futuresType": fut["type"], "front": fut["front"], "close": fut["close"],
                    "session": fut["session"], "day": fut["day"]} if fut else {}),
                "data": {k: state[k] for k in ("state", "progress", "last", "expectedThrough", "nextDue")}
                if state else None,
            })
        return out

    def _auto(self, q: dict[str, str], style_type: str = "candles") -> bool:
        """Whether a request takes the auto-ranging path (window.py): asked for, a series it serves
        (not continuous futures or the composite model) and a chart it draws: time-based types in
        price or a model (the integral at days and longer: each of its values sums the history
        before it), Heikin Ashi, and the price-based types (built whole, at a level of their own).
        Box plots keep the series path."""
        if q.get("auto") != "1" or not self.window.handles(q["source"], q["ticker"]):
            return False
        kind = q.get("model", "price")
        if style_type in PRICE_BASED or style_type == "heikin_ashi":
            return kind == "price"
        return style_type in TIME_TYPES and (kind != "integral" or q.get("period", "minute") in CALENDAR)

    def meta(self, source: str, ticker: str, adjust: str = "none", auto: bool = False) -> dict:
        if auto and self.window.handles(source, ticker):  # its levels, without loading its history
            out = self.window.meta(source, ticker)
            out["attribution"] = self.attribution(out["ticker"], "minute" if out["base"] != "day" else "day", out["asset"])
            return out
        s = self.engine.series(source, ticker, _adjust(adjust))
        if s is None or s.frame.empty:
            raise LookupError(f"no data stored for {source}:{ticker}")
        first, last = s.frame.index[0], s.frame.index[-1]
        return {
            "source": source, "ticker": s.ticker, "base": s.kind, "hasDaily": s.daily is not None,
            "asset": s.asset, "aroundTheClock": s.around_the_clock, "rolls": s.rolls, "adjust": s.adjust,
            "feeds": self.engine.sources_for(ticker) if source == "*" else [source],
            "pricePrecision": s.price_precision, "volumePrecision": s.volume_precision,
            "periods": self.engine.periods(s), "allPeriods": self.engine.all_periods(s),
            "first": first.isoformat(), "last": last.isoformat(),
            "rows": len(s.frame), "attribution": self.attribution(s.ticker, s.kind, s.asset),
        }

    def attribution(self, ticker: str, base: str, asset: str) -> dict:
        """Which feed each span of the series came from (one feed per span): {timespan: [[start
        ms, end ms, feed], ...]} for the base series and the official daily bars."""
        if asset == "future" or base == "trades":
            return {}
        with self._lock:
            out = {}
            for timespan in dict.fromkeys((base, "day")):
                spans = [[a, b, src] for src, ss in self.store.coverage(ticker, timespan).items() for a, b in ss]
                out[timespan] = sorted(spans)
            return out

    @_heavy
    def bars(self, q: dict[str, str]) -> dict:
        return self._bars(q)

    def _bars(self, q: dict[str, str]) -> dict:
        """bars() outside the in-flight cap: the live stream's tail (a few bars, never waiting)."""
        limit = int(q.get("limit", 1000))
        if not 1 <= limit:
            raise ValueError("limit must be at least 1")
        limit = min(limit, MAX_LIMIT)
        style = Style(
            type=q.get("type", "candles"),
            box=float(q["box"]) if q.get("box") else None,
            reversal=float(q["reversal"]) if q.get("reversal") else None,
            lines=int(q.get("lines", 3)),
        )
        if (style.box is not None and style.box <= 0) or (style.reversal is not None and style.reversal <= 0) \
                or not 1 <= style.lines <= 10:
            raise ValueError("box, reversal and lines must be positive (lines 1-10)")
        to_ms = int(q["to"]) if q.get("to") else None
        after_ms = int(q["after"]) if q.get("after") else None
        since_ms = int(q["since"]) if q.get("since") else None
        period, span = _level(q)
        if self._auto(q, style.type):
            return self.window.styled_page(
                q["source"], q["ticker"], period, span, q.get("session", "regular"),
                style=style, model=_model(q), to_ms=to_ms, after_ms=after_ms, since_ms=since_ms, limit=limit)
        return self.engine.bars(
            q["source"], q["ticker"], period, span,
            q.get("session", "regular"), to_ms=to_ms, since_ms=since_ms, after_ms=after_ms,
            limit=limit, style=style, adjust=_adjust(q.get("adjust")), lod=int(q.get("lod", 1)), model=_model(q),
        )

    @_heavy
    def profile(self, q: dict[str, str]) -> dict:
        """The virtual level 2 for the chart's bar at `at` (the newest without)."""
        step = float(q["step"]) if q.get("step") not in (None, "", "auto") else None
        if step is not None and not 0 < step < 1e9:
            raise ValueError("step must be a positive price")
        period, span = _level(q)
        if self.window.handles(q["source"], q["ticker"]):  # the one bar's window, not the whole history
            if q.get("from") and q.get("to"):  # the highlight's stretch, with its major prices
                return self.window.range_profile(q["source"], q["ticker"], period, span, q.get("session", "regular"),
                                                 int(q["from"]), int(q["to"]), step=step)
            return self.window.profile(q["source"], q["ticker"], period, span, q.get("session", "regular"),
                                       at_ms=int(q["at"]) if q.get("at") else None, step=step)
        return self.engine.profile(
            q["source"], q["ticker"], period, span, q.get("session", "regular"),
            at_ms=int(q["at"]) if q.get("at") else None, step=step, adjust=_adjust(q.get("adjust")))

    STREAM_SECONDS = 300  # one stream's life; the page opens the next from where it got to
    STREAM_CHECK = 0.5  # seconds between looks at the stored data
    STREAM_KEEPALIVE = 15

    def stream(self, q: dict[str, str], stop=None):
        """Server-sent events for a live chart: whenever the symbol's stored data changes (the
        live recorder writes every couple of seconds), the bars from the newest one the page
        has, as /api/bars would give them. Runs alongside the page (a thread per stream) so the
        Stock and Analysis charts follow the feed without asking. Ends after STREAM_SECONDS
        with an `end` event."""
        import time as _time

        since = int(q["since"])
        end = _time.monotonic() + self.STREAM_SECONDS
        seen, beat = None, _time.monotonic()
        while _time.monotonic() < end and not (stop and stop()):
            fp = self.engine.fingerprint(q["source"], q["ticker"])
            if fp != seen:
                first, seen = seen is None, fp
                if q.get("model", "price") != "price":
                    # a model's values move back in time when a bar arrives (a centred window; a Fourier
                    # fit, all of it): the page loads again rather than taking the newest bars only
                    if not first:
                        yield b"data: " + json.dumps({"reload": True}).encode() + b"\n\n"
                        beat = _time.monotonic()
                else:
                    page = self._bars({**q, "since": str(since)})  # the bars since its newest: few
                    if page["bars"]:
                        since = page["bars"][-1]["timestamp"]
                        msg = {"bars": page["bars"], "model": None}
                        yield b"data: " + json.dumps(finite(msg), allow_nan=False).encode() + b"\n\n"
                        beat = _time.monotonic()
            if _time.monotonic() - beat > self.STREAM_KEEPALIVE:
                yield b": keep-alive\n\n"
                beat = _time.monotonic()
            _time.sleep(self.STREAM_CHECK)
        yield b"event: end\ndata: {}\n\n"

    def info(self, source: str, ticker: str, adjust: str = "none") -> dict:
        """Side panel: symbol, business details, key statistics and data coverage."""
        if self.window.handles(source, ticker):  # from its days and levels: no history loaded
            return self._info_light(source, ticker)
        s = self.engine.series(source, ticker, _adjust(adjust))
        if s is None or s.frame.empty:
            raise LookupError(f"no data stored for {source}:{ticker}")
        feeds = self.engine.sources_for(s.ticker) if source == "*" else [source]
        with self._lock:
            watch = next((w for w in self.store.watchlist(ticker=s.ticker) if w["source"] in feeds), {})
            details = next((d for d in (self.store.details(f, s.ticker) for f in feeds) if d), None)
            exchanges = self.store.exchanges()
        daily = s.daily if s.daily is not None and not s.daily.empty else resample(
            s.frame, "day", 1, "extended", utc_days=s.asset in ("crypto", "fx"), session_hours=6 if s.asset == "future" else 0)
        mic = watch.get("exchange") or (details or {}).get("primary_exchange")
        names = mic_names(exchanges)
        out = {
            "symbol": {
                "ticker": s.ticker, "name": watch.get("name") or (details or {}).get("name"),
                "asset": s.asset, "kind": watch.get("kind"), "exchange": mic, "exchangeName": names.get(mic, mic),
                "sourceTicker": watch.get("source_ticker"), "inverted": bool(watch.get("invert")),
                "listed": str(watch["listed_from"]) if watch.get("listed_from") else None,
                "figi": watch.get("figi"),
            },
            "details": details_subset(details),
            "detailsUpdated": (details or {}).get("_updated_at"),
            "stats": key_stats(daily, s.asset),
            "attribution": self.attribution(s.ticker, s.kind, s.asset),
            "audits": self.audit_summary(s.ticker),
            "data": {
                "feeds": feeds, "base": s.kind, "rows": int(len(s.frame)),
                "first": s.frame.index[0].isoformat(), "last": s.frame.index[-1].isoformat(),
                "officialDaily": s.daily is not None, "pricePrecision": s.price_precision,
            },
        }
        if s.asset == "future" and s.rolls:
            out["futures"] = {"contract": s.rolls[-1]["to"], "rolls": len(s.rolls), "lastRoll": s.rolls[-1],
                              "adjust": s.adjust}
        return out

    def _info_light(self, source: str, ticker: str) -> dict:
        levels = self.window.levels(source, ticker)
        if not levels:
            raise LookupError(f"no data stored for {source}:{ticker}")
        s = self.window.series(source, ticker)
        feeds = self.window.feeds(source, ticker)
        daily = self.window._days(s, feeds)
        with self._lock:
            watch = next((w for w in self.store.watchlist(ticker=s.ticker) if w["source"] in feeds), {})
            details = next((d for d in (self.store.details(f, s.ticker) for f in feeds) if d), None)
            exchanges = self.store.exchanges()
        mic = watch.get("exchange") or (details or {}).get("primary_exchange")
        names = mic_names(exchanges)
        # the series' base as the interval charts counted it: its minutes (seconds are a layer apart)
        base = next((lv for lv in levels if lv["type"] == "minute"), levels[0])
        iso = lambda ms: pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert("America/New_York").isoformat()
        return {
            "symbol": {
                "ticker": s.ticker, "name": watch.get("name") or (details or {}).get("name"),
                "asset": s.asset, "kind": watch.get("kind"), "exchange": mic, "exchangeName": names.get(mic, mic),
                "sourceTicker": watch.get("source_ticker"), "inverted": bool(watch.get("invert")),
                "listed": str(watch["listed_from"]) if watch.get("listed_from") else None,
                "figi": watch.get("figi"),
            },
            "details": details_subset(details),
            "detailsUpdated": (details or {}).get("_updated_at"),
            "stats": key_stats(daily, s.asset),
            "attribution": self.attribution(s.ticker, "minute" if base["type"] != "day" else "day", s.asset),
            "audits": self.audit_summary(s.ticker),
            "data": {
                "feeds": feeds, "base": base["type"], "rows": self._base_rows(s.ticker, feeds, base["type"]),
                "first": iso(min(lv["first"] for lv in levels)), "last": iso(max(lv["last"] for lv in levels)),
                "officialDaily": bool(len(daily) and (~daily["derived"].astype(bool)).any()), "pricePrecision": 2,
                "levels": levels,
            },
        }

    def markets(self) -> dict:
        from . import world
        from .markets import markets

        # The calendars are built outside the shared lock (the official schedules' calendar under the
        # engine's own, the world's ~70 exchanges' in world.py): layouts, watchlists and the Fetcher
        # waited behind them. Only the reads of the store take it.
        cal = self.engine.calendar()
        with self._lock:
            exchanges, watch = self.store.exchanges(), self.store.watchlist()
            moves = world.market_moves(self.store)
        out = markets(cal, exchanges, watch)
        out["world"] = world.world_markets(moves=moves)
        out["sun"] = world.subsolar()
        out["sky"] = astro.sky()
        return out

    def deps(self) -> dict:
        from .. import deps

        # Its own connections and lock, and its answer kept DEPS_TTL_S (4.6 s cold): under the shared
        # lock the symbol list, and with it the first chart, waited behind it on every page load.
        with self._deps_lock:
            if self._deps is None or time.monotonic() - self._deps[0] > DEPS_TTL_S:
                self._deps = (time.monotonic(), {"checks": deps.as_rows(deps.checks(self._deps_store, self.settings))})
            return self._deps[1]

    def directory(self) -> dict:
        """Every listing in the directory (placeholders), with what is known locally."""
        with self._lock:
            rows = self.store.directory()
            watched = {w["ticker"] for w in self.store.watchlist()}
            profiles = self.store.company_profiles()
            members = self.store.constituents("sp500", "S&P 500")
            changes = self.store.snapshot_changes()
            futures = _futures_moves(self.store)
            names = mic_names(self.store.exchanges())  # a listing abroad: its exchange by name (Tokyo Stock Exchange)
        out = []
        for r in rows:
            dv = (r["close"] or 0) * (r["volume"] or 0) if r["close"] and r["volume"] else None
            # a future's move is its front contract's; a stock's, the whole-market file's
            fut = futures({"source": "massive", "source_ticker": r["ticker"], "rank": 1}) if r["kind"] == "future" else None
            out.append({"ticker": r["ticker"], "kind": r["kind"], "name": r["name"], "exchange": r["exchange"],
                        "exchangeName": names.get(r["exchange"], r["exchange"]) if r["exchange"] else None,
                        "type": r["type"], "close": fut["close"] if fut else r["close"], "dollarVolume": dv,
                        "icon": bool(r["icon"]), "watched": r["ticker"] in watched,
                        "changePct": fut["changePct"] if fut else changes.get(r["ticker"]),
                        **_sector(r["ticker"], profiles.get(r["ticker"]), "future" if r["kind"] == "future" else "stock",
                                  r["close"], members.get(r["ticker"]))})
        return {"items": out}

    def watch(self, ticker: str) -> dict:
        """Add a directory listing to the watchlist (the fetcher then collects it)."""
        from liminal_libs.symbols import resolve

        with self._lock:
            row = next((r for r in self.store.directory() if r["ticker"] == ticker.upper()), None)
            if row is None:
                raise LookupError(f"{ticker} is not in the directory")
            spec = resolve(f"{row['ticker']}::{row['exchange']}" if row["kind"] == "stock" and row["exchange"] else row["ticker"])
            from liminal_libs.sources import providers, supports

            for feed in providers():  # each span is still downloaded by one feed only
                if supports(feed, spec):
                    self.store.watch_spec(feed, spec)
        return {"watched": spec.ticker}

    def announcements(self) -> dict:
        from . import world
        from .announcements import announcements

        now = datetime.now(timezone.utc)
        with self._lock:
            watch = self.store.watchlist()
            moves = world.market_moves(self.store)
        markets = world.world_markets(now, moves=moves)  # ~70 exchanges' calendars: outside the shared lock
        with self._lock:
            return announcements(self.store, watch, now, markets)

    def gov(self) -> dict:
        """The Gov view: the agencies' newest, the current regulatory parameters and each
        source's state (gov.py; fetched by the sync, read here from gov/gov.db)."""
        from liminal_libs import gov

        with self._gov_lock:
            if self._gov_db is None:
                self._gov_db = gov.open_db(self.store.path)
            return gov.view(self._gov_db, contact_set=bool(self.settings.gov_contact), spx_close=self._spx_close())

    def publishers(self) -> list[dict]:
        """The publisher plugins for the Plugins view (publications.view): each one's state, feeds,
        scrape checks, limits as last verified, schedule; a plugin of several (Federal) with its parts,
        and the datasets a part publishes as the Datasets view has them (the CFTC's Commitments of
        Traders). Read from gov/gov.db, the kills and the datasets' files: no requests."""
        from liminal_libs import gov, publications

        with self._gov_lock:
            if self._gov_db is None:
                self._gov_db = gov.open_db(self.store.path)
            with self._lock:
                out = publications.view(self._gov_db, self.settings, self.store)
        if any(p["datasets"] for p in out):
            held = {d["name"]: d for d in self.datasets()["datasets"]}
            for p in out:
                p["datasetRows"] = [held[n] for n in p["datasets"] if n in held]
        from liminal_libs.sources.registry import database_row, key_row, plugin_databases, plugin_keys, publisher_of

        for p in out:  # what you set for it (its parts' too, for a plugin of several): set or not, never a value
            p["keys"] = [key_row(k, self.settings) for k in plugin_keys(p["name"])]
            # where a plugin's data is, its parts' too (a part's are in its plugin's page)
            p["databases"] = ([database_row(d, self.store.path) for d in plugin_databases(p["name"])]
                              if publisher_of(p["name"]) == p["name"] else [])
        return out

    def start_recheck(self, name: str) -> dict:
        """Verify a publisher's limits now (publications.recheck_now) in a thread: robots.txt read
        again and each feed asked once, never within its floor."""
        from liminal_libs import publications
        from liminal_libs.sources import publishers

        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app sends no requests")
        if name not in publishers():
            raise LookupError(f"no publisher named {name}")
        log = _log_to(self.store.path)

        def run() -> None:
            try:
                if publications.recheck_now(self.settings, name, log) is None:
                    log(f"publications: {name}: not rechecked (not set up here, or another reader held the lock)")
            except Exception as e:  # noqa: BLE001 - logged; the view shows the last findings kept
                log(f"publications: {name}: recheck failed ({type(e).__name__}: {e})")
            finally:
                self._plugins_cache = None
        t = threading.Thread(target=run, name=f"recheck-{name}", daemon=True)
        with self._recheck_lock:  # one at a time: two presses at once started two
            if self._recheck and self._recheck[0].is_alive():
                raise RuntimeError("a recheck is already running")
            t.start()
            self._recheck = (t, {"name": name, "since": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self._plugins_cache = None
        return {"started": True, "name": name}

    def _recheck_running(self) -> dict | None:
        return self._recheck[1] if self._recheck and self._recheck[0].is_alive() else None

    def pause_publisher(self, name: str, on: bool) -> dict:
        """Pause a publisher by hand (until resumed), or resume it."""
        from liminal_libs import publications
        from liminal_libs.sources import publishers

        if name not in publishers():
            raise LookupError(f"no publisher named {name}")
        with self._lock:
            kill = publications.pause(self.store, name, on)
        self._plugins_cache = None
        return {"name": name, "paused": bool(on), "kill": kill}

    @property
    def monitor(self):
        """The Resources view's sampler (resources.Monitor): it samples in the background once
        serve() starts it, every 30 s, and every 2 s while the view is open."""
        with self._monitor_lock:
            if self._monitor is None:
                from liminal_libs.resources import Monitor

                self._monitor = Monitor(Path(self.store.path).parent)
            return self._monitor

    def resources(self, span: str = "recent") -> dict:
        """The Resources view: the app's processes, the machine, its bandwidth and connections, the
        disks and the data files, with the last ten minutes (or `span` "day", the last day)."""
        return self.monitor.view(span)

    VENUES_TTL_S = 30.0  # the Exchanges view's answers are kept this long (the watchlist moves slowly)

    def venues(self) -> dict:
        """The Exchanges view's venues (venues.summary): each with its state now and its insight."""
        from liminal_libs.sources import feed_profiles
        from liminal_libs import sourcing
        from . import venues

        hit = getattr(self, "_venues_cache", None)
        if hit and time.monotonic() - hit[0] < self.VENUES_TTL_S:
            return hit[1]
        cal = self.engine.calendar()  # outside the shared lock, like the Markets view's
        feeds = feed_profiles(self.settings)
        with self._lock:
            scheme = sourcing.Scheme.standing(self.store, feeds)
            out = venues.summary(self.store, cal, feeds, scheme)
        self._venues_cache = (time.monotonic(), out)
        return out

    def venue(self, vid: str) -> dict:
        """One venue's products (venues.products) for the Exchanges view's middle column."""
        from liminal_libs.sources import feed_profiles
        from . import venues

        feeds = feed_profiles(self.settings)
        with self._lock:
            return venues.products(self.store, feeds, vid)

    PLUGINS_TTL_S = 10.0  # the Plugins view's answer is kept this long (a rescan's end clears it)

    def plugins(self) -> dict:
        """The Plugins view (plugins.view): every provider plugin, its plan and where that came from,
        its limits, capabilities and last scan, its part in the delivery scheme; the rescan going now."""
        from liminal_libs.sources import feed_profiles
        from . import plugins

        running = self._rescan_running()
        rechecking = self._recheck_running()
        hit = self._plugins_cache
        if hit and time.monotonic() - hit[0] < self.PLUGINS_TTL_S and hit[2] == (bool(running), bool(rechecking)):
            return hit[1]
        settings = self.settings
        feeds = feed_profiles(settings)
        scans = sourcing.Scans.at(self.store.path)
        with self._lock:
            scheme = sourcing.Scheme.standing(self.store, feeds)
            out = plugins.view(self.store, settings, feeds, scheme, scans, running)
        try:
            out["publishers"] = self.publishers()
        except Exception as e:  # noqa: BLE001 - gov.db or a publisher's fault: the providers still show
            out["publishers"], out["publishersError"] = [], f"{type(e).__name__}: {e}"
        out["rechecking"] = rechecking
        self._plugins_cache = (time.monotonic(), out, (bool(running), bool(rechecking)))
        return out

    def _rescan_running(self) -> dict | None:
        proc, info = self._rescan
        if proc is None or proc.poll() is not None:
            return None
        return info

    def start_rescan(self, name: str | None = None) -> dict:
        """Run `capabilities` (one provider's scan, or every one set up here) in its own process: a
        few requests each, within the feeds' machine-wide rate windows and daily caps, so it runs
        beside a sync. The plans it finds count here within SETTINGS_TTL_S."""
        from liminal_libs.sources import providers

        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app sends no requests")
        if self._halted():
            raise RuntimeError("fetching is halted: resume it first")
        from liminal_libs.sources.registry import assistants

        scanned = providers().get(name) or assistants().get(name) if name is not None else None
        if name is not None and (scanned is None or not scanned.has_scan):
            raise LookupError(f"{name} has no capability scan")
        if self._rescan_running():
            raise RuntimeError("a rescan is already running")
        args = [sys.executable, "-m", "liminal_charts", "capabilities", *([name] if name else [])]
        proc = self._spawn(args)
        self._rescan = (proc, {"name": name, "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "pid": proc.pid})
        self._plugins_cache = None
        return {"started": True, "name": name, "pid": proc.pid}

    def set_plan(self, plugin: str, variable: str, value: str | None) -> dict:
        """A plugin's plan chosen in the Plugins view (the user, 2026-10-29: "let me choose also on
        the plugin, in the app"): saved like the key script saves it (a user environment variable,
        config.save_user_variable), or with None removed, leaving it to the plugin's scan or its
        default. Only a plan variable with choices, to one of them: never a key. The app uses it at
        its next look at the settings, the sync from its next run."""
        from liminal_libs.sources import providers
        from liminal_libs.sources.registry import assistants

        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app saves no settings")
        p = providers().get(plugin) or assistants().get(plugin)  # an AI assistant's plan, model and caps too
        if p is None or variable not in p.plan_choices:
            raise LookupError(f"{plugin} has no plan variable {variable}")
        if value is not None:
            value = str(value).strip().lower()
            if value not in p.plan_choices[variable]:
                raise ValueError(f"{variable} is one of {', '.join(p.plan_choices[variable])}")
        self._save_variable(variable, value)
        with self._settings_lock:
            self._settings_at = 0.0  # looked at again on the next request
        self._plugins_cache = None
        return {"plugin": plugin, "variable": variable, "value": value}

    def set_key(self, plugin: str, variable: str, value: str | None) -> dict:
        """A plugin's key, or a detail of yours it sends where asked (GOV_CONTACT), set up in the
        Plugins view (the user, 2026-10-31: "i need a way to setup plugins inside the plugin interface
        in the main window, for all plugins, including keys, user details etc"): saved like the key
        script saves it (a user environment variable, config.save_user_variable), or with None
        removed. Only a variable the plugin declares (registry.plugin_keys). Its value is never
        logged, kept anywhere else or sent back: the answer says only whether it is set."""
        from liminal_libs.sources import keys, providers, publishers
        from liminal_libs.sources.registry import assistants, plugin_keys

        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app saves no settings")
        if plugin not in providers() and plugin not in publishers() and plugin not in assistants():
            raise LookupError(f"no plugin named {plugin}")
        if not any(k.name == variable for k in plugin_keys(plugin)):
            raise LookupError(f"{plugin} reads no variable {variable}")
        why = keys.refusal(variable) or self._claimed_elsewhere(plugin, variable)
        if why:  # a plugin declaring PATH, or another plugin's key, never writes it from its panel
            raise LookupError(why)
        self._save_variable(variable, None if value is None else keys.clean(value))
        with self._settings_lock:
            self._settings_at = 0.0  # looked at again on the next request
        self._plugins_cache = None
        return {"plugin": plugin, "variable": variable, "set": value is not None}

    @staticmethod
    def _claimed_elsewhere(plugin: str, variable: str) -> str | None:
        """Why a plugin may not set a variable another plugin declares too (one package's KeyVar
        naming Massive's key), or None: each variable is one plugin's, its feeds' and parts' included."""
        from liminal_libs.sources import providers, publishers
        from liminal_libs.sources.registry import assistants, plugin_of, publisher_of

        def top(name: str) -> str:
            return publisher_of(name) if name in publishers() else plugin_of(name)

        owners = {top(n) for n, p in [*providers().items(), *publishers().items(), *assistants().items()]
                  if any(k.name == variable for k in p.keys)}
        return f"{variable} is declared by {', '.join(sorted(owners))}: no one plugin's to set" if len(owners) > 1 else None

    def _save_variable(self, name: str, value: str | None) -> None:
        config.save_user_variable(name, value)

    # -- the AI assistants (plugin API 2.0; liminal_libs.assistants) ---------------------------------
    def evaluate(self, body: dict, session=None) -> dict:
        """An argument evaluated by an AI assistant plugin (the Plugins view's Try it, POST
        /api/ai/evaluate, `liminal-charts ai ask`): the body's `argument` (arguments.argument_from_dict: a
        question or a judge, its inputs by evaluation id) or its simple form (`question`, `answer`,
        `scale`, `fields`, `context` strings), sent to `assistant` (else the default) with `model`
        (else its plan's), recorded in the main file's evaluations. Offline: nothing sent. At most
        two answers in flight (the vendors are slow): the rest wait."""
        from liminal_libs import assistants as ai
        from liminal_libs.assistants import argument_from_dict

        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app sends nothing to an assistant")
        name = str(body.get("assistant") or "").strip().lower() or None
        model = str(body.get("model") or "").strip() or None
        arg = body.get("argument")
        if not isinstance(arg, dict):
            question = str(body.get("question") or "").strip()
            if not question:
                raise ValueError("question is required")
            answer: dict = {"kind": str(body.get("answer") or "text").strip().lower()}
            if body.get("scale"):
                answer["scale"] = [s.strip() for s in str(body["scale"]).split(",") if s.strip()]
            if body.get("fields"):
                answer["fields"] = [s.strip() for s in str(body["fields"]).split(",") if s.strip()]
            context = body.get("context") or []
            if not isinstance(context, list):
                raise ValueError("context must be a list of texts")
            arg = {"kind": "question", "question": question[:4000], "answer": answer,
                   "context": [{"kind": "text", "text": str(c)[:12000]} for c in context[:20]]}
        argument = argument_from_dict(arg, lookup=self.store.evaluation)
        with _AI_IN_FLIGHT:
            ev = ai.evaluate(argument, self.settings, assistant=name, model=model, store=self.store, session=session)
        return ev.as_dict()

    def evaluations(self, q: dict) -> dict:
        """The evaluations kept (GET /api/ai/evaluations): by assistant, kind, of one argument; with
        `id`, that one and what it built on (a judge's inputs)."""
        limit = int(q.get("limit") or 50)
        out = {"evaluations": self.store.evaluations(assistant=q.get("assistant") or None, kind=q.get("kind") or None,
                                                     limit=max(1, min(limit, 500)), argument_id=q.get("argument") or None)}
        if q.get("id"):
            out["chain"] = self.store.evaluation_chain(str(q["id"]))
        return out

    def _spawn(self, args: list[str]):
        """A command of the app in its own windowless process (kept, so it is reaped)."""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, creationflags=flags)

    def _spx_close(self) -> tuple[str, float] | None:
        """The S&P 500's newest daily close held (the circuit breakers' reference), or None."""
        try:
            if not self.window.handles("*", "SPX"):
                return None
            bars = self.window.page("*", "SPX", "day", 1, "regular", limit=1)["bars"]
        except (LookupError, KeyError, ValueError):
            return None
        if not bars:
            return None
        day = datetime.fromtimestamp(bars[-1]["timestamp"] / 1000, timezone.utc).date().isoformat()
        return day, float(bars[-1]["close"])

    def calendar(self, q: dict[str, str]) -> dict:
        """Sessions of a symbol between two times (ms), for annotating the chart."""
        from .markets import symbol_sessions

        with self._lock:
            item = next(iter(self.store.watchlist(ticker=q["ticker"])), None)
        source = q.get("source", "*")
        if self.window.handles(source, q["ticker"]):  # the asset is all it needs: no history loaded
            asset = self.window.asset(q["ticker"], None if source == "*" else source)
        else:
            s = self.engine.series(source, q["ticker"])  # under the engine's own lock
            asset = s.asset if s else (item or {}).get("asset", "stock")
        product = item["source_ticker"] if item and item["asset"] == "future" else None
        start = datetime.fromtimestamp(int(q["from"]) / 1000, timezone.utc)
        end = datetime.fromtimestamp(int(q["to"]) / 1000, timezone.utc)
        if end - start > timedelta(days=CALENDAR_MAX_DAYS):
            raise ValueError("calendar range is limited to 10 years")
        return {"asset": asset, "sessions": symbol_sessions(self.engine.calendar(), asset, product, start, end,
                                                            q.get("session", "extended"))}

    @_heavy
    def future(self, q: dict[str, str]) -> dict:
        from .window import UNIT_MS

        style = Style(type=q.get("type", "candles"))
        period, span = _level(q)
        # slots for at most 50 years ahead, offset included (an offset past 2262 overflowed the timestamps)
        reach = max(1, int(50 * 365.25 * 86_400_000 // (UNIT_MS.get(period, 60_000) * max(1, span))))
        offset = min(max(0, int(q.get("offset", 0))), reach)
        count = max(0, min(int(q.get("count", 500)), 5000, reach - offset))
        if not count:
            return {"timestamps": [], "last": None, "total": 0}
        if self._auto(q, style.type):  # projected from the level's newest bar, not the whole history
            from .projection import project

            if style.type in PRICE_BASED:  # bricks follow price: no time axis to project
                return {"timestamps": [], "last": None, "total": 0}
            session = q.get("session", "regular")
            s = self.window.series(q["source"], q["ticker"])
            model = _model(q)
            # a model's newest value may be bars back (a stretch shorter than its window has none)
            newest = self.window.styled_page(q["source"], q["ticker"], period, span, session, style=style, model=model,
                                             limit=1 if model.is_price else 2 * model.window)["bars"]
            if not newest:
                return {"timestamps": [], "last": None, "total": 0}
            last = pd.Timestamp(newest[-1]["timestamp"], unit="ms", tz="UTC").tz_convert("America/New_York")
            if period in ("day", "week", "month", "year"):  # days from the exchange's sessions, not every minute
                s.daily = pd.DataFrame({"close": [newest[-1]["close"]]}, index=pd.DatetimeIndex([last]))
            ts = project(self.engine.calendar(), s, None, period, span, s.session_for(session), style.type, 1, 1, last,
                         offset, count, cache=self.engine._future_cache)
            return {"timestamps": ts, "last": newest[-1]["timestamp"], "total": 1, "lastBar": newest[-1]["timestamp"]}
        return self.engine.future(
            q["source"], q["ticker"], period, span, q.get("session", "regular"),
            style=style, adjust=_adjust(q.get("adjust")), lod=int(q.get("lod", 1)), model=_model(q),
            offset=offset, count=count,
        )

    @_heavy
    def range_stats(self, q: dict[str, str]) -> dict:
        period, span = _level(q)
        if self._auto({**q, "auto": "1"}):  # its own window only
            return self.window.range_stats(q["source"], q["ticker"], period, span, q.get("session", "regular"),
                                           int(q["from"]), int(q["to"]), model=_model(q))
        return self.engine.range_stats(
            q["source"], q["ticker"], period, span,
            q.get("session", "regular"), int(q["from"]), int(q["to"]), adjust=_adjust(q.get("adjust")),
            model=_model(q),
        )

    # -- schemes and replay (calc/schemes.py, window.py; docs/ARCHITECTURE.md) --------
    def schemes(self) -> dict:
        """The built-in schemes and their settings, for the Analysis panel."""
        from liminal_libs.calc.schemes import SCHEMES

        return {"schemes": [{"key": s.key, "label": s.label, "about": s.about, "params": [
            {"key": p.key, "label": p.label, "default": p.default, "lo": p.lo, "hi": p.hi, "step": p.step,
             "choices": list(p.choices), "about": p.about} for p in s.params]} for s in SCHEMES.values()]}

    def _windowed(self, q: dict[str, str], what: str) -> None:
        if not self.window.handles(q["source"], q["ticker"]):
            raise ValueError(f"{what} on the chart's own series only (not continuous futures or the composite model yet)")

    @_heavy
    def scheme(self, q: dict[str, str]) -> dict:
        from liminal_libs.calc.schemes import Costs

        self._windowed(q, "schemes run")
        given = json.loads(q.get("params") or "{}")
        if not isinstance(given, dict):
            raise ValueError("params: an object of settings")
        fee, slip = float(q.get("fee") or 0), float(q.get("slippage") or 0)
        if not (0 <= fee <= 1000 and 0 <= slip <= 1000):
            raise ValueError("fee and slippage: 0 to 1,000 basis points")
        period, span = _level(q)
        return self.window.scheme(q["source"], q["ticker"], period, span, q.get("session", "regular"), int(q["from"]),
                                  int(q["to"]), q["scheme"], given, Costs(fee, slip), q.get("longOnly") == "1")

    @_heavy
    def replay(self, q: dict[str, str]) -> dict:
        from .window import REPLAY_PAGE

        self._windowed(q, "replay runs")
        limit = int(q.get("limit") or REPLAY_PAGE)
        if not 1 <= limit <= 20_000:
            raise ValueError("limit: 1 to 20,000 bars")
        period, span = _level(q)
        return self.window.replay(q["source"], q["ticker"], period, span, q.get("session", "regular"), int(q["from"]),
                                  limit)

    # -- trading (trading/; docs/ARCHITECTURE.md, "Trading"): paper first, live trading off ----------
    @property
    def paper(self):
        """The paper account, on a connection and lock of its own (_paper_lock): matching reads bars,
        and under the shared lock the layouts, watchlists and the Fetcher waited behind it."""
        with self._paper_lock:
            if self._paper is None:
                from ..trading.paper import PaperBook

                self._paper_store = self.store.reader()
                self._paper = PaperBook(self._paper_store.conn, self._fill_bars, self._last_price,
                                        lambda t: self.window.asset(t), self._stored_to)
            return self._paper

    def _fill_bars(self, source: str, ticker: str, at_ms: int, until_ms: int):
        """The finest bars stored from `at_ms` to `until_ms` (1-second where held, else minutes), in
        the symbol's regular session (stocks; round the clock for crypto): what a paper order fills
        from; each settled unless only a feed of one exchange's share holds it (_stored_to)."""
        from ..trading.paper import MATCH_PAGE, _now
        from .bars import epoch_ms

        if not self.window.handles(source, ticker):
            raise ValueError("paper orders fill from the chart's own series (not continuous futures or the composite yet)")
        s = self.window.series(source, ticker)
        feeds = self.window.feeds(source, ticker)
        fine = "second" if self.window.replay_fine(source, ticker, "minute", at_ms) == "second" else "minute"
        step = 1000 if fine == "second" else 60_000
        page = self.window._intraday_page(s, feeds, fine, 1, s.session_for("regular"), None, at_ms - 1, None, MATCH_PAGE)
        bars = page["frame"]
        if not len(bars):
            return pd.DataFrame(columns=["t", "open", "high", "low", "close", "settled"]), step
        t = epoch_ms(bars.index)
        # a bar past the whole market's stored bars may yet be replaced by them, for a while (a sync
        # stores the session within hours); one older than PARTIAL_SETTLE_MS is taken as it is
        settled_to = max(self._stored_to(source, ticker) or 0, _now() - PARTIAL_SETTLE_MS)
        out = pd.DataFrame({"t": t, **{k: bars[k].to_numpy() for k in ("open", "high", "low", "close")},
                            "settled": t + step <= settled_to})
        return out[out["t"] < until_ms], step

    def _stored_to(self, source: str, ticker: str) -> int | None:
        """Where the symbol's stored bars end (ms, any session: its 1-second and minute bars): those
        of the feeds of the whole market where one holds it, not a feed of one exchange's share
        (IEX's session, which the whole market's replaces once a sync stores it)."""
        from liminal_libs.live import PARTIAL_FEEDS

        if not self.window.handles(source, ticker):
            return None
        feeds = self.window.feeds(source, ticker)
        whole = [f for f in feeds if f not in PARTIAL_FEEDS] or feeds
        ends = []
        with self.engine.lock:
            for f in whole:
                for base, step in (("second", 1000), ("minute", 60_000)):
                    e = self.engine.store.bar_edges(f, ticker, base)
                    if e:
                        ends.append(e[1] + step)
        return max(ends, default=None)

    def _last_price(self, source: str, ticker: str) -> float | None:
        if not self.window.handles(source, ticker):
            return None
        bars = self.window.page(source, ticker, "minute", 1, "extended", limit=1)["bars"]
        return float(bars[-1]["close"]) if bars else None

    @_heavy
    def trading(self) -> dict:
        """The Trading view: the brokers, and the paper account (its orders matched first, from bars)."""
        from ..trading.brokers import PLANNED

        with self._paper_lock:
            book = self.paper.summary()
        return {"brokers": [{"key": "paper", "label": self.paper.label, "live": False, "status": "ready"}, *PLANNED],
                "liveTrading": False, "broker": "paper", **book}

    def trade_place(self, body: dict) -> dict:
        with self._paper_lock:
            return self.paper.place(body.get("ticker", ""), body.get("source") or "*", body.get("side", ""),
                                    body.get("qty", 0), body.get("type", "market"), body.get("limit"), body.get("stop"),
                                    body.get("tif", "day"))

    def trade_cancel(self, order_id: int) -> dict:
        with self._paper_lock:
            return self.paper.cancel(order_id)

    def trade_reset(self, body: dict) -> dict:
        with self._paper_lock:
            return self.paper.reset(body.get("cash", 100_000), body.get("feeBps", 0))

    # -- profiles (no passwords: each keeps its own watchlists, layouts, preferences, register) --

    def profiles(self, current: int | None = None) -> dict:
        with self._lock:
            return {"profiles": self.store.profiles(), "current": current}

    PROFILE_IDS_TTL_S = 5.0  # an id not held is looked for again at most this often

    def profile_exists(self, profile_id: int) -> bool:
        """Whether a profile is there (the cookie of every request): from the ids held, read again
        through a connection of its own, never under the shared lock."""
        if profile_id in self._profile_ids:
            return True
        with self._profiles_lock:
            if time.monotonic() - self._profile_ids_at >= self.PROFILE_IDS_TTL_S:
                self._profile_ids = self._profiles_store.profile_ids()
                self._profile_ids_at = time.monotonic()
        return profile_id in self._profile_ids

    def create_profile(self, body: dict) -> dict:
        """A new profile: an empty watchlist, no layouts, the page's own preferences."""
        with self._lock:
            made = self.store.create_profile(str(body.get("name", "")))
        self._profile_ids = self._profile_ids | {made["id"]}
        return made

    def rename_profile(self, profile_id: int, body: dict) -> dict:
        with self._lock:
            found = self.store.rename_profile(profile_id, str(body.get("name", "")))
        if found is None:
            raise LookupError(f"no profile {profile_id}")
        return found

    def sign_in(self, profile_id: int) -> dict:
        """The profile using the app now: the fetcher follows its watchlists."""
        with self._lock:
            found = self.store.sign_in(profile_id)
        if found is None:
            raise LookupError(f"no profile {profile_id}")
        return found

    def register(self, profile: int) -> dict:
        """The profile's register: every stock it opened in the chart, the latest first."""
        with self._lock:
            return {"visits": self.store.visits(profile)}

    # -- watchlists (the rail's lists of symbols): each profile's own ------------------
    MAX_LISTS, MAX_LIST_TICKERS = 50, 1000

    def watchlists(self, profile: int = 1) -> dict:
        with self._lock:
            raw = self.store.kv_get(live.watchlists_key(profile))
        try:
            lists = json.loads(raw) if raw else []
        except ValueError:
            lists = []
        return {"lists": lists if isinstance(lists, list) else []}

    def save_watchlists(self, body: dict, profile: int = 1) -> dict:
        """Replace the lists: [{name, tickers}], names unique (1-40 characters), tickers in order."""
        lists = body.get("lists")
        if not isinstance(lists, list) or len(lists) > self.MAX_LISTS:
            raise ValueError(f"lists must be a list of at most {self.MAX_LISTS}")
        out, names = [], set()
        for item in lists:
            name = str((item or {}).get("name", "")).strip() if isinstance(item, dict) else ""
            tickers = item.get("tickers") if isinstance(item, dict) else None
            if not 1 <= len(name) <= 40 or name in names:
                raise ValueError("each list needs a name of 1-40 characters, not used by another list")
            if not isinstance(tickers, list) or len(tickers) > self.MAX_LIST_TICKERS \
                    or not all(isinstance(t, str) and 0 < len(t.strip()) <= 32 for t in tickers):  # "  " is none
                raise ValueError(f"tickers must be at most {self.MAX_LIST_TICKERS} symbols")
            names.add(name)
            out.append({"name": name, "tickers": list(dict.fromkeys(t.strip().upper() for t in tickers))})
        with self._lock:
            self.store.kv_set(live.watchlists_key(profile), json.dumps(out))
        return {"lists": out}

    # -- layouts (saved markups): each profile's own ----------------------------------

    def layouts(self, profile: int = 1) -> dict:
        with self._lock:
            return {"layouts": self.store.list_layouts(profile), "max": self.store.MAX_LAYOUTS}

    def layout(self, layout_id: int, profile: int = 1) -> dict:
        with self._lock:
            found = self.store.get_layout(layout_id, profile)
        if found is None:
            raise LookupError(f"no layout {layout_id}")
        return found

    def create_layout(self, body: dict, profile: int = 1) -> dict:
        data = body.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        with self._lock:
            return self.store.create_layout(str(body.get("name", "")), data, profile)

    def update_layout(self, layout_id: int, body: dict, profile: int = 1) -> dict:
        data = body.get("data")
        if data is not None and not isinstance(data, dict):
            raise ValueError("data must be an object")
        with self._lock:
            found = self.store.update_layout(layout_id, body.get("name"), data, profile)
        if found is None:
            raise LookupError(f"no layout {layout_id}")
        return found

    def delete_layout(self, layout_id: int, profile: int = 1) -> dict:
        with self._lock:
            if not self.store.delete_layout(layout_id, profile):
                raise LookupError(f"no layout {layout_id}")
        return {"deleted": layout_id}

    OPEN_NOTE_SECONDS = 30

    def note_open(self, ticker: str, now_ms: int | None = None) -> None:
        """A chart page has `ticker` open (its activity poll says so): the live recorder streams it
        if it is a stock (live.wanted_stocks). Noted at most every OPEN_NOTE_SECONDS per symbol;
        symbols not noted for live.OPEN_FRESH_MS drop out."""
        import time as _time

        if self.settings.read_only:  # a copy reading the real data: the recorder follows the user's own app
            return
        ticker = ticker.strip().upper()
        now_ms = now_ms or int(_time.time() * 1000)
        last = self._open_noted.get(ticker)
        if not 0 < len(ticker) <= 32 or (last is not None and now_ms - last < self.OPEN_NOTE_SECONDS * 1000):
            return
        self._open_noted[ticker] = now_ms

        def add(opened: dict) -> dict:  # other chart apps note theirs in the same key
            kept = {t: at for t, at in opened.items()
                    if isinstance(at, (int, float)) and now_ms - at <= live.OPEN_FRESH_MS}
            return {**kept, ticker: now_ms}

        with self._lock:
            self.store.kv_update(live.OPEN_KEY, add, {})
        if self.live_wanted():
            threading.Thread(target=self.attach_live, name="live-attach", daemon=True).start()  # a start takes seconds

    def live_wanted(self) -> bool:
        """Whether this app records live trades at all (crypto, or stocks with Alpaca's keys)."""
        s = self.settings
        return bool(s.live_coins or live.stock_keys(s)) and not s.offline and not s.read_only

    def attach_live(self) -> bool:
        """Attach to the recorder, starting it if it is not running, and watch for the moment
        nothing is open any more (settle_live). Whether it is attached now."""
        with self._live_lock:
            if self._live is not None and self._live.alive():
                return True
            self._live = live.attach(self.store.path, log=_log_to(self.store.path))
            if self._live is not None and (self._live_watch is None or not self._live_watch.is_alive()):
                self._live_watch = threading.Thread(target=self._watch_live, name="live-watch", daemon=True)
                self._live_watch.start()
            return self._live is not None

    def settle_live(self, now_ms: int | None = None) -> bool:
        """Detach from the recorder once no chart has noted a symbol open for live.OPEN_FRESH_MS
        (it then flushes and ends by itself). Whether it was detached here."""
        now_ms = now_ms or int(time.time() * 1000)
        with self._live_lock:
            if self._live is None:
                return False
            if any(now_ms - at <= live.OPEN_FRESH_MS for at in self._open_noted.values()):
                return False
            self._live.detach()
            self._live = None
            _log_to(self.store.path)("live: nothing open on a chart any more; the recorder is let go")
            return True

    LIVE_SETTLE_SECONDS = 15.0

    def _watch_live(self) -> None:
        while True:
            time.sleep(self.LIVE_SETTLE_SECONDS)
            with self._live_lock:
                if self._live is None:
                    return
            self.settle_live()

    def activity(self, q: dict[str, str]) -> dict:
        if q.get("open"):
            self.note_open(q["open"])
        # On connections and a lock of its own (it took 1 to 3 s under the shared lock, polled by every
        # open page, and the symbol list and watchlists waited 17 s behind it), and one answer for the
        # polls that arrive within ACTIVITY_TTL_S of each other: one kept per feed, for the lines and
        # open symbol it was made for (free text in the key kept an answer for every value sent).
        from liminal_libs.sources import providers

        source = q.get("source") or "massive"
        source = source if source in providers() else "massive"  # a feed this app has (a page may remember an old one)
        lines = max(1, min(int(q.get("lines", 200)), 1000))
        focus = (q.get("open") or "").strip()
        focus = focus if 0 < len(focus) <= 32 else None
        with self._status_lock:
            now = time.monotonic()
            for k in [k for k, v in self._activity.items() if now - v[0] > ACTIVITY_TTL_S]:
                del self._activity[k]  # expired: made again when asked
            hit = self._activity.get(source)
            if hit is None or hit[1] != (lines, focus):
                if self._official_at is None or now - self._official_at >= OFFICIAL_TTL_S:
                    # the sync's official holidays, so "new" here agrees with it on an unscheduled closure
                    self._official_at = now
                    freshness.use_official(self._status_store)
                hit = self._activity[source] = (now, (lines, focus), activity.activity(
                    self._status_store, self.settings, self.store.path, source, log_lines=lines, focus=focus,
                    rows_cache=self._activity_rows, rows_ttl_s=SYMBOL_ROWS_TTL_S))
            out = dict(hit[2])
        out["feeds"] = self.feeds()
        out["feedLabels"] = feed_labels()
        # The opened symbol's own request: its end is not every feed's (Massive's pass goes on beside it)
        req = self._symbol_request
        if req is not None:
            with self._queue_lock:
                row = self._queue_store.request_get(req[1])
            out["symbolRun"] = {"ticker": req[0], "going": row is not None and row["state"] in OPEN_REQUEST}
        else:
            out["symbolRun"] = None
        return out

    def queue(self, q: dict[str, str]) -> dict:
        """The Fetcher: the fetch queue across plugins (gui/queue.py), filtered by plugin, kind, state."""
        from . import queue

        limit = max(1, min(int(q.get("limit", 300)), 2000))
        with self._queue_lock:
            return queue.view(self._queue_store, q.get("plugin") or None, q.get("kind") or None,
                              q.get("state") or None, limit, settings=self.settings)

    def plugin_fetching(self, name: str) -> dict:
        """A plugin's fetching status for its page (gui/queue.py): its state, queue, feeds, limits, runs, log."""
        from . import queue

        with self._status_lock:
            return queue.plugin_status(self._status_store, self.settings, self.store.path, name)

    def _gov_conn(self):
        from liminal_libs import gov

        with self._gov_lock:
            if self._gov_db is None:
                self._gov_db = gov.open_db(self.store.path)
            return self._gov_db.conn

    def engines(self) -> dict:
        """The engines here for the Store picker: SQLite's version, DuckDB's (installed or not), the
        MongoDB server's state (mongo.status: installed, running, its memory and the app's data)."""
        import sqlite3

        from liminal_libs import mongo
        from liminal_libs.storage import history

        duck = None
        if history.available():
            import duckdb

            duck = duckdb.__version__
        return {"sqlite": sqlite3.sqlite_version, "duckdb": duck, "mongodb": mongo.status(self.store.path.parent)}

    def plugin_stores(self, name: str) -> dict:
        """A plugin's stores by data type for its page (stores.py): each with its engines, its pick,
        where it should be and is, and how far a move has gone; and the engines here."""
        from liminal_libs import stores

        gconn = self._gov_conn()
        with self._status_lock:
            rows = stores.plugin_stores(self._status_store, name, gconn)
        return {"name": name, "stores": rows, "engines": self.engines()}

    def set_store(self, plugin: str, dtype: str, engine: str | None) -> dict:
        """A plugin's store for a data type picked in the Plugins view (None: the optimal). The user,
        2026-10-31: "lets have a 'store' picker so i can choose manually, but lets try to use the
        optimal data store for the data type". Documents move at once (copied, counted, then used
        there: MongoDB started if it is the one); bars a few closed months each hour (the sync's
        `history run`). Nothing is removed from the store left."""
        from liminal_libs import stores
        from liminal_libs.storage import Store

        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app saves no settings")
        with self._status_lock:
            stores.pick(self._status_store, plugin, dtype, engine)
        note = None
        if dtype == "documents":
            log = _log_to(self.store.path)
            with Store(self.store.path) as store:
                for d in stores.settle(store, self._gov_conn(), self.store.path.parent, log, only=plugin):
                    note = d.get("error") or note
        out = self.plugin_stores(plugin)
        out["error"] = note
        return out

    def datasets(self) -> dict:
        """The research datasets: what is held, its span, files fetched and closed, quality."""
        from liminal_libs import datasets

        with self._datasets_lock:  # its own connections: never behind (or in front of) the shared lock
            rows = datasets.status(self._datasets_store)
        for r in rows:
            r["fetched"] = r["fetched"].isoformat() if r["fetched"] else None
        return {"datasets": rows}

    def dataset_series(self, name: str) -> dict:
        """A dataset's series (or, for the COT report, its markets) for the viewer's picker."""
        from liminal_libs import datasets

        if name not in datasets.DATASETS:
            raise LookupError(f"no dataset {name}")
        ds = datasets.DATASETS[name]
        with self._lock:
            if name == "cftc-cot-legacy":
                return {"name": name, "markets": datasets.cot_markets(self.store)}
            return {"name": name, "series": datasets.series_list(self.store, name),
                    "members": len(self.store.constituents(ds.provider, "S&P 500"))}

    def dataset_values(self, name: str, series: str) -> dict:
        from liminal_libs import datasets

        if name not in datasets.DATASETS:
            raise LookupError(f"no dataset {name}")
        wanted = [s for s in series.split(",") if s]
        if not wanted:
            raise ValueError("no series given")
        with self._lock:
            return {"name": name, "series": datasets.values(self.store, name, wanted)}

    def audit_summary(self, ticker: str | None = None, limit: int = 40) -> dict:
        with self._lock:
            rows = self.store.audits(limit, ticker)
            return {"audits": rows, "scores": self.store.audit_scores(), "queued": self.store.audit_queue_count(),
                    "quality": self.store.qualities()}

    def feeds(self) -> list[dict]:
        """The feeds' summary, kept FEEDS_TTL_S (every status poll carries it)."""
        hit = self._feeds_summary
        if hit is not None and time.monotonic() - hit[0] < FEEDS_TTL_S:
            return hit[1]
        out = self._feeds()
        self._feeds_summary = (time.monotonic(), out)
        return out

    def _feeds(self) -> list[dict]:
        from liminal_libs.sources import feed_profiles, providers

        profiles = feed_profiles(self.settings)
        with self._lock:
            watch = self.store.watchlist()
        out = []
        for name, plugin in providers().items():
            p, s = profiles.get(name), self.settings.for_source(name)
            rows = [w for w in watch if w["source"] == name]
            closed = plugin.archival and all(w.get("closed_at") for w in rows)
            out.append({"name": name, "title": plugin.title, "configured": p is not None,
                        "assets": sorted(p.assets) if p else [], "plan": s.tier.name,
                        "keys": plugin.keys_set(self.settings), "archive": plugin.archival, "closed": closed,
                        "keysNote": plugin.missing_keys_note or f"key not set ({plugin.key_hint})",
                        "keysChip": plugin.missing_keys_chip})
        return out

    def icon(self, ticker: str) -> tuple[str, bytes] | None:
        with self._lock:
            return self.store.icon(ticker)

    def start_scan(self, ticker: str | None = None) -> dict:
        """Run `scan --probe --fetch` (for one ticker, or every watched symbol) in its own
        process: it takes the run lock and the plan caps like a scheduled sync, and shows up in
        the activity view and the status bar while it runs."""
        if self.settings.offline:
            raise RuntimeError("offline (LIMINAL_OFFLINE): this copy of the app fetches nothing")
        if self._halted():
            raise RuntimeError("fetching is halted: resume it first")
        if activity.lock_owner(self.store.path):
            raise RuntimeError("a run is already in progress")
        args = [sys.executable, "-m", "liminal_charts", "scan", "--probe", "--fetch", "--all-sources"]
        if ticker:
            with self._lock:
                watched = {w["ticker"] for w in self.store.watchlist()}
            if ticker.upper() not in watched:
                raise LookupError(f"{ticker} is not on the watchlist")
            args += ["--only", ticker.upper()]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        self._scan = proc  # keep a reference so it is reaped
        return {"started": True, "pid": proc.pid}

    def inventory(self, ticker: str) -> dict:
        """The symbol's data feed by feed and frame by frame (inventory.py): what each feed sent,
        what was built here from its finer data (*derived) and what is not built yet."""
        from liminal_libs import inventory

        t = ticker.upper()
        rank = self.engine.sources_for(t)
        counted = self._counts(t)  # the counts follow on the panel's next read
        with self._panel_lock:
            out = inventory.symbol_inventory(self._panel_store, t, rank, counts=counted[1] if counted else {})
        return out

    def _base_rows(self, ticker: str, feeds: list[str], base: str) -> int | None:
        """The bars a series holds at its base level (the feed holding most, its own and built), from
        the background counts (None until they are made; asked for here)."""
        counted = self._counts(ticker)
        if counted is None:
            return None
        rows = counted[1]
        return max((rows.get((f, "bars", base, 1), 0) + rows.get((f, "built_bars", base, 1), 0) for f in feeds), default=0)

    def _counts(self, ticker: str) -> tuple[float, dict] | None:
        """A symbol's bar counts frame by frame as last made (counted at, counts), or None; made again
        in the background when missing or older than ROW_COUNTS_TTL_S. At most COUNTING_AT_ONCE at
        once (each a thread and connections of its own: a page asking for one ticker after another
        started one each), and the ROW_COUNTS_KEPT symbols counted last are kept; the rest are
        counted when asked again."""
        with self._counts_lock:
            counted = self._row_counts.get(ticker)
            stale = counted is None or time.monotonic() - counted[0] > ROW_COUNTS_TTL_S
            if stale and ticker not in self._counting and len(self._counting) < COUNTING_AT_ONCE:
                self._counting.add(ticker)
                threading.Thread(target=self._count_rows, args=(ticker,), daemon=True, name=f"count-{ticker}").start()
        return counted

    def _count_rows(self, ticker: str) -> None:
        """Count a symbol's bars frame by frame (inventory.row_counts) on connections of this thread."""
        from liminal_libs import inventory

        reader = None
        try:
            reader = self.store.reader()
            counts = inventory.row_counts(reader, ticker)
            with self._counts_lock:
                self._row_counts[ticker] = (time.monotonic(), counts)
        except Exception:  # noqa: BLE001 - the panel shows no counts; the next read tries again
            pass
        finally:
            with self._counts_lock:
                self._counting.discard(ticker)
            if reader is not None and reader is not self.store:
                reader.close()

    def pruned(self, ticker: str, source: str | None = None) -> dict:
        """A symbol's bad data (baddata.py): each feed's pruned list, newest first, and its counts."""
        from liminal_libs import baddata

        t = ticker.upper()
        with self._panel_lock:  # its own connections: a symbol's list can hold millions of kept bars
            counted = self._pruned_counts.get(t)
            if counted is None or time.monotonic() - counted[0] > PRUNED_COUNTS_TTL_S:
                counted = self._pruned_counts[t] = (time.monotonic(), self._panel_store.pruned_counts(t))
            entries = baddata.entries(self._panel_store, t, None if source in (None, "", "*") else source)
        return {"counts": counted[1], "entries": entries}

    @contextmanager
    def _view(self, q: dict[str, str]):
        """The request's view of the data: filtered (bad data left out) unless it asks unfiltered=1."""
        from .bars import UNFILTERED

        token = UNFILTERED.set(q.get("unfiltered") == "1")
        try:
            yield
        finally:
            UNFILTERED.reset(token)

    def start_derive(self, ticker: str) -> dict:
        """Build a symbol's derived frames now (`derive --tickers`) in its own process: from what
        is stored only, no request to any feed. One build at a time from this app."""
        with self._lock:
            watched = {w["ticker"] for w in self.store.watchlist(ticker=ticker)}
        if ticker.upper() not in watched:
            raise LookupError(f"{ticker} is not on the watchlist")
        if self._derive_proc is not None and self._derive_proc.poll() is None:
            raise RuntimeError("a build of derived frames is already running")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._derive_proc = subprocess.Popen([sys.executable, "-m", "liminal_charts", "derive", "--tickers", ticker.upper()],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             stdin=subprocess.DEVNULL, creationflags=flags)
        return {"started": True, "pid": self._derive_proc.pid}

    def set_fetching(self, body: dict) -> dict:
        """Pause background fetching ({"paused": true, "minutes": n}: at most two hours; the open
        symbol's own fetches go on, so the chart gets the feeds to itself), halt all fetching until
        resumed ({"paused": true, "halt": true}: the open symbol's too), or resume ({"paused":
        false}): then a sync starts at once, as the hourly one would (it starts the feeds not
        fetching; one paused mid-run stopped at its next request)."""
        from liminal_libs import collector

        paused = bool(body.get("paused"))
        with self._lock:
            p = collector.set_pause(self.store, paused, minutes=body.get("minutes"), halt=bool(body.get("halt")))
        started = None
        if not paused and not self.settings.offline and None not in activity.fetching_feeds(self.store.path):
            started = self.ask_pass()  # the fetcher's standing pass now (None: no fetcher in this copy)
        return {"paused": p, "started": started}

    def set_updates_pause(self, body: dict) -> dict:
        """Pause the updates of the symbols not picked ({"days": n}, 1-30) or resume them
        ({"days": 0}): history and the picked symbols (opened, or on a watchlist) go on."""
        from liminal_libs import collector

        days = float(body.get("days") or 0)
        if days and not 1 <= days <= 30:
            raise ValueError("days must be 1-30 (0 resumes)")
        with self._lock:
            return {"updatesPause": collector.set_updates_pause(self.store, days)}

    def _halted(self) -> bool:
        from liminal_libs import collector

        with self._lock:
            return bool((collector.pause_state(self.store) or {}).get("halt"))

    PRIORITY_COOLDOWN = 300  # seconds: a symbol opened again sooner is not asked for again

    def priority(self, ticker: str, profile: int | None = None) -> dict:
        """The chart opened `ticker`: every feed holding it updates it before anything else, and,
        opened by you rather than by the page itself, it goes in `profile`'s register (visits). While a feed it needs (`need`) is not fetching, a run
        for that symbol alone starts now (`sync --tickers`: gaps and new data on each such feed, only
        what each plan has published); on a feed that is fetching, the request waits in the database
        and that feed's run takes the symbol next. Within every feed's limits either way: one fetching
        process per feed at a time (its run lock), and limiters that share their window across processes."""
        from liminal_libs import collector

        from liminal_libs.freshness import expected
        from liminal_libs.sources import feed_profiles

        ticker = ticker.upper()
        now = datetime.now(timezone.utc)
        if self.settings.read_only:  # a copy reading the real data: no visit noted, nothing asked of the feeds
            return {"ticker": ticker, "feeds": [], "state": "offline"}
        with self._lock:
            if profile is not None:  # noted whether or not a feed fetches it
                self.store.visit(profile, ticker, now)
            rows = self.store.watchlist(ticker=ticker)
            feeds = sorted({w["source"] for w in rows})
            if not feeds:
                raise LookupError(f"{ticker} is not on the watchlist")
            profiles = feed_profiles(self.settings)
            if self._watch_everywhere(rows[0], profiles):  # a feed added since (Blue Ocean) takes it from now
                rows = self.store.watchlist(ticker=ticker)
                feeds = sorted({w["source"] for w in rows})
            tiers = {name: self.settings.for_source(name).tier for name in profiles if name in feeds}
            assets = {name: profiles[name].assets for name in tiers}  # a feed without its key serves nothing
            serving = any(r["source"] in tiers and r["asset"] in assets[r["source"]] for r in rows)
            found = collector.needs_fetch(self.store, ticker, tiers, now, assets) if serving else []
            need = [name for name in tiers if name in found]  # the feeds it waits for, in their ranked order
            # A retired symbol (no longer listed): its stored data to look at, nothing new to ask for
            # (the user, 2026-11-02); asked of the fetcher only while a feed's history of it is still
            # incomplete (needs_fetch: up to its last listed day), once, so a source that still has
            # it completes it.
            retired = all(r.get("retired_on") for r in rows)
            if retired and not need:
                return {"ticker": ticker, "feeds": feeds, "state": "retired",
                        "retiredOn": max(str(r["retired_on"]) for r in rows)}
            if serving and not self.settings.offline and not self._halted() and not need:
                # up to date on every feed: nothing new can exist yet (out of session, or not released)
                due = [e.next_due for e in (expected(freshness.hours(r["source"], r["asset"]), tiers[r["source"]], now,
                                                     r.get("exchange") if r["asset"] == "stock" else None)
                                            for r in rows
                                            if r["source"] in tiers) if e.next_due]
                return {"ticker": ticker, "feeds": feeds, "state": "current",
                        "nextDue": min(due).isoformat() if due else None}
            asked = collector.priority_requests(self.store, now).get(ticker)
            if asked is None or (now - asked["at"]).total_seconds() > self.PRIORITY_COOLDOWN:
                collector.request_priority(self.store, ticker, now)
                asked = {"at": now, "runs": 0}
        out = {"ticker": ticker, "feeds": feeds, "need": need, "asked": asked["at"].isoformat()}
        if self.settings.offline:
            return {**out, "state": "offline"}  # a test copy of the app: nothing is fetched
        if self._halted():
            return {**out, "state": "halted"}  # nothing is fetched until the user resumes
        if asked["runs"]:
            return {**out, "state": "recent"}  # the fetcher was asked for it already
        # The fetcher takes it next (the newest ask first): it assesses the work over the plugins
        # and holds the runs of the feeds that deliver it; the page sees the request end (symbolRun).
        req = self.request_symbol({"symbol": ticker, "kinds": ["bars"], "by": "chart"})
        self._symbol_request = (ticker, int(req["id"]))
        with self._lock:
            collector.priority_started(self.store, ticker)
        fetching = activity.fetching_feeds(self.store.path)
        if None in fetching or all(name in fetching for name in need or feeds):
            return {**out, "state": "queued", "request": req["id"]}  # each feed it needs is fetching: their runs take it next
        return {**out, "state": "started", "request": req["id"]}

    def _watch_everywhere(self, row: dict, profiles: dict) -> bool:
        """A stock opened in the chart goes on every set-up feed that can carry it and does not yet
        (watching puts a symbol on every feed that can: one added since, such as Blue Ocean's
        overnight feed, takes the symbols as they are opened). Returns whether any was added."""
        from liminal_libs.sources import supports
        from liminal_libs.symbols import resolve

        if row["asset"] != "stock":
            return False
        held = {w["source"] for w in self.store.watchlist(ticker=row["ticker"])}
        spec = resolve(f"{row['ticker']}::{row['exchange']}" if row.get("exchange") else row["ticker"])
        added = [name for name, p in profiles.items()
                 if name not in held and "stock" in p.assets and supports(name, spec)]
        for name in added:
            self.store.watch_spec(name, spec)
        return bool(added)

    def exchanges(self) -> list[dict]:
        with self._lock:
            df = self.store.exchanges()
        return json.loads(df.to_json(orient="records"))


def _sector(ticker: str, profile: dict | None, asset: str, close: float | None = None,
            member: dict | None = None) -> dict:
    """Sector, industry and market cap for the market map (stocks with reference data). The cap is
    this share class's own (its shares outstanding x price) where known: a company with two listed
    classes (GOOG, GOOGL) would otherwise be drawn twice its size. An S&P 500 member takes its
    sector from the index list's GICS classification; its industry keeps the map's own name unless
    that belongs to another sector, when the GICS sub-industry is used."""
    from .sectors import SECTORS, classify

    profile = profile or {}
    if asset != "stock" or not (profile or member):
        return {"sector": None, "industry": None, "marketCap": None}
    sector, industry = classify(ticker, profile.get("sic_code"), profile.get("sic_description"))
    basis = "SIC"
    if member and member.get("sector") in SECTORS:
        if member["sector"] != sector:
            industry = member.get("industry") or industry
        sector, basis = member["sector"], "GICS (S&P 500 list)"
    shares = profile.get("class_shares")
    cap = shares * close if shares and close else profile.get("market_cap")
    return {"sector": sector, "industry": industry, "sectorBasis": basis, "marketCap": cap,
            "companyCap": profile.get("market_cap")}


NOT_COLLECTING = ("blocked", "closed", "retired")


def feed_labels() -> dict[str, str]:
    """How the page names each feed (registry.label, live.LIVE_LABELS): a feed of a plugin's keys
    by that plugin, alpaca (blue ocean), never as a source of its own; each poll of /api/activity."""
    from liminal_libs.sources import providers, registry

    return {**{n: registry.label(n) for n in providers()}, **live.LIVE_LABELS}


def _symbol_state(rows: dict[str, dict], order: list[str]) -> dict | None:
    """One data state for a symbol watched on several feeds (the symbol list's icon): that of the
    best-ranked feed holding its data (the one a merged chart shows first); with none holding
    any, the most pressing of the feeds collecting it. Not whichever feed came last: AMD read "up
    to date" from a feed that leaves its history to another while that one was still backfilling."""
    for feed in order:
        if feed in rows:
            return rows[feed]
    collecting = [r for r in rows.values() if r["state"] not in NOT_COLLECTING] or list(rows.values())
    return min(collecting, key=lambda r: activity.STATE_RANK.get(r["state"], 9), default=None)


def _change(snapshot: dict, ticker: str) -> float | None:
    """Last session's change in percent from local daily bars (any asset class)."""
    d = snapshot.get(ticker)
    return (d["close"] / d["prevClose"] - 1) * 100 if d and d.get("prevClose") else None


FUTURES_MOVE_BARS = 5  # day bars read a front: its session's bar and the one before, past newer ones


def stored_ticker(symbol: str) -> str:
    """The name a symbol is stored under, from the text typed (SPCX::NASDAQ: SPCX; TSE:7203: 7203.T;
    EUR/USD: EURUSD), or the text itself when the library cannot read it (the fetcher then says why)."""
    from liminal_libs.symbols import resolve

    try:
        return resolve(symbol).ticker
    except ValueError:
        return symbol


def _futures_moves(store) -> Callable[[dict], dict]:
    """For the market maps: a continuous future's (a watch row: source, source_ticker, rank) exchange
    and type from the directory, and its close and change from the contract it holds now (its daily
    bars: the continuous series is built from contracts, not stored as one), all on one session: the
    newest one most fronts have a bar for, against the session most have before it. The fetcher
    reaches products at different times, so their newest bars were days apart (2026-10-29: CL's from
    Oct 23 beside ES's from Oct 27 and GC's from Oct 28, one map). A front without that session's bar
    has no change (grey), its newest bar's day in `day`. Read once (one query for the fronts, a few
    day bars a front): call it inside the store's lock."""
    fronts = store.front_contracts()
    listed = {r["ticker"]: r for r in store.directory(kind="future")}
    bars: dict[str, dict] = {}
    for src in {s for s, _, _ in fronts}:
        bars[src] = store.last_days(src, [c for (s, _, _), c in fronts.items() if s == src], FUTURES_MOVE_BARS)
    day = lambda ts: datetime.fromtimestamp(ts / 1000, timezone.utc).date()  # noqa: E731 (bars start at ET midnight)
    held = [[day(r[0]) for r in rows] for b in bars.values() for rows in b.values()]
    newest = Counter(d[0] for d in held if len(d) > 1)
    session = max(newest, key=lambda d: (newest[d], d)) if newest else None
    before = Counter(d[d.index(session) + 1] for d in held if session in d[:-1])
    prior = max(before, key=lambda d: (before[d], d)) if before else None

    def of(w: dict) -> dict:
        product = w.get("source_ticker") or ""
        front = fronts.get((w.get("source"), product, w.get("rank") or 1))
        rows = bars.get(w.get("source"), {}).get(front.upper(), []) if front else []
        days = [day(r[0]) for r in rows]
        i = days.index(session) if session in days[:-1] else None
        move = (rows[i][1] / rows[i + 1][1] - 1) * 100 if i is not None and days[i + 1] == prior and rows[i][1] and rows[i + 1][1] else None
        row = listed.get(product) or {}
        return {"exchange": row.get("exchange"), "type": row.get("type"), "front": front,
                "close": rows[i][1] if i is not None else rows[0][1] if rows else None, "changePct": move,
                "session": session.isoformat() if move is not None else None,
                "day": days[0].isoformat() if days else None}
    return of


def _market(snapshot: dict, ticker: str, watch: dict) -> dict | None:
    """Last close, change, the last session's volume and the 30-session average dollar volume, from
    local daily bars."""
    d = snapshot.get(ticker)
    if not d or watch.get("asset", "stock") != "stock":
        return None  # dollar volume only means something for shares
    change = (d["close"] / d["prevClose"] - 1) * 100 if d["prevClose"] else None
    return {"close": d["close"], "changePct": change, "dollarVolume": d["dollarVolume"], "volume": d.get("volume")}


def _model(q: dict[str, str]) -> Model:
    """Model from query parameters: model, basis, per, window, order, gaps, causal."""
    return Model(
        kind=q.get("model", "price"), basis=q.get("basis", "price"), per=q.get("per", "hour"),
        window=int(q.get("window", 9)), order=int(q.get("order", 3)), reset_gaps=q.get("gaps", "1") != "0",
        causal=q.get("causal", "0") == "1",
    ).validate()


def _level(q: dict[str, str]) -> tuple[str, int]:
    """The chart level a request asks for (period, span): one the chart draws (window.LEVELS, the
    series path's intervals too), checked before anything is read (ValueError: 400)."""
    from .window import check_level

    return check_level(q.get("period", "minute"), int(q.get("span", 1)))


def _adjust(value: str | None) -> str:
    value = value or "none"
    if value not in ("none", "difference", "ratio"):
        raise ValueError("adjust must be none, difference or ratio")
    return value


def make_handler(app: ChartApp, port_ref: list[int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "massive-chart"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
            web.send(self, status, body, ctype, headers)

        def _profile(self) -> int | None:
            """The profile this browser signed in as (its PROFILE_COOKIE), if it still exists."""
            found = web.profile_cookie(self)
            return found if found is not None and app.profile_exists(found) else None

        def _signed_in(self, data, profile_id: int | None) -> None:
            """Answer with the profile cookie set (signed in) or cleared (None: signed out)."""
            cookie = (f"{PROFILE_COOKIE}={profile_id}; Path=/; SameSite=Strict; Max-Age=31536000"
                      if profile_id else f"{PROFILE_COOKIE}=; Path=/; SameSite=Strict; Max-Age=0")
            self._send(HTTPStatus.OK, json.dumps(data).encode(), "application/json", {"Set-Cookie": cookie})

        def _stream(self, q: dict[str, str]) -> None:
            if not q.get("since"):
                return self._json({"error": "since is required"}, HTTPStatus.BAD_REQUEST)
            app.bars({**{k: v for k, v in q.items() if k != "since"}, "limit": "1"})  # bad parameters fail here, as JSON
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                for event in app.stream(q):
                    self.wfile.write(event)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the page went away
            except Exception:  # noqa: BLE001 - the answer has begun: logged, and the stream ends (the page opens another)
                traceback.print_exc()

        def _json(self, data, status: int = 200) -> None:
            try:
                body = json.dumps(data, allow_nan=False)
            except ValueError:  # a NaN slipped in (pandas' missing value): sent as null, not a broken endpoint
                body = json.dumps(finite(data), allow_nan=False)
            self._send(status, body.encode(), "application/json")

        def _failed(self, err: Exception) -> None:
            """An error no route expects: logged and answered (500; 503 while another process holds
            the database), not a dropped connection the page cannot tell from the app being gone."""
            if isinstance(err, sqlite3.OperationalError) and "readonly" in str(err) and app.settings.read_only:
                return self._json({"error": "this copy of the app reads only (LIMINAL_READONLY): nothing is saved"},
                                  HTTPStatus.CONFLICT)
            traceback.print_exc()
            if isinstance(err, sqlite3.OperationalError) and "locked" in str(err):
                return self._json({"error": "the database is busy (another process is writing to it): ask again shortly"},
                                  HTTPStatus.SERVICE_UNAVAILABLE)
            self._json({"error": f"the chart app failed: {type(err).__name__}: {err}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _local(self) -> bool:
            return web.local_only(self)  # only requests addressed to this machine

        def _from_this_app(self) -> bool:
            return web.from_this_app(self)  # only this app's own pages reach the API

        def _write(self, method: str) -> None:
            """POST/PUT/DELETE for layouts. The custom header can't be sent cross-site without a
            CORS preflight, which this server never grants, so other web pages can't write."""
            if not self._local() or not self._from_this_app():
                return
            if self.headers.get(WRITE_HEADER) != "1":
                return self._send(HTTPStatus.FORBIDDEN, b"missing request header", "text/plain")
            raw_length = self.headers.get("Content-Length") or "0"
            length = int(raw_length) if raw_length.isdecimal() else -1  # not a size: refused, never read
            if length < 0:
                return self._json({"error": "bad Content-Length"}, HTTPStatus.BAD_REQUEST)
            if length > MAX_BODY:
                return self._json({"error": "request too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if method != "DELETE" else {}
                if not isinstance(body, dict):
                    raise ValueError("body must be a JSON object")
                if urlparse(self.path).path == web.UI_PATH and method == "POST":  # the machine's, not a profile's (web.py)
                    return self._json(web.save_ui(body))
                parts = urlparse(self.path).path.rstrip("/").split("/")
                me = self._profile() or FIRST_PROFILE  # without a cookie (the command line, tests): the first
                page = self.headers.get(PROFILE_HEADER)
                if page is not None and page != str(me) and parts[:3] != ["", "api", "profiles"]:
                    # the page loaded as another profile than this browser is signed in as now
                    return self._json({"error": "this page is another profile's: reload it", "reload": True},
                                      HTTPStatus.CONFLICT)
                if parts[:3] == ["", "api", "profiles"]:
                    if method == "POST" and len(parts) == 3:
                        return self._json(app.create_profile(body), HTTPStatus.CREATED)
                    if method == "POST" and parts[3:] == ["sign-in"]:
                        found = app.sign_in(int(body.get("id", 0)))
                        return self._signed_in(found, found["id"])
                    if method == "POST" and parts[3:] == ["sign-out"]:
                        return self._signed_in({"signedOut": True}, None)
                    if method == "PUT" and len(parts) == 4:
                        return self._json(app.rename_profile(int(parts[3]), body))
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                if method == "POST" and parts == ["", "api", "watch"]:
                    ticker = parse_qs(urlparse(self.path).query).get("ticker", [None])[-1]
                    if not ticker:
                        return self._json({"error": "ticker is required"}, HTTPStatus.BAD_REQUEST)
                    return self._json(app.watch(ticker), HTTPStatus.CREATED)
                if method == "POST" and parts == ["", "api", "updates-pause"]:
                    return self._json(app.set_updates_pause(body))
                if method == "POST" and parts == ["", "api", "requests"]:
                    try:
                        return self._json(app.request_symbol(body), HTTPStatus.ACCEPTED)
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if method == "DELETE" and len(parts) == 4 and parts[:3] == ["", "api", "requests"]:
                    return self._json(app.cancel_request(int(parts[3])))
                if method == "PUT" and parts == ["", "api", "watchlists"]:
                    return self._json(app.save_watchlists(body, me))
                if method == "POST" and parts == ["", "api", "priority"]:
                    query = parse_qs(urlparse(self.path).query)
                    ticker = query.get("ticker", [None])[-1]
                    if not ticker:
                        return self._json({"error": "ticker is required"}, HTTPStatus.BAD_REQUEST)
                    visit = query.get("visit", [None])[-1] == "1"  # you opened it (not the page by itself)
                    return self._json(app.priority(ticker, me if visit else None), HTTPStatus.ACCEPTED)
                if method == "POST" and parts == ["", "api", "fetching"]:
                    return self._json(app.set_fetching(body))
                if method == "POST" and parts == ["", "api", "ai", "evaluate"]:
                    from liminal_libs.errors import BudgetExhausted, NotEntitled, SourceError, Unauthorized

                    try:
                        return self._json(app.evaluate(body), HTTPStatus.CREATED)
                    except (RuntimeError, BudgetExhausted) as e:  # offline, or a cap reached
                        return self._json({"error": str(e)}, HTTPStatus.TOO_MANY_REQUESTS if isinstance(e, BudgetExhausted)
                                          else HTTPStatus.CONFLICT)
                    except (Unauthorized, NotEntitled) as e:
                        return self._json({"error": str(e)}, HTTPStatus.FORBIDDEN)
                    except SourceError as e:
                        return self._json({"error": str(e)}, HTTPStatus.BAD_GATEWAY)
                if method == "POST" and parts == ["", "api", "plugins", "plan"]:
                    try:
                        return self._json(app.set_plan(str(body.get("plugin") or ""), str(body.get("variable") or ""),
                                                       body.get("value") or None))
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if method == "POST" and parts == ["", "api", "plugins", "key"]:
                    value = body.get("value")
                    try:
                        return self._json(app.set_key(str(body.get("plugin") or ""), str(body.get("variable") or ""),
                                                      None if value is None else str(value)))
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                    except LookupError as e:
                        return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
                if method == "POST" and parts == ["", "api", "plugins", "store"]:
                    try:
                        return self._json(app.set_store(str(body.get("plugin") or ""), str(body.get("type") or ""),
                                                         body.get("engine") or None))
                    except LookupError as e:
                        return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
                    except (ValueError, RuntimeError) as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if method == "POST" and parts == ["", "api", "plugins", "rescan"]:
                    name = parse_qs(urlparse(self.path).query).get("name", [None])[-1] or None
                    try:
                        return self._json(app.start_rescan(name), HTTPStatus.ACCEPTED)
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if method == "POST" and parts[:3] == ["", "api", "plugins"] and parts[3:] in (["recheck"], ["pause"]):
                    q = parse_qs(urlparse(self.path).query)
                    name = q.get("name", [""])[-1]
                    try:
                        if parts[3] == "recheck":
                            return self._json(app.start_recheck(name), HTTPStatus.ACCEPTED)
                        return self._json(app.pause_publisher(name, q.get("on", ["1"])[-1] == "1"))
                    except LookupError as e:
                        return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if method == "POST" and parts == ["", "api", "derive"]:
                    ticker = parse_qs(urlparse(self.path).query).get("ticker", [None])[-1]
                    if not ticker:
                        return self._json({"error": "ticker is required"}, HTTPStatus.BAD_REQUEST)
                    try:
                        return self._json(app.start_derive(ticker), HTTPStatus.ACCEPTED)
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if method == "POST" and parts in (["", "api", "scan"], ["", "api", "fetch"]):
                    ticker = parse_qs(urlparse(self.path).query).get("ticker", [None])[-1] if parts[2] == "fetch" else None
                    if parts[2] == "fetch" and not ticker:
                        return self._json({"error": "ticker is required"}, HTTPStatus.BAD_REQUEST)
                    try:
                        return self._json(app.start_scan(ticker), HTTPStatus.ACCEPTED)
                    except RuntimeError as e:
                        return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
                if parts[:3] == ["", "api", "trading"]:  # paper only: live trading is off
                    if method == "POST" and parts[3:] == ["orders"]:
                        return self._json(app.trade_place(body), HTTPStatus.CREATED)
                    if method == "DELETE" and len(parts) == 5 and parts[3] == "orders":
                        return self._json(app.trade_cancel(int(parts[4])))
                    if method == "POST" and parts[3:] == ["reset"]:
                        return self._json(app.trade_reset(body))
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                if parts[:3] != ["", "api", "layouts"]:
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                if method == "POST" and len(parts) == 3:
                    return self._json(app.create_layout(body, me), HTTPStatus.CREATED)
                if len(parts) == 4:
                    layout_id = int(parts[3])
                    if method == "PUT":
                        return self._json(app.update_layout(layout_id, body, me))
                    if method == "DELETE":
                        return self._json(app.delete_layout(layout_id, me))
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except LayoutLimit as e:
                return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
            except (ValueError, json.JSONDecodeError) as e:
                return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
            except LookupError as e:
                return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            except ConnectionError:
                pass  # the page went away
            except Exception as e:  # noqa: BLE001 - answered, not dropped (_failed)
                return self._failed(e)

        def do_POST(self) -> None:
            with app.using():
                self._write("POST")

        def do_PUT(self) -> None:
            with app.using():
                self._write("PUT")

        def do_DELETE(self) -> None:
            with app.using():
                self._write("DELETE")

        def do_GET(self) -> None:
            with app.using():  # the app awake (its stores open) for this request, and not released during it
                self._get()

        def _get(self) -> None:
            if not self._local():
                return
            url = urlparse(self.path)
            if url.path.startswith("/api/") and not self._from_this_app():
                return
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == web.UI_PATH:  # the UI's preferences: the page painted in the theme (alone, the integrator's own page)
                    return self._json(web.ui_preferences())
                signed = self._profile()
                me = signed or FIRST_PROFILE  # without a cookie (the command line, tests): the first
                if url.path == "/api/profiles":
                    return self._json(app.profiles(signed))
                if url.path == "/api/register":
                    return self._json(app.register(me))
                if url.path == "/api/layouts":
                    return self._json(app.layouts(me))
                if url.path == "/api/watchlists":
                    return self._json(app.watchlists(me))
                if url.path.startswith("/api/layouts/"):
                    return self._json(app.layout(int(url.path.rsplit("/", 1)[1]), me))
                if url.path == "/api/symbols":
                    return self._json(app.symbols())
                if url.path == "/api/meta":
                    return self._json(app.meta(q["source"], q["ticker"], q.get("adjust"), q.get("auto") == "1"))
                if url.path == "/api/bars":
                    with app._view(q):
                        return self._json(app.bars(q))
                if url.path == "/api/stream":
                    with app._view(q):
                        return self._stream(q)
                if url.path == "/api/profile":
                    with app._view(q):
                        return self._json(app.profile(q))
                if url.path == "/api/info":
                    return self._json(app.info(q["source"], q["ticker"], q.get("adjust")))
                if url.path == "/api/interface":  # the frame: this app, the plugins attached and their API
                    return self._json({**app.frame.interface(), "attached": app.frame.plugins()})
                if url.path == "/api/deps":
                    return self._json(app.deps())
                if url.path == "/api/directory":
                    return self._json(app.directory())
                if url.path == "/api/announcements":
                    return self._json(app.announcements())
                if url.path == "/api/gov":
                    return self._json(app.gov())
                if url.path == "/api/resources":
                    return self._json(app.resources(q.get("span", "recent")))
                if url.path == "/api/venues":
                    return self._json(app.venues())
                if url.path == "/api/plugins":
                    return self._json(app.plugins())
                if url.path == "/api/ai/evaluations":
                    return self._json(app.evaluations(q))
                if url.path == "/api/venue":
                    return self._json(app.venue(q.get("id", "")))
                if url.path == "/api/markets":
                    return self._json(app.markets())
                if url.path == "/api/calendar":
                    return self._json(app.calendar(q))
                if url.path == "/api/inventory":
                    return self._json(app.inventory(q["ticker"]))
                if url.path == "/api/pruned":
                    return self._json(app.pruned(q["ticker"], q.get("source")))
                if url.path == "/api/future":
                    with app._view(q):
                        return self._json(app.future(q))
                if url.path == "/api/range":
                    with app._view(q):
                        return self._json(app.range_stats(q))
                if url.path == "/api/schemes":
                    return self._json(app.schemes())
                if url.path == "/api/trading":
                    return self._json(app.trading())
                if url.path == "/api/scheme":
                    with app._view(q):
                        return self._json(app.scheme(q))
                if url.path == "/api/replay":
                    with app._view(q):
                        return self._json(app.replay(q))
                if url.path == "/api/icon":
                    icon = app.icon(q["ticker"])
                    if icon is None:
                        return self._send(HTTPStatus.NOT_FOUND, b"no icon", "text/plain")
                    mime, data = icon
                    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                        return self._send(HTTPStatus.NOT_FOUND, b"no icon", "text/plain")  # no SVG: it can carry script
                    return self._send(HTTPStatus.OK, data, mime)
                if url.path == "/api/activity":
                    # `open` writes (the live recorder streams it): only from this app's own pages
                    mine = self.headers.get("X-Liminal-Charts") == "1"
                    return self._json(app.activity({**q, "open": q.get("open") if mine else None}))
                if url.path == "/api/exchanges":
                    return self._json(app.exchanges())
                if url.path == "/api/audits":
                    return self._json(app.audit_summary(q.get("ticker")))
                if url.path == "/api/queue":
                    return self._json(app.queue(q))
                if url.path == "/api/requests":
                    return self._json(app.requests(q))
                if url.path == "/api/plugin-fetching":
                    return self._json(app.plugin_fetching(q["name"]))
                if url.path == "/api/plugin-stores":
                    return self._json(app.plugin_stores(q["name"]))
                if url.path == "/api/datasets":
                    return self._json(app.datasets())
                if url.path == "/api/dataset/series":
                    return self._json(app.dataset_series(q["name"]))
                if url.path == "/api/dataset/values":
                    return self._json(app.dataset_values(q["name"], q["series"]))
            except (KeyError, ValueError) as e:
                return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
            except LookupError as e:
                return self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            except Busy as e:
                return self._json({"error": str(e)}, HTTPStatus.SERVICE_UNAVAILABLE)
            except ConnectionError:
                return  # the page went away
            except Exception as e:  # noqa: BLE001 - answered, not dropped (_failed)
                return self._failed(e)
            return self._static(url.path)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            if rel == "index.html" and app.mode == "fetcher":  # the fetcher's own page: nobody's, no profile
                return self._send(HTTPStatus.OK, fetcher_page(), "text/html; charset=utf-8")
            if rel == "index.html" and self._profile() is None:
                rel = "login.html"  # no profile chosen in this browser: pick or make one first
            web.static_file(self, rel)

    return Handler


def _log_to(db_path):
    """The app's log (logs/liminal.log beside the database), as the fetcher writes it."""
    from liminal_libs.logs import log_to

    return log_to(db_path)


RESCAN_AFTER_START_S = 10.0  # the start's rescan waits for the first pages' requests


def _prepare(app: ChartApp, rescan: bool) -> None:
    """Nothing runs for nobody (the user, 2026-11-01): the start's rescan, the world's calendars
    and the Resources sampler wait for the first page, and the app goes quiet (its stores closed,
    its hosts let go) once no page has been in touch for a while."""
    if rescan:
        def rescan_now() -> None:
            try:
                app.start_rescan()
            except (RuntimeError, LookupError) as e:  # offline, halted, one running: not this start
                print(f"plugins not rescanned at the start: {e}", flush=True)

        app.on_wake(lambda: threading.Timer(RESCAN_AFTER_START_S, rescan_now).start())
    from . import world
    app.on_wake(lambda: threading.Thread(target=world.warm, daemon=True).start())  # world calendars take a few seconds
    threading.Thread(target=app.watch_quiet, name="quiet", daemon=True).start()


def make_server(app: ChartApp, port: int) -> ThreadingHTTPServer:
    """The HTTP server of `app` on 127.0.0.1:`port`, or a free one when something answers there
    already (web.listening: a bind alone cannot tell on Windows, where the fetcher's page took the
    integrator's front's port, 8785, and the front proxied to itself)."""
    port_ref = [port]
    if port and web.listening(port):
        port = 0
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    except OSError:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, port_ref))  # port taken: pick a free one
    return httpd


def serve(db_path: Path, port: int = 8765, open_browser: bool = True, priority: list[str] | None = None,
          settings: Settings | None = None, reload_settings: Callable[[], Settings] | None = None,
          rescan: bool = False) -> None:
    """The chart app alone (`gui --alone`): its page and the data API in this one process, no
    fetcher in it (nothing is collected: a read-only copy, a test). The app proper is the front
    (gui/front.py), with the API in the integrator's process (serve_integrator). With `rescan` every provider
    plugin set up here scans its capabilities and plan again shortly after the first page."""
    store = Store(db_path, check_same_thread=False)
    app = ChartApp(store, priority, settings, reload_settings=reload_settings)
    app.frame.attach_plugins()  # alone, the charts app holds every plugin loaded (the super app)
    _prepare(app, rescan)
    httpd = make_server(app, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"chart app running alone at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.close()


def serve_integrator(settings: Settings, port: int, *, link, open_browser: bool = False, log=None,
                     stop: threading.Event | None = None, rescan: bool = False,
                     reload_settings: Callable[[], Settings] | None = None, until_quiet: bool = False,
                     frame=None) -> str:
    """The integrator's page and the data API in the integrator's own process (the user, 2026-11-03:
    "for the webserver / ports, i just integrator, and charts": two listeners, this one at
    LIMINAL_FETCHER_PORT and the chart front's), on 127.0.0.1:`port` (a free one when something
    answers there), until `stop` is set or Ctrl+C; with `until_quiet` also once no page has been in
    touch for a while (an integrator the chart front started for itself). The data is read here;
    `link` is this process's hold on the fetcher host over the pipe (liminal_integrator.service's
    FetcherLink: a page in touch keeps the fetcher attached, started if none answers; its status, a
    wake and a pass go over the pipe). `frame` is the integrator as an app of the frame
    (liminal_integrator.app.IntegratorApp): this process's hold on the stock-data plugins, attached here
    with the integrator's login, and what /api/interface describes. Returns the URL served."""
    log = log or _log_to(settings.db_path)
    stop = stop or threading.Event()
    store = Store(settings.db_path, check_same_thread=False)
    app = ChartApp(store, list(settings.source_priority), settings, reload_settings=reload_settings, mode="fetcher",
                   fetcher=link, touch=link.touch, frame=frame)
    app.frame.attach_plugins("integrator")  # the integrator's page holds the stock-data plugins
    _prepare(app, rescan)
    httpd = make_server(app, port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    log(f"integrator: its page and the data API at {url}; the fetcher runs while a page is in touch")
    print(f"the integrator's page at {url}; the fetcher runs while a page is in touch  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    threading.Thread(target=link.watch_quiet, args=(stop, stop.set if until_quiet else None), name="integrator-quiet",
                     daemon=True).start()

    def stopping() -> None:
        stop.wait()
        httpd.shutdown()

    threading.Thread(target=stopping, name="integrator-stop", daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
        app.close()
        link.let_go()
    return url
