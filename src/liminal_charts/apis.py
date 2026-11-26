"""What the charts' own code associates with, for their own External API (0.2.10, Monday list item 17; liminal-libs
1.63.0's App.apis; the user, 2026-11-16: "the external should be all the SDKs/APIs the plugin associates with"; "for
all apps, and all plugins").

What the charts' own code calls that Liminal does not make, and nothing of their plugins' (each says its own on its
page): git, which the Codebase every app holds reads the workspace with; SQLite through Python's sqlite3, the paper
account's tables and the Trading Desk's, written by the charts' own SQL; and Python's signal module, the front's stop on
SIGTERM (SIGBREAK on Windows); and what the page and the charts' Python draw and compute with: the vendored
KLineChart, the sky's and the world's data, WebGL2, Server-Sent Events, pandas and NumPy. Imported only when the charts' own External API is asked (ChartsApp.apis), so the
charts' start loads nothing more for it.

Every address is one already read on a maker's own page for a card that landed before this one, with that card's day:
git's and the signal module's as the platform's apis.py has them (2026-11-17, 2026-11-18), SQLite's and Python's
sqlite3 as the SQLite plugin's (2026-11-16). The other seven were read on their makers' pages on 2026-11-26 (the
user's yes, "Yes, read them"). The charts never fetch any of them. Not a card: exchange_calendars and the tz data the
library's sessions read (the library's), and NASA's Blue Marble and Black Marble images in vendor/world, which no code
reads yet. This is the one .py of the charts that names an outside host
(the user, 2026-10-31: the app's code names no provider's host, links for people excepted):
tests/test_plugin_boundary.py holds every other to that, and pins each of these links by its address."""
from __future__ import annotations

from liminal_libs.apis import Api, Area, Call, Link

_GIT = "https://git-scm.com/docs/"
_SQLITE = "https://www.sqlite.org/"
_SQLITE3 = "https://docs.python.org/3/library/sqlite3.html"
_SIGNAL = "https://docs.python.org/3/library/signal.html"

GIT = Api(
    name="git", maker="The Git project", kind="command",
    about=("The version control command the charts' Codebase reads the workspace's git working copies with (the "
           "frame's Codebase, the Codebase view of the rail; GET /api/codebase, answered by the charts app the front "
           "holds): each one's branch, the commit checked out, its remote, what is changed and not committed, how far "
           "ahead and behind its upstream as of its last fetch, and its tracked files, counted by language. git is "
           "told to take no lock (GIT_OPTIONAL_LOCKS=0) and to ask nothing at a terminal (GIT_TERMINAL_PROMPT=0); no "
           "remote is asked but on the user's Fetch."),
    base="git, in each working copy of the workspace",
    version="git status's porcelain v2",
    auth="none of its own: a remote's credentials are git's and the user's (asked at no terminal: a fetch that needs "
         "them fails in words)",
    limits="20 s for each command, 90 s for a fetch; read at most once in 10 minutes unless asked fresh",
    costs="free (GPL 2)",
    links=(
        Link("Git reference", _GIT, "reference"),
        Link("git (its environment variables)", _GIT + "git", "reference"),
        Link("git status", _GIT + "git-status", "reference"),
        Link("git log", _GIT + "git-log", "reference"),
        Link("git config", _GIT + "git-config", "reference"),
        Link("git ls-files", _GIT + "git-ls-files", "reference"),
        Link("git fetch", _GIT + "git-fetch", "reference"),
        Link("Install Git", "https://git-scm.com/install/", "tool"),
        Link("git/git", "https://github.com/git/git", "source"),
    ),
    areas=(
        Area("The working copy", "status: the branch, its upstream, ahead and behind, what is changed",
             _GIT + "git-status", covered="each repository's branch, changes and distance from its upstream"),
        Area("History", "log: the commits", _GIT + "git-log",
             covered="the commit checked out: its hash, date and subject"),
        Area("Configuration", "config: the repository's settings", _GIT + "git-config",
             covered="its remote's address (shown with no name or token in it)"),
        Area("The index", "ls-files: the files git tracks", _GIT + "git-ls-files",
             covered="the code counted: the tracked files"),
        Area("Remotes", "fetch: what a remote has", _GIT + "git-fetch",
             covered="Fetch, on the user's click: the remote-tracking branches move, nothing checked out does"),
        Area("Commits, branches and merges", "commit, branch, merge, rebase, push: the repository changed",
             _GIT, covered=""),
    ),
    calls=(
        Call("git status --porcelain=v2 --branch", "the working copy's state and its branch's headers",
             "The working copy", used="Codebase: the branch, ahead and behind, the changes", docs=_GIT + "git-status"),
        Call("git log -1 --format=%h%x1f%cI%x1f%s", "the last commit: its short hash, its date, its subject", "History",
             used="Codebase: the commit checked out", docs=_GIT + "git-log"),
        Call("git config --get remote.NAME.url", "the address of the remote its branch follows (git config's older "
             "mode)", "Configuration", used="Codebase: its remote", docs=_GIT + "git-config"),
        Call("git ls-files -z", "the files git tracks, each ended by a NUL", "The index",
             used="Codebase: the files counted by language", docs=_GIT + "git-ls-files"),
        Call("git config --get-regexp ^remote\\..*\\.url$", "whether the working copy has any remote", "Configuration",
             used="before a fetch: a working copy with no remote is not asked", docs=_GIT + "git-config"),
        Call("git fetch --quiet", "what the remote has (its remote-tracking branches move)", "Remotes",
             used="Fetch, on the user's click (POST /api/codebase/fetch)", docs=_GIT + "git-fetch", writes=True),
    ),
    verified="2026-11-17")

SQLITE = Api(
    name="SQLite, through Python's sqlite3", maker="The SQLite project; the Python Software Foundation", kind="sdk",
    about=("The paper account's tables (trading/paper.py: paper_accounts, paper_orders) and the Trading Desk's "
           "(trading/desk.py: its desk_* tables) are the charts' own, made and written by the charts' own SQL on a "
           "reader of the main file the library's store opens (its own connection and lock). The Store picker shows "
           "the SQLite library's version (sqlite3.sqlite_version). Every other table, and the stores' engines, are "
           "the library's (liminal_libs.storage), and the database plugins' on the platform's Databases section."),
    base="the main file beside the app's data (liminal.db), on a connection of its own",
    version="the SQLite library Python is built with (sqlite3.sqlite_version)",
    auth="none: the file's permissions",
    limits="one writer at a time (the main file's locks); the paper account's work under a lock of its own",
    costs="free (SQLite is in the public domain; Python's sqlite3 module comes with Python)",
    links=(
        Link("SQLite Documentation", _SQLITE + "docs.html", "docs"),
        Link("Python's sqlite3 module (DB-API 2.0 interface for SQLite databases)", _SQLITE3, "reference"),
        Link("Write-Ahead Logging", _SQLITE + "wal.html", "guide"),
        Link("Release History Of SQLite", _SQLITE + "changes.html", "changelog"),
        Link("SQLite Copyright (public domain)", _SQLITE + "copyright.html", "terms"),
    ),
    areas=(
        Area("Python's sqlite3 module", "the standard library's interface to SQLite", _SQLITE3,
             covered="the paper account and the Trading Desk, on the connection the store gives them"),
        Area("Pragmas", "the statements that read and set a database's own settings", _SQLITE + "pragma.html",
             covered="PRAGMA table_info: whether the paper orders' table has a column added later"),
    ),
    calls=(
        Call("Connection.executescript(CREATE TABLE IF NOT EXISTS ...)", "the paper account's tables and the "
             "desk's made when missing", "Python's sqlite3 module",
             used="the first use of the paper account (PaperBook) and of the Trading Desk (Desk)", docs=_SQLITE3,
             writes=True),
        Call("PRAGMA table_info(paper_orders)", "the columns of a table", "Pragmas",
             used="before ALTER TABLE paper_orders ADD COLUMN client_id, on a file from before it",
             docs=_SQLITE + "pragma.html"),
        Call("Connection.execute(INSERT, UPDATE, SELECT ...)", "the paper account's and the desk's rows read and "
             "written", "Python's sqlite3 module", used="the Trading view and the Trading Desk (paper only)",
             docs=_SQLITE3, writes=True),
        Call("sqlite3.sqlite_version", "the SQLite library's version", "Python's sqlite3 module",
             used="the Store picker's engines (GET /api/plugin-stores)", docs=_SQLITE3),
    ),
    verified="2026-11-16")

SIGNALS = Api(
    name="Python's signal module", maker="Python Software Foundation", kind="system",
    about=("The system's signals as the charts' front takes them (gui/front.py): SIGTERM, and SIGBREAK on Windows, set "
           "its stop, as Ctrl+C does: the page goes down and its login is closed in good order. On Windows the front "
           "also looks at its stop event (liminal-stop-charts, which liminal-signal sets) every half second. A "
           "handler is set from the main thread alone, and the old ones are put back when the front ends."),
    base="the signal module, from the front's main thread",
    version="Python 3.12 to 3.15",
    auth="none: the signals the system sends this process",
    limits="none: each handler set once, as the front starts, and put back as it ends",
    costs="free (the PSF licence)",
    links=(
        Link("signal: set handlers for asynchronous events", _SIGNAL, "docs"),
        Link("Execution of Python signal handlers", _SIGNAL + "#execution-of-python-signal-handlers", "guide"),
        Link("signal(7): an overview of signals", "https://man7.org/linux/man-pages/man7/signal.7.html", "reference"),
    ),
    calls=(
        Call("signal.signal(SIGTERM, handler), signal(SIGBREAK, handler)", "a handler set for a signal, only from "
             "the main thread (ValueError from another); Python runs it later in the main thread",
             used="as the front starts: each sets the front's stop; the old handler kept and put back at its end",
             docs=_SIGNAL + "#signal.signal"),
    ),
    verified="2026-11-18")

_KLC = "https://klinecharts.com/en-US/"
_KLC_API = _KLC + "api/"
_CELESTIAL = "https://github.com/ofrohn/d3-celestial"
_ATLAS = "https://github.com/topojson/world-atlas"
_NE = "https://www.naturalearthdata.com/"
_GSHHG = "https://www.soest.hawaii.edu/pwessel/gshhg/"
_MDN = "https://developer.mozilla.org/en-US/docs/Web/API/"
_WEBGL2 = "https://registry.khronos.org/webgl/specs/latest/2.0/"
_SSE = "https://html.spec.whatwg.org/multipage/server-sent-events.html"
_PANDAS = "https://pandas.pydata.org/docs/"
_NUMPY = "https://numpy.org/doc/stable/"

# The seven below were read on their makers' pages on 2026-11-26 (the user's yes, "Yes, read them")

KLINECHART = Api(
    name="KLineChart", maker="The KLineChart project", kind="sdk",
    about=("The chart library the Stock and Analysis charts are drawn with (gui/static/app.js), vendored at 10.1.0 "
           "(static/vendor/klinecharts, its VERSION and NOTICE beside it; the page asks no other host for it): the "
           "chart made in the page's element, the charts' own indicators, overlays and one figure registered with it, "
           "its styles set from the theme, and the bars handed to it by the charts' data loader."),
    base="window.klinecharts, from static/vendor/klinecharts/klinecharts.min.js",
    version="10.1.0 (vendored)",
    auth="none: a script of the page",
    limits="none of its own",
    costs="free (Apache 2.0)",
    links=(
        Link("KLineChart: Guide", _KLC + "guide/introduction", "guide"),
        Link("init(ds, options?)", _KLC_API + "chart/init", "reference"),
        Link("registerIndicator", _KLC_API + "chart/registerIndicator", "reference"),
        Link("registerOverlay", _KLC_API + "chart/registerOverlay", "reference"),
        Link("registerFigure", _KLC_API + "chart/registerFigure", "reference"),
        Link("Instance API", _KLC_API + "instance/getDom", "reference"),
        Link("klinecharts/KLineChart", "https://github.com/klinecharts/KLineChart", "source"),
    ),
    areas=(
        Area("The chart", "a chart made in an element, and its extensions registered", _KLC_API + "chart/init",
             covered="one chart, the charts' own indicators (10), overlays (3) and a figure"),
        Area("The instance", "what a chart made is asked", _KLC_API + "instance/getDom",
             covered="styles, indicators, overlays, the symbol and period, the data loader, the pixel mapping"),
    ),
    calls=(
        Call("klinecharts.init('chart', {timezone, styles, locale, layout})", "the chart made", "The chart",
             used="the page's start", docs=_KLC_API + "chart/init"),
        Call("klinecharts.registerIndicator, registerOverlay, registerFigure", "the charts' own extensions",
             "The chart", used="the charts' indicators, the drawing tools' overlays, an anchor's figure",
             docs=_KLC_API + "chart/registerIndicator"),
        Call("chart.setStyles, createIndicator, removeIndicator, createOverlay", "the chart's look and contents",
             "The instance", used="the theme, the indicators shown, the drawings",
             docs=_KLC_API + "instance/setStyles"),
        Call("chart.setSymbol, setPeriod, setDataLoader, getDataList", "what it shows and where its bars come from",
             "The instance", used="the symbol and period picked; the bars from /api/bars and /api/stream",
             docs=_KLC_API + "instance/setDataLoader"),
        Call("chart.convertToPixel, convertFromPixel, subscribeAction, resize, scrollToRealTime",
             "between bars and pixels; its events; its size; the newest bar", "The instance",
             used="the cursor, the handles, the touch gestures, the newest bar",
             docs=_KLC_API + "instance/convertToPixel"),
    ),
    verified="2026-11-26")

SKY = Api(
    name="The sky's data (d3-celestial)", maker="Olaf Frohn (d3-celestial)", kind="file",
    about=("The stars and the constellations the Markets globe draws behind the Earth, turning with sidereal time "
           "(globe.js, globe-gl.js): d3-celestial 0.7.35's data files, vendored in static/vendor/sky (the stars to "
           "magnitude 6, from the Extended Hipparcos Compilation; the constellations' lines and names, from the IAU), "
           "GeoJSON at the J2000 epoch. Read from the charts' own server; d3-celestial's code is not used."),
    base="static/vendor/sky: stars.6.json, constellations.json, constellations.lines.json",
    version="d3-celestial 0.7.35's data",
    auth="none: files of the page",
    limits="none: three files read once when the globe is first drawn",
    costs="free (BSD 3-Clause)",
    links=(
        Link("ofrohn/d3-celestial", _CELESTIAL, "docs"),
    ),
    areas=(
        Area("Stars", "stars.6.json: the stars to magnitude 6", _CELESTIAL, covered="the globe's background stars"),
        Area("Constellations", "constellations.json, constellations.lines.json: names and figures", _CELESTIAL,
             covered="the constellation lines and names"),
    ),
    calls=(
        Call("GET vendor/sky/stars.6.json, constellations.json, constellations.lines.json", "the three files",
             "Stars", used="the Markets globe, once", docs=_CELESTIAL),
    ),
    verified="2026-11-26")

WORLD = Api(
    name="The world's maps (world-atlas, Natural Earth, GSHHG, the tz database)",
    maker="Mike Bostock (world-atlas); Natural Earth; Paul Wessel and Walter H. F. Smith (GSHHG); IANA (tz)",
    kind="file",
    about=("The Earth the Markets globe, its icon and the throbber draw (globe.js, globe-gl.js, globeicon.js, "
           "throbber.js), vendored in static/vendor/world: world-atlas 2.0.2's TopoJSON countries at 1:110m, 1:50m "
           "and 1:10m (Natural Earth's); the civil time-zone grid tools/build_timezones.py built from Natural "
           "Earth's 1:10m time zones (natural-earth-vector v5.1.2) and the tz database's zone.tab (the tzdata "
           "package), each zone's offset worked out in the browser; and the high-resolution coast and border tiles "
           "tools/build_coastlines.py built from GSHHG 2.3.7 (coast-h, 10 x 10 degrees each, read as they come into "
           "view). NASA's Blue Marble and Black Marble images lie beside them, read by no code yet."),
    base="static/vendor/world: countries-*.json, time-zones.json, coast-h/",
    version="world-atlas 2.0.2; Natural Earth 1:10m time zones (v5.1.2); GSHHG 2.3.7 (high)",
    auth="none: files of the page",
    limits="none: the coast tiles read only as they come into view",
    costs="free (world-atlas: ISC; Natural Earth: public domain; GSHHG: LGPL 3)",
    links=(
        Link("topojson/world-atlas", _ATLAS, "docs"),
        Link("Natural Earth: Timezones", _NE + "downloads/10m-cultural-vectors/timezones/", "reference"),
        Link("Natural Earth: Terms of Use (public domain)", _NE + "about/terms-of-use/", "terms"),
        Link("nvkelso/natural-earth-vector", "https://github.com/nvkelso/natural-earth-vector", "source"),
        Link("GSHHG: a Global Self-consistent, Hierarchical, High-resolution Geography Database", _GSHHG, "docs"),
        Link("Time Zone Database (IANA)", "https://www.iana.org/time-zones", "reference"),
    ),
    areas=(
        Area("Countries", "world-atlas's countries and land, at three scales", _ATLAS,
             covered="the globe's borders and land; its icon's and the throbber's outlines (1:50m)"),
        Area("Time zones", "Natural Earth's zones and the tz database's", _NE + "downloads/10m-cultural-vectors/timezones/",
             covered="the globe's civil time-zone shading (time-zones.json)"),
        Area("Coasts and borders", "GSHHG's shorelines and borders", _GSHHG,
             covered="the coast-h tiles, at high resolution, when zoomed in"),
    ),
    calls=(
        Call("GET vendor/world/countries-110m.json, countries-50m.json, countries-10m.json", "the countries",
             "Countries", used="the globe at each zoom, its icon, the throbber", docs=_ATLAS),
        Call("GET vendor/world/time-zones.json", "the time-zone grid", "Time zones", used="the globe's shading",
             docs=_NE + "downloads/10m-cultural-vectors/timezones/"),
        Call("GET vendor/world/coast-h/index.json, coast-h/<lat>_<lon>.bin", "the tiles there, and one tile",
             "Coasts and borders", used="the globe, zoomed in", docs=_GSHHG),
    ),
    verified="2026-11-26")

WEBGL2 = Api(
    name="WebGL2", maker="The Khronos Group (the specification); the browser", kind="sdk",
    about=("The browser's OpenGL ES 3.0 context on a canvas: the GPU bar renderer (gpu.js) draws the chart's bars "
           "and lines in one pass, each bar placed by the vertex shader, and the Markets globe (globe-gl.js) draws "
           "the stars, the sphere with its night side and time-zone shading, the coasts and borders, and the Moon. "
           "Each asks for the context first (GpuBars.probe) and is drawn only where the browser gives one."),
    base="canvas.getContext('webgl2'), in the page",
    version="WebGL 2.0 (OpenGL ES 3.0; GLSL ES 3.00 shaders)",
    auth="none: the browser's",
    limits="the GPU's and the browser's own",
    costs="free",
    links=(
        Link("WebGL2RenderingContext (MDN)", _MDN + "WebGL2RenderingContext", "reference"),
        Link("WebGL 2.0 Specification", _WEBGL2, "spec"),
    ),
    areas=(
        Area("The context", "a WebGL2 context on a canvas", _MDN + "WebGL2RenderingContext",
             covered="the chart's bars, the globe"),
    ),
    calls=(
        Call("canvas.getContext('webgl2', {alpha, antialias, premultipliedAlpha})", "the context (null without "
             "WebGL 2)", "The context", used="gpu.js, globe-gl.js; probed first", docs=_MDN + "WebGL2RenderingContext"),
        Call("WEBGL_debug_renderer_info, WEBGL_lose_context", "the GPU's name; the probe's context let go",
             "The context", used="the probe: a software renderer is told apart", docs=_WEBGL2),
    ),
    verified="2026-11-26")

SSE = Api(
    name="Server-Sent Events", maker="WHATWG (the HTML Standard); the browser", kind="sdk",
    about=("How a live chart follows its feed without asking (gui/server.py GET /api/stream, app.js): the charts' "
           "server answers text/event-stream and sends the bars since the page's newest whenever the stored data "
           "changes (or, for a model, a reload), a keep-alive comment every 15 s, and an `end` event after 300 s, "
           "when the page opens the next stream; on a closed stream the page polls instead. The browser's EventSource "
           "reads it."),
    base="GET /api/stream on the charts' own server; EventSource in the page",
    version="the HTML Standard's server-sent events",
    auth="none: the page's own server",
    limits="a stream lasts 300 s; over HTTP/1.1 a browser keeps about 6 open to one host (MDN)",
    costs="free",
    links=(
        Link("Server-sent events (HTML Standard)", _SSE, "spec"),
        Link("Using server-sent events (MDN)", _MDN + "Server-sent_events/Using_server-sent_events", "guide"),
        Link("EventSource (MDN)", _MDN + "EventSource", "reference"),
    ),
    areas=(
        Area("The event stream", "text/event-stream: data lines, comments, named events", _SSE,
             covered="the bars since the newest, a keep-alive, `end`"),
        Area("EventSource", "the browser's reader of a stream", _MDN + "EventSource",
             covered="the live chart's stream, closed when the chart changes"),
    ),
    calls=(
        Call("new EventSource('/api/stream?...'); onmessage; addEventListener('end')", "a stream opened and read",
             "EventSource", used="a live Stock or Analysis chart", docs=_MDN + "EventSource"),
        Call("Content-Type: text/event-stream; data: {...}; : keep-alive; event: end", "what the server writes",
             "The event stream", used="GET /api/stream", docs=_SSE),
    ),
    verified="2026-11-26")

PANDAS = Api(
    name="pandas", maker="The pandas development team (NumFOCUS)", kind="sdk",
    about=("The tables the charts' Python works on: time bars indexed by time (gui/bars.py, window.py), the chart "
           "types that transform them (charttypes.py: Heikin Ashi, Renko, Line break, Kagi, Point & figure, Range), "
           "the time axis after the last bar (projection.py), the side panel's key statistics (stats.py) and the "
           "paper account's (trading/paper.py), with their times converted between zones."),
    base="the pandas package, in the charts' process",
    version="held below 3.1 (deps.py: exchange_calendars and pandas 3.1)",
    auth="none: a library",
    limits="none of its own; the charts read windows of bars, not whole histories",
    costs="free (BSD 3-Clause)",
    links=(
        Link("pandas documentation", _PANDAS, "docs"),
        Link("API reference", _PANDAS + "reference/index.html", "reference"),
        Link("User Guide", _PANDAS + "user_guide/index.html", "guide"),
        Link("Release notes", _PANDAS + "whatsnew/index.html", "changelog"),
    ),
    areas=(
        Area("Time series", "a time index, its zones, its groups", _PANDAS + "user_guide/index.html",
             covered="bars by time: DatetimeIndex, tz_localize, tz_convert, groupby"),
    ),
    calls=(
        Call("pd.DatetimeIndex, pd.to_datetime, tz_localize, tz_convert", "times made and moved between zones",
             "Time series", used="the bars, the axis, the sessions", docs=_PANDAS + "reference/index.html"),
        Call("DataFrame.groupby(...).agg(...)", "rows gathered and summed up", "Time series",
             used="bars of a longer period, the chart types", docs=_PANDAS + "reference/index.html"),
    ),
    verified="2026-11-26")

NUMPY = Api(
    name="NumPy", maker="The NumPy community (NumFOCUS)", kind="sdk",
    about=("Arrays under the charts' Python: the bars' columns (gui/bars.py, window.py), the chart types' and the "
           "statistics' sums (charttypes.py, stats.py), the axis's searches (projection.py) and the paper account's "
           "arithmetic (trading/paper.py); tools/build_coastlines.py quantises the coast tiles with it."),
    base="the numpy package, in the charts' process",
    version="the one pandas is installed with",
    auth="none: a library",
    limits="none of its own",
    costs="free (BSD 3-Clause)",
    links=(
        Link("NumPy documentation", _NUMPY, "docs"),
        Link("NumPy reference", _NUMPY + "reference/index.html", "reference"),
        Link("NumPy user guide", _NUMPY + "user/index.html", "guide"),
        Link("Release notes", _NUMPY + "release.html", "changelog"),
    ),
    areas=(
        Area("Arrays", "arrays and the functions over them", _NUMPY + "reference/index.html",
             covered="where, searchsorted, isfinite, cumsum, percentile, log, sqrt"),
    ),
    calls=(
        Call("np.where, np.searchsorted, np.isfinite, np.cumsum, np.percentile", "arrays chosen, searched, summed",
             "Arrays", used="the bars, the axis, the statistics", docs=_NUMPY + "reference/index.html"),
    ),
    verified="2026-11-26")

APIS = (GIT, SQLITE, SIGNALS, KLINECHART, SKY, WORLD, WEBGL2, SSE, PANDAS, NUMPY)
