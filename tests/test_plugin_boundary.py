"""Every external data provider is in a plugin structure (the user, 2026-10-31): the app's own code
names no provider's host. What it sends anywhere goes through a plugin (sources/plugins/), which
declares its hosts, keys and limits; the app keeps only links people follow (never fetched)."""
from __future__ import annotations

import re
from pathlib import Path

import liminal_libs

# the app's own package (liminal-charts), and the library's (liminal-libs): the same rules hold in both
ROOTS = {"liminal_charts": Path(__file__).resolve().parents[1] / "src" / "liminal_charts",
         "liminal_libs": Path(liminal_libs.__file__).resolve().parent}
URL = re.compile(r"\b(?:https?|wss?)://([A-Za-z0-9.-]+)")

# links shown to people in the Federal view, never fetched (gov.LINKS, gov.RULES, the FOMC calendar)
FOR_PEOPLE = {
    ("gov.py", "www.finra.org"), ("gov.py", "www.sec.gov"), ("gov.py", "www.federalreserve.gov"),
    ("gov.py", "www.bls.gov"), ("gov.py", "www.ecfr.gov"),
    ("gui/server.py", "127.0.0.1"), ("gui/front.py", "127.0.0.1"),  # this app's own addresses
    ("apps/app.py", "127.0.0.1"), ("apps/login.py", "127.0.0.1"),  # the apps' frame and the login client (liminal-libs 1.7.0)
    ("machines/admin.py", "127.0.0.1"),  # a boot service's own page on this machine (liminal-libs 1.47.0)
}


def sources() -> list[tuple[str, str, Path]]:
    """Every module of the app and the library: (package, path within it, file)."""
    return [(pkg, path.relative_to(root).as_posix(), path) for pkg, root in ROOTS.items() for path in root.rglob("*.py")]


def hosts_named() -> set[tuple[str, str]]:
    out = set()
    for _, rel, path in sources():
        if rel.startswith("sources/plugins/"):
            continue
        out |= {(rel, h.lower().rstrip(".")) for h in URL.findall(path.read_text(encoding="utf-8"))}
    return out


def test_no_providers_host_is_named_outside_the_plugins():
    found = hosts_named()
    assert found - FOR_PEOPLE == set(), "a provider's host outside sources/plugins/: move it into its plugin"


def test_the_datasets_and_the_live_streams_hosts_are_their_plugins():
    found = {h for _, h in hosts_named()}
    for host in ("mba.tuck.dartmouth.edu", "www.cftc.gov", "raw.githubusercontent.com",  # datasets (API 1.4)
                 "ws-feed.exchange.coinbase.com", "ws.bitstamp.net", "stream.data.alpaca.markets",  # streams (1.5)
                 "api.tiingo.com",
                 "api.massive.com", "data.alpaca.markets", "api.alpaca.markets",  # the vendors' clients (1.6)
                 "www.federalregister.gov", "markets.newyorkfed.org"):  # the agencies' parsers (1.7)
        assert host not in found, host


def _imports(path: Path) -> set[str]:
    """The modules a file imports, relative imports resolved (liminal.sources.http ...)."""
    import ast

    root = next(r for r in ROOTS.values() if r in path.parents)
    rel = Path(root.name) / path.relative_to(root).with_suffix("")  # the package's name, then the module's path
    parts = list(rel.parts)
    package = parts[:-1] if parts[-1] != "__init__" else parts[:-1]
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = package[:len(package) - (node.level - 1)] if node.level else []
            mod = [*base, *(node.module.split(".") if node.module else [])]
            out |= {".".join(mod)} | {".".join([*mod, a.name]) for a in node.names}
    return out


def _plugin_of(module: str) -> str | None:
    bits = module.split(".")
    return bits[3] if bits[0] in ROOTS and bits[1:3] == ["sources", "plugins"] and len(bits) > 3 else None


def test_plugins_are_self_contained():
    """Each plugin keeps to itself (memory: plugins move to their own repositories): it imports no
    other plugin, and the app imports none by name (it reads them from the registry)."""
    for pkg, rel, path in sources():
        mine = _plugin_of(pkg + "." + rel[:-3].replace("/", "."))
        if rel.startswith("sources/plugins/") and rel != "sources/plugins/__init__.py":
            others = {_plugin_of(m) for m in _imports(path)} - {None, mine}
            assert not others, f"{rel} imports another plugin: {others}"
        elif not rel.startswith("sources/plugins/"):
            named = {m for m in _imports(path) if _plugin_of(m)}
            assert not named, f"{rel} imports a plugin by name: {named}"


def test_a_plugins_own_parser_reads_its_feed_without_the_app_changing():
    """A new kind of source is its plugin's (Feed.parse, API 1.7): the reader stores what the parser
    gives, items and rates, as for the Federal Register's and the New York Fed's."""
    from liminal_libs.sources.publisher import Feed, Read

    from .test_publications import NOW, Resp, Site, _mark, pub, reader

    def parse(answer, feed):
        lines = answer.content.decode().splitlines()  # a format the app has never seen: one item a line
        return Read(items=[{"id": t, "agency": feed.agency, "title": t, "url": None, "published": None,
                            "kind": feed.item_kind} for t in lines],
                    rates=[{"series": "XR", "day": "2026-10-23", "value": 1.5}])

    p = pub((Feed("t-lines", "T", "Lines", "https://news.example.gov/lines.json", "lines", every_minutes=60,
                  parse=parse, item_kind="notice"),))
    site = Site({"https://news.example.gov/lines.json": Resp(text="One\nTwo", headers={"Content-Type": "application/json"})})
    r = reader(site)
    _mark(r, p)
    r.step(NOW, publishers={"t": p})
    assert sorted((i["title"], i["kind"]) for i in r.gdb.items()) == [("One", "notice"), ("Two", "notice")]
    assert r.gdb.rate("XR")["value"] == 1.5 and r.gdb.fetched()["t-lines"]["message"] == "2 new"
