"""The charts' own routes, as their Internal API lists them beside the frame's (liminal-libs 1.63.0, internal.py; the
user, 2026-11-16: "for all apps, and all plugins, there should be an external api tab, and an internal api tab";
2026-11-17: "all plugins all apps"; Monday list item 17).

Two servers answer the charts' pages, and each has its table. The front (gui/front.py, at the charts' port) answers a
few routes itself and passes every other request of its pages on to the integrator it links: FRONT_ROUTES, then one
row for each of the frame's routes it passes on (passed_on), so the charts' Internal API never claims the integrator's
answers as its own. The data API (gui/server.py, make_handler) answers the pages' data, in the integrator's process
behind the front, or in this one alone (gui --alone): DATA_ROUTES, then the platform core's two Databases routes
(PLATFORM_CORE) and the frame's three writes it does not answer (NOT_ANSWERED). ChartsApp.routes picks the tables by
how the app is served.

Each row is (method, path, what it is, its class, who asks it), as liminal_libs.internal.FRAME_ROUTES: `{...}` is a
part that varies; the class is read, change, ask or stream; who asks it is "page" (the charts' pages), "client" (a
program), "login" (the login the charts app inherits) or "app". Plain tuples: nothing is imported with them but the
frame's table, when the library has it. tests/test_routes.py holds the tables to the code: a route added to the front
or to the data API without its row here fails it."""
from __future__ import annotations

try:  # liminal-libs 1.63.0: the frame's own routes (every floor of the charts has them; an older library: none)
    from liminal_libs.internal import FRAME_ROUTES
except ImportError:  # pragma: no cover - a library from before 1.63.0
    FRAME_ROUTES = ()

INTEGRATOR = "passed on to the integrator it links, which answers as itself: "
# what is said of the frame's writes this page's server does not answer (POST /api/app/stop ...): it ends on its stop
# event (liminal-libs 1.87.0's lifecycle.StopEvent, liminal-stop-charts, which liminal-signal sets), or on SIGTERM
UNANSWERED = ("not answered by this page's server (404): the charts end on their stop event, which liminal-signal "
              "sets, or on SIGTERM; the frame's words: ")

# -- the front (gui/front.py: do_GET, _write) -------------------------------------------------------------------------
FRONT_ROUTES = (
    ("GET", "/api/trays", "the trays at the rail's foot: the login it holds and the platform it links", "read",
     "page"),
    ("GET", "/api/ui", "this machine's UI file, answered here before any integrator runs (the sign-in page asks it "
                       "first)", "read", "page"),
    ("POST", "/api/ui", "the UI's theme set {\"theme\": name}, in this machine's UI file", "change", "page"),
    ("GET", "/api/login/pairs", "the pairs the charts' plugins ask for, asked by the login it holds", "read", "login"),
    ("GET", "/api/app/apis", "the charts' own External and Internal API, answered here; with ?plugin=NAME (and "
                             "&of=publisher), one entry of the Plugins view, passed on to the integrator", "read",
     "page"),
    ("POST", "/api/databases/instances", "a database plugin's form: a test passed on to the integrator; a change "
                                         "refused (409) with the platform's page for that plugin, where it is made",
     "change", "page"),
    ("GET", "/api/{rest}", "every other read of its pages, passed on to the integrator's data API at its port "
                           "(LIMINAL_FETCHER_PORT), its answer and its cookies back; an event stream as it comes",
     "read", "page"),
    ("ANY", "/api/{rest}", "every other write of its pages (the write header asked here first), passed on to the "
                           "integrator's data API", "change", "page"),
    ("ANY", "/v1/{rest}", "the Trading Desk's client API (gui/desk.py), passed on to the integrator's data API",
     "change", "client"),
    ("ANY", "/platform/{rest}", "the platform it links (LIMINAL_PLATFORM_URL), its pages and its API, with only its "
                                "own cookies passed on", "change", "page"),
)
# the frame's routes the front answers itself (the charts app it holds answers GET /api/codebase and POST
# /api/codebase/fetch, as its own; liminal_libs.apps.serve_object): every other frame route is passed on
FRONT_OWN = frozenset({("GET", "/api/app/apis"), ("GET", "/api/login/pairs"), ("GET", "/api/codebase"),
                       ("GET", "/api/ui"), ("POST", "/api/codebase/fetch")})

# -- the data API (gui/server.py, make_handler: _get, then _write), in its chain's order -----------------------------
_GETS = (
    ("/api/ui", "the UI's preferences every page applies (this machine's file)"),
    ("/api/trays", "the trays at the rail's foot: on the integrator's own page, the login it holds; else none"),
    ("/api/profiles", "the profiles kept, and the one this browser is signed in as"),
    ("/api/register", "the profile's register: every stock it opened in the chart, the latest first"),
    ("/api/layouts", "the profile's chart layouts"),
    ("/api/watchlists", "the profile's watchlists"),
    ("/api/layouts/{id}", "one of the profile's layouts"),
    ("/api/symbols", "one entry per ticker, with every feed that has data for it"),
    ("/api/meta", "a series' facts for the chart: its span, its rows, its feeds (?source, ?ticker, ?adjust, ?auto)"),
    ("/api/bars", "a series' bars for the chart, a page at a time (?limit)"),
    ("/api/stream", "the bars and quotes of the symbols a page shows, as they come (Server-Sent Events)"),
    ("/api/profile", "the virtual level 2 for a chart's bar (the newest without ?at)"),
    ("/api/info", "the side panel: a symbol, its business details, key statistics and data coverage"),
    ("/api/interface", "what the app this API speaks for offers (its frame's interface) and the plugins attached"),
    ("/api/deps", "dependency health: every outside thing the app relies on, checked against what is stored and installed"),
    ("/api/directory", "every listing in the directory, with what is known locally"),
    ("/api/announcements", "the announcements of the watchlist's symbols and the world's markets"),
    ("/api/gov", "the Gov view: the agencies' newest, the current regulatory parameters, each source's state"),
    ("/api/resources", "the Resources view: the app's processes, the machine, its bandwidth, disks and data files "
                       "(?span)"),
    ("/api/venues", "the Exchanges view's venues, each with its state now"),
    ("/api/plugins", "the Plugins view: every plugin, its plan, limits, capabilities, last scan and part in the "
                     "delivery scheme; the rescan going now"),
    ("/api/ai/evaluations", "the AI evaluations kept, by assistant, kind and argument (?id: one and its inputs)"),
    ("/api/venue", "one venue's products, for the Exchanges view's middle column (?id)"),
    ("/api/markets", "the markets and their calendars"),
    ("/api/calendar", "a symbol's sessions between two times, for the chart's annotations"),
    ("/api/inventory", "a symbol's data feed by feed and frame by frame (?ticker)"),
    ("/api/pruned", "a symbol's bad data: each feed's pruned list and its counts (?ticker)"),
    ("/api/future", "the chart's slots past its last bar, for drawing ahead of it"),
    ("/api/range", "the figures of a range of the chart (?from, ?to)"),
    ("/api/schemes", "the built-in schemes and their settings, for the Analysis panel"),
    ("/api/trading", "the Trading view: the brokers, and the paper account"),
    ("/api/scheme", "a scheme run over a series, for the Analysis panel"),
    ("/api/replay", "a scheme replayed bar by bar over a series"),
    ("/api/icon", "a symbol's icon (PNG, JPEG, GIF or WebP; never SVG)"),
    ("/api/activity", "the activity log; ?open from this app's own pages only"),
    ("/api/exchanges", "the exchanges kept"),
    ("/api/audits", "the audits kept, their scores, the queue and the quality (?ticker: one symbol's)"),
    ("/api/queue", "the Fetcher: the fetch queue across plugins, by plugin, kind and state"),
    ("/api/requests", "the fetcher's requests, as the Fetcher view shows them, with its status"),
    ("/api/plugin-fetching", "a plugin's fetching status for its page: its state, queue, feeds, limits, runs, log"),
    ("/api/plugin-stores", "a plugin's stores by data type for its page: its engines, its pick, a move's progress"),
    ("/api/datasets", "the research datasets: what is held, its span, files fetched and closed, quality"),
    ("/api/dataset/series", "a dataset's series (or the COT report's markets) for the viewer's picker"),
    ("/api/dataset/values", "a dataset series' values"),
    ("/api/app/apis", "with ?plugin=NAME (and &of=publisher): one entry of the Plugins view, its Internal API, its "
                      "External API's cards and what it learned of them, for every plugin object behind it; nothing "
                      "of a plugin is called. Without ?plugin=, its frame's own two views"),
)
_WRITES = (
    ("POST", "/api/ui", "the UI's theme set, in this machine's file"),
    ("POST", "/api/profiles", "a new profile"),
    ("POST", "/api/profiles/sign-in", "this browser signed in as a profile {id}"),
    ("POST", "/api/profiles/sign-out", "this browser signed out of its profile"),
    ("PUT", "/api/profiles/{id}", "a profile renamed"),
    ("POST", "/api/watch", "a directory listing added to the watchlist (?ticker): the fetcher then collects it"),
    ("POST", "/api/updates-pause", "the updates of the symbols not picked paused (1 to 30 days) or resumed"),
    ("POST", "/api/requests", "a symbol asked of the fetcher {symbol, kinds}, taken next"),
    ("DELETE", "/api/requests/{id}", "a request of the fetcher cancelled"),
    ("PUT", "/api/watchlists", "the profile's watchlists saved"),
    ("POST", "/api/priority", "the chart opened a symbol (?ticker): every feed holding it updates it first; ?visit=1, you opened it: in your register"),
    ("POST", "/api/fetching", "background fetching paused, halted or resumed"),
    ("POST", "/api/ai/evaluate", "an AI evaluation asked of an assistant, within its plan's caps"),
    ("POST", "/api/plugins/plan", "a plugin's plan set {plugin, variable, value}"),
    ("POST", "/api/plugins/key", "a plugin's key kept through the login {plugin, variable, value}: never shown back"),
    ("POST", "/api/plugins/store", "a plugin's store picked for a data type {plugin, type, engine}"),
    ("POST", "/api/plugins/rescan", "the plugins scanned again (?name: one)"),
    ("POST", "/api/plugins/recheck", "a publisher checked again (?name)"),
    ("POST", "/api/plugins/pause", "a publisher paused or resumed (?name, ?on)"),
    ("POST", "/api/derive", "a watched symbol's derived frames built now from what is stored, in a process of its own (?ticker)"),
    ("POST", "/api/scan", "a scan with fetching over every watched symbol, in a process of its own"),
    ("POST", "/api/fetch", "a scan with fetching of one symbol (?ticker), in a process of its own"),
    ("POST", "/api/trading/orders", "a paper order placed (live trading is off)"),
    ("DELETE", "/api/trading/orders/{id}", "a paper order cancelled"),
    ("POST", "/api/trading/reset", "the paper account reset"),
    ("POST", "/api/layouts", "a new layout of the profile's"),
    ("PUT", "/api/layouts/{id}", "a layout saved"),
    ("DELETE", "/api/layouts/{id}", "a layout deleted"),
)
DATA_ROUTES = (
    *((("GET", path, about, "stream" if path == "/api/stream" else "read",
        "client" if path == "/api/interface" else "page")) for path, about in _GETS),
    *((method, path, about, "change", "page") for method, path, about in _WRITES),
    ("ANY", "/v1/{rest}", "the Trading Desk's client API (gui/desk.py): accounts, evaluations, constraints, reviews and "
                          "paper orders, under the page's local-only and write-header rules", "change", "client"),
)
# the writes the data API leaves to its frame (_frame_write): the frame that answers them lists its own rows (the
# integrator's: its databases and auditors; every app's Codebase). For tests/test_routes.py alone.
LEFT_TO_FRAME = (
    ("POST", "/api/databases/{rest}", "asked of its frame", "change", "page"),
    ("POST", "/api/auditor/{rest}", "asked of its frame", "change", "page"),
    ("POST", "/api/codebase/{rest}", "asked of its frame", "change", "page"),
)
# the platform core the charts app inherits (liminal-libs 1.90.0's apps/platform.py; 0.2.8), as alone it answers them
PLATFORM_CORE = (
    ("GET", "/api/databases/plugins", "the Databases view: every database plugin with its instances, its External and "
                                      "Internal API, and writer: false (a change is made on the platform)", "read",
     "page"),
    ("POST", "/api/databases/instances", "a database plugin's form: a test here; a change answers 409, made on the "
                                         "platform's Databases section", "change", "page"),
)
# the frame's writes the data API answers 404 (its _write knows none of them): set apart, no source names them
NOT_ANSWERED_PATHS = (("POST", "/api/app/stop"), ("POST", "/api/app/signal"), ("POST", "/api/plugins/{name}/signal"))


def _frame_row(method: str, path: str):
    return next((r for r in FRAME_ROUTES if (r[0], r[1]) == (method, path)), None)


def not_answered() -> tuple:
    """The frame's writes the data API does not answer, each said so with the frame's own words after (its class and
    who asks it the frame's)."""
    out = []
    for method, path in NOT_ANSWERED_PATHS:
        row = _frame_row(method, path)
        if row is not None:
            out.append((method, path, UNANSWERED + row[2], *row[3:5]))
    return tuple(out)


NOT_ANSWERED = not_answered()


def passed_on() -> tuple:
    """One row for every frame route the front does not answer itself, made from the frame's (its method, path, class
    and who asks it): what the integrator's data API answers there, in its words, class and caller when it has a row
    of its own (GET /api/plugins: the Plugins view), the frame's otherwise, and a write it does not answer said so. A
    frame route added later is placed by itself."""
    data = {(r[0], r[1]): r for r in DATA_ROUTES}
    unanswered = set(NOT_ANSWERED_PATHS)
    out = []
    for row in FRAME_ROUTES:
        method, path, about = row[0], row[1], row[2]
        if (method, path) in FRONT_OWN:
            continue
        if (method, path) in unanswered:
            out.append((method, path, "passed on to the integrator it links, whose data API does not answer it (404): "
                        + about, *row[3:5]))
        elif (method, path) in data:
            out.append((method, path, INTEGRATOR + data[(method, path)][2], *data[(method, path)][3:5]))
        else:
            out.append((method, path, INTEGRATOR + about, *row[3:5]))
    return tuple(out)
