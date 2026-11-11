"""The Plugins view (rail icon: a plug; the user, 2026-10-27): every data provider plugin (a feed,
a source: sources/registry.py), its version and origin (built in, or an installed package), its
plan and where that came from, its limits and capabilities, its last capability scan with a
Rescan, and its part in the delivery scheme; the plugin API this app offers.

Each plugin refreshes its own plan info: its scan asks the provider what the key really gets and
may name the plan that shows (registry.Scanned), applied wherever the user set none
(config.with_detected). Scans run on each chart app start, weekly in the sync, and from this view.

A plugin with several feeds (Alpaca: alpaca, and its nights on Blue Ocean, alpaca (blue ocean);
Tiingo: tiingo and tiingo (forex): Provider.part_of) is one entry: its feeds listed by the plugin's
name (registry.label), their capabilities, scans, eras, requests and symbols together.
While a rescan runs, each feed it has still to ask says "scanning" (the user, 2026-10-29: "when i
hit rescan all, i want the status of each plug in to say scanning, not 'ready'"); one asked in this
run is back to its state, and a feed with no scan (a public one) or none it can ask keeps its own.

A publisher can be part of a feed plugin (Massive's headlines: Publisher.part_of), named in it
(`publishers`): a section of its page, never an entry of its own, as Federal's agencies are of
Federal's (the user, 2026-10-31: "massive headlines should not be separate from massive same thing
with federal agencies and federal"); what it reads comes from publications.view.

Each plugin points to the databases its data is in (registry.plugin_databases, API 1.8): its feeds'
files, its datasets' and publishers', its rows in the main file, each with what it holds of the
plugin's and its size here (the user, 2026-10-31: "the plugin will point to the databases it stores
the data in").

Each plugin is set up here: every variable it reads (registry.plugin_keys: its keys, its feeds' and
publishers', a detail of yours such as GOV_CONTACT) with whether it is set, typed into the view and
saved as your user environment variable (ChartApp.set_key); its plan chosen (ChartApp.set_plan).

Read from the settings, the plugins' reports, the scans kept (kv sourcing.SCAN_KEY), the watchlist
and the request ledger: no requests. A key's value never leaves: only whether it is set.
"""
from __future__ import annotations

from dataclasses import replace

from datetime import date, datetime

from liminal_libs.sources import apis as apis_mod
from liminal_libs.sources import providers
from liminal_libs.sources.registry import (PLUGIN_API, assistants, database_row, errors, key_row, label, plugin_databases,
                                          plugin_feeds, plugin_keys, plugin_of, publisher_parts)

GROUPS = ("Feeds with keys", "Public feeds", "AI assistants")
RECENT_EVALUATIONS = 8  # an assistant's last answers shown on its page


def _fresh(latency: float) -> str:
    return "real time" if latency < 60 else f"{latency / 60:.0f} min delayed" if latency < 86_400 else "end of day"


def _state(keys_set, served, kill) -> str:
    """ready, partly (only what it serves without its keys), no key, killed."""
    if kill:
        return "killed"
    if keys_set is False:
        return "partly" if served else "no key"
    return "ready"


def _asked_since(at, since) -> bool:
    """Whether a scan kept at `at` was asked at or after `since` (ISO times; one without an offset
    is local time, as the scans keep it)."""
    try:
        a, b = (t if t.tzinfo else t.astimezone() for t in (datetime.fromisoformat(at), datetime.fromisoformat(since)))
    except (TypeError, ValueError):
        return False
    return a >= b


def _scanning(p, running: dict | None, scan: dict | None, state: str) -> bool:
    """Whether the rescan going now has this feed still to ask: it covers the feed (every plugin,
    the feed, or the plugin it is part of), the feed has a scan and is set up (ready, or partly:
    what it serves without its keys), and its kept scan is older than the rescan."""
    if not running or not p.has_scan or state not in ("ready", "partly"):
        return False
    if running.get("name") not in (None, p.name, plugin_of(p.name)):
        return False
    return not ((scan or {}).get("at") and _asked_since(scan["at"], running.get("since")))


def _fold(entry: dict, feeds: list[dict]) -> dict:
    """A plugin's entry with the feeds part of it folded in (`feeds`: its own first): what each can
    do, its scan's answers and its eras named by the feed; requests, symbols and load summed (each
    feed's requests are its own in the ledger, its caps the plugin's: one account)."""
    every = [entry, *feeds]
    for e in every:
        for row in (*e["capabilities"], *e["scan"]["results"], *e["delivery"]):
            row["feed"] = e["label"]
    out = {**entry,
           "feeds": [{"name": e["name"], "title": e["title"], "label": e["label"], "about": e["about"], "state": e["state"],
                      "serves": e["serves"], "scanAt": e["scan"]["at"], "archive": e["archive"]} for e in every],
           "archive": all(e["archive"] for e in every),  # a closed archive as a whole: each of its feeds is one
           "assets": sorted({a for e in every for a in e["assets"]}),
           "capabilities": [c for e in every for c in e["capabilities"]],
           "capabilitiesWithKey": any(e["capabilitiesWithKey"] for e in every),
           "streams": [s for e in every for s in e["streams"]],
           # a note names its feed when the plugin has several (Cboe's indices are an archive, its VIX futures live)
           "notes": [*(f"{entry['label']}: {n}" if feeds else n for n in entry["notes"]),
                     *(f"{e['label']}: {n}" for e in feeds for n in e["notes"])],
           "options": list({o["name"]: o for e in every for o in e["options"]}.values()),
           # what it attaches to, its feeds' too, each API once (plugin API 2.2: Tiingo's forex part on the same API)
           "apis": list({a["name"]: a for e in every for a in e.get("apis", [])}.values()),
           "apiFacts": {k: v for e in every for k, v in e.get("apiFacts", {}).items()},
           "delivery": [d for e in every for d in e["delivery"]],
           "watched": sum(e["watched"] for e in every), "load": round(sum(e["load"] for e in every), 3),
           "usage": {**entry["usage"], "today": sum(e["usage"]["today"] for e in every)}}
    ats = [e["scan"]["at"] for e in every if e["scan"]["has"] and e["scan"]["at"]]
    out["scan"] = {"has": any(e["scan"]["has"] for e in every), "at": min(ats) if ats else None,
                   "results": [r for e in every for r in e["scan"]["results"]],
                   "running": any(e["scan"]["running"] for e in every)}
    if any(e["state"] == "scanning" for e in every) and entry["state"] in ("ready", "partly", "scanning"):
        out["state"] = "scanning"
    return out


def _options(p, settings) -> list[dict]:
    """Its other settings (Provider.options: Tiingo's TIINGO_LISTINGS, B3's B3_HISTORY_YEARS), each
    with its value here and where it comes from, and its choices where the record gives them
    (plan_choices): chosen in the view like a plan, else set with the key script."""
    mine = settings.variables or {}
    out = []
    for v in p.options:
        choices = list(p.plan_choices.get(v, ()))
        out.append({"name": v, "value": mine.get(v) or (choices[0] if choices else None),
                    "origin": "you" if mine.get(v) else "default", "choices": choices})
    return out


def _recent(r: dict) -> dict:
    """An evaluation row for the view: what was asked and answered, what it cost; the text cut short."""
    text = r.get("text") or r.get("error") or ""
    return {"id": r["id"], "at": r["at"], "model": r["model"], "kind": r["kind"], "verdict": r.get("verdict"),
            "text": text[:240] + ("…" if len(text) > 240 else ""), "costUsd": r.get("cost_usd"),
            "tokens": (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0), "stop": r.get("stop"),
            "question": ((r.get("argument") or {}).get("question") or "")[:160]}


def _ai_limits(p, plan) -> str:
    """What the vendor publishes for the plan chosen, in a line (the Limits panel's text)."""
    if plan is None:
        return "no plan chosen"
    bits = [f"{plan.rpm:,} requests a minute" if plan.rpm else "", f"{plan.tpm:,} tokens a minute" if plan.tpm else "",
            f"{plan.rpd:,} requests a day" if plan.rpd else "", f"a spend cap of {plan.spend_cap}" if plan.spend_cap else ""]
    said = ", ".join(b for b in bits if b)
    return f"{p.vendor} publishes for the {plan.title} plan: {said}" if said else f"{p.vendor} publishes no limits for the {plan.title} plan"


def _assistant_entry(p, store, settings, scans: dict, running: dict | None) -> dict:
    """An AI assistant plugin's entry (the AI assistants group): its key and account (set or not),
    its plan, model and caps, the vendor's written-in tables, its last Connect, what it spent today
    here and on the whole machine, and its last answers (the user, 2026-11-03: the three AI plugins
    "compliant with the rest of the plugin structure (setup / connecting methods / etc)")."""
    from liminal_libs.ratelimit import MachineLedger

    own = p.own(settings)
    plan = p.plan_of(own.plan) if own else p.free_plan
    keys_set = bool(p.keys_set(settings))
    served = bool(p.served(settings))
    state = "no key" if not keys_set else "no access" if not served else "ready"
    scan = scans.get(p.name)
    scanning = _scanning(p, running, scan, state)
    mine = settings.variables or {}
    found = (scan or {}).get("plan") or {}
    differs = [{"name": v, "yours": mine[v], "found": found[v]} for v in found
               if mine.get(v) and str(mine[v]).strip().lower() != str(found[v]).strip().lower()]
    today = store.evaluations_today(p.name) if hasattr(store, "evaluations_today") else {"requests": 0, "tokens": 0, "costUsd": 0.0}
    key = own.key if own else None
    machine = ({"requests": MachineLedger(p.name, key).today(), "tokens": MachineLedger(f"{p.name}:tokens", key).today()}
               if key else {"requests": 0, "tokens": 0})
    caps = own.caps if own else p.caps
    free = p.free_note(settings)
    recent = store.evaluations(assistant=p.name, limit=RECENT_EVALUATIONS) if hasattr(store, "evaluations") else []
    entry = {
        "name": p.name, "title": p.title, "label": p.name, "about": p.about, "origin": p.origin, "version": p.version,
        "api": p.api, "apis": apis_mod.as_dicts(p.apis), "apiFacts": apis_mod.facts_of(p), "signup": p.signup, "group": GROUPS[2], "archive": False, "kind": "assistant",
        "assets": [], "keyless": [], "serves": [], "configured": keys_set, "keysSet": keys_set,
        "keys": [key_row(k, settings) for k in p.keys], "publishers": [],
        "state": "scanning" if scanning else state, "kill": None,
        "plan": {"tier": own.plan if own else (p.free_plan.name if p.free_plan else ""), "vars": p.plan_state(settings),
                 "found": found, "alike": {}, "differs": differs, "note": p.plan_note},
        "options": _options(p, settings),
        "limits": {"published": _ai_limits(p, plan)},
        "capabilities": [], "capabilitiesWithKey": False, "streams": [], "notes": [free] if free else [],
        "scan": {"has": p.has_scan, "at": (scan or {}).get("at"),
                 "results": [{"what": w, "available": bool(ok), "detail": d} for w, ok, d in (scan or {}).get("results", [])],
                 "running": scanning},
        "usage": {"today": today["requests"], "perDay": caps.requests_per_day, "perRun": None},
        "watched": 0, "delivery": [], "load": 0.0,
        "ai": {**p.table_rows(), "plan": own.plan if own else None, "model": own.model if own else p.default_model,
               "effort": own.effort if own else "", "planAccess": bool(plan is not None and plan.api_access), "free": free,
               "caps": {"requestsPerDay": caps.requests_per_day, "tokensPerDay": caps.tokens_per_day,
                        "maxOutputTokens": caps.max_output_tokens},
               "usage": {"today": today, "machine": machine}, "recent": [_recent(r) for r in recent]},
    }
    return _fold(entry, [])


def _plan(p, settings, scan: dict | None) -> dict:
    """Its plan here: each plan variable with its origin (you, its scan, the default), what its last
    scan found, where that and your own setting disagree, and what its scan cannot tell."""
    found = (scan or {}).get("plan") or {}
    alike = {k: list(v) for k, v in ((scan or {}).get("alike") or {}).items()}
    mine = settings.variables or {}
    vars_ = p.plan_state(settings)
    differs = [{"name": v, "yours": mine[v], "found": found[v]} for v in found
               if mine.get(v) and str(mine[v]).strip().lower() not in
               {str(x).strip().lower() for x in (found[v], *alike.get(v, ()))}]
    return {"tier": settings.for_source(p.name).tier.name, "vars": vars_, "found": found, "alike": alike,
            "differs": differs, "note": p.plan_note}


def view(store, settings, feeds: dict, scheme, scans: dict, running: dict | None = None,
         today: date | None = None) -> dict:
    """Every plugin for the view (see the module's docstring); `running`: the rescan going now
    ({"name": one provider or None for all, "since": iso}) or None."""
    from liminal_libs import killswitch

    today = today or scheme.today
    counts = store.watch_counts()
    entries = {}
    for name, p in providers().items():
        prof = feeds.get(name)  # None: not set up here at all (Massive without its key)
        scan = scans.get(name)
        report = p.report(prof, today, settings, scan) if prof is not None else None
        keys_set = p.keys_set(settings)
        # its key not set (serving nothing yet): what the plan would give with one, so the view tells
        # what a key is for before it is entered (seen 2026-11-03: J-Quants' panel showed nothing)
        would = p.report(replace(prof, assets=frozenset(p.assets)), today, settings, scan) \
            if prof is not None and not prof.assets and keys_set is False and p.assets else None
        served = sorted(prof.assets) if prof is not None else []
        kill = killswitch.killed(store, name, "fetch")
        view_ = settings.for_source(name)
        delivery = [{"job": e.resolution, "asset": e.asset, "market": e.market, "start": e.start.isoformat(),
                     "end": e.end.isoformat() if e.end else None, "first": e.sources[0] == name,
                     "unique": e.unique, "others": [label(s) for s in e.sources if s != name], "symbols": e.symbols}
                    for e in scheme.eras if name in e.sources]
        state = _state(keys_set, served, kill)
        scanning = _scanning(p, running, scan, state)
        entries[name] = {
            "name": name, "title": p.title, "label": label(name), "about": p.about, "origin": p.origin,
            "version": p.version, "api": p.api,
            "apis": apis_mod.as_dicts(p.apis), "apiFacts": apis_mod.facts_of(p),  # what it attaches to (plugin API 2.2)
            "signup": p.signup,
            "group": GROUPS[0] if p.keys else GROUPS[1], "archive": p.archival, "kind": p.kind,
            "assets": sorted(p.assets), "keyless": sorted(p.keyless), "serves": served,
            "configured": prof is not None, "keysSet": keys_set,
            "keys": [key_row(k, settings) for k in plugin_keys(name)],  # its feeds' and publishers' too
            "publishers": publisher_parts(name),  # in this plugin (Massive's headlines)
            "state": "scanning" if scanning else state, "kill": kill,
            "plan": _plan(p, settings, scan), "options": _options(p, settings),
            "limits": ({**report.limits.__dict__, "rate": list(report.limits.rate) if report.limits.rate else None}
                       if report else {"published": p.limits}),
            "capabilities": [{"asset": c.asset, "resolution": c.resolution, "market": c.market, "since": str(c.since),
                              "fresh": _fresh(c.latency), "perRequest": c.per_request}
                             for c in ((would or report).capabilities if (would or report) else ())],
            "capabilitiesWithKey": would is not None,
            "streams": [{"asset": a, "what": w} for a, w in (report.streams if report else ())],
            "notes": list(report.notes) if report else [],
            "scan": {"has": p.has_scan, "at": (scan or {}).get("at"),
                     "results": [{"what": w, "available": bool(ok), "detail": d} for w, ok, d in (scan or {}).get("results", [])],
                     "running": scanning},
            "usage": {"today": store.requests_today(name), "perDay": view_.max_requests_per_day,
                      "perRun": view_.max_requests_per_run},
            "watched": sum(n for (feed, _), n in counts.items() if feed == name),
            "delivery": delivery, "load": round(scheme.load.get(name, 0.0), 3),
        }
    out = [_fold(e, [entries[n] for n in plugin_feeds(name)[1:]]) for name, e in entries.items() if plugin_of(name) == name]
    out += [_assistant_entry(p, store, settings, scans, running) for p in assistants().values()]
    for e in out:
        e["databases"] = [database_row(d, store.path) for d in plugin_databases(e["name"])]
    return {"plugins": out, "groups": list(GROUPS), "errors": [{"where": w, "why": why} for w, why in errors()],
            "api": PLUGIN_API,
            "running": running, "offline": bool(settings.offline),
            "keyScript": r"powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\set-key.ps1"}
