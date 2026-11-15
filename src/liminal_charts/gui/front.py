"""The chart app's front: the chart page, every request of its pages funnelled to the integrator it
links, and the login it inherits (an object it holds) served under /login/.

The user, 2026-11-01: "the concept is to be able to use the app, which connects to the fetcher for
database access"; "so the app needs to funnel requests through the fetcher to the databases"; "i can
look at the fetcher to see what the data looks like, i can use the 'app' to see what i want to do".
2026-11-03: "for the webserver / ports, i just integrator, and charts": two listeners on this
machine, the integrator's page and data API (liminal-integrator, LIMINAL_FETCHER_PORT: 8785) and this
front (8765); the fetcher host itself has no HTTP of its own. 2026-11-04: "objects that I can bind
together, like the login app and the integrator app ... I can still link to an existing integrator
and get its functions tied into the chart app ... seamlessly like it is local". 2026-11-14: "i only
want ports for liminal-login ; liminal-platform ; liminal-charts ; liminal-integrator"; "never provide
ports for anything else"; "don't start any of the applications in the background"; "the platform and
integrator inherit the login function, they are separate login instances, class vs object"; "the idea
is for an app like charts to be able to 'link' to an app like platform or an app like integrator or
both"; "linking vs including the objects, is so that parts of the code can be executed remotely, and
connected through the icon bridge": the integrator is linked, reached at its port and never started
from here (one that does not answer is not running, and the page is told so); the login is included,
an object of the login class held here for the charts app (its superclass, liminal_libs.apps
App.login_object), answered at this front's own port: no login process, no port of its own.

`liminal-charts gui` runs this: a local server (127.0.0.1) serving the chart page and its scripts, with
every /api/ request of its pages passed on to the integrator's data API at its port (liminal_libs.apps
.binding: the answers and the cookies back) and every /login/ request answered by the login held here.
No database is opened here, and no pipe: the integrator holds the fetcher while a page is in touch.
"""
from __future__ import annotations

import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from liminal_libs import config
from liminal_libs.apps import PAIRS_PATH, PortTaken, forward, pairs_wanted, serve_object
from liminal_libs.logs import log_to
from ..frame import ChartsApp
from . import web
from .web import WRITE_HEADER

INTEGRATOR_PORT = 8785  # the integrator's page and data API (LIMINAL_FETCHER_PORT; none: no page)
LOGIN_PREFIX = "/login"  # where the login the charts app inherits answers, at this front's port


def integrator_port() -> int | None:
    """The port of the integrator's page and data API (LIMINAL_FETCHER_PORT, 8785). None: no page at
    all (none, off), or a free one (0) this front cannot know."""
    raw = (config.setting("LIMINAL_FETCHER_PORT") or "").strip().lower()
    if raw in ("none", "off"):
        return None
    try:
        port = int(raw) if raw else INTEGRATOR_PORT
    except ValueError:
        return INTEGRATOR_PORT
    return port or None


def _is_login(path: str) -> bool:
    return path == LOGIN_PREFIX or path.startswith(LOGIN_PREFIX + "/")


class Front:
    """The front's way to the integrator it links (at its port; never started from here) and to the login
    it holds (an object of the login class, the charts app its superclass)."""

    def __init__(self, db_path: Path, log=print, name: str = "the app"):
        self.db_path = Path(db_path)
        self.log = log
        self.name = name
        self._port: int | None = None  # the integrator's port answered at last
        self._absent: int | None = None  # the integrator's port found not answering, said once until it answers
        # the charts app as the login it inherits sees it (its superclass, the one thing that login points to): its
        # name and title, where it is served (set once served), the pairs its plugins ask for (read here)
        self.charts = ChartsApp(log=log)
        self.charts.login_pairs = self.login_pairs
        self.url = ""  # this front's own address once served

    @property
    def connected(self) -> bool:
        return self._port is not None and web.listening(self._port)

    def integrator_port(self) -> int | None:
        """The integrator's port when it answers there. None: no page (LIMINAL_FETCHER_PORT), or nothing
        answers (the integrator is not running: said once; it is the user's to start, never started here)."""
        port = integrator_port()
        if port is None:
            self.log(f"the integrator has no page (LIMINAL_FETCHER_PORT): {self.name} cannot reach its data")
            return None
        if web.listening(port):
            if self._port != port:
                self._port, self._absent = port, None
                self.log(f"{self.name} is connected to the integrator at http://127.0.0.1:{port}/")
            return port
        self._port = None
        if self._absent != port:
            self._absent = port
            self.log(f"the integrator is not running (nothing answers at http://127.0.0.1:{port}/): {self.name} has no "
                     "data until it is started (liminal-integrator serve, or its service); nothing is started from here")
        return None

    def login_object(self):
        """The login the charts app inherits, held here; None when it is turned off (LIMINAL_CHARTS_LOGIN or
        LIMINAL_LOGIN_URL "none"/"off") or the login class is not installed (liminal-login: the app's `login`
        extra): the page then opens as before, on its profile alone."""
        return self.charts.login_object()

    def login_pairs(self) -> list[dict]:
        """The pairs the charts app's plugins ask for (the stock-data plugins and the assistants installed beside it),
        what the login this front holds asks of the charts app (liminal_libs.apps.pairs_wanted): the login knows no
        plugin itself."""
        try:
            from liminal_libs.sources.registry import assistants, providers, publishers
        except Exception:  # noqa: BLE001 - no registry here: none to say
            return []
        return pairs_wanted([*providers().values(), *publishers().values(), *assistants().values()])

    def signed_in(self, handler) -> bool | None:
        """Whether the browser of `handler` (a request: its cookies) is signed in to the login held here; None
        when there is none: the page then opens as before, on its profile alone."""
        return self.charts.signed_in(handler)

    def trays(self) -> list[dict]:
        """The trays drawn at the foot of the rail, after a divider each (the user, 2026-11-04: "Each divider
        represents a separate tray. From a separate object like the login"): the login held here."""
        return self.charts.trays()

    def close(self) -> None:
        """The login held here closed (its sessions)."""
        self.charts.stop()


def make_front_handler(front: Front):
    """The front's requests: the page's files from here, /api/ passed on to the integrator, /login/ to the
    login held here. The page is the chart's (a profile's: the login page first without one)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "liminal-front"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        # what the integrator's server checks and sends the same way is shared (web.py)
        def _send(self, status: int, body: bytes, ctype: str, headers: list[tuple[str, str]] | None = None) -> None:
            web.send(self, status, body, ctype, headers)

        def _json(self, data, status: int = 200) -> None:
            web.send_json(self, data, status)

        def _local(self) -> bool:
            return web.local_only(self)

        def _from_this_app(self) -> bool:
            return web.from_this_app(self)  # another site's page never reaches the integrator through here

        def do_GET(self) -> None:
            if not self._local():
                return
            path = urlsplit(self.path).path
            if path == "/api/trays":
                if self._from_this_app():
                    self._json({"trays": front.trays()})
                return
            if path == web.UI_PATH:
                if self._from_this_app():
                    self._ui("GET")
                return
            if path == PAIRS_PATH:  # the pairs the charts app's plugins ask for (a login run on its own asks it here)
                if self._from_this_app():
                    self._json({"pairs": front.login_pairs()})
                return
            if path.startswith("/api/"):
                if self._from_this_app():
                    self._proxy("GET")
                return
            if _is_login(path):
                if self._from_this_app():
                    self._login("GET")
                return
            self._static(path)

        def _write(self, method: str) -> None:
            if not self._local() or not self._from_this_app():
                return
            if self.headers.get(WRITE_HEADER) != "1":
                return self._send(HTTPStatus.FORBIDDEN, b"missing request header", "text/plain")
            if urlsplit(self.path).path == web.UI_PATH:
                return self._ui(method)
            if _is_login(urlsplit(self.path).path):
                return self._login(method)
            self._proxy(method)

        def do_POST(self) -> None:
            self._write("POST")

        def do_PUT(self) -> None:
            self._write("PUT")

        def do_DELETE(self) -> None:
            self._write("DELETE")

        def _ui(self, method: str) -> None:
            """The UI's preferences (GET; POST {"theme": name}), answered here rather than by the integrator: they
            are this machine's file (one for every app's page), and the sign-in page asks for them before any
            integrator need run (web.ui_preferences, web.save_ui)."""
            import json

            raw = self.headers.get("Content-Length") or "0"
            if method not in ("GET", "POST") or not raw.isdecimal() or int(raw) > web.MAX_BODY:
                self._drain()
                return self._json({"error": "the UI's preferences are read with GET and set with POST {\"theme\": name}"},
                                  HTTPStatus.BAD_REQUEST if method in ("GET", "POST") else HTTPStatus.METHOD_NOT_ALLOWED)
            try:
                if method == "GET":
                    return self._json(web.ui_preferences())
                return self._json(web.save_ui(json.loads(self.rfile.read(int(raw)) or b"{}")))
            except ValueError as e:  # not JSON, not a theme
                return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
            except Exception as e:  # noqa: BLE001 - answered, not dropped: the file unwritable, unreadable
                return self._json({"error": f"the UI's preferences: {type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _drain(self) -> None:
            """A request's body read and dropped before an answer that does not use it (a connection
            kept alive must not find it before the next request)."""
            raw = self.headers.get("Content-Length") or "0"
            if raw.isdecimal() and 0 < int(raw) <= web.MAX_BODY:
                self.rfile.read(int(raw))

        def _proxy(self, method: str) -> None:
            """The request passed on to the integrator's data API, its answer passed back (an event
            stream as it comes)."""
            port = front.integrator_port()
            if port is None:
                self._drain()
                return self._json({"error": f"the integrator is not running (nothing answers at its port): {front.name} "
                                            "has no data to show until it is started; nothing is started from here"},
                                  HTTPStatus.SERVICE_UNAVAILABLE)
            forward(self, method, f"http://127.0.0.1:{port}", self.path, drop_cookies=("liminal_login",),
                    unavailable="the integrator does not answer; ask again shortly")  # a login's session stays here

        def _login(self, method: str) -> None:
            """The request answered by the login held here, as /<the rest> (liminal_libs.apps.serve_object): its
            answer and its cookies (at /, so the main page's request carries the session and the front can ask
            who is signed in)."""
            login = front.login_object()
            if login is None:
                self._drain()
                return self._json({"error": f"{front.name} has no login here (turned off, or liminal-login is not "
                                            "installed)"}, HTTPStatus.SERVICE_UNAVAILABLE)
            path = self.path[len(LOGIN_PREFIX):]
            if not path or path.startswith("?"):
                path = "/" + path
            serve_object(self, login, method, path)

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            # the main page is the sign-in (the user, 2026-11-06: "i need a login or sign up on the main
            # page"): a browser not signed in to the login gets login.html, which holds the login's sign-in
            # and sign-up; so does one signed in as no profile yet (the page makes the profile for the user,
            # then opens the app). Without a login here, the profile alone, as before.
            if rel == "index.html":
                if web.profile_cookie(self) is None or front.signed_in(self) is False:
                    rel = "login.html"
            web.static_file(self, rel)

    return Handler


def serve_front(db_path: Path, port: int = 8765, open_browser: bool = True, log=None,
                stop: threading.Event | None = None) -> None:
    """The chart app: its page here, its data through the integrator it links (never started from here), its
    login held here. On `port` (0, any free one, is a test's); a port something else answers on is refused
    (PortTaken), never traded for another (the user, 2026-11-14: "never provide ports for anything else").
    Until Ctrl+C or `stop` is set."""
    log = log or log_to(db_path)
    front = Front(db_path, log)
    stop = stop or threading.Event()
    if port and web.listening(port):  # a bind alone cannot tell on Windows (web.listening)
        log(f"front: port {port} is taken (something else answers there): the chart app is not started, nor on another port")
        raise PortTaken(front.charts, port)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_front_handler(front))
    except OSError as e:
        log(f"front: port {port} could not be had ({e}): the chart app is not started, nor on another port")
        raise PortTaken(front.charts, port, e) from None
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    front.url = url.rstrip("/")
    front.charts.served = url  # where the login it holds says it is (under /login/)
    log(f"front: the chart's page at {url}, its data through the integrator")
    print(f"chart app running at {url}, its data through the integrator  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()

    def stopping() -> None:
        stop.wait()
        httpd.shutdown()

    threading.Thread(target=stopping, name="front-stop", daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
        front.close()
