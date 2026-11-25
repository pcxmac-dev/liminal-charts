"""Stores by data type (stores.py) and documents in SQLite or MongoDB (storage/documents.py), with a
stand-in for the MongoDB server: the tests never start one nor reach the machine's (conftest)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from liminal_libs import gov, mongo, stores
from liminal_libs.sources.registry import publishers
from liminal_libs.storage import Store
from liminal_libs.storage.documents import GOV_ITEMS, NEWS, MongoDocuments, SqliteDocuments, copy


def _match(doc, flt):
    for k, v in (flt or {}).items():
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$gte" in v and (doc.get(k) is None or doc.get(k) < v["$gte"]):
                return False
        elif doc.get(k) != v:
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, field, direction=1):
        self.docs = sorted(self.docs, key=lambda d: d.get(field), reverse=direction < 0)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def batch_size(self, n):
        return self

    def __iter__(self):
        return iter([dict(d) for d in self.docs])


class Coll:
    def __init__(self):
        self.docs = {}
        self.fail = False

    def create_index(self, *a, **k):
        pass

    def bulk_write(self, ops, ordered=True):
        if self.fail:
            raise RuntimeError("server gone")
        n = 0
        for op in ops:
            _id, d = op._filter["_id"], op._doc
            if "$setOnInsert" in d:
                if _id not in self.docs:
                    self.docs[_id] = {"_id": _id, **d["$setOnInsert"]}
                    n += 1
            else:
                n += _id not in self.docs
                self.docs[_id] = dict(d)
        return SimpleNamespace(upserted_count=n)

    def find(self, flt=None):
        return Cursor([d for d in self.docs.values() if _match(d, flt)])

    def find_one(self, flt=None, sort=None):
        got = list(self.find(flt).sort(*sort[0]) if sort else self.find(flt))
        return got[0] if got else None

    def count_documents(self, flt):
        return sum(1 for d in self.docs.values() if _match(d, flt))


class DB(dict):
    def __missing__(self, name):
        self[name] = Coll()
        return self[name]


@pytest.fixture
def server(monkeypatch):
    """MongoDB "installed" and answering: the stand-in."""
    db = DB()
    monkeypatch.setattr(mongo, "binary", lambda: Path("mongod.exe"))
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: True)
    monkeypatch.setattr(mongo, "database", lambda: db)
    return db


def news(i, t, title="x"):
    return {"source": "massive", "id": str(i), "published_ms": t, "title": title, "publisher": "P", "url": None,
            "tickers": ["AAPL"]}


@pytest.mark.parametrize("engine", ["sqlite", "mongodb"])
def test_documents_answer_the_same_in_both(engine, store, server):
    docs = SqliteDocuments(store.conn, NEWS) if engine == "sqlite" else MongoDocuments(server, NEWS)
    assert docs.put([news(1, 100), news(2, 300), news(3, 200)], replace=True) == 3
    assert docs.put([news(2, 300, "changed")], replace=True) == 1
    assert docs.count() == 3 and docs.count({"source": ["massive"]}) == 3
    assert [d["id"] for d in docs.find({"source": "massive"})] == ["2", "3", "1"]  # newest first
    assert [d["id"] for d in docs.find(since=200, limit=1)] == ["2"]
    assert docs.find({"id": "2"})[0]["title"] == "changed" and docs.find({"id": "2"})[0]["tickers"] == ["AAPL"]
    assert docs.newest({"source": "massive"}) == 300 and docs.newest({"source": "other"}) is None
    assert sorted(d["id"] for batch in docs.every(batch=2) for d in batch) == ["1", "2", "3"]
    items = SqliteDocuments(gov.GovDB(None).conn, GOV_ITEMS) if engine == "sqlite" else MongoDocuments(server, GOV_ITEMS)
    item = {"source": "fed-press", "id": "a", "agency": "Fed", "title": "first", "published": "2026-10-01",
            "tags": ["FOMC"], "fetched_at": "t"}
    assert items.put([item]) == 1
    assert items.put([{**item, "title": "again"}]) == 0  # one held keeps its own
    assert items.find()[0]["title"] == "first" and items.find()[0]["tags"] == ["FOMC"]


def test_plugin_types():
    assert stores.plugin_types("massive") == ["bars", "documents"]
    assert stores.plugin_types("federal") == ["documents", "series", "files"]  # the CFTC's report is a dataset
    assert stores.plugin_types("alpaca") == ["bars"]
    assert stores.plugin_types("french") == ["series", "files"]
    kinds = {k.name: w for k, w in stores.document_scopes("federal")}
    assert set(kinds) == {"gov_items"} and "fed-press" in kinds["gov_items"]["source"]
    assert [(k.name, w) for k, w in stores.document_scopes("massive")] == [("news", {"source": ["massive"]})]
    assert stores.item_plugin("fed-press") == "federal" and stores.item_plugin("nope") is None


def test_optimal_is_the_best_installed(server, monkeypatch):
    assert stores.optimal("bars") == "duckdb"
    assert stores.optimal("documents") == "mongodb"
    assert stores.optimal("series") == "sqlite"
    monkeypatch.setattr(mongo, "binary", lambda: None)
    assert stores.optimal("documents") == "sqlite"
    assert stores.available("mongodb") == (False, "the MongoDB server is not installed here")


def test_pick_checks_the_engine(store):
    with pytest.raises(ValueError):
        stores.pick(store, "massive", "documents", "duckdb")
    with pytest.raises(LookupError):
        stores.pick(store, "alpaca", "documents", "sqlite")
    with pytest.raises(RuntimeError, match="not installed"):
        stores.pick(store, "massive", "documents", "mongodb")
    stores.pick(store, "massive", "documents", "sqlite")
    assert stores.picked(store, "massive", "documents") == "sqlite"
    stores.pick(store, "massive", "documents", None)
    assert stores.picked(store, "massive", "documents") is None


def test_bars_in_sqlite_for_every_feed_of_the_plugin(store):
    assert {"alpaca", "boats", "iex"} <= set(stores.bars_feeds(store))  # its feeds' and their live feeds' files
    stores.pick(store, "alpaca", "bars", "sqlite")
    assert {store.kv_get(f"store:feed:{f}:bars") for f in ("alpaca", "boats", "iex")} == {"sqlite"}
    assert not {"alpaca", "boats", "iex"} & set(stores.bars_feeds(store))
    row = next(r for r in stores.plugin_stores(store, "alpaca") if r["type"] == "bars")
    assert (row["picked"], row["wanted"], row["effect"], row["optimal"]) == ("sqlite", "sqlite", "sqlite", "duckdb")
    stores.pick(store, "alpaca", "bars", None)
    assert store.kv_get("store:feed:boats:bars") == "duckdb"


NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def test_settle_moves_documents_to_mongodb_and_back(tmp_path, server):
    main = tmp_path / "liminal.db"
    s = Store(main)
    gdb = gov.open_db(main)
    gdb.save_items("fed-press", [{"id": "a", "agency": "Fed", "title": "rates", "published": "2026-10-29T18:00:00"}], NOW)
    s.save_news("massive", [news(1, 1000)])
    assert stores.in_effect(s, "federal", "documents") == "sqlite"  # where they are until moved
    lines = []
    moved = stores.settle(s, gdb.conn, tmp_path, lines.append)
    assert {d["plugin"]: (d["engine"], d["copied"]) for d in moved} == {"massive": ("mongodb", 1), "federal": ("mongodb", 1)}
    assert stores.in_effect(s, "federal", "documents") == "mongodb"
    assert any("federal's documents now in MongoDB (1 copied from SQLite, which keeps its copy)" in x for x in lines)
    assert server["gov_items"].count_documents({}) == 1 and server["news"].count_documents({}) == 1
    # read and written there now; SQLite keeps its copy
    gdb2 = gov.open_db(main)
    gdb2.save_items("fed-press", [{"id": "b", "agency": "Fed", "title": "minutes", "published": "2026-10-30T18:00:00"}], NOW)
    assert server["gov_items"].count_documents({}) == 2
    assert gdb2.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert [i["title"] for i in gdb2.items()] == ["minutes", "rates"]
    s.save_news("massive", [news(2, 2000)])
    assert s.latest_news_ms("massive") == 2000 and [n["id"] for n in s.news()] == ["2", "1"]
    assert s.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 1
    # the server down: this file's copy
    server["gov_items"].find = lambda flt=None: (_ for _ in ()).throw(RuntimeError("down"))
    assert [i["title"] for i in gdb2.items()] == ["rates"]
    del server["gov_items"].find
    # picked SQLite: copied back, then read there
    stores.pick(s, "federal", "documents", "sqlite")
    stores.settle(s, gdb2.conn, tmp_path, lines.append, only="federal")
    assert stores.in_effect(s, "federal", "documents") == "sqlite"
    assert gdb2.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    gdb2._engines_seen = None
    assert [i["title"] for i in gdb2.items()] == ["minutes", "rates"]
    for g in (gdb, gdb2):
        g.close()
    s.close()


def test_a_write_falls_back_to_sqlite_while_the_server_is_down(tmp_path, server, monkeypatch):
    """The recorder's headlines and an agency's items are not lost while mongod is down: this file
    holds them, and the next settle copies them over."""
    main = tmp_path / "liminal.db"
    s = Store(main)
    gdb = gov.open_db(main)
    stores.settle(s, gdb.conn, tmp_path)
    assert stores.in_effect(s, "massive", "documents") == stores.in_effect(s, "federal", "documents") == "mongodb"
    server["news"].fail = server["gov_items"].fail = True  # the server gone: every write there fails
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: False)
    mongo._alive[:] = [0.0, False]
    assert s.save_news("massive", [news(1, 1000)]) == 1
    gdb.save_items("fed-press", [{"id": "a", "agency": "Fed", "title": "rates", "published": "2026-10-29"}], NOW)
    assert s.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 1
    assert gdb.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert server["news"].count_documents({}) == 0
    # the server back: the next settle copies them over, the store unchanged
    server["news"].fail = server["gov_items"].fail = False
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: True)
    moved = stores.settle(s, gdb.conn, tmp_path)
    assert {d["plugin"]: d["copied"] for d in moved} == {"massive": 1, "federal": 1}
    assert server["news"].count_documents({}) == 1 and server["gov_items"].count_documents({}) == 1
    gdb.close()
    s.close()


def test_a_read_never_starts_the_server(tmp_path, server, monkeypatch):
    main = tmp_path / "liminal.db"
    s = Store(main)
    gdb = gov.open_db(main)
    s.save_news("massive", [news(1, 1000)])
    stores.settle(s, gdb.conn, tmp_path)
    monkeypatch.setattr(mongo, "running", lambda timeout_ms=500: False)
    mongo._alive[:] = [0.0, False]
    monkeypatch.setattr(mongo, "ensure", lambda data_dir: pytest.fail("a read tried to start the server"))
    assert [n["id"] for n in s.news()] == ["1"]  # from the stand-in: no start asked for
    gdb.close()
    s.close()


def test_settle_keeps_the_store_when_the_copy_fails(tmp_path, server):
    s = Store(tmp_path / "liminal.db")
    gdb = gov.open_db(tmp_path / "liminal.db")
    gdb.save_items("fed-press", [{"id": "a", "agency": "Fed", "title": "rates", "published": "2026-10-29"}], NOW)
    server["gov_items"].fail = True
    lines = []
    stores.settle(s, gdb.conn, tmp_path, lines.append, only="federal")
    assert stores.in_effect(s, "federal", "documents") == "sqlite"
    assert lines == ["stores: federal's documents stay in SQLite: RuntimeError: server gone"]
    gdb.close()
    s.close()


def test_without_mongodb_nothing_moves(tmp_path):
    s = Store(tmp_path / "liminal.db")
    gdb = gov.open_db(tmp_path / "liminal.db")
    assert stores.settle(s, gdb.conn, tmp_path) == []
    row = next(r for r in stores.plugin_stores(s, "federal", gdb.conn) if r["type"] == "documents")
    assert row["effect"] == "sqlite" and row["counts"] == {"mongodb": None, "sqlite": 0}
    assert [e["ok"] for e in row["engines"]] == [False, True]
    gdb.close()
    s.close()


def test_gov_items_are_all_kept():
    db = gov.GovDB(None)
    many = [{"id": str(i), "agency": "Fed", "title": f"t{i}", "published": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}"}
            for i in range(gov.KEEP + 20)]
    assert db.save_items("fed-press", many, NOW) == gov.KEEP + 20
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == gov.KEEP + 20


def test_mongo_is_local_and_capped(tmp_path):
    a = mongo.args(tmp_path)
    assert a[a.index("--bind_ip") + 1] == "127.0.0.1"
    assert a[a.index("--wiredTigerCacheSizeGB") + 1] == str(mongo.CACHE_GB) == "0.5"
    assert Path(a[a.index("--dbpath") + 1]) == tmp_path / "mongo"
    with pytest.raises(RuntimeError, match="not installed"):
        mongo.start(tmp_path)
    assert mongo.ensure(tmp_path) is False


def test_copy_twice_puts_nothing_twice(store, server):
    src = SqliteDocuments(store.conn, NEWS)
    src.put([news(1, 1), news(2, 2)], replace=True)
    dst = MongoDocuments(server, NEWS)
    assert copy(src, dst) == (2, 2)
    assert copy(src, dst) == (2, 0)


def test_the_plugin_page_shows_and_sets_its_stores(tmp_path):
    from liminal_libs.config import load_settings
    from liminal_charts.gui.server import ChartApp

    main = tmp_path / "liminal.db"
    with Store(main, check_same_thread=False) as store:
        app = ChartApp(store, settings=load_settings(env={"LIMINAL_DB": str(main)}, env_file=None))
        got = app.plugin_stores("massive")
        assert [r["type"] for r in got["stores"]] == ["bars", "documents"]
        assert got["engines"]["duckdb"] and got["engines"]["mongodb"]["installed"] is False
        docs = got["stores"][1]
        assert (docs["optimal"], docs["effect"]) == ("sqlite", "sqlite")  # MongoDB not installed (the tests')
        out = app.set_store("massive", "bars", "sqlite")
        assert out["stores"][0]["picked"] == "sqlite" and store.kv_get("store:feed:massive:bars") == "sqlite"
        with pytest.raises(RuntimeError, match="not installed"):
            app.set_store("massive", "documents", "mongodb")
        offline = ChartApp(store, settings=load_settings(env={"LIMINAL_DB": str(main), "LIMINAL_OFFLINE": "1"}, env_file=None))
        with pytest.raises(RuntimeError, match="offline"):
            offline.set_store("massive", "bars", None)


def test_the_page_has_the_store_picker():
    js = (Path(__file__).resolve().parents[1] / "src/liminal_charts/gui/static/plugins.js").read_text(encoding="utf-8")
    assert "function storesHtml(name)" in js and "${storesHtml(p.name)}<h4>Databases</h4>" in js
    assert "if (e.target.matches('select.pl-store')) chooseStore(e.target);" in js
    assert "'/api/plugins/store'" in js and "/api/plugin-stores?name=" in js


def test_the_picker_offers_the_instances_and_says_nothing_moves():
    """0.2.9 (Monday list item 10): the instances added on the platform's Databases section, as engine:instance with
    their titles, after the bare engines; a pick naming one asked as a write from now that moves nothing."""
    js = (Path(__file__).resolve().parents[1] / "src/liminal_charts/gui/static/plugins.js").read_text(encoding="utf-8")
    assert '<optgroup label="Instances you added">' in js and "${esc(i.name)} · ${esc(i.title)}" in js
    assert "function storeTitle(r, name)" in js and "r.refused" in js and "got.stores.find((r) => r.moves).moves" in js
    assert "Nothing is moved: what the store in use holds stays where it is" in js
    assert "postgres: 'PostgreSQL', mariadb: 'MariaDB'" in js


def test_a_pick_naming_an_instance_that_cannot_be_used_is_refused_in_words(tmp_path, monkeypatch, capsys):
    from liminal_charts import cli
    from liminal_charts.gui.server import ChartApp
    from liminal_libs.config import load_settings

    main = tmp_path / "liminal.db"
    monkeypatch.setenv("LIMINAL_DB", str(main))
    assert cli.main(["stores", "pick", "massive", "documents", "postgres:nowhere"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("massive's documents stay where they are: PostgreSQL has ")  # no such instance, or no plugin
    with Store(main, check_same_thread=False) as store:
        assert stores.picked(store, "massive", "documents") is None
        app = ChartApp(store, settings=load_settings(env={"LIMINAL_DB": str(main)}, env_file=None))
        with pytest.raises(ValueError, match="PostgreSQL has"):  # the route's 409, in these words
            app.set_store("massive", "documents", "postgres:nowhere")
        with pytest.raises(ValueError, match="PostgreSQL has no store of its own here"):
            app.set_store("massive", "documents", "postgres")
        row = app.plugin_stores("massive")["stores"][1]
        assert [e["name"] for e in row["engines"]] == ["mongodb", "sqlite"] and isinstance(row["instances"], list)
        assert "moves nothing" in row["moves"] and row["effect"] == "sqlite"


def test_the_hourly_sync_starts_a_history_run(tmp_path, monkeypatch):
    from liminal_charts import cli
    from liminal_libs.config import load_settings

    settings = load_settings(env={"LIMINAL_DB": str(tmp_path / "liminal.db")}, env_file=None)
    started, lines = [], []
    monkeypatch.setattr(cli, "start_history", lambda db_path: started.append(1) or 1)
    cli._stores_pass(settings, lines.append)
    assert started == [1] and lines[-1].startswith("history: started copying the closed months of ")
    with Store(settings.db_path) as s:
        for p in ("massive", "alpaca", "tiingo", "coinbase", "bitstamp", "bitfinex", "kraken", "gemini", "cboe"):
            if "bars" in stores.plugin_types(p):
                stores.pick(s, p, "bars", "sqlite")
        left = stores.bars_feeds(s)
    started.clear()
    cli._stores_pass(settings, lines.append)
    assert bool(started) == bool(left)


def test_the_commands(tmp_path, monkeypatch, capsys):
    from liminal_charts import cli

    monkeypatch.setenv("LIMINAL_DB", str(tmp_path / "liminal.db"))
    assert cli.main(["stores"]) == 0
    out = capsys.readouterr().out
    assert "massive      Bars       DuckDB   optimal here: DuckDB" in out
    assert "federal      Documents  SQLite   optimal here: SQLite" in out
    assert cli.main(["stores", "pick", "massive", "bars", "sqlite"]) == 0
    assert "massive      Bars       SQLite   picked" in capsys.readouterr().out
    assert cli.main(["history"]) == 0
    assert cli.main(["mongo"]) == 0
    assert "MongoDB (not installed): not running" in capsys.readouterr().out
