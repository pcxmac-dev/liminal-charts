"""The chart app's front: the chart page, every request of its pages funnelled to the integrator, and
the apps bound to it (the login app) served under their own prefix.

The user, 2026-11-01: "the concept is to be able to use the app, which connects to the fetcher for
database access"; "so the app needs to funnel requests through the fetcher to the databases"; "i can
look at the fetcher to see what the data looks like, i can use the 'app' to see what i want to do".
2026-11-03: "for the webserver / ports, i just integrator, and charts": two listeners on this
machine, the integrator's page and data API (liminal-integrator, LIMINAL_FETCHER_PORT: 8785) and this
front (8765); the fetcher host itself has no HTTP of its own. 2026-11-04: "objects that I can bind
together, like the login app and the integrator app ... I can still link to an existing integrator
and get its functions tied into the chart app ... seamlessly like it is local": the login app
(liminal-login, LIMINAL_LOGIN_URL: 8795) is bound here too, its page and API under /login/, its tray
drawn at the foot of the rail (`/api/trays`), started for this front when nothing answers.

`liminal-charts gui` runs this: a local server (127.0.0.1) serving the chart page and its scripts, with
every /api/ request of its pages passed on to the integrator's data API at its port, and every
/login/ request to the login app (liminal_libs.apps.binding: the answers and the cookies back). No
database is opened here, and no pipe: the integrator holds the fetcher while a page is in touch. When
nothing answers at an app's port, the front starts the app's server for itself (`serve --no-browser
--until-quiet`: no window, outliving this process; it ends again once no page has been in touch for
LIMINAL_QUIET_SECONDS), unless the integrator's service keeps it there from the user's logon (service.py).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from liminal_libs import config
from liminal_libs.apps import Binding, Scope, forward, start_app, wait_for
from liminal_libs.apps.login import DEFAULT_URL as LOGIN_URL
from liminal_libs.apps.login import URL_VAR as LOGIN_URL_VAR
from liminal_libs.logs import log_to
from . import web
from .web import WRITE_HEADER

INTEGRATOR_PORT = 8785  # the integrator's page and data API (LIMINAL_FETCHER_PORT; none: no page)
START_SECONDS = 30.0  # how long the front waits for an app it started to answer
BOUND = ("login",)  # the apps bound to this front besides the integrator, each served under /<name>/


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


def login_url() -> str | None:
    """Where the login app's page and API are (LIMINAL_LOGIN_URL, http://127.0.0.1:8795). None: no
    login app on this page (none, off)."""
    raw = (config.setting(LOGIN_URL_VAR) or "").strip()
    if raw.lower() in ("none", "off"):
        return None
    return (raw or LOGIN_URL).rstrip("/")


def start_integrator(db_path: Path, port: int, log=print) -> bool:
    """Start the integrator's server for this front (`liminal-integrator serve --no-browser --port
    PORT --until-quiet`): no window, outliving this process, on this database (LIMINAL_DB in its
    environment); it ends again once no page has been in touch for a while. False: the integrator is
    not installed (liminal-integrator: the app's `integrator` extra)."""
    if not start_app("liminal_integrator", port, {"LIMINAL_DB": str(db_path)}, log):
        log("the integrator is not installed (liminal-integrator, the app's `integrator` extra): the app has no data to show")
        return False
    return True


def start_login(port: int, log=print) -> bool:
    """Start the login app's server for this front (`liminal-login serve --no-browser --port PORT
    --until-quiet`, for the charts app: its users' namespace, LIMINAL_LOGIN_FOR). False: the login
    app is not installed (liminal-login)."""
    env = {"LIMINAL_LOGIN_FOR": "charts"}
    scope = Scope.find()  # the login's own file, read the same way by every app (liminal-login.cfg): what it is tied to
    if scope is not None and scope.path is not None:
        env["LIMINAL_LOGIN_CFG"] = str(scope.path)
    if not start_app("liminal_login", port, env, log):
        log("the login app is not installed (liminal-login): no sign-in on this page")
        return False
    return True


class Front:
    """The front's way to the integrator's data API and to the apps bound to it: at their ports,
    each started for it when nothing answers there."""

    def __init__(self, db_path: Path, log=print, name: str = "the app"):
        self.db_path = Path(db_path)
        self.log = log
        self.name = name
        self._lock = threading.Lock()
        self._port: int | None = None  # the integrator's port answered at last
        self._login: str | None = None  # the login app's URL answered at last

    @property
    def connected(self) -> bool:
        return self._port is not None and web.listening(self._port)

    def integrator_port(self) -> int | None:
        """The integrator's port, answering: the one set, or the integrator started for it and
        waited for (START_SECONDS). None: no page (LIMINAL_FETCHER_PORT), or none could be started."""
        port = integrator_port()
        if port is None:
            self.log(f"the integrator has no page (LIMINAL_FETCHER_PORT): {self.name} cannot reach its data")
            return None
        if web.listening(port):
            if self._port != port:
                self._port = port
                self.log(f"{self.name} is connected to the integrator at http://127.0.0.1:{port}/")
            return port
        with self._lock:
            if web.listening(port):
                return port
            self._port = None
            if not start_integrator(self.db_path, port, self.log):
                return None
            if wait_for(port, START_SECONDS):
                self._port = port
                self.log(f"{self.name} is connected to the integrator at http://127.0.0.1:{port}/")
                return port
        self.log(f"the integrator started for {self.name} never answered at port {port}; see logs/liminal.log")
        return None

    def login(self) -> Binding | None:
        """The login app, answering: at its URL (LIMINAL_LOGIN_URL), or started for this front and
        waited for. None: no login app on this page (none, off), or none could be started."""
        url = login_url()
        if url is None:
            return None
        bound = Binding("login", url, "liminal_login")
        if bound.reachable():
            if self._login != url:
                self._login = url
                self.log(f"{self.name} is connected to the login app at {url}/")
            return bound
        with self._lock:
            if bound.reachable():
                return bound
            self._login = None
            if not start_login(bound.port, self.log):
                return None
            if wait_for(bound.port, START_SECONDS):
                self._login = url
                self.log(f"{self.name} is connected to the login app at {url}/")
                return bound
        self.log(f"the login app started for {self.name} never answered at port {bound.port}; see logs/login.log")
        return None

    def bound(self, name: str) -> Binding | None:
        """A bound app by name, answering (started if need be), or None."""
        return self.login() if name == "login" else None

    def signed_in(self, cookies: str | None) -> bool | None:
        """Whether the browser sending `cookies` is signed in to the login app (asked with those
        cookies, by this front); None when there is no login app to ask (none bound, none answering):
        the page then opens as before, on its profile alone."""
        bound = self.login()
        if bound is None:
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {"Accept": "application/json"}
        if cookies:
            headers["Cookie"] = cookies
        try:
            with opener.open(urllib.request.Request(bound.url + "/api/status", headers=headers), timeout=5) as resp:
                return bool(json.loads(resp.read()).get("signedIn"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None

    def trays(self) -> list[dict]:
        """The trays of the apps bound to this front, for the foot of the rail (the user, 2026-11-04:
        "Each divider represents a separate tray. From a separate object like the login")."""
        out = []
        for name in BOUND:
            bound = self.bound(name)
            tray = bound.tray() if bound is not None else None
            if tray:
                out.append(tray)
        return out


def make_front_handler(front: Front):
    """The front's requests: the page's files from here, /api/ passed on to the integrator, /<app>/
    to the app bound under that name. The page is the chart's (a profile's: the login page first
    without one)."""

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

        @staticmethod
        def _bound_of(path: str) -> str | None:
            """The bound app a path belongs to (/login, /login/...), or None."""
            for name in BOUND:
                if path == f"/{name}" or path.startswith(f"/{name}/"):
                    return name
            return None

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
            if path.startswith("/api/"):
                if self._from_this_app():
                    self._proxy("GET")
                return
            bound = self._bound_of(path)
            if bound is not None:
                if self._from_this_app():
                    self._bound("GET", bound)
                return
            self._static(path)

        def _write(self, method: str) -> None:
            if not self._local() or not self._from_this_app():
                return
            if self.headers.get(WRITE_HEADER) != "1":
                return self._send(HTTPStatus.FORBIDDEN, b"missing request header", "text/plain")
            if urlsplit(self.path).path == web.UI_PATH:
                return self._ui(method)
            bound = self._bound_of(urlsplit(self.path).path)
            if bound is not None:
                return self._bound(method, bound)
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
                return self._json({"error": f"the integrator is not running (none answers, none could be started): "
                                            f"{front.name} has no data to show; see logs/liminal.log"},
                                  HTTPStatus.SERVICE_UNAVAILABLE)
            forward(self, method, f"http://127.0.0.1:{port}", self.path, drop_cookies=("liminal_login_",),
                    unavailable="the integrator does not answer; ask again shortly")  # the login's session stays here

        def _bound(self, method: str, name: str) -> None:
            """The request passed on to the app bound under /<name>/, its answer and its cookies
            (their path narrowed to the prefix) back."""
            bound = front.bound(name)
            if bound is None:
                self._drain()
                return self._json({"error": f"the {name} app is not running (none answers, none could be started): "
                                            f"see logs/{name}.log"}, HTTPStatus.SERVICE_UNAVAILABLE)
            path = self.path[len(f"/{name}"):]
            if not path or path.startswith("?"):
                path = "/" + path
            # the bound app's cookies keep their own path (the login's session at /, so the main page's
            # request carries it and the front can ask the login app who is signed in)
            forward(self, method, bound.url, path, unavailable=f"the {name} app does not answer; ask again shortly")

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            # the main page is the sign-in (the user, 2026-11-06: "i need a login or sign up on the main
            # page"): a browser not signed in to the login app gets login.html, which holds the login
            # app's sign-in and sign-up; so does one signed in as no profile yet (the page makes the
            # profile for the user, then opens the app). Without a login app, the profile alone, as before.
            if rel == "index.html":
                if web.profile_cookie(self) is None or front.signed_in(self.headers.get("Cookie")) is False:
                    rel = "login.html"
            web.static_file(self, rel)

    return Handler


def serve_front(db_path: Path, port: int = 8765, open_browser: bool = True, log=None,
                stop: threading.Event | None = None) -> None:
    """The chart app: its page here, its data through the integrator (started for it when nothing
    answers at its port). On `port`, or a free one when it is taken; until Ctrl+C or `stop` is set."""
    log = log or log_to(db_path)
    front = Front(db_path, log)
    stop = stop or threading.Event()
    if port and web.listening(port):  # a bind alone cannot tell on Windows (web.listening)
        log(f"front: port {port} is taken (another server answers there): a free one instead")
        port = 0
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_front_handler(front))
    except OSError:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))  # port taken: pick a free one
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
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
