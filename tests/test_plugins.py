"""Data providers are plugins (sources/registry.py): the built-in ones keep every fact the app had
about them, and a provider from an installed package is found, built, keyed and listed like them."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from liminal_charts import cli
from liminal_libs import composite, config
from liminal_libs.config import Tier, load_settings
from liminal_libs.sources import (build_source, feed_profiles, is_archive, markets, provider, providers,
                                     rate_window, registry, supports)
from liminal_libs.sources.registry import KeyVar, Provider
from liminal_libs.symbols import resolve

from .conftest import FakeSource

BUILT_IN: list[str] = []  # none left in sources/plugins/ (2026-11-01): every provider is a package
# Released on their own (pyproject.toml: the `plugins` extra) and installed beside the app: the suite
# sees them as the user's app does (tests/conftest.py), each with its distribution's version.
PACKAGED = {  # name: (its distribution's version, the contract it needs, its entry point's value)
    # every plugin a class on plugin API 2.0 at 0.2.0 (the user, 2026-11-03: "after the rewrite, lets go to version 0.2.0"),
    # 0.3.0 on plugin API 2.2 since 2026-11-11: each says what it attaches to (the user: "each plugin should reflect the
    # the API its attached to, and present links that provide documentation")
    "bitstamp": ("0.3.0", "2.2", "plugin_integrator_bitstamp:PLUGIN"), "coinbase": ("0.3.0", "2.2", "plugin_integrator_coinbase:PLUGIN"),
    "bitfinex": ("0.3.0", "2.2", "plugin_integrator_bitfinex:PLUGIN"),
    "cboe": ("0.3.0", "2.2", "plugin_integrator_cboe"), "cboefutures": ("0.3.0", "2.2", "plugin_integrator_cboe"),
    "tiingo": ("0.3.0", "2.2", "plugin_integrator_tiingo"), "tiingofx": ("0.3.0", "2.2", "plugin_integrator_tiingo"),
    "alpaca": ("0.3.0", "2.2", "plugin_integrator_alpaca"), "boats": ("0.3.0", "2.2", "plugin_integrator_alpaca"),
    "massive": ("0.3.0", "2.2", "plugin_integrator_massive"), "twse": ("0.3.0", "2.2", "plugin_integrator_twse"),
    "b3": ("0.3.0", "2.2", "plugin_integrator_b3"),
    "alphavantage": ("0.3.0", "2.2", "plugin_integrator_alphavantage"), "jquants": ("0.3.0", "2.2", "plugin_integrator_jquants"),
    "twelvedata": ("0.3.0", "2.2", "plugin_integrator_twelvedata")}
TIINGO_BOATS = "tiingoboats"  # Tiingo's Blue Ocean feed, live: its package's
IEX_FEED = "iex"  # Alpaca's IEX stream, live: its package's
FEEDS = ["massive", "alpaca", "boats", "bitstamp", "coinbase", "bitfinex", "cboe", "cboefutures", "tiingo", "tiingofx",
         "b3", "twse", "alphavantage", "jquants", "twelvedata"]  # all, in order
LISTED = [n for n in FEEDS if n not in ("boats", "tiingofx", "cboefutures")]  # the plugins listed: second feeds in their plugins


class AcmeSource(FakeSource):
    name = "acme"


ACME = Provider(
    name="acme", title="Acme Data", build=lambda settings, budget: AcmeSource(budget=budget),
    assets=frozenset({"stock"}), about="stock minute bars", keys=(KeyVar("ACME_API_KEY"),), signup="acme.example",
    plan_vars=("ACME_PLAN",), tier=Tier("acme-free", requests_per_minute=30, history_years=3), per_run=100,
    per_day=1000, limits="30 requests per minute", kind="exchange", symbols_per_month=None,
    carries=lambda spec: spec.ticker != "ZZZZ",
)


@pytest.fixture
def installed(monkeypatch):
    """Packages' entry points, as `importlib.metadata` would list them; the registry looks again."""
    def install(*entries):
        eps = [SimpleNamespace(name=name, value=value, load=load) for name, value, load in entries]
        monkeypatch.setattr(registry, "_entry_points", lambda: eps)
        return registry.reload()

    yield install
    registry._providers = None  # found again (built-ins only) once the entry points are restored


def test_the_feeds_load_in_their_order_without_errors():
    assert list(providers()) == FEEDS  # the built-ins and the packages together, in the plugins' order
    assert registry.errors() == []
    assert all(callable(p.build) for p in providers().values())
    # each from its package's entry point, or from its checkout in the workspace's plugin directory
    assert {n for n, p in providers().items() if p.origin != "built in"} == set(PACKAGED)
    for n, (_, _, e) in PACKAGED.items():
        assert providers()[n].origin in {f"package: plugin-integrator-{registry.plugin_of(n)} ({e})",
                                         f"plugins/plugin-integrator-{registry.plugin_of(n)} ({e})"}, n
    assert all(providers()[n].origin == "built in" for n in BUILT_IN)
    assert {n for n in FEEDS if is_archive(n)} == {"tiingo", "cboe"}


def test_each_plugin_keeps_its_plan_rate_caps_and_markets():
    s = load_settings(env={"MASSIVE_API_KEY": "k"}, env_file=None)
    assert rate_window("massive", s) == (5, 62.0)
    assert rate_window("alpaca", s) == (190, 60.0)
    assert rate_window("coinbase", s) == (8, 1.0)
    assert rate_window("tiingo", s) == (45, 3600.0)
    assert rate_window("bitfinex", s) == (20, 60.0)
    assert s.for_source("alpaca").tier.name == "alpaca-basic" and s.for_source("alpaca").max_requests_per_day == 30000
    assert s.for_source("massive").tier.name == "free" and s.tier.name == "none"  # the base: no feed's plan
    limited = load_settings(env={"CRYPTO_VENUE_DAYS": "30"}, env_file=None)
    assert limited.for_source("coinbase").tier.history_days == 30
    assert markets("massive", s)["stock"] == "sip" and markets("alpaca", s) == {"stock": "sip", "crypto": "kraken"}
    assert markets("tiingo", s) == {"stock": "sip"}


def test_what_each_built_in_serves_follows_its_keys():
    none = feed_profiles(load_settings(env={}, env_file=None))
    assert "massive" not in none  # not set up at all without its key
    assert none["alpaca"].assets == {"crypto"}  # crypto needs no key
    assert none["tiingo"].assets == frozenset()  # listed, serving nothing
    assert none["coinbase"].assets == {"crypto"}
    keyed = feed_profiles(load_settings(env={"MASSIVE_API_KEY": "k", "ALPACA_API_KEY_ID": "i",
                                             "ALPACA_API_SECRET_KEY": "s", "TIINGO_API_KEY": "t"}, env_file=None))
    assert keyed["alpaca"].assets == {"stock", "crypto"} and keyed["tiingo"].assets == {"stock"}
    assert "future" in keyed["massive"].assets
    assert supports("coinbase", resolve("BTCUSD")) and not supports("coinbase", resolve("AAPL"))


def test_composite_and_audits_read_the_plugins_facts():
    from liminal_libs import audit, initializer

    assert composite.reference("crypto") == "coinbase" and composite.reference("stock") is None
    assert composite.feed_kind("alpaca", "stock") == "consolidated"
    assert composite.feed_kind("alpaca", "crypto") == "exchange"
    assert composite.feed_kind("composite", "crypto") == "consolidated"
    assert audit.venue_note("alpaca", "crypto") == "one exchange's book (Kraken)" and audit.venue_note("tiingo", "stock") is None
    assert initializer.bulk_resolution("massive") == "second" and initializer.bulk_resolution("tiingo") == "auto"
    assert initializer.bulk_resolution("massive", "future") == "minute"  # one per asset class
    assert initializer.bulk_resolution("alpaca", "future") == "minute"  # one for all


def test_a_provider_from_an_installed_package_is_found_built_and_keyed(installed):
    found = installed(("acme", "acme_feed:PLUGIN", lambda: ACME))
    assert list(found) == BUILT_IN + ["acme"]  # after the built-ins (order 100)
    assert provider("acme").origin == "package: acme_feed:PLUGIN"
    assert "ACME_API_KEY" in config.saved_names() and "ACME_PLAN" in config.saved_names()

    without = load_settings(env={}, env_file=None)
    assert feed_profiles(without)["acme"].assets == frozenset()  # its key is not set
    s = load_settings(env={"ACME_API_KEY": "secret-acme", "ACME_PLAN": "pro"}, env_file=None)
    assert s.env == {"ACME_API_KEY": "secret-acme", "ACME_PLAN": "pro"} and "secret-acme" not in repr(s)
    profile = feed_profiles(s)["acme"]
    assert profile.assets == {"stock"} and profile.venues == {"stock": "acme"}
    view = s.for_source("acme")
    assert (view.tier.name, view.max_requests_per_run, view.max_requests_per_day) == ("acme-free", 100, 1000)
    assert rate_window("acme", s) == (30, 60.0)
    source = build_source("acme", s, request_budget=7)
    assert isinstance(source, AcmeSource) and source.budget == 7
    assert supports("acme", resolve("AAPL")) and not supports("acme", resolve("ZZZZ"))
    assert not supports("acme", resolve("BTCUSD"))  # not its asset class
    assert composite.feed_kind("acme", "stock") == "exchange"
    menu = {r["name"]: r for r in registry.menu(s)}["acme"]
    assert menu["keysSet"] is True and menu["keys"] == [{"name": "ACME_API_KEY", "label": "API key", "aliases": [],
                                                                  "secret": True, "hint": ""}]
    assert "secret-acme" not in json.dumps(registry.menu(s))


def test_a_plugin_that_fails_to_load_is_left_out_and_reported(installed):
    def broken():
        raise ImportError("no module named acme_feed")

    found = installed(("broken", "acme_feed:PLUGIN", broken), ("empty", "empty_mod", lambda: SimpleNamespace()),
                      ("acme", "acme_feed:PLUGIN", lambda: ACME),
                      ("twice", "other:PLUGIN", lambda: replace(ACME, title="Not Acme")))
    assert list(found) == BUILT_IN + ["acme"]  # the app goes on with the others; the first of a name is kept
    assert provider("acme").title == "Acme Data"
    why = dict(registry.errors())
    assert "ImportError" in why["broken (acme_feed:PLUGIN)"]
    assert "defines no PLUGIN" in why["empty (empty_mod)"]
    assert any("a second provider named 'acme'" in w for w in why.values())


def test_of_two_plugins_of_one_name_the_first_in_order_is_kept(installed):
    from liminal_libs.sources import publishers
    from liminal_libs.sources.publisher import Policy, Publisher

    # every plugin is a package: of two of one name, the first in order is kept, the other reported
    owner_feed = replace(ACME, name="own", title="Owner", order=-6)
    impostor = replace(ACME, name="own", title="Not Owner", order=-5)
    owner = Publisher("owner", "Owner", "", (), Policy(), order=-6)
    rival = Publisher("owner", "Not Owner", "", (), Policy(), order=-5)
    found = installed(("first", "owner:PLUGIN", lambda: owner_feed), ("second", "impostor:PLUGIN", lambda: impostor),
                      ("own", "owner:PUBLISHER", lambda: owner), ("pub", "rival:PUBLISHER", lambda: rival))
    assert list(found) == ["own"] and provider("own").title == "Owner"
    assert publishers()["owner"].title == "Owner"
    why = dict(registry.errors())
    assert "a second provider named 'own': the first kept" in why["package: impostor:PLUGIN"]
    assert "a second publisher named 'owner': the first kept" in why["package: rival:PUBLISHER"]


def test_a_module_dropped_into_sources_plugins_still_loads_and_keeps_its_name(tmp_path, monkeypatch, installed):
    """The built-ins' way stays open: a module in sources/plugins/ defining PLUGIN is found on the next
    start with origin "built in" and, of two plugins of one name, kept over any package whatever the order."""
    import sys

    from liminal_libs.sources import plugins as dropins

    (tmp_path / "dropin.py").write_text("""from liminal_libs.sources.registry import Provider
PLUGIN = Provider(name='dropin', title='Dropped in', build=lambda s, b: None, assets=frozenset({'stock'}), order=500)
""", encoding="utf-8")
    monkeypatch.setattr(dropins, "__path__", [*dropins.__path__, str(tmp_path)])
    rival = replace(ACME, name="dropin", title="Packaged rival", order=-9)  # listed first: the module is kept all the same
    try:
        found = installed(("rival", "rival:PLUGIN", lambda: rival))
        assert list(found) == ["dropin"] and provider("dropin").title == "Dropped in"
        assert provider("dropin").origin == "built in"
        assert "a second provider named 'dropin': the first kept (built in)" in dict(registry.errors())["package: rival:PLUGIN"]
    finally:
        sys.modules.pop("liminal_libs.sources.plugins.dropin", None)


def test_a_module_may_give_several_providers(installed):
    other = Provider(name="acme2", title="Acme Two", build=lambda s, b: None, assets=frozenset({"crypto"}), order=5)
    found = installed(("acme", "acme_feed", lambda: SimpleNamespace(PLUGINS=[ACME, other])))
    assert list(found) == ["acme2", "acme"]  # in their order (5, then Acme's 100): the fakes are the only entry points


# -- the plugin contract's version (registry.PLUGIN_API for a class, RECORD_API for a record) ---------
def test_a_contract_version_takes_its_own_major_and_no_newer_minor():
    assert registry.api_version("1.2") == (1, 2) and registry.api_version("1") == (1, 0)
    assert registry.api_refusal("1.0", "1.0") is None and registry.api_refusal("1.0", "1.3") is None
    assert registry.api_refusal("1.3", "1.3") is None and registry.api_refusal(" 1 ", "1.3") is None
    assert "needs a newer app" in registry.api_refusal("1.4", "1.3")  # a minor newer than the app's
    assert "needs a newer app" in registry.api_refusal("2.0", "1.3")
    assert "needs a release for 2.x" in registry.api_refusal("1.9", "2.0")
    for bad in ("", "one", "1.0.0", "1.x", "-1.0"):
        assert "not a contract version" in registry.api_refusal(bad, "1.0")


def test_every_plugin_is_versioned_for_this_apps_contract():
    from liminal_libs.sources import publishers

    plugins_ = [*providers().values(), *publishers().values()]
    # the feeds; Federal with its six agencies, Massive's headlines, Ken French's and Datahub's datasets
    assert len(plugins_) == len(FEEDS) + 10
    # each built-in's release and the contract it needs: every feed plugin holds its client (1.6:
    # sources.http, sources.archive), Alpaca's, Tiingo's, Coinbase's and Bitstamp's their live feeds
    # too; Federal declares the SEC's GOV_CONTACT and the CFTC's dataset (1.4), as the dataset
    # libraries their datasets; Federal's feeds their parsers (1.7); Massive's headlines are released
    # with Massive, which declares where they are (1.8: the main file's news)
    want = {"massive-news": ("0.3.0", "2.2"),  # Massive's headlines: released with Massive's package
            **{n: (v, a) for n, (v, a, _) in PACKAGED.items()},  # the packages: their distributions' versions
            **dict.fromkeys(("federal", "fed", "nyfed", "sec", "bls", "fedreg", "cftc"), ("0.3.0", "2.2")),  # its package's
            "french": ("0.3.0", "2.2"), "datahub": ("0.3.0", "2.2")}  # the dataset libraries: packages too
    assert {p.name: (p.version, p.api) for p in plugins_} == want
    assert all(registry.api_refusal(p.api, record=p.is_record) is None for p in plugins_)
    assert registry.PLUGIN_API == "2.2" and registry.RECORD_API == "1.12"  # the classes' contract, the records'


def test_a_plugins_parts_are_released_with_it():
    """A part of a plugin is no plugin of its own (the user, 2026-10-31: "massive headlines should not
    be separate from massive same thing with federal agencies and federal"): each built-in part, a
    feed (Alpaca's Blue Ocean, Tiingo's forex) or a publisher (Massive's headlines, Federal's
    agencies), has its plugin's version and contract."""
    from liminal_libs.sources import publishers

    feeds, pubs = providers(), publishers()
    release = {n: (p.version, p.api) for n, p in [*feeds.items(), *pubs.items()]}
    parts = {n: registry.plugin_of(n) for n in feeds if registry.plugin_of(n) != n}
    parts |= {n: registry.publisher_of(n) for n in pubs if registry.publisher_of(n) != n}
    assert parts == {"boats": "alpaca", "tiingofx": "tiingo", "cboefutures": "cboe", "massive-news": "massive",
                     **dict.fromkeys(("fed", "nyfed", "sec", "bls", "fedreg", "cftc"), "federal")}
    assert {n: release[n] for n in parts} == {n: release[owner] for n, owner in parts.items()}


def test_a_plugin_for_another_major_is_left_out_and_reported_and_a_matching_one_loads(installed):
    from liminal_libs.sources import publishers
    from liminal_libs.sources.publisher import Policy, Publisher

    def acme(name, api, **kw):
        return replace(ACME, name=name, title=name, keys=(), plan_vars=(), api=api, **kw)

    old_news = Publisher(name="old-news", title="Old news", about="", feeds=(), policy=Policy(), api="0.3")
    major, minor = registry.api_version(registry.RECORD_API)
    newer = f"{major}.{minor + 1}"  # a minor of the record form this app does not offer yet

    class AcmeClass(registry.FeedPlugin):  # the class form (API 2.0): a class, or an instance
        name, title, assets = "acme-class", "Acme class", frozenset({"stock"})

        def build(self, settings, budget):
            return AcmeSource(budget=budget)

    class AcmeEarly(AcmeClass):
        name, api = "acme-early", "1.12"  # a class claiming the record form's contract

    class AcmeLater(AcmeClass):
        name, api = "acme-later", "2.2"  # a class written for a newer minor than this app's
    found = installed(("next", "acme_next:PLUGIN", lambda: acme("acme-next", "2.0")),  # a record claiming the classes'
                      ("old", "acme_old:PLUGIN", lambda: acme("acme-old", "0.9")),
                      ("newer", "acme_newer:PLUGIN", lambda: acme("acme-newer", newer)),
                      ("odd", "acme_odd:PLUGIN", lambda: acme("acme-odd", "latest")),
                      ("same", "acme_same:PLUGIN", lambda: acme("acme-same", "1.0", version="2.4.1")),
                      ("plain", "acme_plain:PLUGIN", lambda: acme("acme-plain", "1")),
                      ("news", "old_news:PUBLISHER", lambda: old_news),
                      ("klass", "acme_class:PLUGIN", lambda: AcmeClass),
                      ("early", "acme_early:PLUGIN", lambda: AcmeEarly()),
                      ("later", "acme_later:PLUGIN", lambda: AcmeLater()))
    assert list(found) == BUILT_IN + ["acme-class", "acme-plain", "acme-same"]  # the app goes on with the others
    assert provider("acme-same").version == "2.4.1" and provider("acme-same").api == "1.0"
    assert isinstance(provider("acme-class"), AcmeClass) and provider("acme-class").api == "2.0"
    assert "old-news" not in publishers()
    why = dict(registry.errors())
    assert set(why) == {"next (acme_next:PLUGIN)", "old (acme_old:PLUGIN)", "newer (acme_newer:PLUGIN)",
                        "odd (acme_odd:PLUGIN)", "news (old_news:PUBLISHER)", "early (acme_early:PLUGIN)",
                        "later (acme_later:PLUGIN)"}
    assert why["next (acme_next:PLUGIN)"].startswith("acme-next: written for plugin API 2.0 as a record") \
        and "is a class (FeedPlugin, PublisherPlugin)" in why["next (acme_next:PLUGIN)"]
    assert why["old (acme_old:PLUGIN)"].startswith("acme-old: written for plugin API 0.9") \
        and "needs a release for 1.x" in why["old (acme_old:PLUGIN)"]
    assert f"newer than this app's {registry.RECORD_API} for records" in why["newer (acme_newer:PLUGIN)"]
    assert "not a contract version" in why["odd (acme_odd:PLUGIN)"]
    assert "old-news: written for plugin API 0.3" in why["news (old_news:PUBLISHER)"]
    assert why["early (acme_early:PLUGIN)"] == (f"acme-early: written for plugin API 1.12; this app offers "
                                                f"{registry.PLUGIN_API}: it needs a release for 2.x")
    assert f"newer than this app's {registry.PLUGIN_API}" in why["later (acme_later:PLUGIN)"]


def test_a_plugin_that_says_no_api_was_written_for_the_first_contract(installed, monkeypatch):
    before = Provider(name="acme-before", title="Acme", build=lambda s, b: None, assets=frozenset({"stock"}))
    assert before.api == "1.0"
    assert "acme-before" in installed(("before", "acme_before:PLUGIN", lambda: before))
    monkeypatch.setattr(registry, "RECORD_API", "2.0")  # an app of a later major for records: it is left out then
    assert "acme-before" not in registry.reload()
    why = dict(registry.errors())["before (acme_before:PLUGIN)"]
    assert why == "acme-before: written for plugin API 1.0; this app offers 2.0 for records: it needs a release for 2.x"


def test_every_installed_plugin_package_loads(monkeypatch):
    """The plugins released on their own (plugin-integrator-cboe ...), as installed beside the app
    (pip install -e ".[plugins]"): each loads, none left out, each with a version; their own tests
    are in their repositories. None installed: nothing to load."""
    from importlib.metadata import entry_points

    from liminal_libs.sources import publishers

    eps = list(entry_points(group=registry.ENTRY_POINTS))
    monkeypatch.setattr(registry, "_entry_points", lambda: eps)
    try:
        registry.reload()
        assert registry.errors() == []
        packaged = [p for p in (*providers().values(), *publishers().values()) if p.origin != "built in"]
        assert len(packaged) >= len(eps)  # an entry point may name a module of several
        assert all(p.origin.startswith(("package: ", "plugins/")) and p.version for p in packaged)
    finally:
        registry._providers = None


def test_a_packages_plugin_without_a_version_takes_its_distributions(monkeypatch):
    dist = SimpleNamespace(name="massive-acme", version="0.4.2")
    eps = [SimpleNamespace(name="acme", value="massive_acme:PLUGIN", load=lambda: ACME, dist=dist),
           SimpleNamespace(name="own", value="massive_own:PLUGIN", dist=dist,
                           load=lambda: replace(ACME, name="acme-own", version="9.9.9"))]
    monkeypatch.setattr(registry, "_entry_points", lambda: eps)
    try:
        registry.reload()
        assert provider("acme").version == "0.4.2"
        assert provider("acme").origin == "package: massive-acme (massive_acme:PLUGIN)"
        assert provider("acme-own").version == "9.9.9"  # its own release wins
        row = {r["name"]: r for r in registry.menu()}["acme"]
        assert (row["version"], row["api"], row["origin"]) == ("0.4.2", "1.0", "package: massive-acme (massive_acme:PLUGIN)")
    finally:
        registry._providers = None


def test_the_plugins_command_shows_each_plugins_version_and_origin(monkeypatch, capsys):
    monkeypatch.setattr(config, "read_env_file", lambda path: {})
    assert cli.main(["plugins", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["api"] == registry.PLUGIN_API and out["errors"] == []
    assert {(r["version"], r["api"], r["origin"]) for r in out["providers"]} == {
        *((v, a, f"package: plugin-integrator-{registry.plugin_of(n)} ({e})") for n, (v, a, e) in PACKAGED.items())}
    pubs = {r["name"]: r for r in out["publishers"]}
    # the agencies are Federal's parts; Massive's headlines are in Massive's row; the dataset libraries
    assert list(pubs) == ["federal", "french", "datahub"]
    assert [r["name"] for r in pubs["federal"]["parts"]] == ["fed", "nyfed", "sec", "bls", "fedreg", "cftc"]
    assert (pubs["federal"]["version"], pubs["federal"]["api"]) == ("0.3.0", "2.2")
    assert pubs["federal"]["parts"][-1]["datasets"] == ["cftc-cot-legacy"] and len(pubs["french"]["datasets"]) == 4
    assert [k["name"] for k in pubs["federal"]["keys"]] == ["GOV_CONTACT"]  # the SEC's, set for the plugin
    massive = next(r for r in out["providers"] if r["name"] == "massive")
    assert [(r["name"], r["version"], r["account"]) for r in massive["publishers"]] == [("massive-news", "0.3.0", "massive")]
    assert cli.main(["plugins"]) == 0
    text = capsys.readouterr().out
    assert text.startswith(f"Plugin API {registry.PLUGIN_API}")
    assert "[0.3.0 for plugin API 2.2, package: plugin-integrator-bitfinex (plugin_integrator_bitfinex:PLUGIN)]" in text
    assert any(line.startswith("federal") and "[0.3.0 for plugin API 2.2, package: plugin-integrator-federal (plugin_integrator_federal)]" in line
               and "you set: GOV_CONTACT" in line
               for line in text.splitlines())
    assert any(line.strip().startswith("and Federal Reserve (fed):") for line in text.splitlines())
    assert "[0.3.0 for plugin API 2.2, package: plugin-integrator-alpaca (plugin_integrator_alpaca)]" in text
    assert "[0.3.0 for plugin API 2.2, package: plugin-integrator-bitstamp (plugin_integrator_bitstamp:PLUGIN)]" in text
    assert any(line.strip().startswith("and Massive headlines (publisher massive-news):") for line in text.splitlines())
    assert not any(line.startswith("massive-news") for line in text.splitlines())


def test_the_plugins_view_shows_each_plugins_version_and_origin(tmp_path):
    from liminal_charts.gui.server import ChartApp
    from liminal_libs.storage import Store

    with Store(tmp_path / "m.db") as store:
        out = ChartApp(store, settings=load_settings(env={}, env_file=None)).plugins()
    assert out["api"] == registry.PLUGIN_API
    assert {(p["version"], p["api"], p["origin"]) for p in out["plugins"] if not p.get("ai")} == {  # the feeds (the AI assistants aside)
        *((v, a, f"package: plugin-integrator-{registry.plugin_of(n)} ({e})") for n, (v, a, e) in PACKAGED.items())}
    assert {(p["version"], p["api"]) for p in out["publishers"]} == {("0.3.0", "2.2")}


def test_unknown_providers_are_refused_by_name():
    with pytest.raises(ValueError, match="unknown source 'nope'; available: massive, alpaca"):
        build_source("nope", load_settings(env={}, env_file=None))


def test_the_plugins_command_lists_keys_set_never_their_values(monkeypatch, capsys):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret-massive")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.setattr(config, "read_env_file", lambda path: {})
    assert cli.main(["plugins", "--json"]) == 0
    out = capsys.readouterr().out
    assert "secret-massive" not in out
    rows = {r["name"]: r for r in json.loads(out)["providers"]}
    assert list(rows) == LISTED
    assert rows["massive"]["keysSet"] is True and rows["tiingo"]["keysSet"] is False
    assert rows["bitstamp"]["keysSet"] is None and rows["bitstamp"]["keys"] == []
    assert [k["name"] for k in rows["alpaca"]["keys"]] == ["ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"]
    assert rows["alpaca"]["keys"][0]["aliases"] == ["APCA_API_KEY_ID"] and rows["alpaca"]["keyless"] == ["crypto"]

    assert cli.main(["plugins"]) == 0
    text = capsys.readouterr().out
    assert "MASSIVE_API_KEY (set)" in text and "TIINGO_API_KEY (not set)" in text and "secret-massive" not in text


def test_the_providers_are_listed_even_with_a_wrong_setting(monkeypatch, capsys):
    monkeypatch.setenv("MASSIVE_TIER", "platinum")
    monkeypatch.setattr(config, "read_env_file", lambda path: {})
    assert cli.main(["plugins", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["providers"]
    assert [r["name"] for r in rows] == LISTED and "keysSet" not in rows[0]
    assert cli.main(["status"]) == 2  # every other command still stops at it


def test_the_fetcher_tabs_carry_each_plugins_key_hints(tmp_path):
    from liminal_charts.gui.server import ChartApp
    from liminal_libs.storage import Store

    with Store(tmp_path / "m.db") as store:
        app = ChartApp(store, settings=load_settings(env={"MASSIVE_API_KEY": "k"}, env_file=None))
        feeds = {f["name"]: f for f in app._feeds()}
    assert feeds["alpaca"]["keys"] is False and feeds["alpaca"]["keysChip"] == "stocks need keys"
    assert "crypto needs no key" in feeds["alpaca"]["keysNote"]
    assert feeds["tiingo"]["keysNote"] == "key not set (TIINGO_API_KEY)" and feeds["tiingo"]["keysChip"] == "no key"
    assert feeds["massive"]["keys"] is True and feeds["coinbase"]["keys"] is None
    assert feeds["tiingo"]["archive"] and feeds["coinbase"]["title"] == "Coinbase"


def test_plugin_tiers_keep_their_history_start():
    from liminal_libs.collector import history_floor

    tiingo = load_settings(env={}, env_file=None).for_source("tiingo").tier
    assert history_floor(tiingo, date(2026, 10, 27)) == date(1962, 1, 1)


# -- each feed's limits, verified by its capability scan ----------------------------------------------
def test_a_servers_rate_limit_headers_are_read_with_their_windows():
    assert registry.server_per_minute({"X-RateLimit-Limit": "200", "X-RateLimit-Remaining": "199"}) == 200
    assert registry.server_per_minute({"ratelimit-limit": "10", "RateLimit-Policy": "10;w=1"}) == 600
    assert registry.server_per_minute({"RateLimit-Limit": "100"}) == 100  # no window said: a minute
    assert registry.server_per_minute({}) is None and registry.server_per_minute({"X-RateLimit-Limit": "x"}) is None
    # an hour's limit is not cut to nothing, and every policy counts (this one raised before)
    assert registry.server_limits({"RateLimit-Limit": "50", "RateLimit-Policy": "50;w=3600"}) == [(50, 3600)]
    assert registry.server_per_minute({"RateLimit-Policy": "50;w=3600"}) == pytest.approx(0.833, abs=1e-3)
    assert registry.server_limits({"RateLimit-Policy": "10;w=1, 1000;w=3600"}) == [(10, 1), (1000, 3600)]
    assert registry.server_limits({"RateLimit-Policy": '"default";q=100;w=60'}) == [(100, 60)]


def test_the_window_kept_is_inside_every_server_limit_never_rounded_above_one():
    within = registry.within
    assert within(190, 60.0, [(200, 60)]) == (190, 60.0)  # Alpaca's: already inside
    assert within(8, 1.0, [(30, 60)]) == (1, 3.0)  # 8 a second against 30 a minute: one per 3 s, 20 a minute
    assert within(None, 60.0, [(100, 60)]) == (95, 60.0)  # a plan with no limit of its own takes the server's
    assert within(45, 3600.0, [(50, 3600)]) == (45, 3600.0)
    n, s = within(None, 60.0, [(10, 1), (1000, 3600)])
    assert n == 1 and n / s * 3600 <= 1000 * registry.SERVER_SHARE  # the hour's quota binds, a burst of one
    assert within(30, 60.0, []) == (30, 60.0)


def test_the_client_keeps_the_rate_limit_headers_its_server_sends():
    from plugin_integrator_massive.client import MassiveClient

    class Answer:
        status_code, headers = 200, {"X-RateLimit-Limit": "200", "X-RateLimit-Remaining": "17", "Server": "x"}

        def json(self):
            return {}

    client = MassiveClient("k", session=SimpleNamespace(get=lambda *a, **k: Answer()))
    client.get("/v1/x")
    assert client.limits_seen == {"X-RateLimit-Limit": "200", "X-RateLimit-Remaining": "17"}


def test_the_scan_verifies_the_rate_kept_against_its_servers_and_the_lower_is_used(monkeypatch):
    settings = load_settings(env={"ALPACA_API_KEY_ID": "i", "ALPACA_API_SECRET_KEY": "s"}, env_file=None)
    alpaca = provider("alpaca")
    n, s = alpaca.rate_window(settings)
    kept_pm = n * 60 / s
    lower = SimpleNamespace(client=SimpleNamespace(limits_seen={"X-RateLimit-Limit": str(int(kept_pm // 2))}))
    bare = alpaca.with_(scan=None)  # its own scan's requests left out: only the limits row
    found = bare.run_scan(lower, settings)
    row = found.results[-1]
    assert row[0] == "its server's rate limit" and row[1] is False and "used from now on" in row[2]
    assert found.limits["perMinute"] == int(kept_pm // 2) and found.limits["kept"] == [n, s]
    # the limiter keeps a little inside the server's lower limit from then on
    monkeypatch.setattr(registry, "found_limits",
                        lambda name, st: [(int(kept_pm // 2), 60.0)] if name in ("alpaca", "boats") else [])
    m, t = rate_window("alpaca", settings)
    assert m * 60 / t <= int(kept_pm // 2) * registry.SERVER_SHARE and t == s
    assert rate_window("tiingo", settings) == provider("tiingo").rate_window(settings)  # the others untouched
    # a plan without a limit of its own takes the server's too
    massive = load_settings(env={"MASSIVE_API_KEY": "k", "MASSIVE_TIER": "developer"}, env_file=None)
    assert provider("massive").rate_window(massive)[0] is None
    monkeypatch.setattr(registry, "found_limits", lambda name, st: [(100.0, 60.0)])
    assert rate_window("massive", massive) == (95, 60.0)
    # none sent: its own published limits kept
    quiet = bare.run_scan(SimpleNamespace(client=SimpleNamespace(limits_seen={})), settings)
    assert quiet.results[-1][1] is True and "not sent" in quiet.results[-1][2] and quiet.limits["perMinute"] is None


def test_the_limit_found_is_read_from_the_scan_kept_in_the_database(tmp_path):
    from liminal_libs import sourcing
    from liminal_libs.storage import Store

    with Store(tmp_path / "m.db") as store:
        store.kv_set(sourcing.SCAN_KEY.format("alpaca"), json.dumps({"at": "2026-10-28T12:00:00", "results": [],
                                                                     "limits": {"perMinute": 150}}))
        store.kv_set(sourcing.SCAN_KEY.format("boats"), json.dumps({"at": "2026-10-28T12:00:00", "results": [],
                                                                    "limits": {"seen": {"X-RateLimit-Limit": "50"}}}))
    assert registry.scan_limits(tmp_path / "m.db", "alpaca") == [(150.0, 60.0)]
    assert registry.scan_limits(tmp_path / "m.db", "tiingo") == [] and registry.scan_limits(tmp_path / "none.db", "x") == []
    # Blue Ocean's scan is on Alpaca's account: its limit reaches Alpaca's limiter, which it paces with
    assert set(registry.sharing("alpaca")) >= {"alpaca", "boats"}
    assert sorted(lim for n in registry.sharing("alpaca") for lim in registry.scan_limits(tmp_path / "m.db", n)) == [
        (50.0, 60.0), (150.0, 60.0)]


# -- a plugin's second feed (Provider.part_of, API 1.1): Alpaca's Blue Ocean ----------------------
def test_blue_ocean_is_a_feed_of_the_alpaca_plugin():
    """The user, 2026-10-29: "the Blue Ocean plugin is part of alpaca, so it should be part of the
    alpaca plugin". One module, two feeds: Blue Ocean keeps its own name, market and bars, on
    Alpaca's keys and limits."""
    import importlib.util

    import plugin_integrator_alpaca as alpaca

    assert [p.name for p in alpaca.PLUGINS] == ["alpaca", "boats"] and not hasattr(alpaca, "PLUGIN")
    assert importlib.util.find_spec("plugin_integrator_boats") is None  # no package of its own
    boats = provider("boats")
    assert (boats.title, boats.part_of, boats.account) == ("Blue Ocean", "alpaca", "alpaca")
    assert boats.build.__module__ == "plugin_integrator_alpaca"
    assert registry.plugin_feeds("alpaca") == ["alpaca", "boats"] and registry.plugin_of("boats") == "alpaca"
    assert registry.plugin_feeds("massive") == ["massive"] and registry.plugin_of("coinbase") == "coinbase"
    s = load_settings(env={"ALPACA_API_KEY_ID": "i", "ALPACA_API_SECRET_KEY": "s"}, env_file=None)
    assert markets("boats", s) == {"stock": "boats"} and rate_window("boats", s) == rate_window("alpaca", s)
    assert feed_profiles(s)["boats"].assets == {"stock"} and set(registry.sharing("alpaca")) >= {"alpaca", "boats"}


def test_a_feed_part_of_no_plugin_here_is_a_plugin_of_its_own(installed):
    installed(("acme", "acme_feed:PLUGIN", lambda: replace(ACME, part_of="nope", api="1.1")))
    assert registry.plugin_of("acme") == "acme" and registry.plugin_feeds("acme") == ["acme"]
    assert "acme" in {r["name"] for r in registry.menu()}


def test_the_plugins_command_lists_alpaca_once_with_its_blue_ocean_feed(monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "i")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setattr(config, "read_env_file", lambda path: {})
    assert cli.main(["plugins", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)["providers"]}
    assert "boats" not in rows  # the key script offers Alpaca's keys once
    (feed,) = rows["alpaca"]["feeds"]
    assert (feed["name"], feed["title"], feed["label"], feed["serves"]) == ("boats", "Blue Ocean", "alpaca (blue ocean)", ["stock"])
    assert [f["name"] for f in rows["tiingo"]["feeds"]] == ["tiingofx"]
    assert [f["name"] for f in rows["cboe"]["feeds"]] == ["cboefutures"] and "cboefutures" not in rows
    assert all(r["feeds"] == [] for n, r in rows.items() if n not in ("alpaca", "tiingo", "cboe"))
    assert cli.main(["plugins"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.strip().startswith("and alpaca (blue ocean) (feed boats): US stocks overnight") and "serves: stock" in line
               for line in lines)
    assert not any(line.startswith("boats") for line in lines)


def test_a_rescan_of_alpaca_scans_its_blue_ocean_feed_too(tmp_path, monkeypatch):
    from contextlib import contextmanager

    asked = []

    @contextmanager
    def run(args, settings, label, lock=True):  # no request: the run that would ask
        yield object(), None, print

    monkeypatch.setattr(cli, "api_run", run)
    monkeypatch.setattr(cli, "_scan", lambda name, source, store, log, settings=None: asked.append(name))
    s = load_settings(env={"ALPACA_API_KEY_ID": "i", "ALPACA_API_SECRET_KEY": "s",
                           "LIMINAL_DB": str(tmp_path / "m.db")}, env_file=None)
    for name, want in (("alpaca", ["alpaca", "boats"]), ("boats", ["boats"])):
        asked.clear()
        assert cli.cmd_capabilities(cli.build_parser().parse_args(["capabilities", name]), s) == 0
        assert asked == want


# -- Tiingo's forex a feed of the Tiingo plugin; a feed of a plugin's keys named by that plugin -------
def test_tiingos_forex_is_a_feed_of_the_tiingo_plugin():
    """The user, 2026-10-30: "i also have tiingo and tiingo forex, tiingo should be one plugin"."""
    import importlib.util

    import plugin_integrator_tiingo as tiingo

    assert [p.name for p in tiingo.PLUGINS] == ["tiingo", "tiingofx"] and not hasattr(tiingo, "PLUGIN")
    assert importlib.util.find_spec("plugin_integrator_tiingofx") is None  # no package of its own
    fx = provider("tiingofx")
    assert (fx.title, fx.part_of, fx.account, fx.archival) == ("Forex", "tiingo", "tiingo", False)
    assert provider("tiingo").archival  # its stocks' daily bars stay a closed archive; its forex is kept up to date
    assert registry.plugin_feeds("tiingo") == ["tiingo", "tiingofx"] and registry.plugin_of("tiingofx") == "tiingo"
    s = load_settings(env={"TIINGO_API_KEY": "k"}, env_file=None)
    assert markets("tiingofx", s) == {"fx": "tiingofx"} and rate_window("tiingofx", s) == rate_window("tiingo", s)
    assert set(registry.sharing("tiingo")) >= {"tiingo", "tiingofx"}


def test_a_feed_on_a_plugins_keys_is_named_by_that_plugin_never_as_a_source_of_its_own():
    """The user, 2026-10-30: "the source isn't blue ocean its alpaca w/ blue ocean"; "house all alpaca in
    alpaca"."""
    from liminal_libs import live
    from liminal_charts.gui.server import feed_labels

    assert registry.label("alpaca") == "alpaca" and registry.label("boats") == "alpaca (blue ocean)"
    assert registry.label("tiingofx") == "tiingo (forex)" and registry.label("massive") == "massive"
    assert registry.label("nope") == "nope"  # a name no provider has: as it is
    labels = feed_labels()
    assert set(labels) >= set(providers()) | {IEX_FEED, TIINGO_BOATS}
    assert labels["iex"] == "alpaca (iex)" and labels["tiingoboats"] == "tiingo (blue ocean)"
    assert not any("ocean" in v and not v.startswith(("alpaca", "tiingo")) for v in labels.values())


def test_every_plugin_found_conforms_to_the_class_contract():
    """Every plugin the registry finds (the eighteen checkouts, each of their parts, the built-ins)
    satisfies the class contract's conformance check (liminal_libs.sources.conformance): the user,
    2026-11-03, "evaluate each functional piece of software or plugin and verify correctness and
    valid functionality through unit tests created to address new class scheme"."""
    from liminal_libs.sources.conformance import problems
    from liminal_libs.sources.registry import assistants, publishers

    found = [*providers().values(), *publishers().values(), *assistants().values()]
    assert len(found) >= 28, [p.name for p in found]
    assert [w for p in found for w in problems(p)] == []
    assert all(p.api == "2.2" and not p.is_record for p in found if p.origin != "built in")  # the packages: classes, 2.2
