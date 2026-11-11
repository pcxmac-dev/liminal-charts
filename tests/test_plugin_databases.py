"""Each plugin points to the databases its data is in (plugin API 1.8; layout.py,
registry.plugin_databases). The user, 2026-10-31: "the plugin will point to the databases it stores
the data in"; and, of the engines: "lets prep for mongo and duck"."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from liminal_charts import cli
from liminal_libs import config, datasets, gov, layout
from liminal_libs.config import load_settings
from liminal_libs.layout import GOV, MAIN, Database
from liminal_libs.sources import providers, publishers, registry
from liminal_libs.sources.publisher import Publisher

from .test_plugins import ACME

STATIC = Path(__file__).resolve().parents[1] / "src" / "liminal_charts" / "gui" / "static"
FEDERAL_READ = ("what Federal Reserve, New York Fed, SEC, Bureau of Labor Statistics and Federal Register read, "
                "and the reads' record: requests, robots.txt, rechecks")


@pytest.fixture
def installed(monkeypatch):
    def install(*plugins):
        eps = [SimpleNamespace(name=p.name, value=f"pkg:{p.name}", load=lambda p=p: p) for p in plugins]
        monkeypatch.setattr(registry, "_entry_points", lambda: eps)
        return registry.reload()

    yield install
    registry._providers = None


def wheres(name: str) -> list[str]:
    return [d.where for d in registry.plugin_databases(name)]


def trades(name: str) -> bool:
    """Whether a feed can have trades (2026-11-03): 1-second bars among what it provides, or a stream
    of its own; a live feed's (iex, boats) when it is a stream, not a poller."""
    p = providers().get(name)
    if p is None:
        live = next(x for q in providers().values() for x in q.live if x.name == name)
        return live.stream is not None
    return not p.provides or any("second" in r for r in p.provides.values()) or any(x.stream is not None for x in p.live)


def feed(name: str) -> list[str]:
    """A feed's bars, its closed months in DuckDB, and its trades where it can have them."""
    return [layout.bars(name), layout.history(name), *([layout.ticks(name)] if trades(name) else [])]


NEWS = "mongodb://127.0.0.1:27017/liminal.news"
ITEMS = "mongodb://127.0.0.1:27017/liminal.gov_items"


def test_each_feed_plugin_points_to_its_feeds_files_and_its_rows_in_the_main_file():
    assert wheres("alpaca") == [*feed("alpaca"), *feed("iex"), *feed("boats"), MAIN]  # its live feed's, its part's
    alpaca = {d.where: d for d in registry.plugin_databases("alpaca")}
    assert alpaca["feeds/iex.db"].holds == "alpaca (iex): bars" and alpaca["feeds/iex-ticks.db"].holds == "alpaca (iex): trades"
    assert "feeds/boats-ticks.db" not in alpaca  # Blue Ocean's nights: minutes polled, never trades
    assert "feeds/tiingofx-ticks.db" not in wheres("tiingo") and "feeds/twse-ticks.db" not in wheres("twse")  # daily, minutes
    assert "feeds/massive-ticks.db" in wheres("massive")  # 1-second bars and trades
    assert not alpaca["feeds/alpaca.db"].shared and alpaca[MAIN].shared
    assert alpaca["history/alpaca/"].kind == "duckdb" and not alpaca["history/alpaca/"].shared
    assert wheres("tiingo") == [*feed("tiingo"), *feed("tiingoboats"), *feed("tiingofx"), MAIN]
    assert wheres("bitfinex") == [*feed("bitfinex"), MAIN]
    # Massive's headlines are in it: what they read is in the main file (declared), their reads in gov.db
    massive = registry.plugin_databases("massive")
    assert [d.where for d in massive] == [*feed("massive"), GOV, NEWS, MAIN]
    assert massive[-1].holds == "its rows by feed: requests a day, events, the reference it gave; Massive headlines: the news"
    assert massive[3].holds == "the reads' record: requests, robots.txt, rechecks" and massive[3].shared
    assert [d.kind for d in massive] == ["sqlite", "duckdb", "sqlite", "sqlite", "mongodb", "sqlite"]
    assert massive[4].holds == "its headlines, when its documents are in MongoDB" and massive[4].shared
    assert wheres("boats") == [*feed("boats"), MAIN]  # a part's: its own


def test_each_publisher_plugin_points_to_its_parts_datasets_and_what_they_read():
    federal = registry.plugin_databases("federal")
    assert [(d.where, d.kind, d.shared) for d in federal] == [
        ("datasets/cftc.db", "sqlite", False), ("sources/datasets/cftc/", "files", False),
        (GOV, "sqlite", True), (ITEMS, "mongodb", True), (MAIN, "sqlite", True)]
    assert federal[2].holds == FEDERAL_READ and federal[4].holds == "its fetched files' ledger"
    assert wheres("cftc") == ["datasets/cftc.db", "sources/datasets/cftc/", MAIN]  # a part's: its own
    assert wheres("sec") == [GOV, ITEMS]
    assert wheres("datahub") == ["datasets/sp500.db", "sources/datasets/sp500/", "datasets/shiller.db",
                                 "sources/datasets/shiller/", "datasets/imf.db", "sources/datasets/imf/", MAIN]
    french = registry.plugin_databases("french")
    assert [d.where for d in french] == ["datasets/french.db", "sources/datasets/french/", MAIN]
    assert french[0].holds.startswith("series of french-3-factors-daily, french-3-factors-monthly")


def test_a_file_of_a_plugins_own_is_no_other_plugins_and_every_feed_and_dataset_has_one():
    """Each feed's and dataset file's database is pointed to by exactly one plugin; the shared ones
    (the main file, gov.db) say so."""
    top = [n for n in providers() if registry.plugin_of(n) == n]
    top += [n for n in publishers() if registry.publisher_of(n) == n]
    owner: dict[str, str] = {}
    for name in top:
        for d in registry.plugin_databases(name):
            if d.shared:
                assert d.where in (MAIN, GOV) or d.server, (name, d)
                continue
            assert d.where not in owner, (d.where, owner.get(d.where), name)
            owner[d.where] = name
    feeds = [*providers(), *registry.live_feeds()]
    assert all(owner[layout.bars(n)] == owner[layout.ticks(n)] for n in feeds if layout.ticks(n) in owner)
    assert {n for n in feeds if layout.ticks(n) in owner} == {n for n in feeds if trades(n)}
    assert owner["feeds/iex.db"] == "alpaca" and owner["feeds/tiingofx.db"] == "tiingo"
    assert layout.bars(layout.COMPOSITE) not in owner  # the composite model's: the app's, built from the feeds
    files = {d.provider for p in publishers().values() for d in p.declared}
    assert {owner[layout.dataset_file(f)] for f in files} == {"federal", "french", "datahub"}
    assert all(owner[layout.downloads(f)] == owner[layout.dataset_file(f)] for f in files)


def test_the_layout_is_where_the_store_gov_and_datasets_keep_them(tmp_path):
    from liminal_libs.storage import Store
    from liminal_libs.storage.feeds import _Feed

    main = tmp_path / "liminal.db"
    with Store(main) as store:
        assert store.feeds_dir == tmp_path / layout.FEEDS and store.datasets_dir == tmp_path / layout.DATASETS
        store.feeds_dir.mkdir()  # made with its first feed
        feed = _Feed("x", store.feeds_dir, True)
        assert (feed.path, feed.ticks_path) == (layout.locate(layout.bars("x"), main), layout.locate(layout.ticks("x"), main))
        feed.conn.close()
        store._dataset_db("acme", create=True)
        assert layout.locate(layout.dataset_file("acme"), main).exists()
        ds = SimpleNamespace(provider="acme")
        kept = datasets._keep(store, ds, "f.csv", b"a,b\n", "0123456789abcdef")
        assert kept.parent == layout.locate(layout.downloads("acme"), main) and kept.read_bytes() == b"a,b\n"
    assert gov.db_path(main) == layout.locate(GOV, main) == tmp_path / "gov" / "gov.db"
    assert layout.locate(MAIN, main) == main and layout.locate(MAIN, ":memory:") is None


def test_a_databases_size_counts_its_log_and_a_folders_files(tmp_path):
    main = tmp_path / "liminal.db"
    (tmp_path / "feeds").mkdir()
    (tmp_path / "feeds" / "x.db").write_bytes(b"0" * 100)
    (tmp_path / "feeds" / "x.db-wal").write_bytes(b"0" * 20)
    (tmp_path / "sources" / "datasets" / "x" / "old").mkdir(parents=True)
    (tmp_path / "sources" / "datasets" / "x" / "a.csv").write_bytes(b"0" * 7)
    (tmp_path / "sources" / "datasets" / "x" / "old" / "b.csv").write_bytes(b"0" * 5)
    assert layout.size(layout.bars("x"), main) == 120
    assert layout.size(layout.downloads("x"), main) == 12
    assert layout.size(layout.ticks("x"), main) is None  # not made yet
    assert layout.size("mongodb://127.0.0.1:27017/liminal.filings", main) is None  # a server's: asked nothing


def test_each_database_says_its_engine():
    assert [layout.engine_of(w) for w in ("feeds/x.db", "history/x.duckdb", "history/x.parquet", "history/x.parquet/",
                                          "sources/datasets/x/", "mongodb://127.0.0.1:27017/liminal.filings")] == [
        "sqlite", "duckdb", "parquet", "parquet", "files", "mongodb"]
    assert Database("history/bars/", "closed months", engine="parquet").kind == "parquet"  # said, not told
    assert Database("mongodb://localhost/liminal.filings", "filings").server


def test_a_plugin_pointing_off_this_machine_or_out_of_the_data_folder_is_left_out(installed):
    """Data stays here: a database server off this machine, a path out of the data folder, an engine
    the app has not, or the app's own file declared as the plugin's alone is refused at loading."""
    ok = [Database("mongodb://127.0.0.1:27017/liminal.filings", "filings"), Database("acme/closed.duckdb", "closed months"),
          Database("acme/bars.parquet/", "bars"), Database(MAIN, "its rows", shared=True)]
    bad = {"far": Database("mongodb://db.example.com:27017/liminal.filings", "filings"),
           "srv": Database("mongodb+srv://cluster0.example.net/liminal", "filings"),
           "out": Database("../elsewhere.db", "bars"),
           "abs": Database(str(Path(__file__).resolve().with_name("acme.db")), "bars"),  # absolute here, on Linux too
           "odd": Database("acme/x.db", "bars", engine="oracle"), "pg": Database("postgres://127.0.0.1/x", "bars"),
           "main": Database(MAIN, "its rows")}
    installed(replace(ACME, databases=tuple(ok)),
              *(replace(ACME, name=f"acme-{k}", databases=(d,)) for k, d in bad.items()))
    assert "acme" in providers() and not any(n.startswith("acme-") for n in providers())
    why = {w.removeprefix("package: pkg:"): text for w, text in registry.errors()}
    assert "a database server off this machine" in why["acme-far"] and "off this machine" in why["acme-srv"]
    assert "not in the data folder" in why["acme-out"] and "not in the data folder" in why["acme-abs"]
    assert "no engine 'oracle'" in why["acme-odd"] and "a server's address for a sqlite database" in why["acme-pg"]
    assert "the app's file, every plugin's (declare it shared)" in why["acme-main"]
    assert [(d.where, d.kind) for d in registry.plugin_databases("acme")] == [
        ("feeds/acme.db", "sqlite"), ("history/acme/", "duckdb"), ("feeds/acme-ticks.db", "sqlite"),
        ("mongodb://127.0.0.1:27017/liminal.filings", "mongodb"), ("acme/closed.duckdb", "duckdb"),
        ("acme/bars.parquet/", "parquet"), (MAIN, "sqlite")]
    assert registry.plugin_databases("acme")[-1].holds == "its rows by feed: requests a day, events, the reference it gave; its rows"


def test_a_publishers_own_databases_are_its_and_its_plugins(installed):
    lib = Publisher("lib", "Library", "", (), registry.publishers()["french"].policy,
                    databases=(Database("mongodb://127.0.0.1/liminal.filings", "filings as filed"),), api="1.8")
    installed(lib)
    assert [(d.where, d.holds) for d in registry.plugin_databases("lib")] == [("mongodb://127.0.0.1/liminal.filings", "filings as filed")]


def test_the_plugins_command_and_view_point_each_plugin_to_its_databases(tmp_path, monkeypatch, capsys):
    from liminal_charts.gui.server import ChartApp
    from liminal_libs.storage import Store

    monkeypatch.setattr(config, "read_env_file", lambda path: {})
    assert cli.main(["plugins", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    alpaca = next(r for r in out["providers"] if r["name"] == "alpaca")
    assert alpaca["databases"][0] == {"where": "feeds/alpaca.db", "holds": "alpaca: bars", "shared": False, "engine": "sqlite"}
    federal = next(r for r in out["publishers"] if r["name"] == "federal")
    assert [d["where"] for d in federal["databases"]] == ["datasets/cftc.db", "sources/datasets/cftc/", GOV, ITEMS, MAIN]

    main = tmp_path / "liminal.db"
    with Store(main, check_same_thread=False) as store:
        (tmp_path / "feeds").mkdir(exist_ok=True)
        with closing(sqlite3.connect(tmp_path / "feeds" / "coinbase.db")) as made:
            made.execute("CREATE TABLE t (x)")
        view = ChartApp(store, settings=load_settings(env={"LIMINAL_DB": str(main)}, env_file=None)).plugins()
    coinbase = next(p for p in view["plugins"] if p["name"] == "coinbase")
    assert coinbase["databases"][0].pop("bytes") >= 4096  # the file and its log (the store opened it)
    assert coinbase["databases"][0] == {"where": "feeds/coinbase.db", "holds": "coinbase: bars", "shared": False,
                                        "engine": "sqlite", "path": str(tmp_path / "feeds" / "coinbase.db")}
    assert coinbase["databases"][1]["bytes"] is None  # its closed months in DuckDB: none yet
    assert coinbase["databases"][2]["bytes"] is None  # its trades: none yet
    pubs = {p["name"]: p for p in view["publishers"]}
    assert [d["where"] for d in pubs["federal"]["databases"]] == ["datasets/cftc.db", "sources/datasets/cftc/", GOV, ITEMS, MAIN]
    assert pubs["sec"]["databases"] == [] and pubs["massive-news"]["databases"] == []  # a part's: in its plugin's page
    massive = next(p for p in view["plugins"] if p["name"] == "massive")
    assert massive["databases"][-1]["path"] == str(main) and massive["databases"][-1]["shared"]


def test_the_page_shows_each_plugins_databases():
    js = (STATIC / "plugins.js").read_text(encoding="utf-8")
    assert "function databasesHtml(p)" in js and "<h4>Databases</h4>" in js
    assert "${publishersHtml(p)}${databasesHtml(p)}${apisHtml(p, pubPartsOf(p))}`;" in js  # a feed plugin's, after its headlines, then its API
    assert "${partsHtml(p)}${databasesHtml(p)}`;" in js and "${readsHtml(p)}${databasesHtml(p)}`;" in js
    assert "${datasetsHtml(p)}${databasesHtml(p)}" in js
    assert "mongodb: 'MongoDB'" in js and "duckdb: 'DuckDB'" in js
    assert ".pl-dbs code {" in (STATIC / "style.css").read_text(encoding="utf-8")
