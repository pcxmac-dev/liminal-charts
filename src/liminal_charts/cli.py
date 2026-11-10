"""Command-line interface: `liminal <command> (python -m liminal_charts)`."""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from liminal_libs import analysis, charts, collector, config
from liminal_libs.config import ConfigError, Settings, load_settings
from liminal_libs.storage.feeds import TicksBlocked
from liminal_libs.errors import NotEntitled, RateLimited, SourceError, Unauthorized
from liminal_libs.sessions import ET
from liminal_libs.symbols import resolve
from liminal_libs.sources import DataSource, build_source, feed_profiles, is_archive, providers, supports
from liminal_libs.storage import COMPOSITE, Store, feeds_backup_dir
from liminal_libs.host import attach_fetcher, wait_for_fetcher, wait_for_requests  # noqa: F401 (the fetcher's clients' side)
from liminal_libs.logs import LOG_KEEP, LOG_MAX_BYTES, Logger  # noqa: F401
from liminal_libs.proc import hold_until_interrupted
from liminal_libs.sources import feed_closed as _feed_closed
from liminal_libs.stores import HISTORY_BUDGET_S, HISTORY_LOCK, history_due  # noqa: F401

TASK_NAME = "LiminalSync"


# -- helpers -------------------------------------------------------------------

def prune_backups(folder: Path, keep: int) -> list[str]:
    """The retention policy for `admin backup`'s backups (massive-YYYYMMDD-HHMMSS.db, with its
    -feeds and -datasets folders): the newest `keep` stay, older ones go (keep 0: all stay).
    Other files there (the pre-split safety copies) are never touched. Returns those removed."""
    import re
    import shutil

    if keep <= 0 or not folder.is_dir():
        return []
    sets = sorted(f for f in folder.glob("massive-*.db") if re.fullmatch(r"massive-\d{8}-\d{6}\.db", f.name))
    removed = []
    for f in sets[:-keep]:
        for extra in (f.with_name(f.stem + "-feeds"), f.with_name(f.stem + "-datasets")):
            if extra.is_dir():
                shutil.rmtree(extra)
        for side in (f, f.with_name(f.name + "-wal"), f.with_name(f.name + "-shm")):
            if side.exists():
                side.unlink()
        removed.append(f.name)
    return removed


from liminal_libs.proc import pid_alive  # noqa: E402,F401  (kept here for callers that import it from cli)

_HELD: set[str] = set()  # run locks this process holds


class Busy(SystemExit):
    """The run lock is another process's: a run of that feed (or a move of the data) is going on."""


@contextmanager
def run_lock(db_path: Path, feed: str | None = None) -> Iterator[None]:
    """One downloading run per feed at a time (`feed`), so two runs can never double a feed's
    requests or fetch its symbols twice, while different feeds fetch side by side: their limits
    are the machine's, every process counting in the same windows (ratelimit.MachineLedger), and
    each writes a file of its own. One lock for every feed made the symbol opened in the chart wait
    hours behind Massive's pass for Alpaca's and Blue Ocean's turns. Without `feed`, the whole
    database's: a move of the data, which no run may write through; taken only while no feed's run
    holds its lock, and a feed's run gives way to it (each checks the other after taking its own,
    so the two never both go on). The locks name their owners by id and start (proc.py): a run
    that crashed, or whose id the system gave to another process since, does not hold one; one
    that is alive does, however long it has run (a laptop asleep mid-sync wakes holding it)."""
    from liminal_libs import proc

    lock, whole = proc.run_lock_path(db_path, feed), proc.run_lock_path(db_path)
    if str(lock) in _HELD:  # this process holds it already
        yield
        return
    if not proc.acquire(lock):
        raise Busy(f"another run is in progress (lock file {lock}); exiting")
    _HELD.add(str(lock))
    try:
        if feed is not None and str(whole) not in _HELD and proc.holder(whole) is not None:
            raise Busy(f"the whole database is locked (lock file {whole}): its data is being moved, or a run "
                       "from before the feeds ran apart is going on; exiting")
        if feed is None:
            busy = sorted(f for f, pid in proc.run_holders(db_path).items() if f is not None and pid != os.getpid())
            if busy:
                raise Busy(f"feeds are fetching ({', '.join(busy)}); exiting")
        yield
    finally:
        _HELD.discard(str(lock))
        proc.release(lock)


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _budget(settings: Settings, store: Store, source: str) -> int:
    left_today = settings.max_requests_per_day - store.requests_today(source)
    return max(0, min(settings.max_requests_per_run, left_today))


@contextmanager
def api_run(args, settings: Settings, command: str, lock: bool = True) -> Iterator[tuple[DataSource, Store, Logger]]:
    """Open the store, take the lock, build a source with a hard request cap, and audit the run.
    Without `lock` (a capability scan: a few requests) it runs beside a sync: the rate windows and
    the daily caps are the machine's, held for every process alike."""
    settings = settings.for_source(args.source)  # each feed has its own plan, rate and caps
    log = Logger(settings.db_path.parent / "logs" / "liminal.log")
    if settings.offline:
        with Store(settings.db_path) as store:
            print(f"offline (LIMINAL_OFFLINE): nothing sent to {args.source}")
            yield None, store, log  # type: ignore[misc]
        return
    with Store(settings.db_path) as store, (run_lock(settings.db_path, args.source) if lock else nullcontext()):
        if lock:
            store.close_abandoned_runs(args.source)  # we hold its lock, so a run of it still "running" died
        from liminal_libs import killswitch

        kill = killswitch.killed(store, args.source, "fetch")
        if kill:  # the kill policy: nothing is sent to a killed feed until the kill ends or the user lifts it
            log(f"{args.source}: killed until {kill['until'][:16].replace('T', ' ')} UTC ({kill['why']}); nothing sent")
            store.feed_event(args.source, "fetch", "skipped", detail=f"killed until {kill['until']}")
            yield None, store, log  # type: ignore[misc]
            return
        budget = _budget(settings, store, args.source)
        if budget == 0:
            log(f"daily request cap of {settings.max_requests_per_day} reached for {args.source}; nothing sent")
            yield None, store, log  # type: ignore[misc]
            return
        source = build_source(args.source, settings, request_budget=budget)
        run_id = store.start_run(args.source, command)
        args._run_id = run_id
        client = getattr(source, "client", None)
        if client is not None:  # record usage as it happens, not only at the end
            def on_request(n: int) -> None:
                store.set_run_requests(run_id, n)
                store.count_request(args.source)  # on the day it is made (the daily cap)
            client.on_request = on_request
            _share_limits(client, store, args.source, log)
            # background syncs stop when paused; every run (the open symbol's, a scan) when halted;
            # a run under a host (`host feed-NAME`) when its clients are gone (the app no longer in use)
            client.should_stop = _pause_check(store, halt_only=command != "sync" or bool(getattr(args, "tickers", None)),
                                              stop=getattr(args, "_stop", None))
        log(f"{command}: source={args.source} tier={settings.tier.name} "
            f"rate={settings.tier.requests_per_minute or 'unlimited'}/min cap={budget} requests")
        status, message = "ok", ""
        try:
            yield source, store, log
        except collector.StopRun as e:
            status, message = "stopped", str(e)
            log(f"stopped: {e}")
            if e.strike:
                killswitch.strike(store, args.source, "fetch", str(e), log=log, at_least_s=killswitch.retry_after_s(e))
        except SourceError as e:  # the run as a whole failed on the server's side, or was refused
            status, message = "error", str(e)
            log(f"error: {e}")
            if not isinstance(e, NotEntitled) and (killswitch.is_refusal(e, e.status) or e.status == 0 or e.status >= 500):
                killswitch.strike(store, args.source, "fetch", str(e), log=log, at_least_s=killswitch.retry_after_s(e))
        except Exception as e:
            status, message = "error", repr(e)
            raise
        except BaseException as e:  # Ctrl+C, or the process told to exit: not a run that went through
            status, message = "interrupted", type(e).__name__
            raise
        finally:
            store.finish_run(run_id, source.requests_made, getattr(args, "_rows", 0), status, message)
            store.feed_event(args.source, "fetch", {"ok": "ok", "stopped": "stopped"}.get(status, "failed"),
                             seconds=source.requests_made, detail=f"{command}: {message}")
            log(f"{command}: {source.requests_made} requests used")


HOST_STOPPED = "the app is no longer in use: this run stops here (resumed at the next)"


def _pause_check(store: Store, every: float = 2.0, halt_only: bool = False, stop=None):
    """A client's should_stop: the user's pause or halt (collector.pause_state), read at most every
    `every` s. With `halt_only` (a run for the open symbol, a scan) a pause lets it go on. With
    `stop` (a host's event, host.py) the run stops once the host's clients are gone."""
    seen = [0.0, None]

    def check() -> str | None:
        if stop is not None and stop.is_set():
            return HOST_STOPPED
        if time.monotonic() - seen[0] >= every:
            seen[0] = time.monotonic()
            p = collector.pause_state(store)
            seen[1] = None if not p or halt_only and not p.get("halt") else \
                "fetching halted by you" if p.get("halt") else f"fetching paused by you until {p['until'][11:16]} UTC"
        return seen[1]
    return check


def _share_limits(client, store: Store, name: str, log) -> None:
    """The feed's request window continues from earlier runs (they share it through the
    database), and every wait, for the window or for a server's Retry-After, is recorded for the
    app to show with the limit behind it."""
    import json
    import time

    from liminal_libs.ratelimit import KvLedger, wait_key

    limiter = client._limiter

    def waiting(seconds: float, reason: str) -> None:
        store.kv_set(wait_key(name), json.dumps(
            {"until": round(time.time() + seconds, 1), "seconds": round(seconds, 1), "reason": reason}))
        if seconds >= 5:
            log(f"{name}: waiting {seconds:.0f} s ({reason})")

    limiter.share(KvLedger(store, name))
    limiter.on_wait = lambda s: waiting(s, f"limit: {limiter.describe()}")
    for asset, lim in getattr(client, "class_limiters", {}).items():  # Massive: each class's own window
        lim.share(KvLedger(store, f"{name}:{asset}"))
        lim.on_wait = (lambda lm, a: lambda s: waiting(s, f"{a} limit: {lm.describe()}"))(lim, asset)
    client.on_retry = lambda s, status: waiting(s, f"server answered {status}; trying again")


# -- commands ------------------------------------------------------------------

def _watch_line(w: dict) -> str:
    symbol = f"{w['ticker']}::{w['exchange']}" if w["exchange"] else w["ticker"]
    via = w["source_ticker"] + (" (inverted)" if w["invert"] else "") if w["source_ticker"] != w["ticker"] else ""
    return (f"{w['source']:8} {symbol:14} {w['asset']:6} {via:20} {w['resolution']:8} "
            f"listed: {str(w['listed_from'] or '?'):10}  backfilled to: {w['backfilled_to'] or '-'}")


def cmd_watch(args, settings: Settings) -> int:
    with Store(settings.db_path) as store:
        for text in args.tickers:
            spec = resolve(text)
            if args.action == "add":
                store.watch_spec(args.source, spec, args.resolution)
                if getattr(args, "every_feed", False):  # also on every other feed that can collect it
                    for feed in providers():
                        if feed != args.source and supports(feed, spec):
                            store.watch_spec(feed, spec, "auto")
            elif args.action == "remove":
                store.unwatch(args.source, [spec.ticker])
        for w in store.watchlist(args.source):
            print(_watch_line(w))
    return 0


TIER_KEY = "tier:massive"


def tier_key(settings: Settings) -> str:
    """The Massive plans in force, every asset class's (its plugin's view of the settings): what a plan
    change is noticed by."""
    tier = settings.for_source("massive").tier
    return ",".join([tier.name, *(f"{a}={t.name}" for a, t in sorted(tier.by_asset))])


def _note_tier(store: Store, settings: Settings, log) -> None:
    """When MASSIVE_TIER changes, what the old plan refused may be allowed now (or the reverse):
    Massive's cached entitlements and the market days it refused are asked again, and each
    symbol's resolution is found again (the old plan's finest was remembered)."""
    if settings.plugin("massive") is None:
        return  # the Massive plugin is not installed (or these settings were made directly): no plan of its to notice
    seen = store.kv_get(TIER_KEY)
    key = tier_key(settings)
    tier = settings.for_source("massive").tier
    if seen == key or (seen == tier.name and not tier.by_asset):
        return
    if seen:
        store.clear_entitlements("massive")
        days = store.clear_denied("massive")
        n = reresolve(store, "massive")
        log(f"massive plan changed ({seen} -> {key}): entitlements, {days} refused days "
            f"and {n} symbols' resolutions asked again")
    store.kv_set(TIER_KEY, key)


def reresolve(store: Store, feed: str) -> int:
    """Set a feed's symbols back to `auto` (found again on the next sync), except futures (their
    contracts decide) and the initializer's bulk symbols (kept at minutes). Returns symbols reset."""
    from liminal_libs.initializer import load_bulk

    bulk = set(load_bulk(store))
    reset = [w["ticker"] for w in store.watchlist(feed)
             if w["resolution"] != "auto" and w["asset"] != "future" and w["ticker"] not in bulk]
    for ticker in reset:
        store.set_resolution(feed, ticker, "auto")
    return len(reset)


def cmd_plan(args, settings: Settings) -> int:
    """Which plan a feed's key has, from what it may ask for: its plugin's capability scan, `capabilities`
    for one feed (default Massive, whose scan probes its stocks plan next to MASSIVE_TIER, which sets
    how far back the collector reaches; the plan found is used wherever you set none)."""
    args.name = args.source
    return cmd_capabilities(args, settings)


def cmd_initialize(args, settings: Settings) -> int:
    """One-time: every listed stock of the exchanges onto the watchlist of every stock feed, and
    every futures product of the CME Group exchanges among them (CME for all four) as its
    continuous chart onto every futures feed, most traded first (initializer.py). Sends no
    requests; the hourly syncs then backfill them in that order, within each feed's caps."""
    from liminal_libs import initializer

    feeds = [name for name, p in providers().items() if "stock" in p.assets]
    futures_feeds = [name for name, p in providers().items() if "future" in p.assets]
    exchanges = tuple(dict.fromkeys(m for e in args.exchanges.split(",") if e.strip()
                                    for m in (initializer.CME_GROUP if e.strip().upper() == "CME" else (e.strip().upper(),))))
    would = "would be " if args.dry_run else ""
    with Store(settings.db_path) as store:
        out = initializer.initialize(store, feeds, exchanges, by=args.by, dry_run=args.dry_run, futures_feeds=futures_feeds)
    stocks = [e for e in exchanges if e not in initializer.CME_GROUP]
    if stocks:
        print(f"{out['listed']:,} listings on {', '.join(stocks)} ({out['ranked']:,} with recent volume), "
              f"ranked by {'traded value' if args.by == 'value' else 'shares traded'}")
        print("most traded: " + ", ".join(out["top"]))
        for feed, n in out["added"].items():
            print(f"  {feed:8} {n:,} {would}added")
        if out["listed"] == 0:
            print("the directory has no listings for those exchanges yet: a sync fills it")
    f = out.get("futures")
    if f is not None:
        print(f"{f['listed']:,} futures products on " + ", ".join(f"{m} {n:,}" for m, n in f["byExchange"].items())
              + " (CME Group), most traded first: " + ", ".join(f["top"][:12]))
        for feed, n in f["added"].items():
            print(f"  {feed:8} {n:,} continuous futures {would}added")
        if f["unreadable"]:
            print(f"  {len(f['unreadable'])} product codes left out (not readable as symbols): {', '.join(f['unreadable'][:10])}")
        if f["listed"] == 0:
            print("the directory has no futures products yet: a sync fills it")
    return 0


def cmd_sync(args, settings: Settings) -> int:
    """Sync the feeds, each within its own limits and holding its own run lock while it downloads.
    The hourly sync (every feed, no --tickers) starts each feed not fetching already in a process
    of its own (dispatch) and does not wait for them: Massive's pass takes hours, and every other
    feed waited for it. With --only, the feed given by --source, here. With --tickers (the chart
    opened them), those symbols only, on every feed not fetching now, one after another; a feed
    that is fetching takes them next in its own run. Then, outside the run locks, the composites
    and derived frames from what arrived (after_sync): they fetch nothing; once an hour for the
    hourly sync, not after each feed's run (--fetch-only, as dispatch starts them)."""
    if getattr(args, "extras_only", False):  # the chart app's pass: its feeds are attached by the app itself
        log = Logger(settings.db_path.parent / "logs" / "liminal.log")
        _fetch_gov(settings, log)
        _stores_pass(settings, log, history=False)
        after_sync(args, settings)
        return 0
    code, ran = _sync(args, settings)
    if ran and not getattr(args, "fetch_only", False):
        after_sync(args, settings)
    return code


def dispatch(settings: Settings, log=None, tickers: set[str] | None = None) -> int:
    """`sync` run by hand: the fetcher (fetcher.py: the host `fetcher`, started if none answers)
    held while this command runs and asked for a standing pass (every feed's run, the closed
    months' copy, the extras), or with `tickers` for those symbols (requests on its stack); waited
    for until it has nothing going on (Ctrl+C lets it go: with nobody else holding it, it ends
    and each run stops at its next request). Returns 1 when the fetcher was held, 0 when none
    answered. The user, 2026-11-01: fetching goes on only while the app, or a command, is in use."""
    log = log or Logger(settings.db_path.parent / "logs" / "liminal.log")
    if settings.offline:
        log("sync: offline (LIMINAL_OFFLINE): nothing asked of the fetcher")
        return 0
    held = attach_fetcher(settings.db_path, log)
    if held is None:
        log("sync: no fetcher answered (and none could be started); nothing fetched")
        return 0
    try:
        if tickers:
            with Store(settings.db_path) as store:
                ids = [store.request_put(t, by="cli")["id"] for t in sorted(tickers)]
            held.ask(wake=True)
            log(f"sync: {', '.join(sorted(tickers))} asked of the fetcher (its newest requests go first)")
            wait_for_requests(settings.db_path, ids, held, log)
        else:
            held.ask(**{"pass": True})
            log("sync: a pass asked of the fetcher (every feed's run held by it, the extras in a process of their own)")
            wait_for_fetcher(held, log)
    finally:
        held.detach()
    return 1


def wait_for_hosts(held: list, log, every: float = 2.0) -> None:
    """Wait here while any of `held` (attachments to hosts, host.py) is still up: the hosts run
    while held. Ctrl+C lets them all go, and each stops at its next request."""
    alive = [a for a in held if callable(getattr(a, "alive", None))]
    if not alive:
        return
    log(f"sync: waiting for {len(alive)} run(s) (Ctrl+C lets them go: each stops at its next request)")
    try:
        while any(a.alive() for a in alive):
            time.sleep(every)
    except KeyboardInterrupt:
        for a in alive:
            a.detach()
        log("sync: let go; each run stops at its next request")
        raise


def _stores_pass(settings: Settings, log, history: bool = True):
    """The sync's part in the stores (stores.py): each plugin's documents moved to the store wanted
    (MongoDB under its keeper if it is the one), and with `history` a `history run` attached for
    the feeds whose closed months go to DuckDB, unless one is going on (returned, to be held; the
    chart app holds its own). A failure is logged, never the sync's."""
    from liminal_libs import gov, stores

    try:
        with Store(settings.db_path) as store:
            gdb = gov.open_db(settings.db_path)
            try:
                stores.settle(store, gdb.conn, settings.db_path.parent, log)
            finally:
                gdb.close()
        if history and history_due(settings):
            with Store(settings.db_path) as store:
                feeds = stores.bars_feeds(store)
            log(f"history: started copying the closed months of {', '.join(feeds)} to DuckDB, in a process of its own")
            return start_history(settings.db_path)
    except Exception as e:  # noqa: BLE001 - the stores are the sync's extras: its data is in
        log(f"stores: failed ({type(e).__name__}: {e})")
    return None


def start_history(db_path: Path):
    """Attach to the `history run` of this database (host.history_run): the host `history`, started
    if none answers (no window, a background priority once it runs). None: no answer."""
    from liminal_libs import host

    return host.history_run(db_path, log=Logger(db_path.parent / "logs" / "liminal.log"))


def _background_priority() -> None:
    """This process's CPU, I/O and memory priority lowered (Windows' background mode)."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)


def cmd_history(args, settings: Settings) -> int:
    """Closed history (storage/history.py). `run`: copy the due closed months of the feeds whose
    bars go to DuckDB (stores.py), newest first, within --budget seconds (one run at a time); a halt
    of fetching ends it after the month in hand. `status`: each feed's months in DuckDB."""
    from liminal_libs import proc, provenance, stores
    from liminal_libs.storage import history

    if args.action == "status":
        with Store(settings.db_path) as store:
            on = set(stores.bars_feeds(store))
            for f in store.feed_names():
                months = store.history_months(f)
                if not months and f not in on:
                    continue
                current = [m for m in months if not m["stale"]]
                print(f"{f:12} {'DuckDB' if f in on else 'SQLite':7} {len(current):4} months in DuckDB"
                      + (f" ({current[-1]['month']} to {current[0]['month']}, "
                         f"{sum(m['bytes'] for m in current) / 1e9:,.2f} GB)" if current else "")
                      + (f", {len(months) - len(current)} stale" if len(months) > len(current) else ""))
        return 0
    log = Logger(settings.db_path.parent / "logs" / "liminal.log")
    if settings.read_only:
        print("read-only (LIMINAL_READONLY): nothing is copied")
        return 0
    if not history.available():
        log("history: duckdb is not installed here: every month stays in SQLite alone")
        return 0
    lock = settings.db_path.with_name(HISTORY_LOCK)
    if not proc.acquire(lock):
        log("history: another run is copying; left to it")
        return 0
    try:
        _background_priority()
        with Store(settings.db_path) as store:
            feeds = [f for f in (args.feed or stores.bars_feeds(store))  # never the composite model's: read from SQLite
                     if f in store.feed_names() and f != COMPOSITE]
            conns = {f: store._bars(f) for f in feeds}

            stop = getattr(args, "_stop", None)  # under a host (`host history`): its clients gone, it ends

            def halted() -> bool:
                if stop is not None and stop.is_set():
                    return True
                p = collector.pause_state(store)
                return bool(p and p.get("halt"))
            done = history.run({f: c for f, c in conns.items() if c is not None}, store.history_dir, log,
                               budget_s=args.budget, code=provenance.code_version(), stop=halted)
        if not done:
            log("history: nothing due (every closed month copied, or written lately)")
    finally:
        proc.release(lock)
    return 0


def cmd_mongo(args, settings: Settings) -> int:
    """The MongoDB server here (mongo.py): start it (bound to 127.0.0.1, its cache capped), stop it
    cleanly, or show it."""
    from liminal_libs import mongo

    data = settings.db_path.parent
    if args.action == "start":
        # under its keeper (mongo.attach): held up while this command runs, as the app holds it
        # while it uses the documents; let go at the end, the keeper shuts it down after the idle time
        try:
            up = mongo.attach(data)
        except RuntimeError as e:
            print(e)
            return 1
        print(f"MongoDB {'is running' if up else 'did not answer'} on {mongo.HOST}:{mongo.port()}; "
              "it stays up while this command runs (Ctrl+C to let it go)")
        try:
            hold_until_interrupted()
        finally:
            mongo.detach()
    elif args.action == "stop":
        print("MongoDB stopped" if mongo.stop() else "MongoDB was not running")
    s = mongo.status(data)
    print(f"MongoDB {s['version'] or '(not installed)'}: " + (
        f"running, process {s['pid']}, {s['residentMB']} MB resident, cache capped at {s['cacheGB']} GB, "
        f"the app's data {((s['storageBytes'] or 0) / 1e6):,.1f} MB" if s["running"] else "not running")
        + (f"; {s['binary']}" if s["binary"] else ""))
    return 0


def cmd_stores(args, settings: Settings) -> int:
    """Each plugin's stores by data type (stores.py): `list`; `pick PLUGIN TYPE ENGINE` (auto: the
    optimal); `settle`: move documents to the store wanted now (as the hourly sync does)."""
    from liminal_libs import gov, stores
    from liminal_libs.sources.registry import plugin_of, providers, publisher_of, publishers

    with Store(settings.db_path) as store:
        if args.action == "pick":
            if not (args.plugin and args.type and args.engine):
                print("pick needs PLUGIN TYPE ENGINE (ENGINE: auto for the optimal)")
                return 2
            stores.pick(store, args.plugin, args.type, None if args.engine == "auto" else args.engine)
        gdb = gov.open_db(settings.db_path)
        try:
            if args.action in ("pick", "settle"):
                for d in stores.settle(store, gdb.conn, settings.db_path.parent, print, only=args.plugin):
                    if d.get("error"):
                        print(f"{d['plugin']}: {d['error']}")
            names = dict.fromkeys([n for n in providers() if plugin_of(n) == n]
                                  + [n for n in publishers() if publisher_of(n) == n])
            for name in names:
                for r in stores.plugin_stores(store, name, gdb.conn):
                    t = stores.ENGINE_TITLES
                    print(f"{name:12} {r['title']:10} {t[r['effect']]:8}"
                          + (f" (moving to {t[r['wanted']]})" if r["wanted"] != r["effect"] else "")
                          + (" picked" if r["picked"] else f" optimal here: {t[r['optimal']]}"))
        finally:
            gdb.close()
    return 0


def start_feed(name: str, db_path: Path):
    """Attach to feed `name`'s run of this database (host.feed_run): the host `feed-NAME`, started
    if none answers. Its pass goes on while something holds it; let go, it stops at its next
    request. None: no answer."""
    from liminal_libs import host

    return host.feed_run(name, db_path, log=Logger(db_path.parent / "logs" / "liminal.log"))


POST_LOCK = "post.lock"  # beside the database: one pass of composites and derived frames at a time


def after_sync(args, settings: Settings) -> bool:
    """Composite models, exchange quality and the time frames the feeds do not send, built from
    what the feeds hold (no requests), and research datasets that are due. One such pass at a
    time (POST_LOCK): another going on, this one is left to it. Each series' build is also
    leased (derive.take_lease), so a run's own per-symbol builds never overlap it."""
    from liminal_libs import composite, proc

    logger = Logger(settings.db_path.parent / "logs" / "liminal.log")
    lock = settings.db_path.with_name(POST_LOCK)
    if not proc.acquire(lock):
        logger("derived frames: another process is building them; left to it")
        return False
    tickers = {t.upper() for t in getattr(args, "tickers", None) or []} or None
    try:
        with Store(settings.db_path) as store:
            composite.refresh(store, feed_profiles(settings), collector.now_et(), log=logger)
            _derive(store, tickers, logger)  # the time frames the feeds do not send, from what just arrived
            if args.all_sources and not tickers:  # research datasets fetched before, when due (never starts one)
                from liminal_libs import datasets

                datasets.fetch(store, log=logger, only_started=True)
    finally:
        proc.release(lock)
    return True


def _fetch_gov(settings: Settings, log, force: bool = False) -> None:
    """The publisher plugins' due feeds (publications.py: the agencies, a vendor's headlines), each
    on its own schedule and under its scrape checks (`force`: every feed its floor allows); a
    failure is logged and never fails the caller."""
    from liminal_libs import publications

    try:
        publications.run_due(settings, log, force=force)
    except Exception as e:  # noqa: BLE001 - the publications are extras: the run's data is in
        log(f"publications: failed ({type(e).__name__}: {e})")


def cmd_updates(args, settings: Settings) -> int:
    """Pause or resume the unpicked symbols' updates (collector.updates_pause), or show the pause."""
    with Store(settings.db_path) as store:
        if args.action == "pause":
            collector.set_updates_pause(store, args.days)
        elif args.action == "resume":
            collector.set_updates_pause(store, None)
        p = collector.updates_pause(store)
        picked = collector.picked_symbols(store)
    if p:
        print(f"updates paused until {p['until']} for the symbols not picked ({len(picked)} picked: opened in the "
              f"chart or on a watchlist); a symbol more than {collector.UPDATES_GAP_DAYS} days behind is still "
              "updated, and history goes on")
    else:
        print("updates are not paused")
    return 0


def cmd_gov(args, settings: Settings) -> int:
    """Read the publishers' due feeds now (publications.py; the recorder reads them on their own
    schedules, the hourly sync when no recorder runs; --all: every feed its floor allows) and list
    each agency feed's state."""
    from liminal_libs import gov

    if settings.offline:
        print("this copy of the app is offline (LIMINAL_OFFLINE): nothing is fetched")
    else:
        _fetch_gov(settings, Logger(settings.db_path.parent / "logs" / "liminal.log"),
                   force=bool(getattr(args, "all", False)))
    db = gov.open_db(settings.db_path)
    try:
        for s in gov.view(db, contact_set=bool(settings.gov_contact))["sources"]:
            print(f"{s['agency']:17} {s['title'][:56]:56} {s['state']}" + (f"  ({s['message']})" if s["message"] else ""))
    finally:
        db.close()
    return 0


def _sync(args, settings: Settings) -> tuple[int, int]:
    """The feeds' downloads: (exit code, feeds that ran)."""
    tickers = {t.upper() for t in getattr(args, "tickers", None) or []} or None
    names = list(providers()) if args.all_sources else [args.source]
    with Store(settings.db_path) as store:
        separated = store.kv_get("built-bars:separated:v1")
        _note_tier(store, settings, Logger(settings.db_path.parent / "logs" / "liminal.log"))
        paused = collector.pause_state(store)
        if paused and tickers and not paused.get("halt"):
            paused = None  # a pause leaves the open symbol's fetches; a halt stops them too
    # The live recorder is not started from here: it runs while a chart has a symbol open (the chart
    # app attaches it, live.attach), and ends once nothing has (host.py).
    if paused:
        Logger(settings.db_path.parent / "logs" / "liminal.log")(
            "sync: fetching halted by you; not run" if paused.get("halt")
            else f"sync: background fetching paused by you until {paused['until'][11:16]} UTC; not run")
        return 0, 0
    if not separated:  # one-off: bars built from ticks before they were kept apart from the feeds' own
        try:
            with run_lock(settings.db_path), Store(settings.db_path) as store:
                store.separate_built_bars(log=Logger(settings.db_path.parent / "logs" / "liminal.log"))
                store.kv_set("built-bars:separated:v1", datetime.now().isoformat(timespec="seconds"))
        except Busy:  # a feed is fetching: done by a later sync, with every feed at rest
            pass
    if args.all_sources:  # by hand: the fetcher's pass, or the symbols given on its stack (dispatch)
        return 0, dispatch(settings, tickers=tickers)
    ran, errors = 0, []
    saved = config.saved_user_keys()
    for name in names:
        if config.saved_user_keys() != saved:  # a key or plan saved during the run counts from the next feed
            saved, settings = config.saved_user_keys(), load_settings()
            Logger(settings.db_path.parent / "logs" / "liminal.log")("sync: saved keys or plans changed; read again")
        if args.all_sources and _feed_closed(settings, name):
            continue  # a closed feed: every symbol's archive is complete; not contacted
        args.source = name
        try:
            with api_run(args, settings, "sync") as (source, store, log):
                if source is None:
                    continue
                # Directory placeholders and icons get only what the watchlist leaves, and never the
                # last DAILY_RESERVE requests of the day (later runs need them for updates): reckoned
                # when their turn comes, from what the day has spent by then (this run included).
                feed = settings.for_source(name)

                def reserve_left(store=store, name=name, feed=feed) -> int:
                    return max(0, feed.max_requests_per_day - collector.DAILY_RESERVE - store.requests_today(name))

                if tickers is None:
                    _scan(name, source, store, log, due_days=SCAN_DAYS, settings=settings)

                from .gui.world import PROXIES  # the market's proxies, from the app's exchanges table

                run = collector.Collector(source, store, feed.tier, log=log, refresh_hours=settings.refresh_hours,
                                          feeds=feed_profiles(settings), proxies=PROXIES,
                                          audits=(settings.audits_per_run, settings.audits_random_per_run),
                                          futures_per_run=settings.futures_per_run)
                result = run.run(only=tickers, published_only=True, directory_allowance=reserve_left,
                                 on_item=lambda t: store.set_run_current(args._run_id, t))
                args._rows = result.rows
                log(f"sync {name}: {result.rows} rows saved"
                    + (f", {result.skipped} symbols refreshed within {settings.refresh_hours:g} h left for later"
                       if result.skipped else "")
                    + (f" (stopped: {result.stopped})" if result.stopped else ""))
            ran += 1
        except Busy:
            if tickers is None:
                raise  # this feed's run (--only) is going already
            Logger(settings.db_path.parent / "logs" / "liminal.log")(
                f"sync {name}: fetching already; its run takes {', '.join(sorted(tickers))} next")
        except ConfigError as e:
            errors.append(f"{name}: {e}")  # feed not configured on this machine
        except Exception as e:  # noqa: BLE001 - recorded as this feed's run error (api_run); the next feeds go on
            errors.append(f"{name}: {type(e).__name__}: {e}")
            Logger(settings.db_path.parent / "logs" / "liminal.log")(f"sync {name}: failed ({e}); next feed")
    if not ran and errors:
        raise ConfigError("; ".join(errors))
    for e in errors:
        print(f"skipped {e}", file=sys.stderr)
    return 0, ran


NO_INTEGRATOR = ("the fetcher is the liminal-integrator package (github.com/pcxmac-dev/liminal-integrator): "
                 "install it beside the app (pip install liminal-integrator, or the app's `integrator` extra)")


def integrator():
    """The integrator's command line (liminal_integrator.cli), or None when it is not installed."""
    try:
        from liminal_integrator import cli as integrator_cli
    except ImportError:
        return None
    return integrator_cli


def _instance(found, args, settings: Settings) -> Settings | None:
    """The integrator's `--name`, `--db` and `--plugins` applied to this process (its
    apply_instance: an integrator of its own, on its own database, with the plugins it loads), the
    settings loaded again when that changed anything. None: a name that cannot be one (said)."""
    apply = getattr(found, "apply_instance", None)
    if apply is None:  # an integrator from before 1.4.0
        return settings
    try:
        changed = apply(args)
    except ValueError as e:
        print(e, file=sys.stderr)
        return None
    if not changed:
        return settings
    from liminal_libs import sourcing

    return sourcing.Scans.apply(load_settings())


def cmd_fetcher(args, settings: Settings) -> int:
    """The fetcher (liminal-integrator): serve, status, request, pass, cancel; answered by the
    integrator's own command line (`--name`, `--db`, `--plugins` applied first: _instance)."""
    found = integrator()
    if found is None:
        print(NO_INTEGRATOR, file=sys.stderr)
        return 2
    settings = _instance(found, args, settings)
    return 2 if settings is None else found.cmd_fetcher(args, settings)


def cmd_service(args, settings: Settings) -> int:
    """The fetcher as a service (liminal-integrator): install, remove, status, or run."""
    found = integrator()
    if found is None:
        print(NO_INTEGRATOR, file=sys.stderr)
        return 2
    settings = _instance(found, args, settings)
    return 2 if settings is None else found.cmd_service(args, settings)


def cmd_datasets(args, settings: Settings) -> int:
    """Research datasets: list them, fetch (or refresh) them, show a dataset's series or values."""
    from liminal_libs import datasets

    with Store(settings.db_path) as store:
        if args.action == "fetch":
            unknown = [n for n in args.names if n not in datasets.DATASETS]
            if unknown:
                print(f"unknown datasets: {', '.join(unknown)} (see `datasets list`)")
                return 1
            datasets.fetch(store, args.names or None, force=args.force,
                           log=Logger(settings.db_path.parent / "logs" / "liminal.log"))
            return 0
        if args.action == "show":
            if len(args.names) not in (1, 2) or args.names[0] not in datasets.DATASETS:
                print("usage: datasets show NAME [SERIES]")
                return 1
            ds = datasets.DATASETS[args.names[0]]
            if len(args.names) == 1:
                for r in store.dataset_series(ds.provider, ds.name):
                    span = f"{_day(r['first_ts'])} -> {_day(r['last_ts'])}" if r["n"] else "no values"
                    print(f"{r['series']:28} {r['n'] or 0:>7,} {r['freq']:5} {r['unit']:14} {span}  {r['title']}")
                for t, m in sorted(store.constituents(ds.provider, "S&P 500").items()):
                    print(f"{t:6} {m['sector'] or '':22} {m['industry'] or '':40} {m['name']}")
                return 0
            as_of = int(datetime.combine(args.as_of, datetime.min.time(), ET).timestamp() * 1000) if args.as_of else None
            df = store.dataset_values(ds.provider, ds.name, args.names[1], as_of=as_of)
            for ts, v in df.tail(args.tail).itertuples(index=False):
                print(f"{_day(ts)}  {v:g}")
            return 0
        for d in datasets.status(store):
            if not d["files"]:
                state = "not fetched (datasets fetch " + d["name"] + ")"
            else:
                kept = (f"{d['files']} versions from {d['source']}" if d["source"].startswith("git")
                        else f"{d['files']} files ({d['closed']} closed), {d['bytes'] / 1e6:.1f} MB")
                state = (f"{kept}, "
                         + (f"{d['members']} members" if d["members"] else
                            f"{d['series']:,} series, {d['values']:,} values {_day(d['first'])} -> {_day(d['last'])}")
                         + f", fetched {d['fetched']:%Y-%m-%d}, quality {d['quality']:.2f}"
                         + (f", {d['due']} due" if d["due"] else ""))
            print(f"{d['name']:31} {d['provider']:9} {state}")
    return 0


def cmd_live(args, settings: Settings) -> int:
    """Live trades (crypto from the exchanges' public streams, stocks from Alpaca's IEX stream with
    its keys): run the recorder here, start it in the background, or show what it is recording."""
    import json

    from liminal_libs import live

    if args.action == "stop":  # every recorder on this machine (the strays of old versions too)
        print(f"{live.reap_strays(log=print)} recorder process(es) ended")
        return 0
    if args.action == "status":
        with Store(settings.db_path) as store:
            beat, raw = store.kv_get(live.RECORDER_KEY), store.kv_get(live.STATUS_KEY)
        print(f"recorder: {'running' if live.running(settings.db_path) else 'not running'}, last beat {beat or 'never'}")
        for source, st in (json.loads(raw)["streams"] if raw else {}).items():
            pairs = ", ".join(f"{t} {p['trades']:,}" for t, p in sorted(st.get("pairs", {}).items()))
            state = "connected" if st.get("connected") \
                else "idle: " + (st.get("note") or "nothing of it open on a chart") if st.get("idle") \
                else "down: " + str(st.get("error"))
            print(f"  {source:9} {state}  {pairs}")
        return 0
    if not (settings.live_coins or live.stock_keys(settings)) or settings.offline:
        print("live recording is off (LIVE_CRYPTO=off and no stocks: no Alpaca keys or LIVE_STOCKS=off; "
              "or LIMINAL_OFFLINE)")
        return 0
    if args.action == "start":
        print("the recorder runs by itself while a chart has a symbol open (the chart app attaches it) and "
              "ends once none has; `liminal-charts live` runs one here until Ctrl+C")
        return 0
    locks = live.take_locks(settings.db_path)
    if locks is None:
        print("a recorder is already running (one per database, and one per machine: the exchanges "
              "count every connection from this address)")
        return 0
    recorder = make_recorder(settings, locks)
    try:
        recorder.run()
    except KeyboardInterrupt:
        recorder.stop.set()
    finally:
        live.release_locks(locks)
    return 0


def make_recorder(settings: Settings, locks: list, stop=None):
    """The recorder of this database's live trades (live.Recorder.for_settings: the coins and the stock
    keys the settings allow, a plugin's night poller, the database's log), holding `locks`
    (live.take_locks), stopping on `stop` (a host's, or its own)."""
    from liminal_libs import live

    return live.Recorder.for_settings(settings, locks, stop=stop)


def cmd_host(args, settings: Settings) -> int:
    """A resident host (host.py): a process that runs only while a client is attached to it, and
    ends, its data secured, once the last one has gone. `live`: the recorder (live.HOST)."""
    from liminal_libs import host, live

    log = Logger(settings.db_path.parent / "logs" / "liminal.log")
    if args.name == host.FETCHER:  # the fetcher (liminal-integrator): its requests and passes, its page and the data API
        try:
            from liminal_integrator import fetcher as integrator_fetcher
        except ImportError:
            log(f"host fetcher: {NO_INTEGRATOR}")
            return 2
        integrator_fetcher.run_host(settings, log)
        return 0
    if args.name == live.HOST:
        if not (settings.live_coins or live.stock_keys(settings)) or settings.offline:
            log("host live: live recording is off (LIVE_CRYPTO=off and no stocks, or LIMINAL_OFFLINE); not run")
            return 0
        locks = live.take_locks(settings.db_path)
        if locks is None:
            log("host live: a recorder is already running (one per database, one per machine); not run")
            return 0
        h = host.Host(live.HOST, settings.db_path, log=log)
        recorder = make_recorder(settings, locks, stop=h.stop)
        h.serve(lambda stop: recorder.run(), secure=lambda: live.release_locks(locks))  # run flushes on stop
        return 0
    from liminal_libs import mongo

    if args.name == mongo.KEEPER:  # the server's keeper: started for the first client, shut down after the last
        data = settings.db_path.parent
        try:
            mongo.startable()
        except RuntimeError as e:
            log(f"host mongo: {e}; not run")
            return 0
        h = host.Host(mongo.KEEPER, settings.db_path, log=log)
        h.serve(lambda stop: mongo.keep(data, stop, log), secure=lambda: mongo.stop() and log("host mongo: server shut down"))
        return 0
    if args.name.startswith("feed-"):  # a feed's run: its pass, stopped at its next request once let go
        feed = args.name[len("feed-"):]
        if feed not in providers():
            print(f"no feed named {feed!r}")
            return 2
        h = host.Host(args.name, settings.db_path, log=log)
        run = argparse.Namespace(source=feed, all_sources=False, tickers=None, fetch_only=True, _stop=h.stop)
        h.serve(lambda stop: _sync(run, settings))  # its run is recorded, its data in, before it returns
        return 0
    if args.name == "history":  # the closed months' copy to DuckDB, within its budget; ended after the month in hand once let go
        h = host.Host("history", settings.db_path, log=log)
        run = argparse.Namespace(action="run", feed=None, budget=HISTORY_BUDGET_S, _stop=h.stop)
        h.serve(lambda stop: cmd_history(run, settings))
        return 0
    print(f"no host named {args.name!r} (the hosts: {live.HOST}, {mongo.KEEPER}, feed-NAME, history)")
    return 2


SUPERSEDED_KEY = "derive:superseded:v1"


def _derive(store: Store, tickers, log) -> int:
    """Built bars a feed has since sent its own for go (once: later the database does it as they
    arrive), then the frames above 1 minute are built where anything changed (derive.py)."""
    from liminal_libs import derive

    if not store.kv_get(SUPERSEDED_KEY):
        for feed in providers():
            n = store.drop_superseded_built(feed)
            if n:
                log(f"derive {feed}: {n:,} built bars replaced by the feed's own")
        store.kv_set(SUPERSEDED_KEY, datetime.now().isoformat(timespec="seconds"))
    return derive.run(store, tickers=tickers, log=log)


def cmd_derive(args, settings: Settings) -> int:
    """Build every feed's candles for the time frames it does not send (5 min, 1 h, 1 day from its
    1-minute bars), marked derived; what changed since the last build, or everything the first time."""
    with Store(settings.db_path) as store:
        n = _derive(store, set(args.tickers) if args.tickers else None,
                    Logger(settings.db_path.parent / "logs" / "liminal.log"))
    print(f"{n:,} derived bars stored")
    return 0


def cmd_kills(args, settings: Settings) -> int:
    """The kill policy (killswitch.py): the kills in force and each feed's outcomes (list), or a
    kill by hand, or lifting one (only the user lifts a kill)."""
    from datetime import timedelta, timezone

    from liminal_libs import killswitch

    feeds = list(providers()) + ["iex"]
    with Store(settings.db_path) as store:
        if args.action == "kill":
            kinds = killswitch.KINDS if args.kind == "both" else (args.kind,)
            for kind in kinds:
                k = killswitch.strike(store, args.feed, kind, f"by hand: {args.why}", minutes=args.hours * 60)
                print(f"{args.feed} {kind}: killed until {k['until'][:16].replace('T', ' ')} UTC")
            return 0
        if args.action == "release":
            lifted = killswitch.release(store, args.feed, killswitch.KINDS if args.kind == "both" else (args.kind,))
            print(f"{args.feed}: {', '.join(lifted) + ' released' if lifted else 'nothing was killed'}")
            return 0
        kills = killswitch.kills(store, feeds)
        print("Killed:" if kills else "Killed: none")
        for k in kills:
            print(f"  {k['feed']:9} {k['kind']:5} until {k['until'][:16].replace('T', ' ')} UTC, strike {k['strikes']}"
                  f" ({k.get('by', 'policy')}): {k['why']}")
        since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
        print(f"Outcomes in the last {args.hours:g} h:")
        for r in store.feed_scores(since):
            held = f", connections held {r['medianHeld']:.0f} s (median)" if r["medianHeld"] is not None else ""
            print(f"  {r['feed']:9} {r['kind']:5} " + ", ".join(f"{n} {o}" for o, n in sorted(r["outcomes"].items())) + held)
    return 0


def cmd_pruned(args, settings: Settings) -> int:
    """Bad data (baddata.py): the pruned list per feed and symbol (list), a check of stored bars by
    the rules (scan), putting a bar back in the filtered view (release), or rebuilding a feed's bars
    from its stored ticks with the condition rules as they are now, each changed bar kept on the
    list (repair-ticks). Nothing is deleted; no feed is asked for anything."""
    from datetime import datetime, timezone

    from liminal_libs import baddata, derive

    log = Logger(settings.db_path.parent / "logs" / "liminal.log")
    with Store(settings.db_path) as store:
        if args.action == "repair-ticks":
            if not args.feed:
                raise SystemExit("repair-ticks needs --feed")
            done = baddata.repair_from_ticks(store, args.feed, args.tickers, log=log)
            log(f"repair-ticks {args.feed}: {len(done)} symbols, {sum(done.values()):,} bars kept on the pruned list")
            return 0
        if args.action == "scan":
            if not args.feed:
                raise SystemExit("scan needs --feed")
            tickers = args.tickers or sorted({t for s, t, _ in store.stored_series() if s == args.feed})
            total = 0
            for ticker in tickers:
                n = sum(baddata.scan_series(store, args.feed, ticker, timespan, derive.asset_of(store, args.feed, ticker))
                        for timespan in baddata.BASES)
                if n:
                    log(f"bad data {args.feed}:{ticker}: {n:,} bars on the pruned list")
                total += n
            log(f"scan {args.feed}: {len(tickers)} symbols, {total:,} bars on the pruned list")
            return 0
        if args.action == "release":
            if not (args.feed and args.tickers and args.ts is not None):
                raise SystemExit("release needs --feed, --tickers (one) and --ts")
            n = store.release_pruned(args.feed, args.tickers[0], args.timespan, 1, args.ts)
            if n and args.timespan in baddata.BASES:  # the frames above are built again with it, however old the build
                derive.mark_dirty(store, args.feed, args.tickers[0], args.timespan, args.ts,
                                  args.ts + baddata.STEP_MS[args.timespan])
            print(f"{n} entr{'y' if n == 1 else 'ies'} released")
            return 0
        tickers = args.tickers or store.pruned_tickers(args.feed)  # a seek per symbol, not the whole list
        for ticker in tickers:
            print(f"{ticker}: " + "; ".join(f"{feed} " + ", ".join(f"{n:,} {rule}" for rule, n in c.items())
                                           for feed, c in store.pruned_counts(ticker).items()))
            for e in baddata.entries(store, ticker, args.feed, limit=args.limit):
                at = datetime.fromtimestamp(e["ts"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {at} UTC  {e['source']:9} {e['multiplier']} {e['timespan']:7} {e['rule']:10} "
                      f"o {e['open']} h {e['high']} l {e['low']} c {e['close']} v {e['volume']}"
                      + ("  (released)" if e["releasedAt"] else ""))
    return 0


def cmd_inventory(args, settings: Settings) -> int:
    """What a symbol holds on every feed, frame by frame: sent by the feed, built here (*derived)
    or not built yet; with --json, everything inventory.py reports."""
    import json

    from liminal_libs import inventory

    with Store(settings.db_path) as store:
        inv = inventory.symbol_inventory(store, args.ticker)
    if args.json:
        print(json.dumps(inv, indent=2))
        return 0
    if not inv["feeds"]:
        print(f"{inv['ticker']}: no feed holds or watches it")
        return 1
    feeds = inv["feeds"]
    cell = {}
    for f in feeds:
        for fr in f["frames"]:
            cell[(fr["frame"], f["feed"])] = ("sent" if "sent" in fr else "") + ("+*" if "sent" in fr and "derived" in fr
                                                                                else "*derived" if "derived" in fr else "")
        for label in f["notBuilt"]:
            cell[(label, f["feed"])] = "not built"
    width = max(9, *(len(f["feed"]) for f in feeds))
    print(f"{inv['ticker']}  (sent: the feed's own; *derived: built here from finer data)")
    print("frame  " + "".join(f"{f['feed']:<{width + 2}}" for f in feeds))
    for label in inv["frames"]:
        if any((label, f["feed"]) in cell for f in feeds):
            print(f"{label:<7}" + "".join(f"{cell.get((label, f['feed']), '-'):<{width + 2}}" for f in feeds))
    for f in feeds:
        w, ticks = f["watch"] or {}, f["ticks"]
        print(f"{f['feed']}: " + ", ".join(filter(None, [
            f"resolution {w['resolution']}" if w.get("resolution") else "not watched",
            f"backfilled to {w['backfilled_to']}" if w.get("backfilled_to") else None,
            f"ticks {ticks['first'][:10]} -> {ticks['last'][:16]}" if ticks else None,
            "model" if f["model"] else None])))
    return 0


def cmd_provenance(args, settings: Settings) -> int:
    """How derived results were made: kind, product, code version, settings, inputs, span, rows."""
    import json

    with Store(settings.db_path) as store:
        rows = store.derivations(kind=args.kind, product=args.product, limit=args.limit)
        for d in rows:
            span = f"{_day(d['span_lo'])} -> {_day(d['span_hi'])}" if d["span_lo"] is not None else "-"
            print(f"#{d['id']} {d['created_at']} {d['kind']:16} {d['product']:28} code {d['code']:18} "
                  f"{d['rows'] if d['rows'] is not None else '?':>8} rows  {span}")
            if args.verbose:
                print(f"    settings {json.dumps(d['settings'], sort_keys=True)}")
                print(f"    inputs   {json.dumps(d['inputs'], sort_keys=True)}")
        if not rows:
            print("no derivations recorded" + (f" for {args.product}" if args.product else ""))
    return 0


def _day(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, ET).strftime("%Y-%m-%d") if ms is not None else "-"


def cmd_feeds(args, settings: Settings) -> int:
    """Every feed, its state and plan; `reopen` lets a closed feed (or one of its symbols) fill again."""
    with Store(settings.db_path) as store:
        if args.action == "reopen":
            if args.name not in providers():
                print(f"unknown feed {args.name}")
                return 1
            n = store.set_closed(args.name, resolve(args.ticker).ticker if args.ticker else None, None)
            print(f"{args.name}: {n} symbols reopened; the next sync fills what they still lack")
            return 0
        profiles = feed_profiles(settings)
        for name, p in providers().items():
            rows = store.watchlist(name)
            shut = sum(1 for w in rows if w.get("closed_at"))
            kind = "closed feed (public archive)" if p.archival else "live feed"
            state = ("closed: archive complete" if rows and shut == len(rows) else "nothing watched" if not rows
                     else f"{shut} of {len(rows)} symbols closed" if p.archival else "open")
            served = ", ".join(sorted(profiles[name].assets)) if name in profiles and profiles[name].assets else "-"
            print(f"{name:9} {kind:29} {settings.for_source(name).tier.name:16} serves: {served:28} {state}")
    return 0


def cmd_plugins(args, settings: Settings | None) -> int:
    """The provider plugins (sources/registry.py): each one's keys, set or not (never their values),
    what it serves here, its version and where it came from; the publisher plugins; the ones left
    out (failed to load, or written for another plugin API). --json: the same for tools/set-key.ps1's
    menu (listed even when a setting is wrong: settings is None then)."""
    import json

    from liminal_libs.sources import registry

    rows = registry.menu(settings)
    pubs = registry.publisher_menu()
    assts = registry.assistant_menu(settings)  # the AI assistants (plugin API 2.0)
    left_out = registry.errors()
    if args.json:
        print(json.dumps({"api": registry.PLUGIN_API, "providers": rows, "publishers": pubs, "assistants": assts,
                          "errors": [{"where": w, "why": why} for w, why in left_out]}))
        return 0

    def release(r: dict) -> str:
        return f"[{r['version'] or 'unversioned'} for plugin API {r['api']}, {r['origin']}]"

    print(f"Plugin API {registry.PLUGIN_API} (docs/ARCHITECTURE.md: Providers are plugins)")
    for r in rows:
        keys = ", ".join(k["name"] for k in r["keys"]) or "no key needed"
        if r.get("keysSet") is not None:
            keys += " (set)" if r["keysSet"] else f" (not set{': ' + ', '.join(r['keyless']) + ' only' if r['keyless'] else ''})"
        serves = ", ".join(r.get("serves") or []) or "-"
        print(f"{r['name']:9} {r['title']:10} {'archive' if r['archive'] else 'feed':8} {keys:58} serves: {serves}")
        print(f"{'':9} {r['about']}" + (f"; keys from {r['signup']}" if r["signup"] else "") + f" {release(r)}")
        for f in r["feeds"]:  # a feed of its own in this plugin, on its keys: alpaca (blue ocean)
            print(f"{'':9} and {f['label']} (feed {f['name']}): {f['about']}"
                  + (f"; serves: {', '.join(f['serves']) or '-'}" if "serves" in f else ""))
        for pub in r["publishers"]:  # a publisher in this plugin: Massive's headlines
            print(f"{'':9} and {pub['title']} (publisher {pub['name']}): {pub['about']}")
    for r in pubs:
        print(f"{r['name']:12} {r['title']:28} publisher {release(r)}"
              + (f"; you set: {', '.join(k['name'] for k in r['keys'])}" if r["keys"] else ""))
        for part in r["parts"]:  # a plugin of several publishers: Federal's agencies
            print(f"{'':12} and {part['title']} ({part['name']}): {part['about']}")
    for r in assts:  # an AI assistant: its key and account, its plan and model (the user's choice, or Connect's)
        keys = ", ".join(k["name"] + (" (set)" if k.get("set") else " (not set)") for k in r["keys"])
        print(f"{r['name']:9} {r['title']:10} {'assistant':8} {keys:58} plan: {r.get('plan') or '-'}; model: {r.get('model') or '-'}")
        print(f"{'':9} {r['about']}" + (f"; keys from {r['signup']}" if r["signup"] else "") + f" {release(r)}"
              + (f"; {r['freeNote']}" if r.get("freeNote") else ""))
    for where, why in left_out:
        print(f"left out: {where}: {why}")
    print("Set or remove a plugin's keys: in the Plugins view (Setup), or "
          "powershell -NoProfile -ExecutionPolicy Bypass -File .\\tools\\set-key.ps1")
    return 0


def cmd_ai(args, settings: Settings) -> int:
    """The AI assistants (plugins; liminal_libs.assistants): `ask QUESTION` evaluates a question (with
    --context FILE texts, an --answer form, a --scale or --fields), `models` lists what the vendor
    serves, `connect` reads the plan from its servers, `evaluations` lists what was kept, `judge
    --id EV...` judges earlier answers; by --assistant (default: LIMINAL_ASSISTANT, else the first
    set up) and --model. Offline: nothing is sent."""
    import json

    from liminal_libs import assistants as ai
    from liminal_libs.assistants import Answer, ContextItem, Offline, Question
    from liminal_libs.errors import SourceError

    name = (args.assistant or "").strip().lower() or None
    if args.action in ("ask", "connect", "judge", "models") and settings.offline:
        print(f"offline (LIMINAL_OFFLINE): nothing sent to {name or ai.default_assistant(settings) or 'an assistant'}")
        return 2
    try:
        if args.action == "models":
            chosen = name or ai.default_assistant(settings)
            if chosen is None:
                print("no AI assistant is set up here: set one's key and plan on the Plugins view")
                return 1
            for m in ai.build(chosen, settings).models():
                table = ai.assistants()[chosen].model_of(m["id"])
                print(f"{m['id']:32} {m.get('title', '')[:32]:32} " + (f"${table.input_usd:g} in, ${table.output_usd:g} out per million"
                                                                          if table else "not in the table (no price known)"))
            return 0
        if args.action == "connect":
            chosen = name or ai.default_assistant(settings)
            p = ai.assistants().get(chosen or "")
            if p is None:
                print("no AI assistant is set up here: set one's key and plan on the Plugins view")
                return 1
            with Store(settings.db_path, check_same_thread=False) as store:
                _scan(p.name, p.build(settings), store, print, settings=settings)
            return 0
        with Store(settings.db_path, check_same_thread=False) as store:
            if args.action == "evaluations":
                rows = store.evaluations(assistant=name, kind=args.kind or None, limit=args.limit)
                if args.json:
                    print(json.dumps(rows, default=str))
                    return 0
                for r in rows:
                    cost = f"${r['cost_usd']:.6f}" if r.get("cost_usd") is not None else "cost unknown"
                    print(f"{r['at'][:16]} {r['assistant']:8} {r['model']:28} {r['kind']:8} {r.get('verdict') or '-':10} "
                          f"{(r.get('input_tokens') or 0) + (r.get('output_tokens') or 0):>7} tokens {cost}  {r['id']}")
                    print(f"{'':17} {(r.get('text') or r.get('error') or '')[:200]}")
                return 0
            if args.action == "judge":
                found = [store.evaluation(i) for i in args.id or []]
                if not found or any(e is None for e in found):
                    print("judge needs the ids of evaluations kept here (--id EV_ID, repeatable): `ai evaluations` lists them")
                    return 1
                ev = ai.judge(found, settings, task=args.task, question=args.question or "", assistant=name, model=args.model or None,
                              store=store)
            else:  # ask
                question = " ".join(args.question_words or []).strip() or (args.question or "")
                if not question:
                    print("ask needs a question")
                    return 1
                answer = Answer(args.answer, scale=tuple(s.strip() for s in (args.scale or "").split(",") if s.strip()),
                                fields=tuple(s.strip() for s in (args.fields or "").split(",") if s.strip()))
                context = tuple(ContextItem("document", Path(f).read_text(encoding="utf-8", errors="replace"), id=f,
                                            title=Path(f).name) for f in (args.context or []))
                ev = ai.evaluate(Question(question, context=context, answer=answer), settings, assistant=name,
                                 model=args.model or None, store=store)
        if args.json:
            print(json.dumps(ev.as_dict(), default=str))
            return 0
        print(ev.text.strip() or f"(no text: {ev.stop})")
        cost = f"${ev.cost_usd:.6f}" if ev.cost_usd is not None else "cost unknown"
        print(f"--- {ev.assistant} ({ev.model}), {ev.usage.total} tokens, {cost}, {ev.seconds:g} s"
              + (f"; verdict: {ev.verdict}" if ev.verdict else "") + (f"; {ev.stop}" if ev.stop != "end_turn" else "") + f"; {ev.id}")
        return 0 if ev.ok else 1
    except Offline as e:
        print(str(e))
        return 2
    except LookupError as e:
        print(str(e))
        return 1
    except SourceError as e:
        print(f"{type(e).__name__}: {e}")
        return 1


def cmd_sourcing(args, settings: Settings) -> int:
    """The delivery scheme (sourcing.py): every provider's standard report (its subscription, limits
    and capabilities), and which source delivers each job's eras, unique or common, at what cost."""
    import json

    from liminal_libs import sourcing

    feeds = feed_profiles(settings)
    with Store(settings.db_path) as store:
        scheme = sourcing.Scheme.standing(store, feeds)
        used = {name: store.requests_today(name) for name in feeds}
        scans = sourcing.Scans.read(store, feeds)
    reports = sourcing.Scheme.reports(feeds, scheme.today, settings, scans)
    if args.json:
        print(json.dumps({"reports": [{**r.as_dict(), "usedToday": used.get(n)} for n, r in reports.items()],
                          "scheme": scheme.as_dict()}, default=str))
        return 0
    print(scheme.text(reports))
    return 0


def cmd_capabilities(args, settings: Settings) -> int:
    """Each provider's capability scan (its plugin's `scan`): a few requests asking what its plan
    really gives, within its caps like any run; kept and shown in its report (`sourcing`). A
    plugin's name scans each of its feeds (Alpaca: Blue Ocean's too)."""
    from liminal_libs.sources.registry import assistants, plugin_feeds

    names = plugin_feeds(args.name) if args.name else [n for n, p in [*providers().items(), *assistants().items()] if p.has_scan]
    for name in names:
        p = providers().get(name)
        if p is None and name in assistants():  # an AI assistant's Connect: the key on the models list, the limits
            p = assistants()[name]
            if not p.served(settings):
                print(f"{name}: not set up here ({p.free_note(settings) or p.key_hint}); not connected")
                continue
            with Store(settings.db_path, check_same_thread=False) as store:
                _scan(name, p.build(settings), store, print, settings=settings)
            continue
        if p is None or not p.has_scan:
            print(f"{name}: no capability scan")
            continue
        if not p.served(settings):  # its keys not set: a scan would ask with none
            print(f"{name}: not set up here ({p.key_hint or 'nothing it serves'}); not scanned")
            continue
        args.source = name
        with api_run(args, settings, "capabilities", lock=False) as (source, store, log):
            if source is not None:
                _scan(name, source, store, log, settings=settings)
    return 0


SCAN_DAYS = 7  # each provider's capabilities are scanned again this often, at the start of its sync


def _scan(name: str, source, store: Store, log, due_days: float | None = None, settings: Settings | None = None) -> None:
    """Run a provider's capability scan and keep it (sourcing.Scans.keep); with `due_days` (a sync's),
    only when the last is older and the feed has symbols to collect. A scan that fails is logged;
    the run goes on. An AI assistant's Connect is one too (`source`: its Assistant): asked, kept and
    logged alike; the rate window below is a feed's alone (an assistant's limiter keeps its plan's)."""
    from liminal_libs import sourcing
    from liminal_libs.sources.registry import assistants

    p = providers().get(name)
    feed = p is not None
    if p is None:
        p = assistants().get(name)
    if p is None or not p.has_scan:
        return
    last = sourcing.Scans.last(store, name)
    now = datetime.now().astimezone()
    if due_days is not None and last and now - datetime.fromisoformat(last["at"]) < timedelta(days=due_days):
        return
    if due_days is not None and not any(n for (feed, _), n in store.watch_counts().items() if feed == name):
        return  # a feed with nothing to do spends no request on its scan
    try:
        found = p.run_scan(source, settings)
    except (RateLimited, Unauthorized):
        raise  # the server said stop: the run ends and the feed is killed (api_run), not asked on at once
    except Exception as e:  # noqa: BLE001 - a scan must not stop the feed's run
        log(f"{name}: capability scan failed ({type(e).__name__}: {e})")
        return
    sourcing.Scans.keep(store, name, found, now)
    for what, ok, detail in found.results:
        log(f"{name}: {'yes' if ok else 'no '} {what}" + (f": {detail}" if detail else ""))
    client = getattr(source, "client", None)
    if feed and settings is not None and found.limits.get("perMinute") is not None and hasattr(client, "tighten"):
        from liminal_libs.ratelimit import describe
        from liminal_libs.sources.registry import rate_window

        n, s = rate_window(p.account or name, settings)  # its limiter's (its account's), with what was just kept
        if client.tighten(n, s):
            log(f"{name}: its limiter keeps {describe(n, s)} from now on, this run included (its server's limit)")
    for var, value in found.plan.items():
        alike = found.alike.get(var)
        log(f"{name}: plan found: {var}={value}" + (f" (or {', '.join(alike)}: the same answers)" if alike else "")
            + f" (used wherever you have not set {var})")


def _scan_table(result: collector.ScanResult) -> None:
    order = {"behind": 0, "empty": 1, "current": 2}
    print()
    print(f"{'symbol':10} {'state':8} {'stored through':17} {'should have through':20} {'source has':17} next due")
    for r in sorted(result.items, key=lambda r: (order[r["state"]], r["ticker"])):
        fmt = lambda t: f"{t:%Y-%m-%d %H:%M}" if t else "-"  # noqa: E731
        state = "NEW" if r["new"] else r["state"]
        print(f"{r['ticker']:10} {state:8} {fmt(r['stored']):17} {fmt(r['expectedThrough']):20} "
              f"{fmt(r['available']):17} {fmt(r['nextDue'])}")


def _only(args) -> set[str] | None:
    return {resolve(t).ticker for t in args.only} if getattr(args, "only", None) else None


def cmd_scan(args, settings: Settings) -> int:
    """Which symbols should have new data? Free without --probe; --probe confirms with the
    source (one request per symbol that looks behind); --fetch then downloads what was found.
    With --all-sources every configured feed is scanned in turn (each only for its own spans)."""
    if getattr(args, "all_sources", False):
        for name in feed_profiles(settings):
            if is_archive(name):
                continue  # closed feeds are not rechecked
            args.source = name
            _scan_one(args, settings)
        return 0
    return _scan_one(args, settings)


def _scan_one(args, settings: Settings) -> int:
    tier = settings.for_source(args.source).tier
    if not args.probe:
        with Store(settings.db_path) as store:
            result = collector.scan(None, store, tier, source_name=args.source, only=_only(args))
        _scan_table(result)
        return 0
    label = "scan" + (f" {' '.join(sorted(_only(args)))}" if args.only else "") + (" + fetch" if args.fetch else "")
    with api_run(args, settings, label) as (source, store, log):
        if source is None:  # daily cap reached: still report what is known
            result = collector.scan(None, store, tier, source_name=args.source, log=log, only=_only(args))
        else:
            result = collector.scan(source, store, tier, log=log, probe_limit=args.limit, only=_only(args),
                                    feeds=feed_profiles(settings))
            if args.fetch and result.new and not result.stopped:
                done = collector.sync(source, store, tier, log=log, only=set(result.new), feeds=feed_profiles(settings),
                                      on_item=lambda t: store.set_run_current(args._run_id, t))
                args._rows = done.rows
                log(f"scan: fetched {done.rows} new rows for {', '.join(result.new)}")
    _scan_table(result)
    return 0


def _audit_line(r: dict) -> str:
    rep = r["report"] or {}
    close, rng, vol = ((rep.get("box") or {}).get(k) or {} for k in ("c", "range", "v"))
    start = datetime.fromtimestamp(r["start_ms"] / 1000, ET)
    fmt = lambda b, u: f"{b['median']:+.2f}{u} (p95 {b['p95']:+.2f})" if b else "-"  # noqa: E731
    return (f"{r['at'][:16]}  {r['ticker']:8} {start:%Y-%m-%d %H:%M} {r['timespan']:6} {r['owner']}->{r['auditor']:8} "
            f"{r['verdict']:7} matched {r['matched'] or 0:5} only {r['only_owner'] or 0}/{r['only_auditor'] or 0}  "
            f"close {fmt(close, 'bps')}  high-low {fmt(rng, 'bps')}  volume {fmt(vol, '%')}")


def daily_audit_text(a: dict, show: int = 10) -> str:
    """The daily history audit (audit.daily_history) for the terminal."""
    lines = [f"Daily history of {a['stocks']:,} stocks (from the coverage ledger: no requests)",
             f"  complete from their listing date: {a['complete']:,}"]
    if a["head"]:
        # the closed archives of stocks installed (Tiingo's package), named as they are: not an index's
        # (Cboe) nor a crypto venue's, which hold no stock
        stocks = [n for n, p in providers().items() if is_archive(n) and "stock" in p.assets]
        archives = ', '.join(f"{n} ({collector.SYMBOLS_PER_MONTH[n]} a month)" if n in collector.SYMBOLS_PER_MONTH else n
                             for n in stocks) or "none installed"
        lines.append(f"  missing their oldest years: {len(a['head']):,} (the closed archives fill them: {archives}, "
                     "most traded first)")
        lines += [f"    {t}: listed {l}, daily bars from {f}" for t, l, f in a["head"][:show]]
    if a["none"]:
        lines.append(f"  no daily bars yet: {len(a['none']):,} (daily bars first: {collector.DAILY_FIRST_PER_RUN:,} "
                     f"a run, most traded first): {', '.join(a['none'][:show])}{' ...' if len(a['none']) > show else ''}")
    if a["unchecked"]:
        lines.append(f"  listing date not checked yet: {len(a['unchecked']):,}")
    return "\n".join(lines)


def cmd_audit(args, settings: Settings) -> int:
    """Audit stored spans against another feed: the second copy is compared, never stored."""
    from liminal_libs import audit

    if args.daily:
        with Store(settings.db_path) as store:
            print(daily_audit_text(audit.daily_history(store)))
        return 0
    if args.list:
        with Store(settings.db_path) as store:
            for r in store.audits(30, args.ticker):
                print(_audit_line(r))
            scores = store.audit_scores()
        for feed, s in sorted(scores.items()):
            print(f"{feed}: {s['passed']} of {s['audits']} audits passed in 90 days")
        return 0
    args.source = args.auditor or args.source
    feeds = feed_profiles(settings)
    with api_run(args, settings, "audit") as (source, store, log):
        if source is None:
            return 0
        me = feeds.get(source.name)
        if me is None:
            log(f"{source.name} is not configured here")
            return 1
        now = collector.now_et()
        if not args.ticker:
            for _ in range(args.random):
                pick = audit.random_span(store, source, me, now, random.Random(), feeds=feeds)
                if pick:
                    audit.audit_span(source, store, *pick, "manual", log)
                else:
                    log("nothing to audit: no other feed holds data this feed can serve")
            return 0
        w = next((x for x in store.watchlist(source.name) if x["ticker"] == args.ticker), None)
        cov = store.coverage(args.ticker, "minute")
        owners = sorted(o for o in cov if o != source.name)
        if w is None or not owners:
            log(f"{args.ticker}: not on {source.name}'s watchlist, or no other feed holds its minute bars")
            return 1
        if args.day:
            span = audit.manual_day(store, args.ticker, args.day, w["asset"])
        else:
            pick = audit.random_span(store, source, me, now, random.Random(), {args.ticker: w})
            span = pick[2:4] if pick else None
        if not span:
            log(f"{args.ticker}: no session to audit")
            return 1
        owner = next((o for o in owners if any(a <= span[0] < b for a, b in cov[o])), owners[0])
        audit.audit_span(source, store, args.ticker, "minute", span[0], span[1], owner, "manual", log)
    return 0


def cmd_deps(args, settings: Settings) -> int:
    """Dependency health (see docs/DEPENDENCIES.md): exits 1 if anything has failed."""
    from . import deps

    with Store(settings.db_path) as store:
        rows = deps.checks(store, settings, args.source)
    area = None
    for c in rows:
        if c.area != area:
            area = c.area
            print()
            print(area)
        print(f"  {c.status:4}  {c.item:40} {c.found}")
        if c.action:
            print(f"        -> {c.action}")
    worst = "FAIL" if any(c.status == "FAIL" for c in rows) else "WARN" if any(c.status == "WARN" for c in rows) else "OK"
    print()
    print(f"{worst}: {sum(c.status == 'OK' for c in rows)} OK, {sum(c.status == 'WARN' for c in rows)} WARN, "
          f"{sum(c.status == 'FAIL' for c in rows)} FAIL")
    return 1 if worst == "FAIL" else 0


def cmd_gui(args, settings: Settings) -> int:
    """The chart app: its page here, its data through the integrator's data API (gui/front.py),
    the integrator started for it when nothing answers at its port. `--alone`: page and data in
    this one process, no fetcher (nothing collected: a read-only copy, a test)."""
    if args.alone:
        from .gui.server import serve

        serve(settings.db_path, port=args.port, open_browser=not args.no_browser,
              priority=list(settings.source_priority), settings=settings, reload_settings=load_settings,
              rescan=not args.no_rescan)
        return 0
    from .gui.front import serve_front

    if args.no_rescan:
        os.environ["LIMINAL_NO_RESCAN"] = "1"  # the integrator this front starts rescans nothing at its start
    serve_front(settings.db_path, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_probe(args, settings: Settings) -> int:
    with api_run(args, settings, f"probe {args.ticker}") as (source, store, log):
        if source is None:
            return 0
        spec = resolve(args.ticker)
        if args.watch:
            store.watch_spec(args.source, spec)
        if spec.asset == "future":
            log(f"{spec.ticker}: continuous futures are resolved by `sync` (it needs the current contract)")
            return 0
        collector.resolve_resolution(source, store, spec.ticker, log, asset=spec.asset, fetch=spec.source_ticker)
    return 0


def cmd_download(args, settings: Settings) -> int:
    with api_run(args, settings, f"download {' '.join(args.tickers)}") as (source, store, log):
        if source is None:
            return 0
        rows = 0
        for t in args.tickers:
            rows += collector.download_bars(
                source, store, settings.for_source(args.source).tier, t.upper(), args.start, args.end, args.timespan, args.multiplier, log
            )
        args._rows = rows
    return 0


def cmd_market_daily(args, settings: Settings) -> int:
    with api_run(args, settings, "market-daily") as (source, store, log):
        if source is None:
            return 0
        result = collector.sync_market_daily(source, store, settings.for_source(args.source).tier, log=log)
        args._rows = result.rows
    return 0


def cmd_exchanges(args, settings: Settings) -> int:
    if args.refresh:
        with api_run(args, settings, "exchanges") as (source, store, log):
            if source is not None:
                args._rows = store.upsert_exchanges(source.exchanges())
    with Store(settings.db_path) as store:
        df = store.exchanges(args.source)
        print(df.to_string(index=False) if not df.empty else "no exchanges stored yet; run with --refresh")
    return 0


def cmd_info(args, settings: Settings) -> int:
    with api_run(args, settings, f"info {args.ticker}") as (source, store, log):
        if source is not None:
            inst = source.instrument(args.ticker)
            print(inst.describe())
            print(f"listed: {inst.list_date or '?'}  active: {inst.active}  FIGI: {inst.figi or '?'}")
            if inst.country:
                print(f"country: {inst.country.name} ({inst.country.code}), timezone {inst.country.timezone}")
    return 0


def cmd_status(args, settings: Settings) -> int:
    with Store(settings.db_path) as store:
        s = store.summary(counts=args.counts)
        print(f"database: {s['db_path']} ({s['size_bytes'] / 1e6:.1f} MB)")
        print(f"feeds: {s['feeds_dir']} " + ", ".join(f"{k} {v / 1e9:.2f} GB" for k, v in s["feed_bytes"].items()))
        for src, p in providers().items():
            feed = settings.for_source(src)
            t = feed.tier
            history = f"from {t.history_start}" if t.history_start else f"{t.history_years or 'unlimited'} years"
            has = p.keys_set(settings)
            keys = "" if has is None else ", keys set" if has else \
                f", keys not set ({', '.join(sorted(p.keyless))} only)" if p.keyless else ", key not set"
            print(f"{src}: plan {t.name}, {t.requests_per_minute or 'unlimited'} requests/min, history {history}, "
                  f"caps {feed.max_requests_per_run}/run {feed.max_requests_per_day}/day, "
                  f"requests today {store.requests_today(src)}{keys}")
        print("\nwatchlist:")
        for w in store.watchlist():
            print("  " + _watch_line(w))
        print("\nticks:")
        print(f"  tick files: {store.ticks_bytes() / 1e9:.2f} GB in {store.feeds_dir}")
        for src, t, n, lo, hi in s["trades"]:
            print(f"  {src:8} {t:8} {_ns(lo)} -> {_ns(hi)}")
        print("\nbars:")
        for src, ts, mult, tickers, n, lo, hi in s["bars"]:
            bars = f" {n:>12,} bars" if n is not None else ""  # counted with --counts only
            print(f"  {src:8} {mult}/{ts:7} {tickers:>6} tickers{bars}  {_ns(lo * 10**6)} -> {_ns(hi * 10**6)}")
        print("\nrecent runs:")
        for r in store.recent_runs(args.runs):
            print(f"  #{r[0]} {r[3]} {r[2]:24} {r[7]:7} {r[5]} requests, {r[6]} rows {r[8] or ''}")
    return 0


def _ns(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, collector.ET).strftime("%Y-%m-%d %H:%M:%S")


def cmd_admin(args, settings: Settings) -> int:
    if args.action == "split-feeds":
        from liminal_libs.splitfeeds import split

        with run_lock(settings.db_path):  # no sync may write while the data moves
            split(settings.db_path, log=print)
        return 0
    if args.action == "memory":  # no database needed
        from liminal_libs import memory

        print(memory.report(memory.measure(max(0.0, args.seconds))))
        return 0
    if args.action == "backup" and args.ticks and _ticks_refused("backup --ticks", settings):
        return 2
    if args.action == "compact":  # by copying (storage/admin.compact_file); this store closed before any swap
        from liminal_libs.storage.admin import compact_file

        with Store(settings.db_path) as store:
            files = store.data_files()
        if args.file:
            files = [f for f in files if f.resolve() == Path(args.file).resolve()]
            if not files:
                raise SystemExit(f"{args.file} is not one of this database's files"
                                 + (" (tick files only with BARS_ONLY=off)" if settings.bars_only else ""))
        if args.swap:  # no sync may start while the file is copied and swapped
            with run_lock(settings.db_path):
                done = [compact_file(f, swap=True) for f in files]
        else:
            done = [compact_file(f) for f in files]
        print(f"compacted {sum(d is not None for d in done)} of {len(files)} files"
              + ("" if args.swap else " into copies beside them (--swap puts them in place)"))
        return 0
    with Store(settings.db_path) as store:
        if args.action == "check":
            print(f"integrity: {store.integrity_check()}")
        elif args.action == "vacuum":
            with run_lock(settings.db_path):
                done = store.vacuum()
            print(f"vacuumed {len(done)} files")
        elif args.action == "backup":
            dest = args.dest or settings.db_path.parent / "backups" / f"massive-{datetime.now():%Y%m%d-%H%M%S}.db"
            print(f"backup written to {store.backup(Path(dest), ticks=args.ticks)}, feeds in {feeds_backup_dir(Path(dest))}"
                  + (" (with ticks)" if args.ticks else " (bars only; add --ticks for the tick files)"))
            if not args.dest:  # the retention policy: the newest BACKUPS_KEEP of these backups
                for old in prune_backups(Path(dest).parent, settings.backups_keep):
                    print(f"removed old backup {old}")
        elif args.action == "reset-denied":
            print(f"cleared {store.clear_denied(args.source)} denied days")
    return 0


def _load_prices(store: Store, args) -> tuple:
    """(bars DataFrame indexed by time, trades DataFrame or None) from the finest stored data."""
    trades = store.load_trades(args.ticker, args.source, args.start, args.end)
    if not trades.empty:
        return analysis.trades_to_bars(trades, args.rule), trades
    for timespan in ("second", "minute", "hour", "day"):
        bars = store.load_bars([args.ticker], timespan, 1, args.start, args.end, args.source)
        if not bars.empty:
            return bars.set_index("time"), None
    raise SystemExit(f"no data stored for {args.ticker}")


def cmd_analyze(args, settings: Settings) -> int:
    import pandas as pd

    with Store(settings.db_path) as store:
        bars, trades = _load_prices(store, args)
    daily = bars["close"].groupby(bars.index.date).last()
    daily.index = pd.to_datetime(daily.index)
    print(f"{args.ticker.upper()} — daily statistics from the finest stored data\n")
    print(analysis.summary(daily).to_string())
    if trades is not None:
        print("\nper exchange:")
        print(analysis.exchange_breakdown(trades).to_string())
        rv = analysis.realized_volatility(trades.set_index("time")["price"])
        print(f"\nmean daily realised volatility (from ticks): {rv.mean():.4%}")
    return 0


def cmd_chart(args, settings: Settings) -> int:
    if args.ticks and _ticks_refused("chart --ticks", settings):
        return 2
    with Store(settings.db_path) as store:
        bars, trades = _load_prices(store, args)
    out_dir = settings.db_path.parent / "charts"
    stem = f"{args.ticker.upper()}-{datetime.now():%Y%m%d-%H%M%S}"
    paths = [charts.save(
        charts.candlestick(bars, f"{args.ticker.upper()} ({args.rule} bars)", args.sma, show_vwap=True),
        Path(args.out) if args.out else out_dir / f"{stem}-candles.html",
    )]
    if trades is not None:
        paths.append(charts.save(
            charts.exchange_share(analysis.exchange_breakdown(trades), f"{args.ticker.upper()} volume by exchange"),
            out_dir / f"{stem}-exchanges.html",
        ))
        if args.ticks:
            paths.append(charts.save(charts.tick_scatter(trades, f"{args.ticker.upper()} ticks"),
                                     out_dir / f"{stem}-ticks.html"))
    for p in paths:
        print(f"chart written to {p}")
    return 0


def _ticks_refused(what: str, settings: Settings) -> bool:
    """In bars-only mode a command that would read the tick files says so instead (exit 2)."""
    if settings.bars_only:
        print(f"{what}: bars-only mode does not read the tick files (BARS_ONLY=off allows it)", file=sys.stderr)
    return settings.bars_only


def cmd_export(args, settings: Settings) -> int:
    if args.kind == "trades" and _ticks_refused("export of trades", settings):
        return 2
    from liminal_libs.derive import day_zone

    zone = day_zone(resolve(args.ticker).asset)  # whole days where the series has them: New York, or UTC (crypto, fx)
    with Store(settings.db_path) as store:
        if args.kind == "trades":
            df = store.load_trades(args.ticker, args.source, args.start, args.end, zone=zone)
        else:
            df = store.load_bars([args.ticker], args.kind, 1, args.start, args.end, args.source, zone=zone)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"{len(df)} rows written to {out}")
    return 0


NOT_SCHEDULED = ("nothing is scheduled: the app fetches while it is in use (a chart open), and `sync` by hand "
                 "fetches while it runs (the user, 2026-11-01: no processes when the app is not in use); "
                 "`schedule remove` takes an old scheduled sync away")


def cmd_schedule(args, settings: Settings) -> int:
    """The scheduled sync of old (Windows Task Scheduler): shown or removed; none is installed any more."""
    if args.action == "install":
        print(NOT_SCHEDULED)
        return 2
    if sys.platform != "win32":
        print("nothing is scheduled on Linux/macOS; a crontab line of old can go")
        return 0
    if args.action == "show":
        return subprocess.call(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if args.action == "remove":
        return subprocess.call(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window popping up every hour
    runner = pythonw if pythonw.exists() else exe
    command = f'"{runner}" -m liminal_charts sync'
    return subprocess.call([
        "schtasks", "/Create", "/TN", TASK_NAME, "/TR", command,
        "/SC", "HOURLY", "/MO", str(args.every_hours), "/F",
    ])


# -- parser --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="liminal-charts", description=__doc__)
    p.add_argument("--source", default="massive", choices=sorted(providers()), help="data source (default: massive)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("watch", help="manage the watchlist")
    s.add_argument("action", choices=["add", "remove", "list"])
    s.add_argument("tickers", nargs="*")
    # no "trades": ticks are not fetched for history (only the live recorder records trades)
    s.add_argument("--resolution", default="auto", choices=["auto", "second", "minute"])
    s.add_argument("--every-feed", action="store_true",
                   help="add to every feed that can collect it (each span is still downloaded once)")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("sync", help="scheduled run: update and backfill the watchlist on every feed")
    s.add_argument("--only", dest="all_sources", action="store_false",
                   help="sync only the feed given by --source instead of all configured feeds")
    s.add_argument("--tickers", nargs="+", metavar="TICKER",
                   help="only these watched symbols, on every feed holding them, before anything else")
    s.add_argument("--fetch-only", action="store_true",
                   help="download only: no composites or derived frames after (the hourly sync builds them)")
    s.add_argument("--extras-only", action="store_true",
                   help="no feed: the publishers' due feeds, the stores' pass and the derived frames (the chart app's pass)")
    s.set_defaults(func=cmd_sync, all_sources=True)

    s = sub.add_parser("scan", help="check which symbols should have new data (free); --probe asks the source")
    s.add_argument("--probe", action="store_true",
                   help="confirm with the source: one request per symbol that looks behind")
    s.add_argument("--fetch", action="store_true", help="with --probe: download the new data found")
    s.add_argument("--limit", type=int, default=30, help="most symbols to probe (default 30)")
    s.add_argument("--only", nargs="+", metavar="TICKER", help="only these watched symbols")
    s.add_argument("--all-sources", action="store_true", help="scan every configured feed in turn")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("audit", help="compare stored data with another feed's copy (stores the comparison only)")
    s.add_argument("ticker", nargs="?", help="a watched symbol (default: random stored sessions)")
    s.add_argument("--day", type=_date, help="the session to audit (default: a random one)")
    s.add_argument("--random", type=int, default=1, help="random sessions to audit when no ticker is given")
    s.add_argument("--auditor", choices=sorted(providers()), help="feed asked for the second copy (default: --source)")
    s.add_argument("--list", action="store_true", help="show recent audits instead of running one")
    s.add_argument("--daily", action="store_true",
                   help="fast audit: every stock's daily bars held from its listing date? (no requests)")
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("updates", help="pause the updates of the symbols you have not picked (opened in the "
                                      "chart or on a watchlist) for some days, or resume them; history goes on")
    s.add_argument("action", nargs="?", choices=("pause", "resume"), help="without one: show the pause")
    s.add_argument("--days", type=float, default=7.0, help="how long (default: 7)")
    s.set_defaults(func=cmd_updates)

    s = sub.add_parser("gov", help="the regulators' news and rates (the Gov view): fetch the due ones now, "
                                  "at most hourly each, and show each source's state")
    s.add_argument("--all", action="store_true", help="ask every source now, not only the due ones (once each)")
    s.set_defaults(func=cmd_gov)

    s = sub.add_parser("feeds", help="list the feeds and their state; reopen a closed one")
    s.add_argument("action", nargs="?", default="list", choices=["list", "reopen"])
    s.add_argument("name", nargs="?", help="reopen: the feed")
    s.add_argument("ticker", nargs="?", help="reopen: only this symbol")
    s.set_defaults(func=cmd_feeds)

    s = sub.add_parser("plugins", help="the data providers (a plugin each): keys set or not, what each serves")
    s.add_argument("--json", action="store_true", help="as JSON (tools/set-key.ps1's menu); no key's value")
    s.set_defaults(func=cmd_plugins, settings_optional=True)

    s = sub.add_parser("ai", help="the AI assistants (plugins): ask QUESTION..., models, connect, evaluations, "
                                 "or judge --id EV_ID...; --assistant and --model choose; offline sends nothing")
    s.add_argument("action", choices=["ask", "models", "connect", "evaluations", "judge"])
    s.add_argument("question_words", nargs="*", help="ask: the question")
    s.add_argument("--assistant", help="claude, grok or codex (default: LIMINAL_ASSISTANT, else the first set up)")
    s.add_argument("--model", help="a model of its table (default: its plan's)")
    s.add_argument("--answer", default="text", choices=["text", "verdict", "fields"], help="ask: the answer form")
    s.add_argument("--scale", help="ask, a verdict: the verdicts allowed, comma separated")
    s.add_argument("--fields", help="ask, fields: the JSON fields wanted, comma separated")
    s.add_argument("--context", action="append", help="ask: a text file given as material (repeatable)")
    s.add_argument("--question", help="judge: what to do with the answers (task free); ask: the question, as one argument")
    s.add_argument("--task", default="compare", choices=["compare", "score", "reconcile", "free"], help="judge: how")
    s.add_argument("--id", action="append", help="judge: an evaluation kept here (repeatable)")
    s.add_argument("--kind", help="evaluations: question or judge")
    s.add_argument("--limit", type=int, default=20, help="evaluations: how many (default 20)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_ai)

    s = sub.add_parser("sourcing", help="which source delivers what: each provider's report (subscription, limits, "
                                       "capabilities) and the plan by cost and capability (no requests)")
    s.add_argument("--json", action="store_true", help="as JSON")
    s.set_defaults(func=cmd_sourcing)

    s = sub.add_parser("capabilities", help="scan what each provider's plan really gives and which plan that is "
                                           "(a few requests each, within its caps; used where you set no plan); "
                                           "shown in `sourcing` and the Plugins view")
    s.add_argument("name", nargs="?", help="one provider (default: every one with a scan)")
    s.set_defaults(func=cmd_capabilities)

    s = sub.add_parser("deps", help="check every external dependency (data feeds, calendars, icons, files)")
    s.set_defaults(func=cmd_deps)

    s = sub.add_parser("gui", help="open the chart app in your browser (its data through the fetcher)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-rescan", action="store_true",
                   help="do not rescan the provider plugins' capabilities and plans at the start")
    s.add_argument("--alone", action="store_true",
                   help="page and data in this one process, no fetcher (nothing collected: a read-only copy, a test)")
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("plan", help="find which plan a feed's key has (--source, default massive): its plugin's "
                                    "capability scan, a few small requests; the plan found is used where you set none")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("initialize", help="one-time: every NYSE and Nasdaq listing and every CME Group futures "
                                          "product onto the watchlist, most traded first (no requests)")
    s.add_argument("--exchanges", default="XNYS,XNAS,CME",
                   help="exchange MICs; CME is CME Group's four (XCME, XNYM, XCBT, XCEC) (default XNYS,XNAS,CME)")
    s.add_argument("--by", choices=("value", "shares"), default="value",
                   help="rank by traded value (default) or shares traded")
    s.add_argument("--dry-run", action="store_true", help="show what would be added")
    s.set_defaults(func=cmd_initialize)

    s = sub.add_parser("probe", help="find the finest resolution your plan allows for a ticker")
    s.add_argument("ticker")
    s.add_argument("--watch", action="store_true", help="also add it to the watchlist")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("download", help="one-off bar download")
    s.add_argument("tickers", nargs="+")
    s.add_argument("--from", dest="start", type=_date)
    s.add_argument("--to", dest="end", type=_date)
    s.add_argument("--timespan", default="minute")
    s.add_argument("--multiplier", type=int, default=1)
    s.set_defaults(func=cmd_download)

    s = sub.add_parser("market-daily", help="daily bars for the whole US market")
    s.set_defaults(func=cmd_market_daily)

    s = sub.add_parser("exchanges", help="list exchanges")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_exchanges)

    s = sub.add_parser("info", help="reference details for a ticker")
    s.add_argument("ticker")
    s.set_defaults(func=cmd_info)

    # the fetcher's and the service's arguments are the integrator's (liminal-integrator), the same
    # on its own command line; without it the commands say where it is
    found = integrator()
    s = sub.add_parser("fetcher", help="the fetcher (liminal-integrator): serve (hold it here, its page in the "
                                      "browser), status, request SYMBOL..., pass, or cancel --id N")
    if found is not None:
        found.add_fetcher_arguments(s)
    else:
        s.add_argument("action", nargs="?", default="serve")
    s.set_defaults(func=cmd_fetcher)

    s = sub.add_parser("service", help="the fetcher as a service (liminal-integrator; your decision: it then updates "
                                      "what it can while you are away): install, remove, status, or run")
    if found is not None:
        found.add_service_arguments(s)
    else:
        s.add_argument("action", nargs="?", default="status")
    s.set_defaults(func=cmd_service)

    s = sub.add_parser("history", help="closed months of bars in DuckDB: run (copy the due ones, as the hourly "
                                      "sync starts it) or status")
    s.add_argument("action", nargs="?", default="status", choices=["run", "status"])
    s.add_argument("--feed", action="append", help="run: this feed only (repeatable)")
    s.add_argument("--budget", type=float, default=HISTORY_BUDGET_S, help="run: seconds of copying (default: 1200)")
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("mongo", help="the MongoDB server here (127.0.0.1 only): start, stop or status")
    s.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status"])
    s.set_defaults(func=cmd_mongo)

    s = sub.add_parser("stores", help="each plugin's stores by data type (SQLite, DuckDB, MongoDB): list, pick "
                                     "PLUGIN TYPE ENGINE|auto, or settle")
    s.add_argument("action", nargs="?", default="list", choices=["list", "pick", "settle"])
    s.add_argument("plugin", nargs="?")
    s.add_argument("type", nargs="?", help="bars, documents, series or files")
    s.add_argument("engine", nargs="?", help="sqlite, duckdb, mongodb, files, or auto")
    s.set_defaults(func=cmd_stores)

    s = sub.add_parser("datasets", help="research datasets: list, fetch (or refresh), show NAME [SERIES]")
    s.add_argument("action", choices=["list", "fetch", "show"], nargs="?", default="list")
    s.add_argument("names", nargs="*", help="fetch: datasets (default all); show: a dataset and one of its series")
    s.add_argument("--force", action="store_true", help="fetch: also files fetched recently (closed files never)")
    s.add_argument("--tail", type=int, default=12, help="show: how many of the newest values")
    s.add_argument("--as-of", type=_date, help="show: the values as public on that date (YYYY-MM-DD)")
    s.set_defaults(func=cmd_datasets)

    s = sub.add_parser("derive", help="build each feed's candles for the time frames it does not send (marked derived)")
    s.add_argument("--tickers", nargs="+", metavar="TICKER", help="only these symbols")
    s.set_defaults(func=cmd_derive)

    s = sub.add_parser("live", help="live trades (crypto: the exchanges' public streams; stocks: Alpaca's IEX stream): "
                                     "run, start (background), status, or stop (every recorder on this machine)")
    s.add_argument("action", nargs="?", choices=["run", "start", "status", "stop"], default="run")
    s.set_defaults(func=cmd_live)

    s = sub.add_parser("host", help="run a resident host while a client is attached to it (host.py): "
                                     "fetcher; live, the recorder; mongo, the MongoDB server's keeper; "
                                     "feed-NAME, a feed's run; history, the closed months' copy")
    s.add_argument("name", help="which host: fetcher, live, mongo, feed-NAME, history")
    s.set_defaults(func=cmd_host)

    s = sub.add_parser("kills", help="the kill policy: killed feeds and each feed's outcomes; kill or release one")
    s.add_argument("action", choices=["list", "kill", "release"], nargs="?", default="list")
    s.add_argument("feed", nargs="?", help="kill/release: the feed (massive, alpaca, coinbase, bitstamp, iex ...)")
    s.add_argument("--kind", choices=["live", "fetch", "both"], default="both")
    s.add_argument("--hours", type=float, default=24.0, help="list: how far back; kill: for how long")
    s.add_argument("--why", default="no reason given", help="kill: the reason kept with it")
    s.set_defaults(func=cmd_kills)

    s = sub.add_parser("pruned", help="bad data: each feed's pruned list (list, scan, release; repair-ticks rebuilds "
                                      "a feed's bars from its ticks, keeping what changes)")
    s.add_argument("action", choices=["list", "scan", "release", "repair-ticks"], nargs="?", default="list")
    s.add_argument("--feed", help="the feed (scan and repair-ticks: required; list: all feeds without it)")
    s.add_argument("--tickers", nargs="*", help="symbols (all of the feed's without it)")
    s.add_argument("--timespan", default="minute", help="release: the bar's frame")
    s.add_argument("--ts", type=int, help="release: the bar's start (ms)")
    s.add_argument("--limit", type=int, default=30, help="list: entries shown per symbol")
    s.set_defaults(func=cmd_pruned)

    s = sub.add_parser("inventory", help="what a symbol holds on every feed, frame by frame (sent, *derived, not built)")
    s.add_argument("ticker")
    s.add_argument("--json", action="store_true", help="everything, as JSON")
    s.set_defaults(func=cmd_inventory)

    s = sub.add_parser("provenance", help="how derived results were made (bars from ticks, composites, quality, audits)")
    s.add_argument("product", nargs="?", help="product prefix, e.g. composite:BTCUSD or alpaca:BTCUSD")
    s.add_argument("--kind", choices=["bars-from-ticks", "bars-from-bars", "composite", "quality", "quality-prior",
                                       "audit"])
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("-v", "--verbose", action="store_true", help="also the settings and inputs")
    s.set_defaults(func=cmd_provenance)

    s = sub.add_parser("status", help="what is stored, limits, and recent runs")
    s.add_argument("--runs", type=int, default=10)
    s.add_argument("--counts", action="store_true", help="also count the bars (reads every bar: hours on a large file)")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("admin", help="database maintenance")
    s.add_argument("action", choices=["check", "vacuum", "compact", "backup", "reset-denied", "split-feeds", "memory"],
                   help="vacuum: compact the files in place (small files only: it holds every writer off); "
                        "compact: copy each file compacted beside it (VACUUM INTO), swapped in with --swap; "
                        "split-feeds: move every feed's data from the single-file layout to files of its own; "
                        "memory: each app process's own memory and disk reads, apart from Windows' file cache")
    s.add_argument("--dest")
    s.add_argument("--ticks", action="store_true", help="backup: also copy the tick files (large)")
    s.add_argument("--seconds", type=float, default=5.0, help="memory: how long to watch disk reads (0: not)")
    s.add_argument("--file", help="compact: only this database file (default: every file)")
    s.add_argument("--swap", action="store_true",
                   help="compact: put each copy in the file's place, keeping the old file beside it; only with the "
                        "chart app, the sync and the live recorder stopped")
    s.set_defaults(func=cmd_admin)

    for name, func, helptext in (
        ("analyze", cmd_analyze, "statistics from stored data"),
        ("chart", cmd_chart, "write interactive HTML charts"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("ticker")
        s.add_argument("--from", dest="start", type=_date)
        s.add_argument("--to", dest="end", type=_date)
        s.add_argument("--rule", default="1min", help="bar size built from ticks, e.g. 1s, 1min, 1h, 1D")
        s.set_defaults(func=func)
        if name == "chart":
            s.add_argument("--sma", type=int, nargs="*", default=[])
            s.add_argument("--ticks", action="store_true", help="also plot every trade")
            s.add_argument("--out")

    s = sub.add_parser("export", help="export stored data to CSV")
    s.add_argument("ticker")
    s.add_argument("--kind", default="trades", choices=["trades", "second", "minute", "hour", "day"])
    s.add_argument("--from", dest="start", type=_date)
    s.add_argument("--to", dest="end", type=_date)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("schedule", help="install/remove the scheduled sync (Windows Task Scheduler)")
    s.add_argument("action", choices=["install", "remove", "show"])
    s.add_argument("--every-hours", type=int, default=1)
    s.set_defaults(func=cmd_schedule)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if isinstance(getattr(args, "ticker", None), str) and args.command not in ("probe",):
        args.ticker = resolve(args.ticker).ticker  # data commands accept SPCX::NASDAQ, USDEUR, NQ1! ...
    try:
        try:
            settings = load_settings()
        except ConfigError:
            if not getattr(args, "settings_optional", False):
                raise
            return args.func(args, None)  # the providers are listed even with a wrong setting
        from liminal_libs import sourcing
        from liminal_libs.storage.feeds import set_tick_access

        from liminal_libs.storage import set_read_only

        set_read_only(settings.read_only)  # LIMINAL_READONLY: every store this command opens reads only
        settings = sourcing.Scans.apply(settings)  # each plugin's plan as its scan found it, unless you set it

        set_tick_access(not settings.bars_only)  # bars-only: no store this command opens reads ticks
        return args.func(args, settings)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except TicksBlocked as e:
        print(str(e), file=sys.stderr)
        return 2
