"""The Trading Desk (Monday list item 37; the user's design, Trading_Desk.docx): the worked case exactly, the return
calculations, constraints in their own units, authority routed to owners, unknowns kept unknown, real orders off, one
order per idempotency key, and the API and the view drawing the same evaluation. Stand-ins and tmp stores only: no
broker, no network, the paper account over an in-memory file or the test's own."""
from __future__ import annotations

import json
import sqlite3
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest

from liminal_charts.trading import paper
from liminal_charts.trading.desk import Conflict, Desk, NoAuthority, RealOrdersOff, annualized, real_return
from liminal_charts.trading.paper import PaperBook
from liminal_charts.gui import desk as routes

ET = "America/New_York"
MIN = 60_000
NOW = int(pd.Timestamp("2026-10-14 10:00", tz=ET).timestamp() * 1000)
STATIC = Path(routes.__file__).parent / "static"


class Market:
    """Quotes and bars a test sets: the stand-in of the stored bars (Massive's)."""

    def __init__(self):
        self.quotes: dict[str, tuple[float, int]] = {}

    def quote(self, symbol):
        if symbol not in self.quotes:
            return None
        px, at = self.quotes[symbol]
        return {"price": px, "asOf": at, "source": "stand-in"}

    def after(self, source, ticker, at, until):  # no bars stored after the order: it stays working
        return pd.DataFrame(columns=["t", "open", "high", "low", "close"]), MIN

    def last(self, source, ticker):
        return self.quotes.get(ticker, (None,))[0]


@pytest.fixture
def clock(monkeypatch):
    c = {"now": NOW}
    monkeypatch.setattr(paper, "_now", lambda: c["now"])
    return c


@pytest.fixture
def d(clock):
    m = Market()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    book = PaperBook(conn, m.after, m.last)
    book.reset(35_000, now_ms=NOW - 1000)  # the worked case: $35,000 of settled cash, no other commitments
    desk = Desk(conn, book, m.quote, asset=lambda s: "etf" if s in ("QQQ", "SPY") else "stock")
    desk.market = m
    m.quotes["QQQ"] = (500.0, NOW - 30_000)  # a hypothetical $500, half a minute old
    return desk


CASE = {"symbol": "QQQ", "horizon": "1_session", "quantity": 100, "entry": 500, "target": 510, "invalidation": 495,
        "gap": 485, "benchmark": "cash", "currency": "USD"}


def _ev(d, **more):
    return d.evaluate({"account_id": d.paper_id(), **CASE, **more})


# -- the document's worked case -----------------------------------------------------------------------------------------
def test_the_worked_case_sizes_70_shares_bound_by_settled_cash(d):
    ev = _ev(d)
    s = ev["sizing"]
    by = {c["id"]: c for c in s["constraints"]}
    assert by["desk.equities.exposure"]["feasibleQty"] == 120 and by["desk.equities.exposure"]["threshold"] == 60_000
    assert by["account.concentration.issuer"]["feasibleQty"] == 80
    assert by["broker.settled_cash"]["feasibleQty"] == 70 and by["broker.settled_cash"]["threshold"] == 35_000
    assert s["feasibleQty"] == 70 and s["binding"] == ["broker.settled_cash"] and s["executableQty"] == 70
    assert by["broker.settled_cash"]["toFit"] == {"reduceTo": 70, "raiseBy": 15_000, "unit": "USD cash",
                                                 "owner": "Broker (external): the paper account here"}
    for c in ("desk.equities.exposure", "account.concentration.issuer", "broker.settled_cash"):
        assert by[c]["owner"]  # every constraint shown with its owner
    sc = {r["name"]: r for r in ev["scenarios"]}
    assert sc["target"]["requested"]["gross"] == 1_000 and sc["target"]["feasible"]["gross"] == 700
    assert sc["invalidation"]["requested"]["gross"] == -500 and sc["invalidation"]["feasible"]["gross"] == -350
    assert sc["gap"]["requested"]["gross"] == -1_500
    assert ev["beforeCosts"] is True and ev["decision"]["submits"] is False


def test_a_reservation_by_another_desk_cuts_it_to_60_and_invalidates_the_old_preview(d):
    acct = d.paper_id()
    old = d.preview({"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 70, "price": 500})
    assert old["status"] == "valid" and old["sizing"]["feasibleQty"] == 70
    d.reserve({"account_id": acct, "desk": "Options desk", "amount": 5_000})
    assert d.preview_of(old["id"])["status"] == "invalidated"
    assert _ev(d)["sizing"]["feasibleQty"] == 60
    with pytest.raises(Conflict):
        d.order({"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 70, "price": 500,
                 "preview_id": old["id"], "authorized": True}, "k-1")
    new = d.preview({"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 70, "price": 500})
    assert new["status"] == "blocked" and new["sizing"]["feasibleQty"] == 60  # a new decision, at the changed size
    kinds = [e["kind"] for e in d.events(acct)["events"]]
    assert "commitment.reserved" in kinds and "preview.invalidated" in kinds


def test_two_desks_cannot_spend_the_same_dollars(d):
    acct = d.paper_id()
    ask = {"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 70, "price": 500}
    a, b = d.preview(ask), d.preview(ask)  # both checked against the same $35,000
    first, placed = d.order({**ask, "preview_id": a["id"], "authorized": True}, "desk-a")
    assert placed and first["status"] == "accepted"
    with pytest.raises(Conflict):  # the first order's working buy now holds the cash: b is out of date
        d.order({**ask, "preview_id": b["id"], "authorized": True}, "desk-b")
    assert d.snapshot(acct)["available"] == 0


# -- the return calculations ----------------------------------------------------------------------------------------
def test_twenty_years_100_to_400_is_7_18_a_year_and_4_06_real_at_3_inflation():
    nominal = annualized(100, 400, 20)
    assert round(nominal * 100, 2) == 7.18
    assert round(real_return(nominal, 0.03) * 100, 2) == 4.06


def test_a_long_horizon_evaluation_shows_annualized_and_real(d):
    d.market.quotes["SPY"] = (100.0, NOW - 1000)
    ev = d.evaluate({"account_id": d.paper_id(), "symbol": "SPY", "horizon": "20_years", "quantity": 1, "entry": 100,
                     "target": 400, "assumptions": {"inflation": 0.03}})
    assert round(ev["returns"]["annualized"] * 100, 2) == 7.18 and round(ev["returns"]["real"] * 100, 2) == 4.06
    assert ev["horizon"]["kind"] == "long" and any(g.startswith("earnings") for g in ev["gaps"])
    short = d.evaluate({"account_id": d.paper_id(), "symbol": "SPY", "horizon": "1_session", "quantity": 1,
                        "entry": 100, "target": 400})
    assert short["horizon"]["reviewAt"] != ev["horizon"]["reviewAt"]  # changing horizon recalculates the review date


# -- constraints in their own units ---------------------------------------------------------------------------------
def test_constraints_in_different_units_are_never_compared_raw(d):
    with pytest.raises(ValueError, match="never compared raw"):
        d.add_rule("desk.delta", {"owner": "CRO and market risk", "measure": "equity_delta", "unit": "USD notional",
                                  "threshold": 65, "scope": {}, "severity": "block"})
    d.add_rule("desk.delta", {"owner": "CRO and market risk", "measure": "equity_delta",
                              "unit": "delta (share equivalents)", "threshold": 65, "scope": {}, "severity": "block"})
    s = _ev(d)["sizing"]
    by = {c["id"]: c for c in s["constraints"]}
    # a 65-delta limit binds at 65 shares: its threshold (65) is never set against the $35,000 or the $60,000 raw
    assert by["desk.delta"]["unit"] == "delta (share equivalents)" and by["desk.delta"]["feasibleQty"] == 65
    assert s["feasibleQty"] == 65 and s["binding"] == ["desk.delta"]
    assert {c["unit"] for c in s["constraints"]} >= {"USD notional", "USD cash", "shares", "delta (share equivalents)"}


# -- authority: delegated changes, parents to their owners ----------------------------------------------------------
def test_a_parent_cap_change_routes_to_its_owner(d):
    sup = "Equities desk supervisor"
    tight = d.review({"rule_id": "desk.equities.exposure", "threshold": 50_000, "requested_by": sup,
                      "rationale": "less risk into the close"})
    assert tight["status"] == "applied" and d.rule("desk.equities.exposure")["threshold"] == 50_000
    beyond = d.review({"rule_id": "desk.equities.exposure", "threshold": 150_000, "requested_by": sup,
                       "rationale": "more room"})  # beyond the firm's 100,000: the parent's owner decides
    assert beyond["status"] == "pending" and beyond["routedTo"] == "CRO and market risk"
    parent = d.review({"rule_id": "firm.exposure", "threshold": 200_000, "requested_by": sup, "rationale": "growth"})
    assert parent["status"] == "pending" and parent["routedTo"] == "CRO and market risk"
    assert d.rule("firm.exposure")["threshold"] == 100_000  # nothing changed until its owner decides
    with pytest.raises(NoAuthority):
        d.decide(parent["id"], {"by": sup, "decision": "approve", "reason": "mine"})
    done = d.decide(parent["id"], {"by": "CRO and market risk", "decision": "approve", "reason": "within appetite"})
    assert done["status"] == "approved" and d.rule("firm.exposure")["threshold"] == 200_000
    assert d.rule("firm.exposure")["version"] == 2
    with pytest.raises(NoAuthority):  # external broker limits remain binding
        d.review({"rule_id": "broker.settled_cash", "threshold": 1e9, "requested_by": "CRO and market risk",
                  "rationale": "x"})


def test_an_old_evaluation_reads_as_it_was_after_the_policy_changes(d):
    ev = _ev(d)
    d.review({"rule_id": "desk.equities.exposure", "threshold": 20_000, "requested_by": "Equities desk supervisor",
              "rationale": "tighter"})
    again = d.evaluation(ev["id"])
    assert again["sizing"] == ev["sizing"] and again["policyVersion"] == ev["policyVersion"]
    assert again["current"]["policyChangedSince"] is True
    assert _ev(d)["sizing"]["binding"] == ["desk.equities.exposure"]  # evaluated now: the new cap binds at 40


def test_an_exception_holds_for_its_scope_until_it_expires(d, clock):
    acct = d.paper_id()
    r = d.review({"kind": "exception", "rule_id": "account.concentration.issuer", "threshold": 60_000,
                  "requested_by": "Client", "rationale": "a hedge elsewhere", "scope": {"account": acct, "symbol": "QQQ"},
                  "expires_at": NOW + 3_600_000})
    assert r["routedTo"] == "CRO and market risk" and r["status"] == "pending"
    d.decide(r["id"], {"by": "CRO and market risk", "decision": "approve", "reason": "for one session"})
    by = {c["id"]: c for c in _ev(d)["sizing"]["constraints"]}
    assert by["account.concentration.issuer"]["threshold"] == 60_000 and by["account.concentration.issuer"]["exception"]
    clock["now"] = NOW + 3_600_001
    d.market.quotes["QQQ"] = (500.0, clock["now"] - 1000)
    by = {c["id"]: c for c in _ev(d)["sizing"]["constraints"]}
    assert by["account.concentration.issuer"]["threshold"] == 40_000


# -- unknowns stay unknown ------------------------------------------------------------------------------------------
def test_a_stale_quote_is_shown_as_unknown(d):
    d.market.quotes["QQQ"] = (500.0, NOW - 20 * MIN)
    ev = _ev(d)
    assert ev["quote"]["status"] == "stale" and ev["quote"]["price"] is None and ev["quote"]["lastSeen"] == 500
    assert ev["sizing"]["executableQty"] is None and ev["sizing"]["exploratoryOnly"]
    assert ev["sizing"]["feasibleQty"] == 70  # the exploratory analysis is kept, at the entry assumed
    assert any(g.startswith("quote stale") for g in ev["gaps"])
    market = d.preview({"account_id": d.paper_id(), "symbol": "QQQ", "side": "buy", "quantity": 10})
    assert market["status"] == "indeterminate"  # a market order needs a current quote
    margin = _ev(d, margin=True)
    assert margin["sizing"]["executableQty"] is None and margin["financing"]["borrowed"] is None


# -- orders: paper only, authorized, once per key ---------------------------------------------------------------------
def test_an_order_on_a_non_paper_account_is_refused(d):
    for acct in ("ibkr", "ibkr-U1234567", "", "live-1"):
        with pytest.raises(RealOrdersOff, match="real orders are off"):
            d.order({"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 1, "authorized": True}, "k")
    status, body = _answer(d, "POST", "/v1/orders", {"account_id": "ibkr-U1", "symbol": "QQQ", "side": "buy",
                                                     "quantity": 1, "authorized": True})
    assert status == 403 and body["error"] == "real orders are off"


def test_retries_with_the_same_idempotency_key_place_once(d):
    acct = d.paper_id()
    ask = {"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 10, "price": 500}
    p = d.preview(ask)
    order = {**ask, "preview_id": p["id"], "authorized": True}
    first, placed = d.order(order, "retry-1")
    again, placed2 = d.order(order, "retry-1")
    assert placed and not placed2 and again["order"]["id"] == first["order"]["id"]
    assert d.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    with pytest.raises(Conflict):
        d.order({**order, "quantity": 11}, "retry-1")  # that key is another request's
    with pytest.raises(NoAuthority):
        d.order({**order, "authorized": False}, "no-auth")


def test_an_order_placed_by_a_try_that_ended_before_its_record_is_answered_on_the_retry(d, clock):
    """Item 14's review (A2): the paper order was placed (committed) and the try ended before the desk's row was written
    (a kill, a stop, a crash). The retry under the same key found no row and was checked again: the checks counted the
    order's own working buy, so its preview was out of date (409, and invalidated; expired, had two minutes passed). The
    order stayed working with no record, and the client was told it failed. The paper order carries the key: the retry
    finds it before the checks, records it as the try would have, and is answered with it, nothing placed again."""
    acct = d.paper_id()
    ask = {"account_id": acct, "symbol": "QQQ", "side": "buy", "quantity": 10, "price": 500}
    place = d.book.place

    def ends_after_placing(*a, **k):
        place(*a, **k)
        raise RuntimeError("the process ended here")  # the paper order committed, the desk's row never written

    def ended(key, qty):
        p = d.preview({**ask, "quantity": qty})
        order = {**ask, "quantity": qty, "preview_id": p["id"], "authorized": True}
        d.book.place = ends_after_placing
        try:
            with pytest.raises(RuntimeError):
                d.order(order, key)
        finally:
            d.book.place = place
        assert not d.conn.execute("SELECT 1 FROM desk_orders WHERE key=?", (key,)).fetchone()
        return p, order

    def placed_under(key):
        return [r[0] for r in d.conn.execute("SELECT id FROM paper_orders WHERE client_id=?", (f"v1:{key}",))]

    # retried at once: the checks would count its own working buy
    p, order = ended("ended-1", 10)
    again, placed = d.order(order, "ended-1")
    (oid,) = placed_under("ended-1")  # one paper order: none placed again
    assert not placed and again["status"] == "accepted" and again["order"]["id"] == oid and again["key"] == "ended-1"
    assert again["orderStatus"] == "open" and d.preview_of(p["id"])["status"] == "used"
    assert [e["kind"] for e in d.events(acct)["events"]].count("order.accepted") == 1
    third, placed3 = d.order(order, "ended-1")  # recorded: from now on a retry as any other
    assert not placed3 and third["order"]["id"] == oid
    # retried after the preview's two minutes
    p, order = ended("ended-2", 10)
    clock["now"] = NOW + 121_000
    again, placed = d.order(order, "ended-2")
    assert not placed and again["order"]["id"] == placed_under("ended-2")[0] and d.preview_of(p["id"])["status"] == "used"
    # another request under a key whose try ended so: refused, as it is with the row there, and nothing recorded
    p, order = ended("ended-3", 5)
    with pytest.raises(Conflict, match="another request's"):
        d.order({**order, "quantity": 6}, "ended-3")
    assert not d.conn.execute("SELECT 1 FROM desk_orders WHERE key='ended-3'").fetchone()
    assert d.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 3


def test_saving_a_thesis_never_submits_an_order(d):
    _ev(d, decision="paper_trade", rationale="a bounce")
    assert d.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    with pytest.raises(ValueError):
        _ev(d, decision="submit")


def test_events_resume_by_cursor(d):
    acct = d.paper_id()
    _ev(d)
    first = d.events(acct)
    assert first["events"] and first["cursor"] == first["events"][-1]["seq"]
    d.reserve({"account_id": acct, "desk": "FX desk", "amount": 1})
    later = d.events(acct, first["cursor"])
    assert [e["kind"] for e in later["events"]] == ["commitment.reserved"]
    assert d.events(acct, later["cursor"])["events"] == []


# -- the API and the view: one source of decision facts -----------------------------------------------------------------
def _answer(d, method, path, body=None, query=None, headers=None):
    try:
        return routes.answer(d, method, path, query or {}, body or {}, headers)
    except RealOrdersOff as e:
        return 403, {"error": str(e)}


def test_the_api_returns_the_same_evaluation_it_saved(d):
    status, made = _answer(d, "POST", "/v1/evaluations", {"account_id": d.paper_id(), **CASE})
    assert status == 201 and made["id"].startswith("e-")
    status, got = _answer(d, "GET", f"/v1/evaluations/{made['id']}")
    assert status == 200 and got["id"] == made["id"] and got["assumptions"] == made["assumptions"]
    assert got["sizing"]["binding"] == made["sizing"]["binding"] == ["broker.settled_cash"]
    status, listed = _answer(d, "GET", "/v1/evaluations")
    assert listed["evaluations"][0]["id"] == made["id"]
    status, ov = _answer(d, "GET", f"/v1/accounts/{d.paper_id()}/overview")
    assert ov["account"]["settledCash"] == 35_000 and ov["account"]["kind"] == "paper"


def test_the_view_draws_the_evaluation_the_api_returns():
    js = (STATIC / "desk.js").read_text(encoding="utf-8")
    assert "/v1/evaluations/${encodeURIComponent(id)}" in js  # the view reads it back from the API, by its id
    assert "ev.id" in js and "binding" in js and "/v1/orders" in js and "Idempotency-Key" in js
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="viewDesk"' in html and 'data-pane="desk"' in html and '<script src="desk.js"></script>' in html
    panels = (STATIC / "panels.js").read_text(encoding="utf-8")
    assert "desk: 'desk'" in panels and "['desk', 'Desk'," in panels


# -- the routes on the charts' server (an in-process server over a tmp store, as test_paper's) -----------------------------
@pytest.fixture
def server(tmp_path, monkeypatch):
    from liminal_libs import derive
    from liminal_libs.storage import Store
    from liminal_charts.gui.server import ChartApp, make_handler

    monkeypatch.setattr(paper, "_now", lambda: NOW + 90_000)
    t0 = int(pd.Timestamp("2026-10-14 09:30", tz=ET).timestamp() * 1000)
    with Store(tmp_path / "m.db", check_same_thread=False) as s:
        rows = [{"t": t0 + i * MIN, "o": 10.0, "h": 10.05, "l": 9.95, "c": 10.0, "v": 100.0, "n": 3} for i in range(31)]
        s.upsert_bars("massive", "SPCX", "minute", 1, rows)
        s.watch("massive", ["SPCX"], "minute")
        derive.run(s, log=lambda *_: None)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ChartApp(s), [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield httpd.server_address[1]
        httpd.shutdown()


def _call(port, method, path, body=None, header=True, extra=None):
    c = HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", **(extra or {})}
    if header:
        headers["X-Liminal-Charts"] = "1"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"null") if r.getheader("Content-Type") == "application/json" else None


def test_the_v1_routes_on_the_charts_server(server):
    status, accts = _call(server, "GET", "/v1/accounts")
    assert status == 200 and accts["accounts"][0]["kind"] == "paper"
    acct = accts["accounts"][0]["id"]
    status, ov = _call(server, "GET", f"/v1/accounts/{acct}/overview")
    assert status == 200 and ov["account"]["settledCash"] == paper.DEFAULT_CASH
    ask = {"account_id": acct, "symbol": "SPCX", "horizon": "1_session", "quantity": 100, "entry": 10, "target": 10.5}
    status, ev = _call(server, "POST", "/v1/evaluations", ask)
    assert status == 201 and ev["quote"]["status"] == "current" and ev["quote"]["price"] == 10.0
    status, got = _call(server, "GET", f"/v1/evaluations/{ev['id']}")
    assert status == 200 and got["id"] == ev["id"] and got["sizing"] == ev["sizing"]
    status, _ = _call(server, "POST", "/v1/evaluations", ask, header=False)
    assert status == 403  # the write header, as every write of the charts' API
    status, _ = _call(server, "GET", "/v1/accounts", extra={"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.test"})
    assert status == 403  # another site's page reads nothing
    status, off = _call(server, "POST", "/v1/orders", {"account_id": "ibkr-U1", "symbol": "SPCX", "side": "buy",
                                                       "quantity": 1, "authorized": True})
    assert status == 403 and off["error"] == "real orders are off"
    order = {"account_id": acct, "symbol": "SPCX", "side": "buy", "quantity": 5, "price": 10}
    status, p = _call(server, "POST", "/v1/previews", order)
    assert status == 201 and p["status"] == "valid"
    key = {"Idempotency-Key": "page-1"}
    s1, o1 = _call(server, "POST", "/v1/orders", {**order, "preview_id": p["id"], "authorized": True}, extra=key)
    s2, o2 = _call(server, "POST", "/v1/orders", {**order, "preview_id": p["id"], "authorized": True}, extra=key)
    assert (s1, s2) == (201, 200) and o1["order"]["id"] == o2["order"]["id"]
    status, trading = _call(server, "GET", "/api/trading")
    assert [o["id"] for o in trading["orders"]] == [o1["order"]["id"]]  # the existing paper account holds it
    status, events = _call(server, "GET", f"/v1/events?account={acct}&cursor=0")
    assert status == 200 and "order.accepted" in [e["kind"] for e in events["events"]]
    status, _ = _call(server, "GET", "/v1/nothing")
    assert status == 404


def test_the_trading_ticket_takes_no_name_of_the_desks(server):
    """The desk's paper orders are named v1:<its key>, and a retry whose try ended before its record takes the paper
    order under that name for its own (A2): so the Trading ticket's names may not begin so."""
    ticket = {"ticker": "SPCX", "side": "buy", "qty": 1, "type": "market"}
    status, err = _call(server, "POST", "/api/trading/orders", {**ticket, "clientId": "v1:page-1"})
    assert status == 400 and "Trading Desk" in err["error"]
    status, trading = _call(server, "GET", "/api/trading")
    assert trading["orders"] == []
    status, o = _call(server, "POST", "/api/trading/orders", {**ticket, "clientId": "page-1"})
    assert status == 201 and o["client_id"] == "page-1"


def test_the_front_passes_v1_reads_to_the_integrator():
    src = (Path(routes.__file__).parent / "front.py").read_text(encoding="utf-8")
    assert 'path.startswith(("/api/", "/v1/"))' in src
