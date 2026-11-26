"""The charts app as an app of the frame (liminal_libs.apps.App; the user, 2026-11-04: every app has one
master icon, the charts app's the symbol icon, the disk at the top of the rail; 2026-11-06: "the app itself
(either chart or integrator) has plugin ABC as well"; "the plugin ABC will enumerate attached plugins and
their API"): what holds the plugins the charts app runs with and its login, the one authorization
interface they read their keys through (`self.login.key(self.name)`). The page and the data API are still
gui/server.py's (ChartApp) and the front gui/front.py's; the App base takes the server over at 0.3.0.
Alone (gui --alone) the charts app holds every plugin the registry loaded, as the super app; behind the
front, the integrator holds the stock-data plugins in its own process (liminal_integrator.app).

0.2.8 (Monday list item 30, step 5; the user, 2026-11-24: "inheritance bro"; 2026-11-25: the instance forms move to the
platform's Databases section, the platform the only writer of instance settings): ChartsApp inherits the library's
platform core (liminal_libs.apps.platform.Platform, liminal-libs 1.90.0), not the whole liminal-platform app. It needs
the database plugins (PLATFORM_NEEDS) and is not the writer: its Databases routes read (GET /api/databases/plugins, a
test through POST /api/databases/instances) and a change is refused, "change this on the platform's Databases
section" (409). Machines and AI stay the running platform's, linked at /platform/ by the front."""
from __future__ import annotations

from liminal_libs.apps import EnvironmentLogin, Login, attach_registry
from liminal_libs.apps.platform import Platform


class ChartsApp(Platform):
    NAME = "charts"
    TITLE = "Liminal Charts"
    ICON = "disk"  # the symbol's disk at the top of the rail (icons.js)
    PORT = 8765
    PORT_VAR = "LIMINAL_CHARTS_PORT"
    # the login the charts app inherits (the user, 2026-11-14: "the platform and integrator inherit the login function,
    # they are separate login instances, class vs object"): an object of the login class the front holds (gui/front.py),
    # at the front's port, its users the charts app's; LIMINAL_CHARTS_LOGIN "none" or "off" turns it off, and so does
    # LIMINAL_LOGIN_URL's, as when the login was an app the front bound at its address
    LOGIN, LOGIN_VAR = True, "LIMINAL_CHARTS_LOGIN"
    LOGIN_URL_VAR = "LIMINAL_LOGIN_URL"
    # the platform part it inherits (0.2.8): the database plugins, read here; never the writer of their instance settings
    PLATFORM_NEEDS = ("database",)
    PLATFORM_WRITER = False
    # its own Internal API (0.2.10, Monday list item 17; liminal-libs 1.63.0's App, GET /api/app/apis): its routes as
    # it is served (routes()), the apps it links and a few words of its own
    fronting = False  # set by the front that holds it (gui/front.py): its routes are then the front's
    LINKED = (
        ("integrator", "linked, reached at its own port on this machine and never started from here: every request of "
                       "the charts' pages but the front's own is passed on to its data API", "LIMINAL_FETCHER_PORT",
         "/api/"),
        ("platform", "linked, reached at its own address, on this machine or another, and never started from here: its "
                     "pages and its API under /platform/, only its own cookies passed on, its tray at the rail's foot",
         "LIMINAL_PLATFORM_URL", "/platform/"),
    )
    INTERNAL_NOTES = (
        "Behind the front (liminal-charts gui), the charts' pages ask the integrator it links for their data: the "
        "integrator holds the stock-data plugins in its own process, so the charts app here attaches none, and the data "
        "API's routes (gui/server.py) are listed on the integrator's Internal API, where they are answered.",
        "The login is an object of the login class held here, the charts app its superclass: its page and its API under "
        "/login/ at the charts' own port, no port of its own. The plugins read their keys through it.",
        "Alone (liminal-charts gui --alone), the charts app holds every plugin the registry loaded and answers the data "
        "API itself, in this one process: its routes are then the data API's.",
        "A plugin's Internal API in the Plugins view is asked only while its tab is shown (GET "
        "/api/app/apis?plugin=NAME, &of=publisher for a publisher): the data API answers it for every plugin object "
        "behind that entry, read from the objects and nothing of them called.",
    )

    def __init__(self, log=print, login: Login | None = None):
        # The user environment variables stand in for a login session until the app holds one of its own
        # (the front asks the login app of the charts namespace who is signed in; a key furnished per
        # signed-in user is the design to come).
        super().__init__(log=log, login=login if login is not None else EnvironmentLogin())

    # what the Plugins view's code (gui/plugins.py; a publisher's cards in gui/server.py ChartApp.publishers) calls and
    # reads on a plugin of each kind, held to those sources by tests/test_api_views.py. The fetching is the library's
    # (its collector and sourcing): not listed here
    CALLS = {
        "feed": {
            "report": "its report for the view: its plan's limits, capabilities, streams and notes (GET /api/plugins)",
            "keys_set": "whether its keys are set (never their values), for its state and its Setup",
            "plan_state": "each plan variable with where it came from, for its Plan",
            "api_facts": "what it learned of the APIs it attaches to, for its External API (liminal_libs.apis.facts_of)",
        },
        "publisher": {
            "api_facts": "what it learned of the APIs it attaches to, for its External API (liminal_libs.apis.facts_of)",
        },
        "assistant": {
            "own": "its settings here: its plan, model, effort, caps and key",
            "plan_of": "the plan of its settings, as its vendor's table writes it",
            "keys_set": "whether its key is set (never its value)",
            "served": "whether its key reaches the vendor's API on its plan",
            "plan_state": "each plan variable with where it came from, for its Plan",
            "free_note": "what its vendor gives free, in words",
            "table_rows": "its vendor's plans and models as written in, and the day they were verified",
            "api_facts": "what it learned of the APIs it attaches to, for its External API (liminal_libs.apis.facts_of)",
        },
        "auditor": {
            "api_facts": "what it learned of the APIs it attaches to, for its External API (liminal_libs.apis.facts_of)",
        },
    }
    ATTRIBUTES = {  # read, never called
        "feed": ("name", "title", "about", "origin", "version", "api", "apis", "signup", "keys", "archival", "kind",
                 "assets", "keyless", "limits", "has_scan", "plan_note", "options", "plan_choices"),
        "publisher": ("name", "apis"),
        "assistant": ("name", "title", "about", "origin", "version", "api", "apis", "signup", "keys", "vendor", "caps",
                      "default_model", "free_plan", "has_scan", "plan_note", "options", "plan_choices"),
        "auditor": ("name", "title", "about", "origin", "version", "api", "apis", "options", "plan_choices"),
    }

    @property
    def apis(self) -> tuple:
        """What the charts' own code associates with, for their own External API (apis.py): imported when first
        asked, so the charts' start loads nothing more for it."""
        from .apis import APIS

        return APIS

    def routes(self) -> tuple:
        """Its own routes as its Internal API lists them (gui/routes.py): behind the front, the front's (what it answers
        itself, then each of the frame's routes it passes on to the integrator); alone, the data API's, the platform
        core's two Databases routes and the frame's writes the data API does not answer."""
        from .gui import routes

        if self.fronting:
            return (*routes.FRONT_ROUTES, *routes.passed_on())
        return (*routes.DATA_ROUTES, *routes.PLATFORM_CORE, *routes.NOT_ANSWERED)

    def attach_plugins(self, only: str | None = None) -> list[str]:
        """Every plugin the registry loaded (one system with `only`: "integrator", "platform"), attached
        here with this app's login; the names attached (a plugin another app holds is left alone)."""
        return attach_registry(self, only=only)
