"""The Fetcher view (the queue) and each plugin's fetching status (its page in the Plugins view).

The user, 2026-10-31: "the fetcher will then just show the queue of all resources being downloaded
like symbols", "the plugin status will be available in the plugins:: plugin page". The Fetcher lists
the fetch queue (storage/queue.py) across plugins: every resource waiting, being fetched, held,
failed or done, by plugin, kind and state; a row left running by a process that is gone says so.
A plugin's page shows its own: whether it is fetching (and what), waiting, paused or stopped, its
queue, its feeds' requests against their caps, their limits and runs, and its lines of the log.
Read from the queue, the ledgers, the kills, the run locks and the log: no requests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from liminal_libs import collector, killswitch
from liminal_libs.sources import feed_profiles, providers, publishers
from liminal_libs.sources.registry import label, plugin_feeds, plugin_of, publisher_of, publisher_parts
from liminal_libs.symbols import ASSETS, resolve
from .activity import ET, fetching_feeds, limits, log_path, log_tail, symbol_status

RESOLUTIONS = ("day", "minute", "second")  # the bars a feed sends, coarsest first


def _plugins() -> dict[str, str]:
    """Every plugin by name with its title (a part's rows are its plugin's)."""
    out = {n: p.title for n, p in providers().items() if plugin_of(n) == n}
    out.update({n: p.title for n, p in publishers().items() if publisher_of(n) == n})
    return out


def _part_label(plugin: str, part: str) -> str:
    if part == plugin:
        return ""
    if part in providers():
        return label(part)
    pub = publishers().get(part)
    return pub.title if pub else part


def _row(r: dict, gone: set) -> dict:
    """A queue row for the page; `gone`: the owners (instance, pid) of running rows whose process is no more
    (store.queue_gone: this installation's looked up, another's judged by its claims, so a job the suite on
    the other system is fetching is not shown stopped)."""
    out = {**r, "partLabel": _part_label(r["plugin"], r["part"])}
    if r["state"] == "running" and r["pid"] and (r.get("instance"), r["pid"]) in gone:
        out["state"], out["detail"] = "stopped", "its process ended before it"
    if r["kind"] == "bars":
        out["ticker"] = r["key"]
        head = (r["detail"] or "").split(":", 1)[0]  # as planned ("stock: new data"; a future's ends so too)
        out["asset"] = head if head in ASSETS else _asset(r["key"])
    return out


def _asset(ticker: str) -> str | None:
    try:
        return resolve(ticker).asset
    except (ValueError, KeyError):
        return None


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, ET).date().isoformat()


def progress(store, feed: str, ticker: str, tier, today, now: datetime) -> dict | None:
    """How much of a symbol's bars a feed has downloaded (the user, 2026-11-02: "how much of the
    symbol has been downloaded respective of the plugin its associated with on the queue"): the
    share of the history the feed is to hold (from its plan's floor or the listing, to today or to
    the symbol's last listed day: activity.symbol_status), its state there, and what is stored at
    each resolution the feed sends (first and last day). None: the feed does not watch it."""
    item = next(iter(store.watchlist(feed, ticker)), None)
    if item is None:
        return None
    stored: dict[str, list[str]] = {}
    edges: dict[str, tuple] = {}
    for ts in RESOLUTIONS:
        e = store.bar_edges(feed, ticker, ts)
        if e:
            edges[ts] = e
            stored[ts] = [_day(e[0]), _day(e[1])]
    finest = edges.get("minute") or edges.get("second") or edges.get("day")
    floor = collector.history_floor(tier, today, item["asset"])
    st = symbol_status(item, finest, floor, today, now, tier, None)
    return {"share": st["progress"], "state": st["state"], "target": st["target"], "stored": stored,
            "retiredOn": item["retired_on"].isoformat() if item.get("retired_on") else None}


def view(store, plugin: str | None = None, kind: str | None = None, state: str | None = None,
         limit: int = 300, settings=None) -> dict:
    """The Fetcher: the queue's rows (running, waiting, held, failed, the newest done), counted by
    plugin, kind and state, with each plugin's title; with `settings`, each symbol's bars row says
    how much of it its feed has downloaded (progress)."""
    raw = store.queue_rows(plugin, kind, None if state == "stopped" else state, limit)
    gone = store.queue_gone(raw)
    rows = [_row(r, gone) for r in raw]
    if state == "stopped":
        rows = [r for r in rows if r["state"] == "stopped"]
    if settings is not None:
        tiers = {n: p.tier for n, p in feed_profiles(settings).items()}
        now = datetime.now(timezone.utc)
        today = now.astimezone(ET).date()
        for r in rows:
            if r["kind"] == "bars" and r["part"] in tiers:
                try:
                    r["progress"] = progress(store, r["part"], r["key"], tiers[r["part"]], today, now)
                except Exception:  # noqa: BLE001 - a row's measure never fails the queue
                    r["progress"] = None
    counts = _counts(store)
    titles = _plugins()
    totals: dict[str, int] = {}
    for by in counts["plugins"].values():
        for s, n in by.items():
            totals[s] = totals.get(s, 0) + n
    return {"rows": rows, "counts": counts, "totals": totals, "limit": limit,
            "plugins": [{"name": n, "title": titles.get(n, n)} for n in counts["plugins"]],
            "kinds": sorted(counts["kinds"])}


def _counts(store) -> dict:
    """The queue's counts by plugin and kind, a row running by a process that is gone counted stopped."""
    counts = store.queue_counts()
    running = store.queue_rows(state="running", limit=1000)
    gone = store.queue_gone(running)
    for r in running:
        if r["pid"] and (r.get("instance"), r["pid"]) in gone:
            for by in (counts["plugins"].setdefault(r["plugin"], {}), counts["kinds"].setdefault(r["kind"], {})):
                by["running"] = by.get("running", 1) - 1
                by["stopped"] = by.get("stopped", 0) + 1
                if not by["running"]:
                    del by["running"]
    return counts


def _parts(name: str) -> tuple[list[str], list[str]]:
    """A plugin's feeds (a feed plugin's, its own first) and its publishers (its parts)."""
    if name in providers():
        return plugin_feeds(name), publisher_parts(name)
    return [], [n for n in (name, *publisher_parts(name)) if n in publishers()]


def _mentions(line: str, words: list[str]) -> bool:
    low = line.lower()
    return any(f"{w.lower()}:" in low or f"{w.lower()} " in low for w in words)


def plugin_status(store, settings, db_path: Path, name: str, now: datetime | None = None, log_lines: int = 40) -> dict:
    """A plugin's fetching on its page (see the module's docstring)."""
    if name not in providers() and name not in publishers():
        raise LookupError(f"no plugin named {name}")
    now = now or datetime.now(timezone.utc)
    feeds, pubs = _parts(name)
    raw = store.queue_rows(plugin=name, limit=60)
    gone = store.queue_gone(raw)
    rows = [_row(r, gone) for r in raw]
    counts = _counts(store)["plugins"].get(name, {})
    held = fetching_feeds(db_path)
    runs_going = sorted(f for f in feeds if f in held) + ([name] if name in held and name not in feeds else [])
    pause = collector.pause_state(store, now)
    kill = next((k for k in (killswitch.killed(store, n, "fetch", now) for n in (name, *feeds)) if k), None)
    running = [r for r in rows if r["state"] == "running"]
    if kill:
        state, why = "killed", kill.get("why") or ""
    elif pause:
        state, why = ("halted", "every fetch halted until you resume") if pause.get("halt") else \
            ("paused", f"background fetching paused until {pause['until'][:16].replace('T', ' ')} UTC")
    elif running or runs_going:
        state, why = "fetching", ", ".join(r["title"] for r in running[:3]) or f"{', '.join(map(label, runs_going))} running"
    elif counts.get("waiting"):
        state, why = "waiting", f"{counts['waiting']:,} waiting for its next run"
    else:
        state, why = "idle", "nothing waiting"
    feed_rows = []
    for f in feeds:
        s = settings.for_source(f)
        feed_rows.append({"name": f, "label": label(f), "tier": s.tier.name, "rpm": s.tier.requests_per_minute,
                          "today": store.requests_today(f), "perDay": s.max_requests_per_day,
                          "perRun": s.max_requests_per_run, "running": f in held})
    limit_rows = [r for r in limits(store, settings, now) if r["source"] in feeds] if feeds else []
    runs = [r for r in store.runs(60) if r["source"] in (*feeds, name)][:10]
    for r in runs:
        try:
            a = datetime.fromisoformat(r["started_at"])
            b = datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else now
            r["seconds"] = round((b.astimezone(timezone.utc) - a.astimezone(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            r["seconds"] = None
    words = [*feeds, *pubs, name, *(publishers()[p].title for p in pubs)]
    log = [line for line in log_tail(log_path(db_path), 1500) if _mentions(line, words)][-log_lines:]
    return {"name": name, "state": state, "why": why, "running": running, "counts": counts, "rows": rows,
            "feeds": feed_rows, "limits": limit_rows, "runs": runs, "log": log, "parts": pubs,
            "paused": pause, "kill": kill}
