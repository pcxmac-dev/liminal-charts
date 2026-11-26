"""What the charts' own code associates with, for their own External API (0.2.10, Monday list item 17; liminal-libs
1.63.0's App.apis; the user, 2026-11-16: "the external should be all the SDKs/APIs the plugin associates with"; "for
all apps, and all plugins").

What the charts' own code calls that Liminal does not make, and nothing of their plugins' (each says its own on its
page): git, which the Codebase every app holds reads the workspace with; SQLite through Python's sqlite3, the paper
account's tables and the Trading Desk's, written by the charts' own SQL; and Python's signal module, the front's stop on
SIGTERM (SIGBREAK on Windows). Imported only when the charts' own External API is asked (ChartsApp.apis), so the
charts' start loads nothing more for it.

Every address is one already read on a maker's own page for a card that landed before this one, with that card's day:
git's and the signal module's as the platform's apis.py has them (2026-11-17, 2026-11-18), SQLite's and Python's
sqlite3 as the SQLite plugin's (2026-11-16). The charts never fetch any of them. What else the charts' code works
with (the vendored KLineChart, the sky's and the world's data, WebGL2, Server-Sent Events, pandas and numpy) has no
card yet: none of their makers' pages was read for one. This is the one .py of the charts that names an outside host
(the user, 2026-10-31: the app's code names no provider's host, links for people excepted):
tests/test_plugin_boundary.py holds every other to that."""
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

APIS = (GIT, SQLITE, SIGNALS)
