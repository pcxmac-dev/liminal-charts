"""The Federal plugin (sources/plugins/federal/): the federal agencies, each a part of one plugin
(Publisher.part_of, plugin API 1.2), with its own scrape checks, state and pause; pausing the plugin
stops them all; its record and a part with datasets only are never read; one Plugins view entry, one
`plugins` row; the Gov view named Federal. The user, 2026-10-30: "merge everything in to a Federal
plugin, from news feeds to edgar access, to everything that matters"."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from liminal_libs import publications
from liminal_libs.config import load_settings
from liminal_libs.gov import GovDB
from liminal_libs.sources import publishers, registry
from liminal_libs.sources.publisher import Feed, Policy, Publisher

from .test_publications import Site, pub, reader

AGENCIES = ["fed", "nyfed", "sec", "bls", "fedreg", "cftc"]
STATIC = Path(__file__).resolve().parents[1] / "src" / "liminal_charts" / "gui" / "static"


def test_the_federal_agencies_are_parts_of_one_plugin():
    import plugin_integrator_federal as federal

    assert [p.name for p in federal.PUBLISHERS] == ["federal", *AGENCIES]
    for gone in ("fed", "nyfed", "sec", "bls", "fedreg"):  # no plugin of their own now
        assert importlib.util.find_spec(f"plugin_integrator_{gone}") is None, gone  # no package of their own either
    pubs = publishers()
    assert list(pubs)[:7] == ["federal", *AGENCIES]  # the plugin's record first, its agencies after it
    assert all(pubs[n].part_of == "federal" and (pubs[n].version, pubs[n].api) == ("0.3.0", "2.2") for n in AGENCIES)
    assert pubs["federal"].feeds == () and pubs["federal"].part_of == ""
    assert registry.publisher_parts("federal") == AGENCIES and registry.publisher_of("sec") == "federal"
    assert registry.publisher_of("massive-news") == "massive" and registry.publisher_parts("massive-news") == []
    # what you set for it: the SEC's GOV_CONTACT, declared by the SEC, the plugin's as a whole
    assert [k.name for k in pubs["sec"].keys] == ["GOV_CONTACT"] and not pubs["sec"].keys[0].secret
    assert [k.name for k in registry.plugin_keys("federal")] == ["GOV_CONTACT"] == [k.name for k in registry.plugin_keys("sec")]
    # each agency keeps what it was: its feeds (their keys name the state kept), its hosts and checks
    assert [f.key for f in pubs["fed"].feeds] == ["fed-press", "fed-speeches"] and pubs["sec"].policy.contact
    assert pubs["fed"].has_calendar and pubs["nyfed"].has_moments
    # the CFTC: its Commitments of Traders, a dataset; nothing of its own to read (its feeds are off)
    assert pubs["cftc"].feeds == () and pubs["cftc"].dataset_names == ("cftc-cot-legacy",)
    from liminal_libs import datasets

    assert datasets.DATASETS["cftc-cot-legacy"].publisher == "cftc"  # declared by it (API 1.4)


def test_a_publisher_part_of_none_here_is_one_of_its_own(monkeypatch):
    stray = Publisher("stray", "Stray", "", (Feed("stray-a", "S", "A", "https://s.gov/a"),), Policy(hosts=("s.gov",)),
                      part_of="nope", api="1.2", order=900)
    monkeypatch.setattr(registry, "_entry_points", lambda: [type("EP", (), {"name": "stray", "value": "pkg:stray",
                                                                              "load": lambda self: stray})()])
    try:
        registry.reload()
        assert registry.publisher_of("stray") == "stray" and "stray" in {r["name"] for r in registry.publisher_menu()}
        view = publications.view(GovDB(None), publishers=publishers())
        assert next(p for p in view if p["name"] == "stray")["partOf"] == ""  # listed on its own, not lost
    finally:
        registry._providers = None


def test_pausing_federal_pauses_every_agency_in_it_and_only_them(store):
    site = Site()
    r = reader(site, store=store)
    part = Publisher("t", "Test agency", "test", pub().feeds, pub().policy, part_of="plug")
    plugin = Publisher("plug", "Plugin", "", (), Policy())
    alone = pub()
    alone = Publisher("u", "Other", "", (Feed("u-press", "U", "Press", "https://news.example.gov/u.xml"),), alone.policy)
    now = datetime.now(timezone.utc)
    publications.pause(store, "plug", True)
    assert r.kill(part, now)["whose"] == "plugin" and r.kill(alone, now) is None
    r.step(now, publishers={"plug": plugin, "t": part})
    assert site.asked == []  # nothing for the paused plugin's part, robots.txt neither
    view = {p["name"]: p for p in publications.view(GovDB(None), store=store, now=now,
                                                     publishers={"plug": plugin, "t": part, "u": alone})}
    assert view["t"]["state"] == "paused" and view["plug"]["state"] == "paused" and view["u"]["state"] != "paused"
    assert view["plug"]["parts"] == ["t"] and view["t"]["partOf"] == "plug"
    publications.pause(store, "plug", False)
    r.step(datetime.now(timezone.utc), publishers={"plug": plugin, "t": part})
    assert site.asked  # resumed with it


def test_a_publisher_with_no_feed_of_its_own_is_never_read_or_rechecked(store):
    site = Site()
    r = reader(site, store=store)
    pubs = publishers()
    now = datetime.now(timezone.utc)
    r.step(now, publishers={"federal": pubs["federal"], "cftc": pubs["cftc"]})
    found = r.recheck(pubs["cftc"], now)
    assert site.asked == [] and found["notes"] == ["no feed of its own to read: nothing sent"]  # robots.txt neither
    assert r.gdb.recheck_get("cftc") is None


def test_the_plugins_view_lists_federal_once_with_its_agencies_and_the_cftcs_dataset(tmp_path):
    from liminal_charts.gui.server import ChartApp
    from liminal_libs.storage import Store

    with Store(tmp_path / "m.db", check_same_thread=False) as store:
        out = ChartApp(store, settings=load_settings(env={"LIMINAL_DB": str(tmp_path / "m.db")}, env_file=None)).publishers()
    by = {p["name"]: p for p in out}
    assert by["federal"]["parts"] == AGENCIES and all(by[n]["partOf"] == "federal" for n in AGENCIES)
    assert by["federal"]["requestsToday"] == sum(by[n]["requestsToday"] for n in AGENCIES)
    assert by["cftc"]["datasets"] == ["cftc-cot-legacy"] and by["cftc"]["datasetRows"][0]["name"] == "cftc-cot-legacy"
    assert by["massive-news"]["partOf"] == "massive" and by["massive-news"]["parts"] == []  # in Massive
    assert [k["name"] for k in by["federal"]["keys"]] == ["GOV_CONTACT"] and set(by["federal"]["keys"][0]) == {
        "name", "label", "aliases", "secret", "hint", "set"}  # whether it is set, never its value


def test_the_page_shows_federal_once_with_its_agencies_in_its_page_and_the_gov_view_is_named_federal():
    """The user, 2026-10-31: "massive headlines should not be separate from massive same thing with
    federal agencies and federal": a part is a section of its plugin's page, no entry of its own."""
    js = (STATIC / "plugins.js").read_text(encoding="utf-8")
    assert "const top = (data.publishers || []).filter((p) => !parentOf(p));" in js
    assert "top.filter((p) => !setsOnly(p)).map(pubButton)" in js and "pl-sub" not in js
    assert "if (p.parts.length) return `${(p.keys || []).length ? pubSetupHtml(p) : ''}${fetchingHtml(p.name)}${partsHtml(p)}${databasesHtml(p)}`;" in js
    assert "${partsOf(p).map((q) => partHtml(q, false)).join('')}" in js  # each agency a section
    assert '<details class="pl-part" data-part="${esc(q.name)}"' in js and "${q.feeds.length ? readsHtml(q) : datasetsHtml(q)}" in js
    assert "if (up) { folds.set(part.name, true); sel = up.id; }" in js  # a part picked: its plugin's page
    assert "function datasetsHtml(p)" in js
    assert "whose === 'plugin'" in js and "(all ? 'Pause all' : 'Pause')" in js
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".pl-part > summary {" in css and ".pl-sub" not in css and ".pl-link {" in css
    assert "<h2>Federal</h2>" in (STATIC / "gov.js").read_text(encoding="utf-8")
    assert 'id="railGov" title="Federal: the federal agencies' in (STATIC / "index.html").read_text(encoding="utf-8")
