# Architecture and refactor plan

One application. The aim of the refactor is that every result can say what it was computed from,
with what settings, and what was known at the time, so charts, analysis and backtests agree and a
backtest can never see the future.

The project was named massive until 2026-11-01 (PROGRESS.md: "Renamed liminal"); the Massive data
provider, its plugin `massive`, keeps its name. The app is `liminal-charts` (the package
`liminal_charts`, the command `liminal-charts`) since 2026-11-03, the naming by system (the user): apps
are `liminal-<app>` (liminal-charts, liminal-integrator; liminal-platform, liminal-login, liminal-plugins
at 0.3.0), the one library `liminal-libs`, plugins `plugin-<app>-<name>` (plugin-integrator-massive,
plugin-platform-claude, plugin-charts-analysis at 0.3.0); liminal-libs' INTEGRATION.md is the brief.

## Principles

- **What was known when.** Every stored observation carries when it became available (published by
  its source) and when it was stored here; a later correction is a new version, never an overwrite.
  A backtest reads only what was available at its simulated time, including delayed reports (the COT
  report is as of Tuesday, published Friday 15:30 ET) and revisions (the French library revises
  history every month).
- **Raw apart from derived.** Raw observations (feed bars and ticks, dataset values) are kept apart
  from what is computed from them (bars built from ticks, composites, quality scores, audits,
  indicators, forecasts). Each derived result records its inputs (sources and their versions), the
  code version and the settings used.
- **No look-ahead outside viewing.** Calculations used for decisions or tests are causal: a value at
  time t uses data up to t only. Centred calculations (smoother, but they use later data) are for
  viewing charts only and are marked as such.
- **One implementation per calculation.** Calculations live in `calc/`, outside the GUI; the chart
  app, analysis and backtests call the same code.
- **One responsibility at a time.** Each refactor step moves one responsibility, keeps the public
  interface so callers change separately, and is checked by the full test suite before the next.

## Bad data (2026-10-25)

All source data is kept, always: received bars and ticks are never deleted or changed. Every series
has two views:

- **Unfiltered**: all data, everything stored, nothing taken out (the chart's "All data" switch).
- **Filtered** (the target data, what charts and calculations use by default): unfiltered without
  the bad data. The frames built from a base frame (5 minutes to days, weeks to years) are built from
  its filtered bars.

The **pruned list** holds the bad data, one per feed and symbol (the `pruned` table of each feed's
file): each entry is a bar as it was when taken out of the filtered view, the rule below that caught
it, the evidence (the numbers the rule compared, and for a spike the trades inside it priced outside
its neighbours' range), when it was pruned and by which code. A feed's own bar stays where it is,
only left out of filtered reads, and a bar built here from the feed's finer data takes its place there
(unfiltered and `as_of` reads still give the feed's own); a bar built here that a rebuild replaced
lives on in the list alone, each rebuild's version kept. Pruning and releasing are changes like any
write: the frames built above the bar are rebuilt and the chart's caches see it.
An entry can be released (put back) without losing the record that it was pruned, and a released
entry is not pruned again. Pruning never mixes feeds: a feed's series is judged on its own. Reads of
a series as it stood at a past moment (`as_of`) include bad data: it was there. (`baddata.py`;
`liminal-charts pruned`; the chart app's Data panel.)

What constitutes bad data (rules 1 to 3 are checked whenever a series is derived: after each
symbol's fetch and in the live recorder, from where it changed; `pruned scan` checks stored history):

1. **Malformed**: a bar whose prices are not positive and finite, whose high is below its low or
   whose open or close lies outside them, whose volume is negative, or whose time is in the future.
   A malformed bar is no other bar's neighbour in rule 2.
2. **Spike without volume**: a bar whose range is at least 10 times the median range of its 20
   nearest neighbours (10 each side) in the same part of the session (US stocks: regular hours
   against regular hours, the rest against the rest: they trade very different volumes), and at
   least 1 % of its price, while its volume is at most 2 times their median volume, and whose open
   and close both lie within the neighbours' range (the price did not move on). A real move has
   volume behind it. Judged on seconds, minutes and days; a bar without 10 neighbours on both sides
   waits for them.
3. **Outside its own finer bars**: a feed's own bar whose high or low lies more than 1 % beyond the
   highest high or lowest low of the same feed's finer bars inside it (a day's minutes, all hours; a
   minute's seconds): an extreme in no trade the feed shows at the finer frame. Judged where the feed
   holds the finer frame for the whole bar and has bars in it, its pruned finer bars supporting
   nothing. (2026-10-25: Massive's daily bar for NVDA on 2024-06-10, the first day after its split,
   had a high of $195.95; its own minutes that day reached $123.10.)
4. **Built from the wrong rules**: a bar built here (from ticks, or finer bars) whose build was
   wrong, found when it is rebuilt: the old version goes to the pruned list, the rebuilt one takes
   its place. (2026-10-25: Massive's condition id 2, Average Price Trade, took the rule of the tape
   letter '2' and set highs and lows; NVDA after hours showed $177 to $251 in a minute at $231.
   `pruned repair-ticks --feed massive` rebuilt every bar built from Massive's ticks.)
5. **Out of line with the other feeds** (specified, not built yet): a feed's bar whose high or low
   lies beyond the other feeds' range for the same time by more than 5 times the composite's
   deviation, where at least two other feeds hold that time.

Not bad data: trades the consolidated tape's rules keep from setting prices (odd lots, average-price
and cash-sale prints, ...). They are kept and counted in volume where the rules say so; they only do
not set the open, high, low or close of bars built from ticks (`ticks.py`), in either view. A bar a
feed corrects later is a revision (`bar_revisions`), not bad data. A spike with volume behind it is a
real move (NVDA pre-market 2025-05-07 04:00:00: 133 trades in one second from $113.92 to $114.93, the
book opening). The thresholds of rules 2, 3 and 5 are constants in `baddata.py`.

**Every fetch is verified.** No program can make a feed's numbers right; what is guaranteed is that
nothing a fetch stores reaches the filtered data unchecked. Right after a symbol's fetch
(`collector.verify_fetched`), the bars it stored are checked by rule 3; each failing bar is asked
for once more (at most 10 requests a symbol, inside the run's caps): a different answer is stored as
a correction (the first kept in `bar_revisions`), the same answer is bad data at the source and the
check that follows prunes it. Every span the fetch updated, and every bar still failing, is queued
for a second feed's audit (`audit.py`: a random hour of minutes, two months of days), so auditors run
against what was just updated, not only the newest session.

## Memory and disk (2026-10-25)

The databases stay on disk; RAM holds what is being drawn or built, in batches, and lets it go.
(2026-10-25: the chart app's live check counted a stock's every bar and trade each time a file
changed, about every two seconds while the sync and the live recorder wrote: 31 GB of
`massive-ticks.db` stayed active in Windows' file cache and the chart app read 76 MB/s at rest. The
live recorder, whose "what changed since" read every bar's `stored_at`, had read 1.7 TB.)

- **Windows, not whole histories.** The auto-ranging chart reads the visible window and what its
  indicators and models need beside it (`gui/window.py`). A whole history is read only at the level
  drawn: a calendar level's bars, or the level a price-based chart builds whole. The page reads the
  next finer level ahead for a smooth change of level. Still on the whole-history series path:
  continuous futures, the composite model, box plots and the intraday integral.
- **The change log.** Whether a series changed, and what changed since a moment, come from each feed
  file's `changes` table (per series and minute written: the earliest and latest bar time, rows
  written), kept by triggers on `bars` and `built_bars`, so every writer keeps it whatever its code
  version. A seek, not a read of the series: the live chart's check, derive's and the bad-data
  scan's "since the last build", the fetch check's "what this fetch stored". It is complete from
  `changes_since` and kept a week (`CHANGES_KEEP_MS`); a reader further behind reads `stored_at`, once.
- **Bars-only mode** (`BARS_ONLY`, on by default). The app works from bars: no process opens the
  feeds' tick files but the live recorder, which records the streams' trades and builds their bars.
  The chart app, the sync, audits and checks never read them; a volume profile is spread from the
  finest bars instead (1-second bars where held, for bars up to a day); commands that would read ticks (exports of trades, tick charts, tick backups,
  `pruned repair-ticks`) say so and stop. The tick files are kept, never touched.
- **Caps.** The chart app's caches share one budget (`CHART_CACHE_MB`, 1 GB: whole histories 40 %,
  styled frames 30 %, bricks and 30-minute bins 15 % each; they had 4 GB between them, and the app
  once grew to 13 GB). At most two requests build chart data at once (`HEAVY_REQUESTS`); the rest wait
  up to 30 s, then are answered busy (503). The live recorder holds at most 64 MB of trades while the
  database refuses writes. numpy loads with one thread: OpenBLAS committed 757 MB per process, per
  CPU thread's buffer, almost none of it used.
- **Measured apart.** `liminal-charts admin memory` shows each app process's own memory
  (private bytes, working set) and disk reads, apart from Windows' file cache: active (held) and
  standby (handed back whenever anything needs it, counted as available).

## Stores: SQLite, DuckDB and MongoDB by data type (2026-10-31)

The user, 2026-10-31: "each module needs to support sqlite or mongo or duck db based on data type";
"lets have a 'store' picker so i can choose manually, but lets try to use the optimal data store for
the data type". `stores.py` lists each data type's engines, the optimal first, and keeps each
plugin's pick per data type (the Stores section of its page in the Plugins view, or `stores pick
PLUGIN TYPE ENGINE|auto`); left to the app, the optimal one installed here.

| Data type | Engines (optimal first) | Where |
|---|---|---|
| Bars | DuckDB, SQLite | The open months in `feeds/<feed>.db` (where the feeds write), the closed ones in `history/<feed>/<YYYY-MM>.<n>.duckdb` |
| Documents | MongoDB, SQLite | gov.db's `items` and the main file's `news`, or the `gov_items` and `news` collections of the `massive` database on the server here |
| Series | SQLite | `datasets/<file>.db` |
| Files | Folder | `sources/datasets/<file>/` |

- **Bars in DuckDB** (`storage/history.py`). Measured on Massive's September 2026 (180 million
  minute and second bars): 22% of SQLite's size, a symbol's month read twice as fast, market-wide
  scans over 100 times faster. The hourly sync starts `history run` in a process of its own at
  background priority. It copies each feed's closed months (closed a week, nothing written in them for
  two days) newest first, at most three a feed, for 20 minutes. Each month is read a series at a time
  (short reads, so the writers and checkpoints are never held up), written to a new file, checked
  against what was read (rows, a timestamp checksum, the volume) and entered in the feed's manifest
  (`history`, in its SQLite file) only if nothing was written in it meanwhile. Reads (`bars_window`,
  `bar_rows`, `load_bars`) take an archived month from its file, through one in-memory DuckDB per
  store with the month files attached read-only (256 MB, two threads, twelve months attached at most).
  A write to an archived month in SQLite (a correction, a rebuild) marks it stale through triggers in
  the feed's file; it is read from SQLite until it is copied again into a new file. The pruned list
  and the revisions stay in SQLite, so filtered and as-of reads come out the same; so do the weeks,
  months and years built from the days (`derive.py` rebuilds them whole, so a symbol's 2003 year
  bar is written again every hour: archived, no month would ever have closed). The SQLite rows
  stay ("we should always keep all source data"): freeing them is the user's decision.
- **Documents in MongoDB** (`mongo.py`, `storage/documents.py`). MongoDB Community Server 9.0.4,
  approved by the user the same day, is unpacked under `%LOCALAPPDATA%\Programs\MongoDB` with no
  service. The app starts it when a store uses it, detached, bound to 127.0.0.1 only, with its
  WiredTiger cache capped at 0.5 GB, its data in `mongo/` and its log in `logs/mongod.log`. A kind of
  document has the same fields in either engine and answers the same calls, so a plugin's code does
  not change with its store. A move (`stores.settle`: the hourly sync, or a pick in the view) copies
  every document, counts them, and only then reads and writes there. Each pass also copies over what
  a process from before the move still wrote to the old store. The store left keeps its copy, and
  reads fall back to it while the server does not answer. A read never starts the server (the chart
  app reads the news every few seconds; a ping's answer is kept three seconds); a write and a move
  do, and a write while it does not answer goes to the SQLite copy, which the next pass copies over.
  An agency's items are all kept (until this change only the newest 200 of a source).
- `history status`, `mongo start|stop|status` and `stores list|pick|settle` show and manage them.

## Three programs: the library, the integrator, the app (2026-11-01)

The user, 2026-11-01: plugins should run as services "so that in they update what they can"; "the
fetcher takes a symbol and uses the plugins 'to best effect (efficiency) price / performance'
retrieve all info about the symbol"; "the fetcher uses the plugins that it knows about to assess
the work, and then begin collecting data"; "the fetcher can take a queue/stack of requests"; "the
concept is to be able to use the app, which connects to the fetcher for database access"; "so the
app needs to funnel requests through the fetcher to the databases"; "i can look at the fetcher to
see what the data looks like, i can use the 'app' to see what i want to do"; "make sure that the
library calls contribute to code reuse"; "lets move the fetcher in to it's own repo called
liminal-integrator"; "all lib calls will point to liminal-libs".

- **liminal-libs** (`liminal_libs`, [its repository](https://github.com/pcxmac-dev/liminal-libs)): every
  module of the app but its own (`gui/`, `cli.py`, `deps.py`, `trading/`): the stores, the plugin
  contract and registry, the collector and the scheme, the calendars, the hosts, the live recorder,
  the composites and derived frames, the datasets, the publishers, the settings, the log
  (`logs.py`). The two places the library reached into the GUI were turned around: the regular
  session's mask and close are the library's (`sessions.regular_mask`, `regular_close`), the market
  proxies are a parameter of `collector.sync` (`proxies`, the app's `gui/world.PROXIES`). The app
  (package `liminal_charts` since 2026-11-02, the user: "lets rename the app to liminal-charts", "not
  liminal", "refactor"), its tests, the integrator and every plugin import the library as
  `liminal_libs`; the alias package `liminal` that once mapped `liminal.<module>` onto it is gone. The
  GitHub-hosted datasets' submodules stay in the app's repository (`LIMINAL_DATA_REPOS`). The suite
  proper still runs here, with the library installed; it moves there module by module.
- **liminal-integrator** (`liminal_integrator`, [its repository](https://github.com/pcxmac-dev/liminal-integrator)):
  the fetcher, the resident host `fetcher` (one per database, `host.FETCHER`), up while someone holds
  it and gone once nobody does. `lib.py` is the one place naming the library (what it uses of
  `liminal_libs`, by name; a piece it needs is added to the library, never written twice), and the
  app's page is its one dependency outside it (`lib.page`: `gui/server.serve_fetcher`).
  - *The requests* (`storage/requests.py`, the table `fetch_requests`): one row per ask (who asked:
    a chart page that opened the symbol, the request box on a page, the command line, the fetcher's
    standing work; which symbol; what about it: bars, live; when). Asked interactively, the newest
    goes first (a stack); the standing work goes in order (a queue); a symbol asked again while its
    request is open is one request, asked anew. Taken: assessing, collecting; ended: done, failed,
    cancelled. The Fetcher view shows the stack above the plugins' queue, with a request box.
  - *The assessment* (`fetcher.assess`): a symbol nobody watches goes on the watchlist of every feed
    that can collect it; then each holding feed's offers for its asset class (resolution, since,
    latency, requests it would take, its capacity) from the standing scheme (sourcing.py), and the
    plan: each era of each resolution to the first holding feed in the scheme's order, in the user's
    order of jobs (days, minutes, seconds); live: what can serve the current session, freshest first.
  - *Collecting*: every feed of the plan is asked for the symbol first (`collector.request_priority`)
    and its run held (the host `feed-NAME`, `host.feed_run`) until it has served the symbol (synced
    since the ask) or its run ended; the request is done once one has served it and none is still
    pending, failed when none did (SERVE_TIMEOUT_S). A run held for nobody is let go.
  - *The standing pass*: every PASS_HOURS (LIMINAL_SYNC_HOURS) while the fetcher is up, and only once
    new data can exist (`next_due`: the earliest `freshness.expected` next due over the feeds set
    up here and their asset classes) or a run stopped short (its caps: `last_runs` says "stopped"),
    or when asked (resuming fetching, `sync` by hand, the page): every feed's run held (a closed
    archive left alone, a feed fetching elsewhere left to that run, the feeds not set up here named),
    the closed months' copy (`history_due`, the host `history`), the extras in a process of their own
    (`sync --extras-only`: the publishers' due feeds, the stores' pass, the composites and derived
    frames), the plugins that fetch on their own through their queue (runner.py).
  - *Its status* in the main file (`STATUS_KEY`, `read_status`/`write_status`), for every page; over
    its pipe it answers `status`, `pass` and `wake` (`Fetcher.handle`).
  - *Its page*: the chart app's page in fetcher mode (`gui/web.fetcher_page`: the body marked
    `data-mode="fetcher"`; style.css hides the top bar, the drawing tools, the chart and its side panel,
    the upper rail group and the profile; app.js loads no chart; panels.js keeps to the data and
    monitoring views) and the data API, on LIMINAL_FETCHER_PORT (8785; 0 a free one; none: no page),
    served by the integrator's own process (`gui/server.serve_integrator`, reached through the
    integrator's `lib.page()`): the user, 2026-11-03, "for the webserver / ports, i just integrator, and
    charts", so there are two listeners on the machine, the integrator's and the chart front's, and the
    fetcher host has no HTTP of its own. The integrator's process reads the data itself and holds the
    fetcher host over the pipe while a page is in touch (`service.FetcherLink`: attached at the first
    request, started if none answers, let go once none has been in touch for LIMINAL_QUIET_SECONDS; the
    status, a wake and a pass go over the pipe; `Fetcher.handle`). `liminal-integrator serve` and the
    service keep that page at the port from the browser like the chart app's (the user, 2026-11-02: "i
    need a way to run it from the browser like the other liminals"); `serve --until-quiet` ends the
    server itself once no page has been in touch for a while (what the chart front starts for itself).
  - *The service* (`service.py`): that page from the user's logon, the fetcher running only while a
    page is in touch ("Only while I use it" stays the default), or with `--daemon` held for good and
    started again when it ends, so its standing passes go on on their own (the user, 2026-11-02: "the
    integrator needs to be able to run autonomously in a 'daemon' mode"); `liminal-integrator serve`
    runs the same in the foreground (the page in the browser). Installed as a Task Scheduler task at
    logon as the user on Windows (`LiminalIntegrator`: pythonw, no window; as the user it sees the
    keys in the user environment variables) or a systemd user unit on Linux (`liminal-integrator`;
    lingering for no-login runs; the keys need a home the unit can read at boot: the keyring or
    systemd's credentials, never a file the program writes: the user's decision, open).
    `liminal-integrator serve|status|request|pass|cancel|host [--daemon]` and `service
    install|remove|status|run [--daemon]`; the app's `fetcher` and `service` commands and `host
    fetcher` answer through it (`cli.integrator()`), and say where it is when it is not installed.
  - *Which plugins, and integrators of their own* (2026-11-03; integrator 1.4.0, library 1.3.1): the
    user, "the default case is to load all plugins, however the integrator can be loaded with select
    plugins or none"; "multiple integrators can address databases". `--plugins twse,tiingo` or
    `--plugins none` on `serve`, `host`, `service install|run` is the library's selection
    (`registry.Selection`, LIMINAL_PLUGINS_LOADED: the plugins named, each with its parts, or none;
    every plugin found without it; `select`, `selection`, `plugins_loaded`), inherited by every
    process the integrator starts; the plugins not named are left out quietly (`Selection.left_out`,
    not `errors()`), and the fetcher's status (`plugins`: selection, loaded, leftOut, unknown) and log
    say so (the Fetcher view's status line, `fetcher status`). `--name NAME` installs or serves an
    integrator of its own, on its own database (`store/data/NAME/liminal.db` unless `--db`), with its
    own page port (`--port`) and plugins, as the task `LiminalIntegrator-NAME` (the unit
    `liminal-integrator-NAME`), its name in its status (LIMINAL_INTEGRATOR_NAME); the host `fetcher`
    stays one per database, the machine's ledgers shared by key. The integrator's `cli.apply_instance`
    applies the three before the settings load; the app's `fetcher` and `service` commands do the
    same (`cli._instance`) and load the settings again when anything changed.
- **The app** (this repository): the chart front (`gui/front.py`, `liminal-charts gui`): serves the chart page
  and its scripts and passes every `/api/` request of its pages to the integrator's data API at its
  port (LIMINAL_FETCHER_PORT, 8785), the profile cookie and the app's own headers with it, where it
  came from checked here and not passed on; an event stream as it comes. No database is opened here
  and no pipe: when nothing answers at the integrator's port, the front starts the integrator's server
  for itself (`front.start_integrator`: `liminal-integrator serve --no-browser --until-quiet`, which ends
  again once no page has been in touch for LIMINAL_QUIET_SECONDS); the integrator holds the fetcher.
  (Until 2026-11-03 the front attached to the fetcher host itself and proxied to the page that host
  served on a port of its own: three listeners. The user: "for the webserver / ports, i just
  integrator, and charts".) `gui
  --alone` keeps the old one-process app (page and data API, no fetcher: a read-only copy, a test).
  The data API (`ChartApp`) runs inside the fetcher: a symbol a page opens is a request on the
  fetcher's stack (`priority`: no `sync --tickers` process of the app's own any more); resuming
  fetching asks the fetcher for a pass; `activity.symbolRun` is that request's state. `sync` by hand
  holds the fetcher and asks it for a pass, or puts the symbols given on its stack, and waits
  (`dispatch`, `wait_for_fetcher`, `wait_for_requests`).
- Tests: `tests/test_fetch_requests.py`, `test_front.py`, `test_fetcher_page.py`, `test_fetch_in_use.py`
  here; the integrator's `tests/test_fetcher.py`, `test_service.py` in its repository (its conftest
  copies the guards: no process, no host, no key); the library's import smoke test in its. The
  boundary and console-window audits read both packages.
- **The workspace** (the user, 2026-11-02: "common, app and store are the required folders, plugins
  as well, make sure the app is in the app folder, make sure the integrator is in the store w/ the
  databases, make sure plugins get loaded in to plugins, and lib is in common"): one folder holds
  the four (`liminal_libs.layout`: `home`, LIMINAL_HOME or the folder above the library's checkout
  when it has them): `common/` the library's checkout, `app/` the chart app's (its .venv inside),
  `store/` the integrator's with the databases in `store/data/` (`layout.data_dir`, the default
  main file; LIMINAL_DB still overrides), `plugins/` a checkout per plugin, each loaded from there
  (`registry.plugin_checkouts`: its pyproject's `liminal.providers` entry points, its `src/` on
  `sys.path`, its version from the pyproject, origin `plugins/<checkout>`; the same plugin installed
  as a package besides is not loaded twice; LIMINAL_PLUGINS names another folder). Without the
  workspace nothing changes: the data in ~/LiminalData, the plugins as installed packages. On the
  user's PC: `%USERPROFILE%\source\liminal\` (the junction of old replaced by the folder).

## The library's classes (2026-11-03)

The user, 2026-11-03: "most of the code should be OO based" ... "where it is not a detriment to
performance". liminal-libs 1.5.0 (Phase 5 of the rewrite) gives each core module a class that owns a
run's or a view's state and its decisions; the hot loops and the pure functions stay functions, and
every module keeps a function under each name a caller or a test used (`collector.sync` is
`Collector(...).run(...)`, `coverage.plan` a one-plan `Planner`, `sourcing.standing` is
`Scheme.standing`, and so on), so a test's stand-in set on the module is the one used. What this app
and the integrator call since then:

- `collector.Collector(source, store, tier, *, now, log, refresh_hours, feeds, audits,
  futures_per_run, proxies).run(only, published_only, on_item, directory_allowance)`: a feed's
  scheduled run, stage by stage (`_before_symbols`: the exchanges, the calendars, the watched stocks'
  market days, then the market moves; `_plan_run`; `_step`; `_after_symbols`); `cli._sync` makes one
  per feed. `audit.Auditor` and `initializer.Initializer` behind the audits and `init`.
- `coverage.Planner(store, feeds, now).plan(me, ticker, asset, timespan, floor, listed_from,
  exchange)`: the audit scores, the standing scheme and a ticker's watch rows read once for the
  planner's life; the Fetcher view's `activity.symbol_rows` makes one per pass (not one per symbol),
  the collector one per symbol step.
- `sourcing.Scheme.standing(store, feeds, now)` (the delivery scheme, kept by store),
  `Scheme.reports`, `scheme.text(reports)`; `sourcing.Scans` (`keep`, `last`, `read`, `at`, `plans`,
  `apply`): the providers' capability scans in one place (`cli._scan` keeps one, `sourcing` reads
  them, every command starts from `Scans.apply(settings)`, the chart server too when a key was saved).
- `config.Settings.load(env, env_file)` and `settings.with_detected(plans)`; `config.UserEnvironment`
  behind `saved_user_keys` (the registry read that the tests stand in for).
- `live.Recorder.for_settings(settings, locks, stop, log)`: `cli.make_recorder`.
  `runner.Runner(store_factory, settings, log).run()`: the integrator's workers for the plugins that
  fetch on their own.
- `derive.Builder` and `Lease`, `baddata.Scanner`, `datasets.DatasetFetcher`, `publications.Reader`,
  `killswitch.KillSwitch`, `host.Attachment.to`, `host.Host.start`: behind the module functions this
  app still calls by name (`derive.run`, `datasets.fetch`, `publications.view`/`run_due`/`recheck_now`,
  `killswitch.killed`/`strike`/`release`/`kills`, `host.attach`), which its tests patch.

Stays functional, deliberately (the user's "where it is not a detriment to performance"):
`sessions.py` (the calendar masks), `freshness.expected` and the publication rules, `storage/*` (the
queries), `stores.py`, `memory.py`, `resources.py`, `proc.py`, `ratelimit.py` (classes already),
`gov.view`/`parameters`/`upcoming`, `audit.compare`, `derive.aggregate` (vectorised), the bad-data
rules (`malformed`, `spikes`, `inconsistent`, `scan_series`) and the collector's request loops
(`sync_bars`, `sync_future`, `check_listing`, `daily_first`, `market_days`, `refresh_directory`).

liminal-libs 1.7.0 (2026-11-05), the apps' frame (the user: "define all this stuff in liminal libs ...
the library basically facilitates the interprocess communication, the way the apps can bind to each
other ... As well as the framework for the graphics"): `gui/web.py` moved to the library's `web.py`
(this app's module re-exports it and keeps `STATIC` and `fetcher_page`), `icons.js` to
`liminal_libs.ui.STATIC` with `liminal.css` (served beside this app's files: `static_file` reads the
app's folder first, then the library's), and `liminal_libs.apps` gives every app its frame (`App`:
its master icon, its tray, its routes at its own port) and the login app's client (`LoginClient`).
The chart front and the integrator's page still run their own servers by hand; they move onto `App`
at 0.3.0, when the charts app binds the other apps' trays under its own.

## Providers are plugins (2026-10-27)

Every data provider the fetcher uses is a plugin: one `FeedPlugin` (`sources/plugin.py`, plugin
API 2.1 since 2026-11-04, 2.0 the classes; the `Provider` record of `sources/registry.py` before it, a subclass of the same class)
holding everything the app knows about it, so nothing else names a provider to decide how to treat
it. Every one is a package of its own since 2026-11-01 (`plugin-integrator-<name>`, a repository each
under pcxmac-dev, installed by the app's `plugins` extra): Massive with its headlines publisher,
Alpaca with its overnight feed on Blue Ocean, Bitstamp, Coinbase, Bitfinex, Tiingo with its forex
feed, Cboe; the publishers Federal (the federal agencies in one plugin: the Fed, New York Fed, SEC,
BLS, Federal Register, CFTC) and the dataset libraries Ken French and Datahub. Each is a module
defining `PLUGIN` (Alpaca's and Tiingo's `PLUGINS`: two feeds each; Federal's `PUBLISHERS`), holding
its own client, source classes, live streams, datasets and settings (the user, 2026-10-31: "every
external data provider must be in a plugin structure"), on the contract's shared pieces
(`sources/http.py`, `archive.py`, `stream.py`, `dataset.py`). A module added to `sources/plugins/` is found on the next start; a
provider shipped as its own package registers under the entry point group `liminal.providers`.
A plugin that fails to load is left out and reported (`plugins`), never fatal.

- **What a plugin gives.** `build(settings, request_budget)` returns its `DataSource` with its
  limiter and caps (`registry.limiter`, `machine_caps`, `public_client` help). Facts: its keys
  (`KeyVar`, aliases too) and where to get them, its plan variables, the asset classes it collects
  and those it serves without keys, whether it is a closed archive, what its server publishes, its
  kind of data for the composite model, the exchange it is the reference for, its audits' venue
  notes, bars per request, a monthly symbol cap, the bulk symbols' resolution, its Fetcher hints.
- **Callbacks** where a fact depends on the settings: `view` (its plan and caps: `Settings.for_source`),
  `rate` (its limiter's window), `has_keys`, `serves` (None: not set up here at all), `markets`
  (which market each asset class is) and `carries` (its own list of symbols). Left out, each takes a
  default: a plan from `tier`/`per_run`/`per_day`, the plan's rate a minute, keys set when every
  key's variable is, each asset class its own market.
- **Keys and settings.** Keys stay in the user's environment variables only. `Settings.env` holds
  the variables the plugins declare (never in a repr), read from the saved user
  variables too (`config.saved_names`). `tools/set-key.ps1` asks the app for the plugins
  (`liminal-charts plugins --json`: names and whether set, never a value) and offers a
  menu per provider: set, replace or remove its keys, typed hidden.
- **Read everywhere from the registry**: the sync's feed list and `--source` choices, feed profiles
  and the one-download-per-span rule, the Fetcher's tabs, rows, limits and health, `status`, `feeds`,
  `deps`, the composite model's reference and priors, audits, the initializer, the monthly caps,
  the live recorder's streams and pollers (`Provider.live`, API 1.5), `plan` (the capabilities scan
  of the feed given). Not yet: the plan-change check (`cli._note_tier`), Massive's alone.
- **The standard report.** Every plugin reports in one form (`Provider.report`: a `Report`): its
  plan, its keys, its limits (requests a day: its daily cap or its rate where tighter; per run; a
  monthly symbol cap; what its server publishes), one `Capability` per job it can do on that plan
  (asset class, resolution, market, from when, how fresh, bars a request) and its live streams. It
  comes from the plugin's declarations (`provides`, `plan_resolutions`, `per_request`, `streams`);
  a plugin whose plan says more amends it (`reporter`). The delivery scheme reads nothing else.
- **Each plugin refreshes its own plan info** (the user, 2026-10-27). Its capability scan (`scan`) asks
  the provider what the key really gets and may answer with the plan that shows (`registry.Scanned`:
  values of its own `plan_vars` only). The scans are kept (kv `capabilities:<name>`: at, results,
  plan) and every command applies the plans found wherever the user set none
  (`sourcing.apply_detected`, `config.with_detected`: the user's own variable always wins; a value
  the settings refuse is not applied). They run on each start of the chart app (`gui`, unless
  `--no-rescan`; never on an offline copy), weekly at the start of a feed's sync, from the Plugins
  view's Rescan, the key script's menu (`set-key.ps1 <name> -Rescan`) and `capabilities [name]` (a
  plugin's name scans each of its feeds); a scan takes no run lock (a few requests, inside the
  machine-wide rate windows and daily caps), and a provider whose keys are not set is not scanned.
  While a rescan runs, the Plugins view shows "scanning" on each plugin it has still to ask (the
  user, 2026-10-29), from its kept scans' times against the rescan's start; one asked in this run
  is back to its state, and a public feed with no scan keeps its own.
  - Massive: its stocks plan from `plan_reach` (daily bars 3, 6, 11, 21 years back, 1-second bars,
    trades, quotes; the plugin's `tier_for`): `MASSIVE_TIER`. The other classes' plans are not probed: a
    free class is held to 5 requests a minute, and a probe that took one for paid would lift that.
  - Alpaca: the consolidated tape's latest 15 minutes are served on Algo Trader Plus and refused
    on Basic (`AlpacaSource.recent_allowed`): `ALPACA_PLAN`. Blue Ocean shares Alpaca's.
  - Tiingo: its API has no account endpoint and sends no rate-limit headers (checked 2026-10-27),
    but its News API is the paid plans' only (the pricing table: Starter no, Power and Commercial
    yes), so the scan's News request tells them apart: served, a paid plan; refused (401, 403),
    Starter; anything else, nothing. Power and Commercial reach the same data, so a paid key is
    taken for Power, the lower limits (`Scanned.alike` names Commercial as the same answers: your
    `TIINGO_PLAN=commercial` is not shown as disagreeing). The scan also asks for daily, IEX minute,
    Blue Ocean (an add-on), forex and crypto bars, and the fundamentals add-on for ROKU (the Dow 30
    are free to evaluate; without it Tiingo answers "Free and Power plans are limited to the DOW
    30"). Seven requests. `TIINGO_PLAN` sets its limits, a little inside what Tiingo publishes: starter
    (the default; free) 45 requests an hour, 900 a day, 450 symbols a month (of 50, 1,000, 500);
    power (individuals, $30 a month) 9,500 an hour, 95,000 a day (of 10,000, 100,000); commercial
    (a business's internal use, up to two developers, $50 a month; `business` too) 19,000 an hour,
    142,500 a day (of 20,000, 150,000); neither paid plan caps the symbols a month. Enterprise and
    redistribution are priced by Tiingo's sales with no published limits, so they are not offered.
    An unknown value is Starter.
- **The plugin contract, versioned** (2026-10-29; the user: "eventually plugins will need to be in
  their own repo with their own version"). The app offers `registry.PLUGIN_API` (major.minor, now
  2.0: the class contract) and, for the record form, `registry.RECORD_API` (1.12: every minor of
  the records' contract, still loaded). Each plugin, a `FeedPlugin` or a `PublisherPlugin` (a
  `Provider` or a `Publisher` record), gives its own `version` (its release: major.minor.patch) and
  the contract it was written for (`api`: a literal such as `"2.0"`, never `PLUGIN_API` itself,
  which would always match; a class's default is 2.0, a record's 1.0). At discovery a plugin written for another major,
  or for a newer minor than the app offers, is left out and reported with the ones that fail to
  load (`errors()`, `plugins`, the key script, the Plugins view's "Left out"), never fatal. An `api`
  left out is 1.0. Each plugin's release is its distribution's and its contract (`api`) in its module
  (`plugins` lists both).
  `plugins` (and its `--json`: `api`, each
  plugin's `version`, `api`, `origin`; the publishers too), the key script's list and the Plugins
  view show each plugin's version and origin.
  - **Minor and major.** A minor only adds: a field with a default, a new optional callback, a new
    helper. A major changes or removes something a plugin may rely on: a field's meaning, a
    callback's arguments, a helper's behaviour, a name moved. A plugin for 1.0 loads in every 1.x.
  - **1.1** (2026-10-29) adds `Provider.part_of`: a plugin's second feed names the feed it is part of
    (Blue Ocean: `part_of="alpaca"`; the user: "the Blue Ocean plugin is part of alpaca, so it should
    be part of the alpaca plugin"). The feed keeps its own name, bars, ledgers and watchlist rows; the
    plugin lists it once (`registry.plugin_of`, `plugin_feeds`): `plugins` and the key script show
    one row with its feeds, the Plugins view one entry (its feeds, their capabilities, scans, eras,
    requests and symbols together), and its Rescan or `capabilities <plugin>` scans every feed.
    Tiingo's forex is one too (`tiingofx`, `part_of="tiingo"`; the user, 2026-10-30: "tiingo should
    be one plugin"). Such a feed is never named as a source of its own (`registry.label`, the page's
    `feedLabel`; the user: "the source isn't blue ocean its alpaca w/ blue ocean"; "house all alpaca
    in alpaca"): its plugin with what it is of it, `alpaca (blue ocean)`, `tiingo (forex)`, and the
    live feeds so too (`live.LIVE_LABELS`: `alpaca (iex)`, `tiingo (blue ocean)`), in the chart's
    feed lists, the Fetcher, the Exchanges view and the Plugins view.
  - **1.2** (2026-10-30) adds `Publisher.part_of` and `Publisher.datasets`: a plugin of several
    publishers (Federal: its agencies name `part_of="federal"`), and the datasets a publisher puts
    out (the CFTC's Commitments of Traders). Each part keeps its own name, scrape checks, state,
    pause and recheck; the plugin is listed once with its parts (`registry.publisher_of`,
    `publisher_parts`, `publisher_menu`), and pausing it stops them all (`publications.Reader.kill`:
    `whose="plugin"`). A publisher with no feed of its own (the plugin's record, a part with
    datasets only) is never read or rechecked: nothing is sent for it, robots.txt neither.
  - **1.3** (2026-10-31) adds `Publisher.keys`, `KeyVar.secret` and `KeyVar.hint` (`sources/keys.py`):
    every variable a plugin reads that the user sets is declared by it, a key or a detail of the
    user's it sends where asked (the SEC's `GOV_CONTACT`, typed in the open; no list of names
    elsewhere: `config.saved_names` is `registry.variables`). Each plugin is set up in the Plugins
    view (the user: "i need a way to setup plugins inside the plugin interface in the main window,
    for all plugins, including keys, user details etc"): its Setup lists every variable it reads
    (`registry.plugin_keys`: its feeds' and parts' too), set or not, never a value; one typed is
    sent once (`POST /api/plugins/key`, `ChartApp.set_key`) and saved where the key script saves it
    (the user's environment variables), or removed. Only a variable the plugin declares, one line
    of sane length, never Windows' or Python's own nor the app's own setting (`keys.refusal`), nor
    one another plugin declares too. And `Publisher.part_of` may name a feed plugin: Massive's
    headlines (`massive-news`, `part_of="massive"`; the user: "massive headlines needs to be rolled
    in to Massive") are in Massive's row (`menu()`'s `publishers`) and a section of its Plugins view
    page, released with it (its version and contract), on its key and limits; Massive's kill stops
    them as its account's. A part, a feed or a publisher, is never a Plugins view entry of its own
    (the user, 2026-10-31: "massive headlines should not be separate from massive same thing with
    federal agencies and federal"); `test_a_plugins_parts_are_released_with_it` holds a plugin's parts
    to its release.
  - **1.4** (2026-10-31) lets `Publisher.datasets` hold the `Dataset` definitions themselves
    (`sources/dataset.py`: its files, parser, publication rule, the file it is kept in; a name, as in
    1.2, still lists one another plugin declares). Every external data provider is in a plugin (the
    user: "lets add all the different data sources to plugins, respective of their source"; "every
    external data provider must be in a plugin structure"): `datasets.py` declares none of its own
    (`datasets.DATASETS` reads them from the plugins), and a library of datasets is a publisher with
    no feeds: Ken French's Data Library (`french`), Datahub's core datasets on GitHub (`datahub`: the
    S&P 500's members, Shiller's market data, the IMF's commodity prices), the CFTC's Commitments
    of Traders in Federal. A dataset's files are read only from its publisher's hosts over HTTPS,
    never a blocked site (`publications.url_refusal`; a GitHub submodule's from `github.com`), and
    none while its plugin is paused (`datasets.stopped`). A dataset's name and its file are one
    plugin's: a second plugin declaring either is left out and reported. Each keeps the file it
    always had (`datasets/<provider>.db`): no stored data moves. `tests/test_plugin_boundary.py`
    holds the app to it: no provider's host named outside `sources/plugins/` but links people follow.
  - **1.5** (2026-10-31) adds `Provider.live` (`sources/stream.py`: `Live`) and `Provider.partial`:
    each live feed is declared by its plugin, its stream's class (its URL, messages, parsing) or a
    poller, with its own feed name, how the page names it (`label`) and whether it is one venue's
    share of the market (`partial`): Coinbase's and Bitstamp's trade streams, Alpaca's IEX stream
    (`iex`, signing in with `Live.credentials`: Alpaca's keys), Tiingo's Blue Ocean nights
    (`tiingoboats`, `Live.poller`). The recorder (`live.py`) names no provider: it runs the crypto
    streams the plugins declare (`live.crypto_streams`), their stock stream (`stock_stream`,
    `stock_keys`) and their night poller (`night_poller`), and connects, follows the charts,
    stores and kills the same way for each; `live.PARTIAL_FEEDS` and `live.LIVE_LABELS` are read
    from the plugins (`registry.partial_feeds`, `registry.live_feeds`). A live feed's name is its
    own provider's or one no other provider has; another plugin's is left out and reported. The
    stream helpers (`Reconnect`, `Refused`, `Notice`, `PING`, `trade`, `ns`) are the contract's.
  - **1.6** (2026-10-31) adds `sources.http` (`HttpClient`: the HTTP client every plugin's client
    builds on, its headers, its window or a window per asset class (`class_of`, Massive's), the
    request and daily caps, pauses, retries with Retry-After, links on its own host only;
    `PublicClient`, its keyless form that identifies itself; `SameHostSession`, `retry_delay`) and
    `sources.archive` (`Archive`: a public feed's source; `bounds`, `ms`, `ET`, `CRYPTO_PAIRS`,
    `crypto_pair`, `EXCHANGE_FRAMES`, `venue_view`). Each vendor's client and source classes are in
    its plugin: `plugins/massive/client.py` (`MassiveClient`, `MassiveSource`), `plugins/alpaca/client.py`,
    `plugins/tiingo/client.py`, and the exchanges' in their modules (Cboe's in its package); `sources/massive.py`,
    `alpaca.py` and `public.py` are gone, and Massive's base URL is its plugin's (`MASSIVE_BASE_URL`
    only overrides it). No plugin imports another, and the app imports none by name
    (`tests/test_plugin_boundary.py`).
  - **1.7** (2026-10-31) adds `Feed.parse` and `Feed.item_kind` (`sources.publisher`: `Read`,
    `parse_rss`): each feed's parser is its plugin's, `parse(answer, feed)` giving a `Read` (items
    for the news and the Federal view, reference rates), so a new kind of source needs no change to
    the app: the Federal Register's (`fedreg.parse`, its agencies and FINRA's and the exchanges'
    filings tagged) and the New York Fed's (`nyfed.parse`, its rates) are in their parts, and an RSS
    or Atom feed is read by the contract's `parse_rss`, its items what the feed says they are
    (`item_kind`: the Fed's speeches, the SEC's trading suspensions, BLS's data releases). `gov.py`
    keeps what they send and makes the Federal view; it parses no agency's answer. With it no
    provider's host is named outside `sources/plugins/` but the links people follow.
  - **1.8** (2026-10-31) adds where a plugin's data is (the user: "the plugin will point to the
    databases it stores the data in"): `layout.py` names the data folder's files and
    `registry.plugin_databases(name)` lists each plugin's (`layout.Database`: where, what it holds
    of the plugin's, shared or its own, its engine): each feed's bars and trades
    (`feeds/<feed>.db`, `-ticks.db`, its live feeds' too), its datasets' files and their downloads
    (`datasets/<file>.db`, `sources/datasets/<file>/`), what its publishers read (`gov/gov.db`) and
    its rows in the main file, with any its own code writes besides (`Provider.databases`,
    `Publisher.databases`: Massive's headlines, the main file's news). The Store, `gov.db_path` and
    the datasets' downloads take their paths from `layout.py`. An engine is SQLite, DuckDB, Parquet,
    a MongoDB collection or a folder of files (the user: "lets prep for mongo and duck"); a server
    off this machine, a path out of the data folder, an unknown engine or the main file not declared
    shared is refused at loading. `plugins --json` and the Plugins view (each plugin's Databases,
    with its size here) show them (`tests/test_plugin_databases.py`).
  - **1.9** (2026-10-31) adds a plugin's own fetching (the user: "each plugin will manage it's own
    fetching"; "the fetcher will then just show the queue of all resources being downloaded like
    symbols"; "the plugin status will be available in the plugins:: plugin page").
    `sources/fetching.py`: a `Fetcher` (`Provider.fetcher`, `Publisher.fetcher`) gives `plan(ctx)`,
    the resources it wants now (`Job`: kind, key, title, part, priority, not before), and
    `fetch(ctx, job)` one of them (`Fetched`: requests, rows, bytes, when asked again). The fetch
    queue (`fetch_queue` in the main file, `storage/queue.py`) holds one row per resource by plugin,
    part, kind and key: waiting, running (with its process), done, failed or held. `runner.py`
    gives each such plugin a worker of its own (`liminal-charts fetcher`), taking only
    its own jobs and stopping between two for the fetching pause, its kill or pause, a StopRun; one
    plugin's failure stops its worker alone. Until a plugin fetches on its own, the app's paths keep
    the same queue: the bars sync (each symbol it plans waiting, then running, done or failed), the
    news reader (each feed's read, and its next) and the datasets' files. The Fetcher view shows the
    queue across plugins, by plugin, kind and state; each plugin's page its Fetching (its state,
    queue, feeds' requests against their caps, limits, runs, audits and its lines of the log;
    `gui/queue.py`); the dependencies moved to the Resources view (`tests/test_fetch_queue.py`).
    Step 1 of the plan: no plugin fetches on its own yet; the datasets, the news sources, the
    release documents and the feeds move into their plugins next, one at a time.
  - **2.0** (2026-11-03; the user: "all the plugins under liminal should basically follow
    inheritance/oop paradigm") is the class contract (`sources/plugin.py`, liminal-libs 1.3.0). A
    plugin is an instance of a `FeedPlugin` or `PublisherPlugin` subclass (an `AssistantPlugin`'s,
    `assistants/plugin.py`): what it declares is a class attribute of the field's name (`name`,
    `assets`, `keys`, `provides` ...), what it does a method of the callback's name with `self`
    (`build(self, settings, budget)`, `view(self, base)`, `scan(self, source)`, `settings(self,
    variables)`; a publisher's `moments`, `calendar`, `read`), overriding the contract's default
    (`FeedPlugin.view`: its fixed tier and caps; `rate`: its plan's rate a minute ...). The registry
    loads an instance, a class (instantiated) or a module's `PLUGIN(S)`, `PUBLISHER(S)`,
    `ASSISTANT(S)`; a second feed or part of a package shares a base class of its own (`TiingoFeed`,
    `Agency`), and a subclass drops an inherited hook with `scan = None`. The record form (`Provider`,
    `Publisher`: the callbacks as fields) is a subclass of the same classes (`is_record`), plugin
    API 1.x still (`RECORD_API`), so every plugin loads as before; the app asks both the same way:
    `gives(hook)` (a record's field set, a class's method overriding the contract's), `has_scan`,
    `has_settings`, `settings_of`; a publisher's `has_moments`, `has_calendar`, `reads_itself`;
    `with_(**changes)` (a changed copy: the registry's origin and version, a test's `with_(scan=None)`;
    `dataclasses.replace` is the records' alone). A feed declares what `freshness.py` held for it
    (`delays`: tier name -> asset class -> `Delay`; `hours`: a market with hours of its own), kept
    in `freshness.DELAYS` when it loads. `sources.archive`: `PublicFeedPlugin` (a keyless feed's
    source on the contract's public client) and `CryptoVenuePlugin` (a crypto exchange's plan and
    caps, `CRYPTO_VENUE_DAYS`). `config.load_settings` builds every kind's own settings
    (`Settings.plugin` for a publisher too). The recipe for a record: a field becomes an attribute,
    a callback a method of the same name; module helpers other functions use stay functions; a
    `Live` needing `self` becomes a `live` property; the module keeps its instances (`PLUGIN =
    TwsePlugin()`; `PLUGINS = (TIINGO, FOREX)`), tests move from module functions to them.
  - **2.1** (2026-11-04; the user: "every plugin needs to furnish the correct mechanisms for assessing
    data, data integrity, provide a 'spec' for the data, different source plugins like massive and
    Tingo can inherit 'stock_symbol_data'") is the data specs (`sources/data.py`, liminal-libs 1.6.0):
    a class per kind of data a plugin delivers, each with `spec()` (the fields and dtypes, the
    resolutions, the sessions and calendar, the adjustments, the symbol form; a document's parts; the
    products and the store each goes to), `assess(frame, since, until)` (held and missing spans as the
    coverage ledger has them, completeness against the calendar's slots, latency, with a plan the
    freshness state) and `integrity(frame)` (the bad-data rules behind one method, duplicates, rows
    outside the sessions, gaps, a required field missing; `compare` for two feeds' copies, `scan` for
    the rules that need the store). `TimeSeriesData` (`StockSymbolData`, `IndexData`, `FxData`,
    `CryptoSymbolData`, `FutureData`) for the feeds' bars and ticks; `DocumentData` (`ReleaseData`,
    `HeadlineData`, `FilingData`; the parts `Paragraph`, `Page`, `Statement`, and `Document`) for the
    publishers' documents. Each names its products (`Product`): the original to MongoDB, or SQLite
    until it moves, and what is evaluated or derived from it kept apart in SQLite or DuckDB, keyed to
    the original and never written back (the user: "mongo is used to store the original source
    document, while a different database is used to evaluate or derive information from the original
    document"). A feed declares its own classes where the vendor's data differs (`data = {"stock":
    MassiveStockData}`, saying `api = "2.1"`) and takes the asset class's otherwise (`data_for(asset)`);
    a publisher its `data` or its kind's (`document_data()`); the conformance check reads the
    declarations. A 2.0 plugin loads unchanged; the fetching stays in each plugin's `DataSource`.
  - **2.2** (2026-11-11; the user: "each plugin should reflect the the API its attached to, and present
    links that provide documentation, the plugin interface should more or less contain what it already
    has, BUT, it should also expose more of what the API is about"; "ensure this is true") is what a
    plugin attaches to (`sources/apis.py`, liminal-libs 1.49.0): `apis`, an `Api` for each thing it
    talks to (its maker, what it is about, its form, where it is reached, its version, how a caller
    proves who it is, its limits and what costs money, the day its facts were read from the maker's
    pages), with the maker's pages (`Link`), every part of the API (`Area`, covered by the plugin or not)
    and its calls (`Call`, made by the plugin or not, each saying whether it changes something or costs
    money); `api_facts()`, what it learned of them while it ran, asking nothing to answer it. The Plugins
    view (and the platform's page) draws them with the library's `apis.js` after what a plugin's page
    already shows, its parts' too. A plugin written for 2.2 declares at least one, each with a
    documentation link and https addresses only (conformance); a 2.0 or 2.1 plugin loads unchanged.
  - **What a plugin may rely on (API 1.0; 2.0: the classes; 2.1: the data specs; 2.2: what it attaches to)**, and nothing else of `liminal`:
    `sources.registry`: `PLUGIN_API`, `RECORD_API`, `FeedPlugin`, `Plugin`, `Provider`, `KeyVar`,
    `Scanned`, and `Report`, `Limits`, `Capability` for a `reporter`; the helpers `limiter`,
    `machine_caps`, `public_client`, `minute_window`. `sources.plugin`: the same classes and records.
    `sources.publisher`: `PublisherPlugin`, `Publisher`, `Feed`, `Policy`. `sources.archive` (2.0):
    `PublicFeedPlugin`, `CryptoVenuePlugin`. `sources.data` (2.1): `TimeSeriesData` and its asset classes,
    `DocumentData` and its kinds, their parts, `Product`. `sources.apis` (2.2): `Api`, `Link`, `Area`,
    `Call`, `problems`, `dead_links`. `freshness`: `Delay` (a feed's `delays`). `sources.dataset` (1.4):
    `Dataset`, `Parsed` and the parsers' helpers (`ms`, `day_of`, `at`, `months_after`, `num`,
    `text`). `sources.stream` (1.5): `Live`, `Reconnect`, `Refused`, `Notice`, `PING`, `trade`, `ns`.
    `sources.base`:
    `DataSource` (what `build` returns), `Page`, `RESOLUTIONS_BY_ASSET`; `models`: the `Exchange`
    and `Instrument` a source returns; `errors`: `SourceError`, `NotEntitled`, `Unauthorized`,
    `RateLimited`, `BudgetExhausted`. `config`: `Tier`, `ConfigError`, and `Settings` read only:
    `env` (the variables it declares), `offline`, `for_source(name)` and on that view `tier`,
    `max_requests_per_run`, `max_requests_per_day`. The HTTP client the feeds' clients build on
    (1.6): `sources.http.HttpClient` (retries, its limiter, the request budget, the rate-limit
    headers seen) and its keyless form `sources.http.PublicClient` (`public_client`); a public
    feed's source base, `sources.archive.Archive`.
  - **The fields that matter across releases.** `name` is the feed's name everywhere (its database
    file, ledgers, watchlist rows, kept scans), so a release never renames it. `origin` is set by
    the registry, never by the plugin. `order` places it in lists (the feeds 0-61, the
    publishers 200-300, the dataset libraries 400-410, a package's 100 unless it says). Of two
    plugins of one name the first by order is kept (a module in `sources/plugins/` before any
    package, as the built-ins were); the other is reported. What each other field gives: the bullets above and the
    docstrings of `sources/registry.py` and `sources/publisher.py`.
  - **A package registers** under the entry point group `liminal.providers`, naming a
    module that defines `PLUGIN`/`PLUGINS` and/or `PUBLISHER`/`PUBLISHERS`, or one record:

    ```toml
    [project.entry-points."liminal.providers"]
    acme = "massive_acme"   # or "massive_acme:PLUGIN"
    ```

    Installed into the app's environment (`.venv\Scripts\python.exe -m pip install ...`) it is
    found on the next start, listed as `package: <distribution> (<entry point>)`; one that leaves
    `version` empty shows its distribution's version. Its keys are declared as every plugin's
    (`KeyVar`), so the key script and the Plugins view offer them unasked.
- **Splitting the built-ins into packages: the plan** (the user, 2026-10-31: "Yes, Cboe pilot
  first"; Cboe split; 2026-11-01: "all the plugins should be plugin-integrator-xxxx and have a separate
  repo": the others next, one repository each).
  1. *In this repository first*, so each plugin can leave without taking app code with it (each a
     minor of the API where it adds to the contract):
     - done (2026-10-31, API 1.6): the HTTP client every feed builds on out of `sources/massive.py`
       into the app (`sources/http.py`: `HttpClient`, Massive's `MassiveClient` built on it), so
       Massive's plugin can leave alone;
     - what the built-ins still reach outside the contract either into the contract or into the
       plugin: `config.PUBLIC_FEEDS` (each public feed's plan and caps, into its module),
       `ALPACA_TIERS` and `AlpacaSettings` (`settings.alpaca`, read from `settings.env` by the
       plugin), Massive's `TIERS`, `MASSIVE_CLASS_TIERS`, `asset_tier`, `tier_for`, `symbols.ASSETS`,
       `sessions.ET`, `ratelimit.MachineLedger` (done: Coinbase's `venue_view`, which Bitfinex
       borrowed, into `sources.archive`; `gov.FEDREG_URL`, into the Federal Register's part);
     - the app's own modules that still name a provider (some 40 lines on 2026-10-29; `live.py`'s
       streams and Alpaca's trade parsing done 2026-10-31, API 1.5: `config.py`, `cli.py`'s Massive plan commands,
       `gui/venues.py`, `gui/server.py`, `storage/directory.py`, `deps.py`, `freshness.py` ...) read it
       from the plugin's record instead (new fields, a minor each);
     - done (2026-10-31, API 1.6): each source class next to its plugin (`MassiveSource`,
       `sources/alpaca.py`, the public venues' classes in `sources/public.py`).
  2. *Then one repository and package per vendor*, its version going on from the built-in's (tags
     `v1.2.0`), kept in its `pyproject.toml` only (`PLUGIN.version` left empty: the distribution's
     is shown), its own tests run against the app installed (the fakes it needs from
     `tests/conftest.py` copied in), its own changelog, depending on `liminal` (which of the
     app's releases can load it is its `api`, checked on loading, not a version bound). The
     built-in module leaves `sources/plugins/` in the same app release that brings the package (an
     optional extra of the app's `pyproject.toml`, from the packages' tags),
     so no feed is missing between the two: the package installed first (a built-in keeps its name,
     so the package is reported and left out until the module goes). The app keeps the contract's
     own tests (a fake package plugin, the version check) and a test that every installed plugin
     loads (`test_every_installed_plugin_package_loads`); the rest of its suite runs with the
     packages installed, as the app does (2026-11-01: with every plugin leaving, no built-in is
     left to stand in; a package's record is the fixture where a test names its feed), and a
     plugin's own tests (its pages, its stream's messages) move to its package. The app's CI installs
     `.[dev,plugins]`; a package's CI installs the app from git (`MASSIVE_REF`).
  3. *A pilot first*: Cboe (one keyless feed, a closed archive, the smallest), to prove the path
     (install, entry point, version shown, its tests, its CI) before the others. Done 2026-10-31:
     [plugin-integrator-cboe](https://github.com/pcxmac-dev/plugin-integrator-cboe) 1.2.0, its plan
     and caps out of `config.PUBLIC_FEEDS` into it, `sources/plugins/cboe.py` gone; here it is
     installed editable from its clone beside the app's (`%USERPROFILE%\source\plugin-integrator-cboe`).
  4. *Then each of the others* (the user, 2026-11-01: "all the plugins should be plugin-integrator-xxxx
     and have a separate repo"), the same way, one at a time: `plugin-integrator-bitstamp` 1.3.0
     (2026-11-01, its trade stream with it); `plugin-integrator-coinbase` 1.3.0 and
     `plugin-integrator-bitfinex` 1.2.0 (2026-11-01: `sources.archive.venue_view` reads the plan and
     caps from the plugin's record, no longer from `config.PUBLIC_FEEDS`); `plugin-integrator-tiingo`
     1.4.0 (2026-11-01, its forex feed and its Blue Ocean poller with it: API 1.10 gives a poller
     the session calendar (`sources.stream.session_at`) and a plugin the machine-wide ledger
     (`sources.registry.MachineLedger`); its key read from `Settings.env`, the app's
     `tiingo_api_key` field and `config.PUBLIC_FEEDS` gone); the dataset libraries
     `plugin-integrator-french` 1.1.0 and `plugin-integrator-datahub` 1.1.0 (2026-11-01; Datahub's
     mirrors' git submodules stay in the app's `data/github/`, where `LIMINAL_DATA_REPOS` points:
     the plugin names each repository and file); the publishers `plugin-integrator-federal` 1.4.0
     (2026-11-01, the six agencies with it; New York time from `sources.archive.ET`);
     `plugin-integrator-alpaca` 1.4.0 (2026-11-01, Blue Ocean and the IEX stream with it: API 1.11 gives a
     plugin its own settings, `Provider.settings` built when the app's load and reached with
     `Settings.plugin(name)`, so Alpaca's keys, plan, feed, venue and caps left `config.py`);
     `plugin-integrator-massive` 1.4.0 (2026-11-01, its headlines with it; the last: its key, plans
     (`TIERS`, `tier_for`) and caps are its own settings too, the app's base `Settings` keep no
     feed's plan (`NO_PLAN` until a feed's view sets one), `plan` is the capabilities scan of one
     feed, and `sources/plugins/` is empty).

  The repositories, under `pcxmac-dev`, all made: `plugin-integrator-massive` (Massive and its
  headlines publisher, on its key; made), `plugin-integrator-alpaca` (Alpaca with its overnight feed on Blue
  Ocean, reached through Alpaca's API, keys and limits; made), `plugin-integrator-tiingo` (Tiingo with its
  forex feed: one key and account; made), `plugin-integrator-coinbase` (made), `plugin-integrator-bitstamp` (made),
  `plugin-integrator-bitfinex` (made), `plugin-integrator-cboe` (made), and the publishers `plugin-integrator-federal`
  (the federal agencies, one plugin; made), the dataset libraries `plugin-integrator-french` (made) and
  `plugin-integrator-datahub` (made), and, later, a state regulators' plugin of its own.

## AI assistants are plugins (2026-11-03)

The user: three AI plugins "that have a vanilla API, are compliant with the rest of the plugin
structure (setup / connecting methods / etc) but allow interfacing with Claude, Grock and ChatGPT
Codex via API key, an email / account name", "used to evaluate certain standardized arguments from
other plugins or apps", "extensible, and allow for higher order evaluation", supporting free plans,
with each vendor's plans and rates written in and a way to find out when they change.

- **The kind** (liminal-libs 1.3.0, `liminal_libs.assistants`): `AssistantPlugin` is the third kind of
  plugin, on the platform's base (`PlatformPlugin`): its `vendor`, its `assistant` (an `Assistant`
  subclass: the vendor's headers, request body, answer parsing, error shapes and rate-limit headers;
  `ResponsesAssistant` for the OpenAI-shaped Responses API), its tables (`plans`, the free one first;
  `models`, the default first; `sources`, the vendor's pages they were written from; `verified`, the
  date) and its `caps`. Its keys (`<NAME>_API_KEY`, read under the vendor's own name too; `<NAME>_ACCOUNT`,
  the email or account name, typed in the open, sent to the vendor only as a digest where the API
  takes one), its plan variables (`<NAME>_PLAN`, `_MODEL`, `_REQUESTS_PER_DAY`, `_TOKENS_PER_DAY`) and its
  options (`_MAX_OUTPUT_TOKENS`, `_EFFORT`) are derived from the tables; its settings are an
  `AssistantSettings` (never shown with the key or the account). The packages: plugin-platform-claude
  (Anthropic's Messages API), plugin-platform-grok (xAI's Responses API), plugin-platform-codex
  (OpenAI's Responses API), each with the vendor's tables verified 2026-11-03.
- **Arguments** (`assistants/arguments.py`): a `Question` (the question, context items such as headlines,
  an assessment, a report or plain text, and the answer form: text, a verdict on a scale, or JSON
  fields) or a `Judge` (higher order: earlier evaluations compared, scored or reconciled); kinds are
  registered by name, so a plugin adds its own by subclassing, and an argument crosses processes as
  a dict. An answer is an `Evaluation` (`assistants/evaluation.py`: the text, the verdict and fields
  parsed, what it cost by the vendor's own figure or the table's prices, the usage, how it ended),
  kept in the main file's `evaluations` table (`storage/evaluations.py`). The library's `assistants.evaluate`,
  `evaluate_all`, `judge` and `default_assistant` (`LIMINAL_ASSISTANT`, else the first set up) are what a
  plugin or the app calls: a plugin never imports an AI package.
- **Free, caps, offline.** The free plan is the default; where a vendor's free plan reaches no API
  (Anthropic: a Claude.ai subscription is not an API key; xAI: no free credits) the plugin says so and
  sends nothing until the plan the key is on is chosen; OpenAI lists a Free usage tier, so Codex's
  free plan sends. The caps are per machine (the machine ledgers, every database together): 50
  requests and 200,000 tokens a day, 1,024 output tokens an answer, the cheapest model; raised on the
  Plugins view. Nothing is sent from an offline copy, and every error is scrubbed of the key and the
  account before it is logged or shown.
- **Connect and the weekly check.** Connect (the plugin's capability scan, `capabilities <name>`, the
  Plugins view, the key script) tries the key on the vendor's models list, sends one answer of a few
  tokens for the limits the server sends, names the plan they show (applied where you set none, as a
  feed's scan is) and compares the models listed and the vendor's own cost figure with the tables;
  each package's weekly CI run (`LIMINAL_VENDOR_PAGES=1`) reads the vendor's public pages again
  (`assistants/verify.py`) and fails when a written-in price or limit is gone: a change of plans is
  a new release of that plugin.
- **The app**: the "AI assistants" group on the Plugins view (Setup with the key hidden and the
  account in the open, Plan with the plan, model and caps, the written-in tables, Limits, Connect,
  Usage today here and on the machine, the last answers, a Try it box), `POST /api/ai/evaluate` and
  `GET /api/ai/evaluations` (the fetcher's page serves them too), `liminal-charts ai ask|models|connect|evaluations|judge`,
  the key script's assistants section.

## Stocks abroad, VIX futures and more forex (2026-11-02)

The user: "are there any other public sources for 'bars'/data for stocks on foreign exchanges?",
"lets include futures and forex/currencies", "update existing plugins, add exchanges where applicable
and symbols, and if new sources, create a new plugin". Built with liminal-libs 1.2.0/1.2.1 (plugin
API 1.12), liminal-integrator 1.2.0 and this app:

- **One name per listing.** A stock listed outside the US is stored as `TICKER.SUFFIX` with the
  Reuters-style suffix of its exchange's MIC (`models.VENUES`: `7203.T` Tokyo XTKS, `2330.TW` Taiwan
  XTAI, `PETR4.SA` B3 BVMF, `600519.SS` Shanghai XSHG, `000001.SZ` Shenzhen XSHE, `VOD.L` London
  XLON, `SHOP.TO` Toronto XTSE, `SAP.DE` Xetra XETR, ...); `TSE:7203` and `7203::XTKS` normalise to
  it (`symbols.stock_spec`), US tickers and pins (`SPCX::NASDAQ`, `BRK.B`) are as before. `VOD` on
  London and on Nasdaq are two names, never one row.
- **A feed carries the exchanges it names.** `Provider.exchanges` (API 1.12) lists the MICs a stock
  feed carries, `"US"` standing for every US listing; a feed without it carries US listings alone, so
  Massive and Alpaca refuse `7203.T` and the fetcher's assessment puts a listing abroad on the feeds
  that carry its exchange, or says "no feed set up here carries XTKS listings". The fetcher assesses
  by the stored ticker (`resolve` first: before, a request for `SPCX::NASDAQ` or `EUR/USD` failed).
- **Its own days.** A listing abroad counts every weekday as a trading day covering its local calendar
  day (`sessions.MarketCalendar.local_day_session`, the seam where `exchange_calendars`' holidays
  will go), through `freshness`, `coverage` and the collector (the watch row's `exchange`), so a
  Tokyo trading day that is a US holiday is asked for; its day bars stay stamped at midnight ET of
  the local date (`derive.aggregate` bins by the local day, no regular-session filter); the bad-data
  scans and audits built on New York sessions leave it alone. Publication delays by tier name
  (`freshness.PLAN_DELAYS`: `twse`, `b3`, `alphavantage-free` end of day; `jquants-free` twelve weeks
  late; `twelvedata-free` whole UTC days). The Markets globe reads the library's
  `sessions.world_calendar`.
- **In the app.** The Exchanges view gives a stock exchange abroad its own venue (name, local
  timezone, the local day as its session, its feeds' market behind the delivery), the Markets panel a
  group of its own (`x-xtks`); US equities keep US listings alone; the stocks tab names each listing's
  exchange (`exchangeName`), the directory spanning every source's listings (`Store.directory()`,
  one row per listing); a listing's freshness, horizon and next due moment follow its exchange's days.
- **The plugins.** `plugin-integrator-tiingo` 1.5.0 carries Shanghai's and Shenzhen's A-shares (Tiingo's
  bare codes, from 2007; `TIINGO_LISTINGS=on` lists them from its supported-tickers file);
  `plugin-integrator-cboe` 1.3.0 adds a second feed, `cboefutures`: Cboe's daily file of each VX
  contract, named by its settlement date (the Wednesday 30 days before the next month's third Friday,
  holiday-shifted), so `VX1!` rolls by volume from them; new packages `plugin-integrator-twse` 1.0.0
  (every Taiwan Stock Exchange listing's daily bars from TWSE's open data, no key),
  `plugin-integrator-b3` (B3's COTAHIST files, no key), `plugin-integrator-jquants` (JPX's J-Quants API
  V2, an API key in a header: V1 and its refresh tokens were discontinued on 2026-06-01; the free
  plan twelve weeks late), `plugin-integrator-alphavantage`
  (stocks on London, Toronto, Xetra, Bombay, Shanghai and Shenzhen, a free key, the latest 100 days
  accumulating run by run) and `plugin-integrator-twelvedata` (forex minutes on a free key, its own
  market like Tiingo's forex). Deferred, with why, in the plan of 2026-11-02: NSE/BSE (bot
  protection, terms), KRX (registration terms), Cboe Europe files, Deutsche Börse's dataset (tens of
  GB, needs a minute-bar foreign calendar), TrueFX/HistData/Dukascopy (accounts or terms), the
  exchanges without a free official route (HKEX, ASX, LSE, TSX, Euronext); exchange-holiday
  precision for foreign calendars and intraday foreign sessions in the chart are follow-ups.

- **Five passes over the fetcher's page (2026-11-03;** the user: "loop at least 5 times, each time
  go through the UI and look for discrepancies and attempt to fix them, then cycle, and make sure
  fixes worked, just go through the integrator with emphasis on function of each plugin"**).** On the
  offline preview, plugin by plugin: the Plugins view offers a plugin's own settings in an Options
  table (`Provider.options` with choices in `plan_choices`: Tiingo's `TIINGO_LISTINGS`, B3's
  `B3_HISTORY_YEARS`, saved like a plan); a plugin is "a closed archive" only when each of its feeds
  is (Cboe's indices are, its VIX futures live; the feeds table says which); a keyed feed without a
  scan no longer calls itself public; a feed of daily bars alone lists no trades database
  (`registry.plugin_databases`); a keyed plugin without its key shows what the plan would give once
  the key is set; VIX futures are end of day (`PLAN_DELAYS["cboe-futures"]`). The Live view lists
  the stocks abroad with why they have no session feed. The dependency checks read a ticker's asset
  class from the ticker when it is no longer watched, name the integrator's service, and explain the
  pandas 3.1 calendar failure. The fetcher assesses a request even offline, so the stack shows which
  feeds it would ask; a request is put under its stored name however it is typed
  (`server.stored_ticker`, the integrator's `request` too); a listing abroad's plan is one job per
  feed (liminal-integrator 1.2.1 to 1.2.3, liminal-libs 1.2.2 to 1.2.4, Tiingo 1.5.1, B3 1.0.1, Cboe
  1.3.1).

## The Federal plugin (2026-10-30)

The user: "lets take a look at all regulatory agencies that affect corporations and stocks, review
whats already in place on this app and merge everything in to a Federal plugin, from news feeds to
edgar access, to everything that matters"; "if there are state regulatory agencies, those would be
in another plugin"; "if api keys are required we can address that". One publisher plugin,
`plugin-integrator-federal` (a package of its own since 2026-11-01), a module per agency, each a part
(`part_of="federal"`, API 1.2) with
its own scrape checks. Official feeds and documented APIs only, never a page; nothing stored moves
(gov/gov.db, datasets/cftc.db stay); new data in new files beside them. It holds three kinds:
releases and news (read on schedules), data series (point in time, chartable), and company data
(per stock, matched by the SEC's CIK to ticker list).

- **Phase 1, the merge (done 2026-10-30):** the Federal Reserve, the New York Fed, the SEC, BLS and
  the Federal Register (each a plugin of its own before), and the CFTC's Commitments of Traders (a
  dataset) as its parts; one Plugins view entry (each agency a section of its page, opened for its
  feeds, checks, limits and own pause; Pause all) and one `plugins` row; the Gov view renamed
  Federal; the Datasets view names the CFTC's as the Federal plugin's. No new request.
- **Phase 2, EDGAR company data:** the ticker to CIK list; per watched or open stock its filings
  (8-K with items, 10-Q/10-K, offerings S-1/S-3/424B, proxies), share structure (shares outstanding
  from each cover, public float in dollars yearly with float shares from that day's close, diluted
  shares), Form 4 insider trades, Schedule 13D/13G holders, fails-to-deliver (twice a month); new
  filings within about 10 minutes from the XBRL RSS under /Archives (sec.gov/cgi-bin, the "latest
  filings" Atom, is disallowed by its robots.txt); SEC litigation releases and administrative
  proceedings. Beside it, from Massive's plugin (every Stocks plan): short interest and days to cover,
  daily short volume, free float. The side panel's Share structure, Filings, Insiders and holders.
- **Phase 3, markets:** the Fed's monetary and enforcement feeds and H.15 (its release XML: the Data
  Download Program is being retired); the New York Fed's repo and reverse repo, SOMA holdings,
  primary dealer statistics and more rates; the Treasury's daily yield curves, auctions
  (TreasuryDirect), Daily Treasury Statement and debt (Fiscal Data API); the CFTC's API
  (disaggregated and financial futures reports); FDIC failures, OCC and CFPB feeds; OFAC's sanctions
  list compared daily (its RSS retired).
- **Phase 4, economic calendar and data:** one calendar (FOMC by hand, BLS's ICS, BEA's JSON schedule,
  Census, EIA's weekly reports, Treasury auctions, WASDE); series from BLS, BEA, Census and EIA (their
  keys).
- **Phase 5, sector regulators linked to stocks:** FDA (openFDA approvals and recalls, its RSS), FTC
  (competition releases, merger early terminations: an api.data.gov key) and DOJ Antitrust, the
  Consolidated Screening List against listed companies, NRC events, EPA enforcement, USDA reports;
  NHTSA, FCC and FERC once tested (their sites refused a research fetcher). Matched by CIK, else by
  name with a confidence shown; never acted on automatically.
- **Phase 6, Congress and rulemaking:** Congress.gov, Regulations.gov, GovInfo (presidential
  documents too), on an api.data.gov key.
- **Keys** (the user signs up; the key script sets them): GOV_CONTACT (the SEC), one api.data.gov key,
  EIA's, BEA's, Census's and BLS's own, openFDA's optionally. Not planned unless asked: USPTO, USITC
  and FFIEC (identity checks or an account).
- **Left out:** FINRA's site and MSRB's EMMA (their terms bar automated reading), FRED (no storing),
  SEC full-text search (not a documented API), sec.gov/cgi-bin (robots.txt), the CFTC's RSS (off),
  pages only (the Beige Book), PCAOB (terms not read), the White House site's feed (GovInfo instead).

## The Plugins view (2026-10-27)

A rail icon of a plug, below Fetcher and Live, opens every provider plugin (`gui/plugins.py`,
`plugins.js`): on the left the plugins with keys and the public ones, each with its state (ready,
keys not set, no key, killed) and its plan; in the middle the one picked: each plan variable, its
value and where it came from (set by you, found by its scan, the default), what the last scan found
and where your own setting disagrees with it, what its scan cannot tell; its limits (requests a
day, the rate kept, a run, symbols a month, what its server publishes), what it can do on that
plan, its live streams, and its last capability scan with a Rescan button; on the right its part in
the delivery scheme (the eras it delivers and those it stands by for, its load in days of its
limits), its requests today, the symbols it watches, its keys (set or not, never their values).
`/api/plugins` (kept 10 s) reads the settings, the reports, the scans kept, the watchlist and the
request ledger: no requests. `POST /api/plugins/rescan[?name=]` runs `capabilities` in a process of
its own, one at a time; the plans it finds count in the app within SETTINGS_TTL_S.

A plan its scan cannot tell (Tiingo's Power from Commercial) is chosen there (the user, 2026-10-29):
each plan variable with choices is a dropdown, "automatic" (its scan or its default) or one of its
choices, confirmed first. `POST /api/plugins/plan` ({plugin, variable, value or null}) saves it where
the key script saves it, the user's environment variables (`config.save_user_variable`: the
registry's user Environment key, and the app's own environment, which load_settings ranks above the
saved variables); null removes it. Only a plugin's own plan variable with choices, to one of them:
never a key, never a variable without choices. The app uses it at its next look at the settings, the
sync from its next run. An offline copy refuses: it shares the user's real variables.

## Which source delivers what (2026-10-27)

The user's order of collection: **#1 daily bars, #2 1-minute bars, #3 1-second bars**. `sourcing.py`
reads every provider's standard report and makes the delivery plan; `liminal-charts sourcing` shows the
reports and the plan (no requests).

- **Cost** is what a job spends of a subscription's limits (its price is fixed): requests per
  symbol-year (from the bars a request returns) against the requests a day it allows, as days of
  its limits; a monthly symbol cap counts too (Tiingo: 450 symbols a month is 15 a day).
- **Unique or common.** Per job and market (copies of one market: the consolidated tape from
  Massive, Alpaca and Tiingo; each exchange's own book is its own market), history is cut into eras
  where the set of sources that reach it changes. An era one source reaches is unique to it: it
  delivers it. An era several reach is common.
- **Delivery**, job by job in the user's order: a job's unique eras go to their source; its
  common eras each to the source that finishes its load soonest with it (days of its limits), so
  cheap capacity does the common work and a scarce subscription keeps to what only it has. A later
  job's load never pushes an earlier job's work aside. Ties: cheaper, fresher, SOURCE_PRIORITY.
  Audits are shown beside each source, not used to order them (most fail on cross-exchange
  differences today).
- **The fetcher follows it** (`coverage.plan`): in an era, the sources ahead in the scheme's order
  download it and the others stand back, unless the one ahead does not watch the symbol or found
  nothing there (then the next takes it). Feeds the scheme does not know keep the old rank.
- On this machine (Massive Advanced at 200,000 requests a day): Tiingo the daily bars before 2003,
  Massive everything since (its 50,000 bars a request make it the cheapest), Alpaca standing by;
  each exchange its own crypto, Cboe its index files. At Massive's default 2,000 a day, Alpaca takes
  the bars since 2016.
- The current session is the live streams' (only for the symbols open on a chart: `LIVE_STREAMS`).
- **Capability scans.** A plugin may scan what its plan really gives (`scan`: a few requests);
  each feed's sync runs it once a week (`cli.SCAN_DAYS`), `liminal-charts capabilities`
  on demand, and its report shows the answers. Tiingo's (2026-10-27): daily bars, IEX's 1-minute
  bars, forex and crypto bars on the free plan; Blue Ocean's overnight bars refused (a $9 a month
  add-on). Only its daily bars are collected.

## The fourth session: overnight (2026-10-27)

US stocks trade overnight on Blue Ocean ATS, 20:00-04:00 ET, Sunday to Thursday nights before a
trading day; the consolidated tape (Massive, Alpaca's SIP) carries none of it.

- **Its feed:** `boats` (sources/plugins/alpaca/'s `BOATS`, part of the Alpaca plugin; `BoatsSource`): Alpaca's market data with
  `feed=boats`, minute bars, on the free Basic plan up to 15 minutes ago. Its own market, one
  venue: ranked last in a chart of all feeds (live.PARTIAL_FEEDS, like IEX), so its bars show
  where no feed of the whole market has any: the nights. It uses Alpaca's keys and shares Alpaca's
  limits (one rate window and one daily cap, kept under Alpaca's name for the machine). It sends
  no daily bars (`collector.gives_days`), and no feed of one venue's share gives a stock its days.
  A stock opened in the chart goes on it (and on any other set-up feed that can carry it).
- **The calendar** (sessions.py): `overnight_open(day)`; with `overnight`, a stock's session runs
  from 20:00 the evening before. The collector's checks keep to 04:00-20:00.
- **The chart**: the ETH button steps RTH, ETH (04:00-20:00, overnight left out), ETH+ON (all
  four sessions: `session=all`). Overnight is shaded violet, pre-market blue, after-hours grey;
  a night belongs to the next day's daily bar (`session_hours` 4 for stocks). Blue Ocean has no
  1-second bars, so the nights show from the minute levels up.

## CME Group alongside NYSE and Nasdaq (2026-10-27)

`initialize` takes CME Group's four exchanges (XCME, XNYM, XCBT, XCEC; `--exchanges CME`) next to
XNYS and XNAS: every futures product in the directory is watched as its continuous chart (`ES1!`)
on every feed that collects futures (Massive), after the stocks in the fetch order. The directory
has no futures volume (its market snapshot is the stocks': CL there is Colgate-Palmolive), so the
best-known products go first (`initializer.MAJOR_FUTURES`), then by sector, event, power and
single-stock contracts last. Codes the symbol rules cannot read as continuous (ending in digits:
sports event swaps, single-stock futures, synthetic "price series") are left out and listed.

All futures share one rate (Massive's free class: 5 requests a minute, at most 7,200 a day), and a
run works one symbol at a time, so a run takes futures first and stops at `FUTURES_PER_RUN`
requests (default 100, about 20 minutes); the stocks take the rest. The first `FUTURES_DAILY` (250)
in the order and those picked are kept current each session; the rest are refreshed weekly. Their
official session schedules are asked for those picked and the first 20; the others follow the
calendar's CME rules.

## The Exchanges view (2026-10-27)

A rail icon of a pair of scales opens every venue the app tracks symbols on (`gui/venues.py`,
`venues.js`), grouped: the stock exchanges (NYSE, Nasdaq, the other US venues, Blue Ocean's
overnight), CME Group's four (each futures product by its exchange in the directory), the indices,
each crypto exchange (a feed's own market), forex. The middle column lists the venue's products (a
search, a sector filter; the last session's change for stocks); the right shows the venue's
insight (open now or not and the next change, products by sector, how much is collected and how
freshly, the feeds, the delivery scheme's eras, the last session's movers) or, for a product
picked, the symbol panel's statistics (`/api/info`) with a button to open its chart. Built from the
watchlist and the directory only (`/api/venues`, kept 30 s; `/api/venue?id=`): no bars, no requests.
Its grid applies only while it is shown (`main > .view.ex-view:not([hidden])`): with `!important` it
outranked `main > .view[hidden]` and showed beside every other view (fixed 2026-10-27).

## Schemes and replay (2026-10-25)

**Schemes** are trading algorithms run over a stretch of time on the Analysis view: the highlight
when one is drawn, otherwise what is in view, at the chart's level (1-second bars to years), in the
view the chart shows (filtered or all data). Built in, each with its settings; long and short. They
are backtests only: nothing places orders anywhere. (`calc/schemes.py`; `/api/scheme`; the Analysis
panel's Schemes section.)

Every scheme is causal: what it decides at a bar uses that bar and the bars before it, never a later
one. A bar's signal is known when the bar closes (the bar still forming is left out) and filled at
the next bar's open; a stop or target
fills inside a bar at its level (at the open when the bar opens beyond it; when one bar reaches both,
the stop is taken: the worse case). A position is a fixed quantity from its entry (equity at entry
over the entry price), so a trade's return is its side times its exit over its entry, less costs.
Costs: a fee and slippage in basis points of what is traded, charged at every entry and exit (a
reversal pays both). The run starts flat at the stretch's first bar; the bars before it are warm-up
only (each scheme's indicators and state, read from the store as far back as the scheme needs and no
further). A position still open at the stretch's end is marked at its last close and shown as open.
The test suite proves the causality: a run over any prefix of the bars gives the signals the full run
gave up to that point.

1. **ATR swing reversal** (stop and reverse). In an up-swing the highest high since the swing began
   is tracked; when a close falls more than `mult` × ATR(`atr`) below it, the swing has turned: short,
   and the lowest low is tracked from then on; the mirror turns it long. ATR is Wilder's average true
   range. Defaults: ATR 14, mult 3. Drawn: the reversal level each bar, and the swing highs and lows
   (each known only at the bar that confirmed it).
2. **Failed breakout reclaim.** The range is the highest high and lowest low of the `lookback` bars
   before a bar. A breakout is a close beyond the range by more than `depth` × ATR (or, with
   `breakout` = wick, a high or low beyond it); it fails when, within `window` bars, a close comes
   back inside the level it broke: long after a failed breakdown, short after a failed break up. The
   stop is beyond the failed breakout's extreme by `stop` × ATR; the target the range's far side as
   it was at the breakout. Exits: the stop, the target, or the opposite signal (a reversal).
   Defaults: lookback 20, window 3, depth 0, stop 0.25, ATR 14.
3. **CUSUM change detection** (Page's two-sided cumulative sum). Each bar's log return is scaled by
   the volatility of the returns before it (an exponentially weighted average of their squares, span
   `vol`); S+ adds up how far the scaled returns run above `k`, S− how far they run below −k, each
   floored at zero. When S+ passes `h` a shift up is declared (long), when S− does a shift down
   (short), and both start again from zero. Defaults: k 0.5, h 5, vol 50. Drawn: S+ and S− with h.

Results: the trades (side, entry and exit with times, prices and reason, return, bars held), the
equity curve and position per bar, and statistics (net return, buy and hold over the same bars, the
deepest drawdown, trades, share won, average win and loss, profit factor, time in a position).

**Replay** plays a symbol's bars from a start point as if they were arriving live. The start is a
bar picked on the chart; the chart shows only what was known then. Speeds 1×, 2×, 4×, 8×, 16× and
32× real market time; a gap longer than 15 minutes (nights, weekends, holidays, halts) passes in an
instant. The forming bar builds up from the finest bars held (1-second bars where the feed has them,
else minutes; each tagged with the chart bar it belongs to, binned by the server exactly as the
chart's bars are), so a candle grows as it did; each finer bar shows once its own time has passed.
Pause, step one finer bar, step one chart bar, or stop (back to the chart as it is now). Replay reads
a page of finer bars ahead (`/api/replay`), never the rest of the history. Schemes run during replay:
signals, positions, trades and the equity curve appear as the replay reaches them, each when the bar
that decided it closes: the scheme runs over the bars replayed and complete, so it sees no more than
it would have live.

## Trading (2026-10-25)

The Trading view (the rail) holds accounts at brokers, each a plugin behind one interface
(`trading/brokers.py`): its account (cash, equity, profit and loss), positions, orders and fills,
placing and cancelling an order. **Paper** is the first: a simulated account here, filled from the
stored bars by the backtests' rules. **Interactive Brokers** is the first real broker, planned and
not built; live trading stays off (`LIVE_TRADING`, off by default) until it is decided otherwise,
and then every order is placed by the user in this app, never by anything automatic without the
user's say-so.

Paper (`trading/paper.py`; its own tables in the main file): one account at a time, with a starting
cash and a fee in basis points; a reset starts a new account and keeps the old one's record. Orders:
buy or sell a quantity (selling more than is held goes short) at market, at a limit or on a stop,
for the day or until cancelled. Each working order is filled from the finest bars stored after it was
placed (1-second bars where held, else minutes), the first that reach it: a market order at the next
bar's open; a buy limit when a bar's low reaches it, at the limit or the open if the bar opened below
it (a sell limit the mirror); a buy stop when a bar's high reaches it, at the stop or the open if it
opened above (a sell stop the mirror). A day order not filled by its session's close expires, once
the stored bars reach that close (bars stored late still count); bars only a partial feed (IEX) holds
are looked at again for up to 2 days, until the whole-market feeds' bars replace them. Orders
are matched whenever the Trading view asks (and so after the fact, from the stored bars, when it was
closed: fills are what the bars allowed, whenever they are looked at). Positions are kept at their
average cost; realized profit and loss when a position is reduced, unrealized at the last stored price.

## State (2026-10-24)

Already separated: data sources behind one interface (`sources/`), coverage planning
(`coverage.py`), audits (`audit.py`), the composite model and quality (`composite.py`).

Too broad: `storage.py` (≈2,000 lines: feed files, ticks, coverage ledger, audits, quality,
directory and listings, datasets, news, icons, layouts, runs) and `collector.py` (≈1,100 lines: bars,
ticks, futures, directory and icons, calendars, conditions, news, audit scheduling).

Calculations still in the GUI: resampling and calendar grouping (`gui/bars.py`), chart types such as
Renko (`gui/charttypes.py`), statistics (`gui/stats.py`). `gui/projection.py` (the time axis after
the last bar: where future bars will start; not a price forecast) imports private functions of
`gui/bars.py`.

Look-ahead: the derivative model's centred Savitzky-Golay window uses later bars. Nothing feeds it to
a backtest yet; there is no pattern engine in the repository yet.

Lost history: bar and dataset upserts overwrite values; one-file datasets are replaced whole on
refresh, so the French library's revisions are lost.

## Steps

1. **Derivatives out of the GUI, with a causal mode.** Done: `calc/derivatives.py`; `Model.causal`
   (trailing window, value at t from data up to t; tests prove later data never changes it). The
   chart keeps the centred window for viewing.
2. **Split `storage.py` by responsibility.** Done: `storage/` is a package; the `Store` composes one
   part per responsibility (feeds, watchlist, ledger, audits, markets, directory, datasets, app
   state, maintenance; shared schema and helpers), each moved verbatim in its own commit.
3. **Point-in-time storage.** Datasets done: every version of every value with `available_at`,
   `stored_at` and `origin`; revisions and withdrawals are versions; `as_of` reads; raw downloads
   kept locally by checksum; GitHub-hosted datasets are pinned git submodules whose commit history
   is imported version by version. Feed bars done: `stored_at` per bar; a re-download with the same
   values changes nothing, a correction keeps the replaced version in `bar_revisions` (not for the
   derived composite); `bars_window(as_of=, published=)` gives the series as it stood, gated by the
   plan's publication rule (`freshness.published_at`). Bars stored before tracking have no
   `stored_at` (taken as known since before tracking). Ticks: not versioned yet.
4. **Derived results with provenance.** Done: a `derivations` record (kind, product, code version as
   the git commit with "+dirty" for uncommitted changes, settings, inputs read, span, rows) for bars
   built from ticks (one per day built, with the tick count and a digest of the condition rules),
   composite builds, quality scores, audits and dataset quality priors; derived rows point to it.
   Bars built from ticks live apart from the feed's own in `built_bars` (reads combine them, built
   first); the composite has its own file; indicators (computed on request) return their settings,
   code version and whether they look ahead. `liminal-charts provenance [product] -v`.
   Bars built before provenance moved apart under "before-provenance" records.
5. **Calculations out of the GUI.** Resampling, calendar grouping, chart-type construction and
   statistics move to `calc/`; `gui/bars.py` becomes a thin adapter; the projection uses public
   `calc/` functions only.
6. **Split `collector.py` by job**: bars, ticks, futures, directory and icons, calendars and
   conditions, news; `sync` orchestrates.
7. **Backtest harness.** A simulated clock; data only through `as_of`; causal calculations only;
   each result records its inputs, code version and settings.
8. **Dataset prices in symbol composites** (Shiller S&P composite behind the S&P 500 index, IMF
   commodity prices behind their futures): built on 3–5, so the composite records which source and
   resolution supplies each epoch.
