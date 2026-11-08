"""What the app's two servers share (the user, 2026-11-01: "make sure that the library calls
contribute to code reuse"): since liminal-libs 1.7.0 the library's web.py, every app's (the apps'
frame, liminal_libs.apps), re-exported here for the chart front (front.py) and the data API
(server.py); the page's own folder (STATIC) and the fetcher's page (fetcher_page) stay here. The
icons (icons.js), the theme (liminal.css, theme.js) and the context menu (menu.js) are served from the
library's ui.STATIC beside this folder's files; so are the UI's preferences (ui_preferences, save_ui).

Nothing here opens a database: the front has none, the data API's is the app's (ChartApp)."""
from __future__ import annotations

import json
from pathlib import Path

from liminal_libs import ui
from liminal_libs import web as _web
from liminal_libs.web import (ALWAYS, LOCAL_HOSTS, MAX_BODY, PROFILE_COOKIE, PROFILE_HEADER,  # noqa: F401
                              WRITE_HEADER, cookie, from_this_app, listening, local_only, profile_cookie,
                              send, send_json)

STATIC = Path(__file__).parent / "static"
UI_PATH = "/api/ui"  # the UI's preferences (GET; POST a theme): answered by the front itself, and by the data API


def ui_preferences() -> dict:
    """The UI's preferences every app's page applies (liminal_libs.ui: one file for this machine, the data folder's
    ui.json, which the platform's Preferences plugin writes too; theme.js paints a page in them), and `saved`: whether
    that file holds a theme yet (while it holds none, the chart page carries a profile's own theme from before over)."""
    try:
        kept = json.loads(ui.preferences_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = None
    theme = kept.get("theme") if isinstance(kept, dict) else None
    return {**ui.preferences(), "saved": isinstance(theme, str) and theme in ui.THEMES}


def save_ui(body) -> dict:
    """A theme picked on the chart page ({"theme": name}: one of ui.THEMES), kept in the UI's file, so every app's
    page follows it; ValueError for anything else (another field, a theme there is not). Returns the preferences."""
    if not isinstance(body, dict) or set(body) != {"theme"} or not isinstance(body["theme"], str):
        raise ValueError('the UI\'s preferences are set as {"theme": name}')
    ui.save_preferences(theme=body["theme"])
    return ui_preferences()


def static_file(handler, rel: str) -> None:
    """One of the page's files (index.html, a script, a stylesheet, the vendored chart) from this
    folder, else the library's (icons.js, liminal.css), never a file outside them."""
    _web.static_file(handler, rel, (STATIC, ui.STATIC))


_FETCHER_PAGE: list[bytes] = []


def fetcher_page() -> bytes:
    """The fetcher's own page: the chart page without the chart, the tools and what is the user's
    (style.css and the scripts read the body's mode): the data and monitoring views, the icons.
    Nobody's: no profile, no login."""
    if not _FETCHER_PAGE:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace("<body>", '<body data-mode="fetcher">', 1).replace("<title>Liminal Charts</title>",
                                                                               "<title>Liminal Fetcher</title>", 1)
        _FETCHER_PAGE.append(html.encode("utf-8"))
    return _FETCHER_PAGE[0]
