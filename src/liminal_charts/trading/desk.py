"""The Trading Desk (Monday list item 37; the user's design, Trading_Desk.docx, built on the user's yes of 2026-11-25:
"build it as designed there, read-only and paper first"): a decision-support layer over a broker's account.

One analysis, three ways in: the workspace (the rail's Desk view, desk.js), the client API (/v1/..., gui/desk.py) and
the market and account interfaces (the stored bars here, Massive's; a broker's snapshot, none connected). Each
evaluation is saved whole, with its assumptions, the policy version, the rules as they were and the evidence, so an old
decision reads as it was decided after the rules or the assumptions change.

What is here:
- accounts: the paper account (paper.py), simulated, its balances its own; a broker's account only as an interface
  (BrokerSnapshot), with no implementation: none is connected, and real orders are off (the user's rule)
- constraints: rules with an id, owner, scope, unit, threshold, effective dates, version, severity and response, each
  checked in its own unit against holdings, pending commitments and the proposal; the largest feasible quantity across
  all of them, the binding rule named; quantities are what is compared, never a dollar cap against a share count
- reviews: a change or an exception routed to the rule's owner (a tightening, or a relaxing within the parent, by the
  role it is delegated to is applied at once); approval, rejection or expiry, with a reason; a broker's limit stays
- previews: the current checks for one order, expiring, made invalid by any change to what they were checked against
  (a reservation, an order, a rule)
- orders: through the paper account only, with explicit authorization, a current preview and an idempotency key
- events: every change, in order, read from a cursor
- the return calculations the document gives: annualized, real at an assumed inflation, short trades in dollars and
  percent over the holding period

Unknown stays unknown: a stale or missing quote, a position without a price, a margin request with no broker to ask
leave the executable size indeterminate (None), the exploratory analysis kept with its gaps marked.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from . import paper

ET = ZoneInfo("America/New_York")
DAY_MS = 86_400_000
PREVIEW_S = 120  # a preview is current this long (then asked again)
QUOTE_MAX_AGE_S = 15 * 60  # a quote older than this is stale: shown, its value unknown for sizing
REVIEW_DAYS = 7  # a review not decided in this long expires
THESIS_REVIEW_DAYS = 90  # a long holding's thesis is reviewed quarterly unless a date is given (a stated assumption)
REAL_ORDERS_OFF = "real orders are off"  # the user's rule: no order reaches a real account
PAPER_SOURCE = "paper book (simulated, filled from the stored bars)"

SEVERITIES = ("info", "review", "block")
RESPONSES = {"info": "warn", "review": "request review", "block": "block"}


class RealOrdersOff(PermissionError):
    """An order for an account that is not a paper one: real orders are off (the user's rule)."""


class NoAuthority(PermissionError):
    """A role deciding what is not its to decide (viewing a role confers no authority)."""


class Conflict(RuntimeError):
    """The request is out of date or collides with another (409), with what is current now."""

    def __init__(self, message: str, **payload):
        super().__init__(message)
        self.payload = {"error": message, **payload}


class BrokerSnapshot(Protocol):
    """A broker's account, read only: what a broker plugin gives the desk (Interactive Brokers later, not now). None is
    implemented; balances, custody, settlement and the final status of an order stay with the broker."""

    key: str
    label: str
    live: bool

    def snapshot(self, account_id: str) -> dict: ...  # currency, settled cash, loans, positions and lots, open orders,
    # buying power, account type, settlement status, permissions, each with its source and time
    def margin_preview(self, account_id: str, order: dict) -> dict: ...  # the broker's own terms for one order


PLANNED_BROKERS = [{"key": "ibkr", "label": "Interactive Brokers", "live": True, "connected": False,
                    "status": "planned, not connected: real orders are off"}]


# -- the return calculations the document gives ------------------------------------------------------------------------
def annualized(start: float, end: float, years: float) -> float:
    """(end / start) ^ (1 / years) - 1: a fully funded position with no interim cash flows."""
    if not (start > 0 and end > 0 and years > 0):
        raise ValueError("annualized return: start and end above 0, over more than 0 years")
    return (end / start) ** (1 / years) - 1


def real_return(nominal: float, inflation: float) -> float:
    """The real rate at an assumed inflation: (1 + nominal) / (1 + inflation) - 1."""
    if inflation <= -1:
        raise ValueError("inflation above -100%")
    return (1 + nominal) / (1 + inflation) - 1


def trade_outcome(side: str, qty: float, entry: float, exit: float, cost: float = 0.0) -> dict:
    """A short trade's outcome over its holding period: dollars and percent of the money put in, before or after a
    cost (stated apart)."""
    sign = 1 if side == "buy" else -1
    gross = sign * qty * (exit - entry)
    base = qty * entry
    return {"price": exit, "gross": round(gross, 2), "net": round(gross - cost, 2),
            "pct": round(gross / base * 100, 4) if base else None}


# -- the market session (US stocks: the regular session) ----------------------------------------------------------------
def market_session(now_ms: int) -> dict:
    from liminal_libs.sessions import is_nyse_holiday, nyse_early_close

    at = datetime.fromtimestamp(now_ms / 1000, ET)
    d = at.date()
    trading = d.weekday() < 5 and not is_nyse_holiday(d)
    if trading:
        opens = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
        closes = datetime(d.year, d.month, d.day, 13 if nyse_early_close(d) else 16, tzinfo=ET)
        if opens <= at < closes:
            return {"market": "US stocks", "session": "regular", "open": True, "closesAt": int(closes.timestamp() * 1000)}
    return {"market": "US stocks", "session": "closed", "open": False, "nextClose": paper.day_end(now_ms)}


# -- constraints --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Measure:
    unit: str
    what: str


# What each rule measures, and the one unit it is measured in: a rule in another unit is refused, never compared raw.
MEASURES = {
    "gross_exposure": Measure("USD notional", "the gross value of holdings in scope, working buys and the proposal"),
    "issuer_exposure": Measure("USD notional", "one issuer's holding, its working buys and the proposal"),
    "settled_cash": Measure("USD cash", "settled cash less reservations and working buys, against the cost"),
    "short_shares": Measure("shares", "shares sold beyond the shares held"),
    "order_notional": Measure("USD notional", "one order's value"),
    "equity_delta": Measure("delta (share equivalents)", "the stock delta held in scope and the proposal's"),
}

# The illustrative policy of the document's worked case (Trading_Desk.docx, "How the processor behaves"): changed only
# through a review. Owners are the document's roles; one user explores them, and viewing one confers no authority.
DEFAULT_RULES = [
    {"id": "firm.exposure", "owner": "CRO and market risk", "measure": "gross_exposure", "unit": "USD notional",
     "threshold": 100_000, "scope": {}, "severity": "block",
     "note": "the firm's appetite for this account's gross exposure"},
    {"id": "desk.equities.exposure", "owner": "Head of trading and desk heads", "parent": "firm.exposure",
     "delegated_to": "Equities desk supervisor", "measure": "gross_exposure", "unit": "USD notional",
     "threshold": 60_000, "scope": {"asset_class": ["stock", "etf"]}, "severity": "block",
     "note": "the equities and ETFs desk's exposure, inside the firm's"},
    {"id": "account.concentration.issuer", "owner": "CRO and market risk", "measure": "issuer_exposure",
     "unit": "USD notional", "threshold": 40_000, "scope": {}, "severity": "block",
     "note": "the account's concentration in one issuer"},
    {"id": "broker.settled_cash", "owner": "Broker (external): the paper account here", "measure": "settled_cash",
     "unit": "USD cash", "threshold": None, "scope": {}, "severity": "block", "broker": True,
     "note": "a cash account: a buy is paid from settled cash (the threshold is the account's, read each time)"},
    {"id": "broker.no_short", "owner": "Broker (external): the paper account here", "measure": "short_shares",
     "unit": "shares", "threshold": 0, "scope": {}, "severity": "block", "broker": True,
     "note": "a cash account sells only what it holds"},
    {"id": "supervision.order_size", "owner": "Compliance, legal, AML and supervision", "measure": "order_notional",
     "unit": "USD notional", "threshold": 50_000, "scope": {}, "severity": "review",
     "note": "an order above this is reviewed before it is sent"},
]

ROLES = sorted({r["owner"] for r in DEFAULT_RULES} | {r["delegated_to"] for r in DEFAULT_RULES if r.get("delegated_to")}
               | {"Client"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS desk_policy (
    version     INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    why         TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS desk_rules (
    id          TEXT    NOT NULL,
    version     INTEGER NOT NULL,
    body        TEXT    NOT NULL,   -- JSON: owner, parent, delegated_to, measure, unit, threshold, scope, severity, ...
    effective_from INTEGER NOT NULL,
    effective_to   INTEGER,         -- ms: a later version took over
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS desk_commitments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT    NOT NULL,
    desk        TEXT    NOT NULL,
    amount      REAL    NOT NULL,   -- USD cash reserved
    note        TEXT,
    created_at  INTEGER NOT NULL,
    released_at INTEGER
);
CREATE TABLE IF NOT EXISTS desk_evaluations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    policy      INTEGER NOT NULL,
    body        TEXT    NOT NULL    -- JSON: the whole evaluation as it was made
);
CREATE TABLE IF NOT EXISTS desk_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    body        TEXT    NOT NULL,   -- JSON: rule, change or exception, scope, requested by, routed to, rationale
    status      TEXT    NOT NULL,   -- pending | applied | approved | rejected | expired
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    decided_at  INTEGER,
    decided_by  TEXT,
    reason      TEXT
);
CREATE TABLE IF NOT EXISTS desk_previews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT    NOT NULL,
    body        TEXT    NOT NULL,   -- JSON: the order, the checks, the fingerprint of what they read
    status      TEXT    NOT NULL,   -- valid | blocked | indeterminate | invalidated | expired | used
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS desk_orders (
    key         TEXT    PRIMARY KEY, -- the client's idempotency key: a retry returns this
    account     TEXT    NOT NULL,
    preview     INTEGER,
    request     TEXT    NOT NULL,   -- JSON: what was asked
    result      TEXT    NOT NULL,   -- JSON: what was answered
    paper_order INTEGER,
    status      TEXT    NOT NULL,   -- accepted | rejected | open | filled | canceled | expired
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS desk_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT,
    kind        TEXT    NOT NULL,
    at          INTEGER NOT NULL,
    data        TEXT    NOT NULL
);
"""

PAPER_STATUS = {"working": "open", "filled": "filled", "cancelled": "canceled", "expired": "expired",
                "rejected": "rejected"}
_HORIZON = re.compile(r"^(\d+(?:\.\d+)?)_(session|sessions|day|days|week|weeks|month|months|year|years)$")
_PAPER_ID = re.compile(r"^paper-(\d+)$")


def _j(data) -> str:
    return json.dumps(data, allow_nan=False, sort_keys=True)


def _num(v, name: str, positive: bool = True) -> float | None:
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: a number") from None
    if not math.isfinite(x) or (positive and x <= 0):
        raise ValueError(f"{name}: a number above 0")
    return x


def horizon(text: str, now_ms: int) -> dict:
    """A horizon named as '1_session', 'intraday', '20_days', '6_months', '20_years': its kind (session, medium, long),
    its length in years and the review date it implies."""
    text = (text or "").strip().lower()
    if text == "intraday":
        text = "1_session"
    m = _HORIZON.match(text)
    if not m:
        raise ValueError("horizon: like 1_session, 5_sessions, 30_days, 6_months or 20_years")
    n, unit = float(m.group(1)), m.group(2).rstrip("s")
    if n <= 0:
        raise ValueError("horizon: more than 0")
    years = {"session": n / 252, "day": n / 365, "week": n * 7 / 365, "month": n / 12, "year": n}[unit]
    kind = "session" if unit == "session" else "long" if years >= 1 else "medium"
    if kind == "session":
        end = now_ms
        for _ in range(int(math.ceil(n))):  # a close is the next session's start: each step one session on
            end = paper.day_end(end)
        review, why = end, "the close of the horizon's last session (the time exit unless one is given)"
    elif kind == "medium":
        review, why = now_ms + int(years * 365 * DAY_MS), "the end of the horizon"
    else:
        review, why = now_ms + THESIS_REVIEW_DAYS * DAY_MS, f"a thesis review every {THESIS_REVIEW_DAYS} days (assumed)"
    return {"text": text, "kind": kind, "years": years, "sessions": n if unit == "session" else None,
            "reviewAt": review, "reviewWhy": why}


# What each kind of horizon needs (the document's "Time horizon and targets"): what is missing is asked or assumed.
HORIZON_NEEDS = {
    "session": ("session", "catalyst", "invalidation", "time_exit"),
    "medium": ("catalyst", "invalidation", "review_date"),
    "long": ("earnings", "distributions", "valuation", "review_date"),
}


class Desk:
    """The desk over the paper account's store (its own tables beside paper.py's: an additive schema)."""

    def __init__(self, conn: sqlite3.Connection, book: paper.PaperBook, quote: Callable[[str], dict | None],
                 closes: Callable[[str, int], list | None] = lambda _s, _n: None,
                 asset: Callable[[str], str] = lambda _s: "stock", lock=None,
                 quote_max_age_s: int = QUOTE_MAX_AGE_S):
        self.conn, self.book, self.quote_of, self.closes_of, self.asset = conn, book, quote, closes, asset
        self.lock = lock if lock is not None else threading.RLock()
        self.quote_max_age_s = quote_max_age_s
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM desk_rules").fetchone()[0] == 0:
            with conn:
                for r in DEFAULT_RULES:
                    self._put_rule({k: v for k, v in r.items() if k != "id"}, r["id"], 1, 0)
                self._bump("the document's illustrative policy (Trading_Desk.docx)", 0)

    @staticmethod
    def now() -> int:
        return paper._now()

    # -- storage helpers ------------------------------------------------------------------------------------------
    def _put_rule(self, body: dict, rid: str, version: int, effective_from: int) -> None:
        m = MEASURES.get(body.get("measure"))
        if m is None:
            raise ValueError(f"rule {rid}: measure one of {', '.join(MEASURES)}")
        if body.get("unit") != m.unit:  # a dollar cap, a delta limit and a share count are never compared raw
            raise ValueError(f"rule {rid}: {body.get('measure')} is measured in {m.unit}, not {body.get('unit')!r}: "
                             "a limit is checked in its own unit, never compared raw with another")
        if body.get("severity") not in SEVERITIES:
            raise ValueError(f"rule {rid}: severity one of {', '.join(SEVERITIES)}")
        body = {**body, "response": RESPONSES[body["severity"]]}
        self.conn.execute("INSERT INTO desk_rules (id, version, body, effective_from) VALUES (?, ?, ?, ?)",
                          (rid, version, _j(body), effective_from))

    def _bump(self, why: str, at: int) -> int:
        return self.conn.execute("INSERT INTO desk_policy (at, why) VALUES (?, ?)", (at, why)).lastrowid

    def policy_version(self) -> int:
        return self.conn.execute("SELECT MAX(version) FROM desk_policy").fetchone()[0] or 0

    def _emit(self, account: str | None, kind: str, data: dict, at: int | None = None) -> None:
        self.conn.execute("INSERT INTO desk_events (account, kind, at, data) VALUES (?, ?, ?, ?)",
                          (account, kind, at or self.now(), _j(data)))

    # -- rules ----------------------------------------------------------------------------------------------------
    def rules(self, at: int | None = None) -> list[dict]:
        """Every rule in force at `at` (now): its newest version effective then."""
        at = self.now() if at is None else at
        out = []
        for rid, version, body, frm, to in self.conn.execute(
                "SELECT id, version, body, effective_from, effective_to FROM desk_rules WHERE effective_from <= ? AND "
                "(effective_to IS NULL OR effective_to > ?) ORDER BY id, version", (at, at)):
            out.append({"id": rid, "version": version, **json.loads(body), "effectiveFrom": frm, "effectiveTo": to})
        return out

    def rule(self, rid: str, at: int | None = None) -> dict:
        for r in self.rules(at):
            if r["id"] == rid:
                return r
        raise LookupError(f"no rule {rid}")

    def add_rule(self, rid: str, body: dict, at: int | None = None) -> dict:
        """A rule added to the policy (tests and setup; the page changes rules through reviews)."""
        at = self.now() if at is None else at
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM desk_rules WHERE id=?", (rid,)).fetchone():
                raise ValueError(f"rule {rid} exists: change it through a review")
            self._put_rule(body, rid, 1, at)
            v = self._bump(f"rule {rid} added", at)
            self._invalidate(None, f"policy version {v}: rule {rid} added", at)
            self._emit(None, "policy.changed", {"policyVersion": v, "rule": rid, "why": "added"}, at)
        return self.rule(rid, at)

    def _new_version(self, rule: dict, threshold: float, at: int, why: str) -> int:
        body = {k: v for k, v in rule.items() if k not in ("id", "version", "effectiveFrom", "effectiveTo")}
        body["threshold"] = threshold
        self.conn.execute("UPDATE desk_rules SET effective_to=? WHERE id=? AND effective_to IS NULL", (at, rule["id"]))
        self._put_rule(body, rule["id"], rule["version"] + 1, at)
        v = self._bump(why, at)
        self._invalidate(None, f"policy version {v}: {why}", at)
        self._emit(None, "policy.changed", {"policyVersion": v, "rule": rule["id"], "version": rule["version"] + 1,
                                            "threshold": threshold, "why": why}, at)
        return v

    def exceptions(self, at: int | None = None) -> list[dict]:
        """The exceptions approved and in force: each a rule's threshold for a scope, until its expiry."""
        at = self.now() if at is None else at
        out = []
        for rid, body, decided_by, decided_at in self.conn.execute(
                "SELECT id, body, decided_by, decided_at FROM desk_reviews WHERE status='approved'"):
            b = json.loads(body)
            if b.get("kind") == "exception" and b["effectiveFrom"] <= at < b["expiresAt"]:
                out.append({"review": rid, "rule": b["rule"], "threshold": b["threshold"], "scope": b.get("scope") or {},
                            "approver": decided_by, "approvedAt": decided_at, "effectiveFrom": b["effectiveFrom"],
                            "expiresAt": b["expiresAt"], "rationale": b.get("rationale")})
        return out

    # -- accounts -------------------------------------------------------------------------------------------------
    def paper_id(self) -> str:
        return f"paper-{self.book.account()['id']:02d}"

    def accounts(self) -> list[dict]:
        acct = self.book.account()
        return [{"id": self.paper_id(), "kind": "paper", "label": "Paper (simulated)", "currency": "USD",
                 "connected": True, "realOrders": False, "openedAt": acct["createdAt"]},
                *[{"id": f"{b['key']}", "kind": "broker", **b, "realOrders": False} for b in PLANNED_BROKERS]]

    def _paper(self, account_id: str) -> None:
        if not _PAPER_ID.match(str(account_id or "")):
            raise LookupError(f"{account_id}: no broker is connected (Interactive Brokers later); only the paper "
                              f"account ({self.paper_id()}) is here")
        if account_id != self.paper_id():
            raise LookupError(f"{account_id} is closed or unknown: the paper account now is {self.paper_id()}")

    def snapshot(self, account_id: str, now: int | None = None) -> dict:
        """The account's resources and obligations now, each with its source and age (the paper book's: simulated,
        settled at the fill)."""
        self._paper(account_id)
        now = self.now() if now is None else now
        book = self.book.summary(now)
        a = book["account"]
        commitments = [{"id": i, "desk": d, "amount": amt, "note": n, "createdAt": c} for i, d, amt, n, c in
                       self.conn.execute("SELECT id, desk, amount, note, created_at FROM desk_commitments WHERE "
                                         "account=? AND released_at IS NULL ORDER BY id", (account_id,))]
        working = [o for o in book["orders"] if o["status"] == "working"]
        working_buys = 0.0
        for o in working:
            if o["side"] == "buy":
                px = o["limit_px"] or o["stop_px"]
                if px is None:
                    pos = next((p for p in book["positions"] if p["ticker"] == o["ticker"]), None)
                    q = self.quote(o["ticker"], now)
                    px = q["price"] if q["status"] == "current" else (pos or {}).get("last")
                working_buys = None if px is None or working_buys is None else working_buys + o["qty"] * px
        reserved = sum(c["amount"] for c in commitments)
        available = None if working_buys is None else round(a["cash"] - reserved - working_buys, 2)
        positions = [{"symbol": p["ticker"], "qty": p["qty"], "avg": p["avg"], "last": p["last"], "value": p["value"],
                      "assetClass": self.asset(p["ticker"]), "unrealized": p["unrealized"]} for p in book["positions"]]
        return {"id": account_id, "kind": "paper", "currency": "USD", "asOf": now, "source": PAPER_SOURCE,
                "settledCash": round(a["cash"], 2), "cash": round(a["cash"], 2), "loans": 0.0,
                "equity": round(a["equity"], 2), "feeBps": a["feeBps"],
                "commitments": commitments, "reserved": round(reserved, 2),
                "workingOrders": [{k: o[k] for k in ("id", "ticker", "side", "qty", "type", "limit_px", "stop_px",
                                                     "placed_at")} for o in working],
                "workingBuys": None if working_buys is None else round(working_buys, 2), "available": available,
                "positions": positions,
                "permissions": {"accountType": "cash", "short": False, "margin": False, "options": False,
                                "realOrders": False},
                "balancesAge": {"cash": 0, "positions": 0, "note": "the paper book is read at each request"}}

    def overview(self, account_id: str) -> dict:
        with self.lock:
            now = self.now()
            snap = self.snapshot(account_id, now)
            exposures = []
            eq = snap["equity"] or 0
            for p in snap["positions"]:
                exposures.append({"symbol": p["symbol"], "value": p["value"], "assetClass": p["assetClass"],
                                  "share": round(abs(p["value"]) / eq, 4) if p["value"] is not None and eq else None})
            return {"account": snap, "exposures": sorted(exposures, key=lambda e: -(abs(e["value"] or 0))),
                    "session": market_session(now), "policyVersion": self.policy_version(),
                    "brokers": PLANNED_BROKERS, "observedAt": now}

    # -- quotes and evidence --------------------------------------------------------------------------------------
    def quote(self, symbol: str, now: int | None = None) -> dict:
        """The newest stored price, with its source and time: current, stale (shown, its value unknown for sizing)
        or missing."""
        now = self.now() if now is None else now
        q = self.quote_of(symbol)
        if not q or q.get("price") is None:
            return {"symbol": symbol, "status": "missing", "price": None, "lastSeen": None, "asOf": None,
                    "source": None, "label": "observed"}
        age = (now - int(q["asOf"])) / 1000
        status = "current" if age <= self.quote_max_age_s else "stale"
        return {"symbol": symbol, "status": status, "price": float(q["price"]) if status == "current" else None,
                "lastSeen": float(q["price"]), "asOf": int(q["asOf"]), "ageS": round(age), "source": q.get("source"),
                "label": "observed"}

    def performance(self, symbol: str, days: int) -> dict:
        rows = self.closes_of(symbol, days + 1) or []
        if len(rows) < 2:
            return {"symbol": symbol, "days": days, "return": None, "from": None, "to": None,
                    "gap": "not enough stored daily bars"}
        (t0, c0), (t1, c1) = rows[0], rows[-1]
        return {"symbol": symbol, "days": len(rows) - 1, "return": round(c1 / c0 - 1, 6) if c0 else None,
                "from": int(t0), "to": int(t1), "label": "observed"}

    # -- the sizing core: every rule in its own unit, the largest feasible quantity across them --------------------
    def check(self, snap: dict, symbol: str, side: str, qty: float, price: float | None, at: int,
              margin: bool = False) -> dict:
        asset = self.asset(symbol)
        exceptions = self.exceptions(at)
        held = sum(p["qty"] for p in snap["positions"] if p["symbol"] == symbol)
        fee = (snap.get("feeBps") or 0) / 10_000
        rows = []
        for r in self.rules(at):
            if r.get("scope", {}).get("asset_class") and asset not in r["scope"]["asset_class"]:
                continue
            threshold, exc = r.get("threshold"), None
            for e in exceptions:
                if e["rule"] == r["id"] and all(e["scope"].get(k) in (None, v) for k, v in
                                                (("account", snap["id"]), ("symbol", symbol))):
                    threshold, exc = e["threshold"], e["review"]
            used, per, note = self._measure(r, snap, symbol, side, price, held, fee, asset)
            if r["measure"] == "settled_cash":
                threshold = snap.get("available")
            drawn = not (per is not None and per <= 0)  # a proposal that does not draw on the rule is within it
            known = not drawn or (used is not None and threshold is not None and per is not None)
            feasible = None
            if known and drawn:
                feasible = max(0, math.floor((threshold - used) / per + 1e-9))  # whole shares (a lot of 1)
            proposed = None if per is None else round(per * qty, 2)
            row = {"id": r["id"], "version": r["version"], "owner": r["owner"], "parent": r.get("parent"),
                   "delegatedTo": r.get("delegated_to"), "measure": r["measure"], "unit": r["unit"],
                   "threshold": threshold, "used": None if used is None else round(used, 2), "proposed": proposed,
                   "headroom": None if used is None or threshold is None else round(threshold - used, 2),
                   "feasibleQty": feasible, "limits": drawn, "severity": r["severity"], "response": r["response"],
                   "effectiveFrom": r["effectiveFrom"], "effectiveTo": r["effectiveTo"], "exception": exc,
                   "broker": bool(r.get("broker")), "note": note or r.get("note")}
            if not known:
                row["status"] = "unknown"
            elif drawn and qty > feasible:
                row["status"] = "fails"
                row["toFit"] = {"reduceTo": feasible, "raiseBy": round(used + per * qty - threshold, 2),
                                "unit": r["unit"], "owner": r["owner"]}
            else:
                row["status"] = "ok"
            rows.append(row)
        blocks = [r for r in rows if r["severity"] == "block"]
        unknown = [r["id"] for r in blocks if r["status"] == "unknown"]
        known_q = [r["feasibleQty"] for r in blocks if r["feasibleQty"] is not None]
        feasible = min(known_q) if known_q else None
        binding = [r["id"] for r in blocks if r["feasibleQty"] is not None and r["feasibleQty"] == feasible]
        for r in rows:
            if r["id"] in binding and r["status"] == "ok":
                r["status"] = "binding"
            elif r["id"] in binding:
                r["binding"] = True
        reviews = [r["id"] for r in rows if r["severity"] == "review" and r["status"] == "fails"]
        warnings = [r["id"] for r in rows if r["severity"] == "info" and r["status"] == "fails"]
        why = []
        if unknown:
            why.append(f"unknown inputs for {', '.join(unknown)}")
        if price is None:
            why.append("no current price (the quote is stale or missing)")
        if margin:
            why.append("margin terms need the broker's own check: none is connected")
        executable = None if why or feasible is None else min(qty, feasible)
        return {"symbol": symbol, "side": side, "requestedQty": qty, "price": price,
                "feasibleQty": feasible, "exploratoryOnly": bool(why), "binding": binding,
                "failing": [r["id"] for r in rows if r["status"] == "fails" and r["severity"] == "block"],
                "reviewRequired": reviews, "warnings": warnings, "executableQty": executable,
                "indeterminate": why, "constraints": rows}

    def _measure(self, r: dict, snap: dict, symbol: str, side: str, price: float | None, held: float, fee: float,
                 asset: str) -> tuple[float | None, float | None, str | None]:
        """(used, per share, a note) in the rule's own unit. per <= 0: the proposal does not draw on it."""
        m = r["measure"]
        buy = side == "buy"
        if m in ("gross_exposure", "issuer_exposure"):
            scope = r.get("scope", {}).get("asset_class")
            pos = [p for p in snap["positions"] if (m == "gross_exposure" and (not scope or p["assetClass"] in scope))
                   or (m == "issuer_exposure" and p["symbol"] == symbol)]
            if any(p["value"] is None for p in pos):
                return None, None, "a holding without a current price: its exposure is unknown"
            used = sum(abs(p["value"]) for p in pos)
            pend = 0.0
            for o in snap["workingOrders"]:
                if o["side"] == "buy" and (m == "gross_exposure" or o["ticker"] == symbol):
                    px = o["limit_px"] or o["stop_px"]
                    if px is None:
                        return None, None, "a working market order without a current price"
                    pend += o["qty"] * px
            used += pend
            if price is None:
                return used, None, None
            if buy or held <= 0:
                return used, price, None
            return used, 0.0, "a sale of what is held reduces this exposure (it is evaluated on its own)"
        if m == "settled_cash":
            if not buy:
                return 0.0, 0.0, "a sale does not draw on cash"
            return 0.0, None if price is None else price * (1 + fee), None
        if m == "short_shares":
            if buy:
                return 0.0, 0.0, None
            return -max(held, 0.0), 1.0, None
        if m == "order_notional":
            return 0.0, price, None
        if m == "equity_delta":
            if asset not in ("stock", "etf"):
                return None, None, f"no delta model for {asset} here"
            scope = r.get("scope", {}).get("asset_class")
            used = abs(sum(p["qty"] for p in snap["positions"] if not scope or p["assetClass"] in scope))
            return used, 1.0 if buy else -1.0 if held > 0 else 1.0, None
        return None, None, f"measure {m} not known"

    # -- evaluations ----------------------------------------------------------------------------------------------
    def evaluate(self, body: dict) -> dict:
        """An evaluation saved with everything it read: no trading authority needed, and nothing is ever sent."""
        with self.lock:
            now = self.now()
            account = str(body.get("account_id") or "")
            snap = self.snapshot(account, now)
            symbol = str(body.get("symbol") or "").strip().upper()
            if not symbol:
                raise ValueError("symbol: needed")
            if (body.get("currency") or "USD").upper() != "USD":
                raise ValueError("currency: the paper account is in USD (conversion is not built yet)")
            side = body.get("side") or "buy"
            if side not in ("buy", "sell"):
                raise ValueError("side: buy or sell")
            h = horizon(body.get("horizon") or "1_session", now)
            q = self.quote(symbol, now)
            entry = _num(body.get("entry"), "entry")
            target = _num(body.get("target"), "target")
            invalidation = _num(body.get("invalidation"), "invalidation")
            price = entry if entry is not None else q["price"]
            qty = _num(body.get("quantity"), "quantity")
            budget = _num(body.get("budget"), "budget")
            if qty is None and budget is not None and price:
                qty = math.floor(budget / price)
            if not qty:
                raise ValueError("quantity or budget: needed")
            margin = bool(body.get("margin"))
            a = dict(body.get("assumptions") or {})
            sizing = self.check(snap, symbol, side, qty, price, now, margin)
            if q["status"] != "current":  # sized on the entry assumed; the executable size waits for a current quote
                sizing["indeterminate"].append(f"the quote is {q['status']}: a current broker check is needed")
                sizing["executableQty"], sizing["exploratoryOnly"] = None, True
            fee = (snap["feeBps"] or 0) / 10_000
            scen = []
            for name, px in (("target", target), ("invalidation", invalidation),
                             *((s.get("name") or f"at {s.get('price')}", _num(s.get("price"), "scenario price"))
                               for s in (body.get("scenarios") or [])),
                             *((("gap", _num(body.get("gap"), "gap")),) if body.get("gap") not in (None, "") else ())):
                if px is None or price is None:
                    continue
                row = {"name": name, "price": px, "label": "user assumption" if name != "gap" else "scenario"}
                for size_name, n in (("requested", qty), ("feasible", sizing["feasibleQty"])):
                    if n is None:
                        row[size_name] = None
                        continue
                    cost = round(n * (price + px) * fee, 2)
                    row[size_name] = {"qty": n, **trade_outcome(side, n, price, px, cost), "cost": cost}
                scen.append(row)
            gaps = []
            for need in HORIZON_NEEDS[h["kind"]]:  # what is missing is a gap, or an assumption stated
                if (need == "invalidation" and invalidation) or a.get(need):
                    continue
                if need == "session":
                    a["session"] = "regular (assumed: none was given)"
                elif need == "time_exit":
                    a["time_exit"] = "the session's close (assumed: none was given)"
                elif need == "review_date":
                    gaps.append(f"review date: not given ({h['reviewWhy']})")
                else:
                    gaps.append(f"{need.replace('_', ' ')}: not given")
            if q["status"] != "current":
                gaps.append(f"quote {q['status']}" + (f" (last seen {q['lastSeen']} at {q['asOf']})" if q["asOf"] else ""))
            gaps += ["spread and executable depth: not in the stored bars",
                     "issuer events and catalysts: not linked yet (the News view holds headlines)",
                     "sector and correlated holdings: not modelled yet"]
            returns = self._returns(h, price, target, a)
            gaps += returns.pop("gaps")
            review_at = a.get("review_date") or h["reviewAt"]
            financing = ({"borrowed": None, "cost": None, "note": "margin asked: the broker's terms are needed "
                          "(none connected); financing, stressed equity and maintenance unknown"} if margin else
                         {"borrowed": 0.0, "cost": 0.0, "note": "a cash account: nothing is borrowed"})
            impact = self._impact(snap, symbol, side, price, qty, sizing["feasibleQty"], fee)
            benchmark = (body.get("benchmark") or "cash").strip()
            look = max(5, int(round((h["sessions"] or h["years"] * 252)))) if h["kind"] != "long" else 252
            alts = [s.strip().upper() for s in (body.get("alternatives") or []) if str(s).strip()]
            compare = [{"symbol": "CASH", "kind": "cash", "return": a.get("cash_rate", 0.0),
                        "label": "user assumption" if "cash_rate" in a else "assumed: 0 (no interest counted)"}]
            seen = {"CASH"}
            for s in [symbol, *alts, *([] if benchmark.lower() == "cash" else [benchmark.upper()])]:
                if s in seen:
                    continue
                seen.add(s)
                compare.append({"symbol": s, "kind": "benchmark" if s == benchmark.upper() else
                                "proposal" if s == symbol else "alternative",
                                "quote": self.quote(s, now), "performance": self.performance(s, look)})
            evidence = [
                {"label": "observed", "what": f"{symbol} quote", "value": q["lastSeen"], "status": q["status"],
                 "asOf": q["asOf"], "source": q["source"]},
                {"label": "observed", "what": "account balances", "value": snap["settledCash"], "asOf": snap["asOf"],
                 "source": snap["source"]},
                *({"label": "user assumption", "what": k, "value": v, "asOf": now} for k, v in
                  (("entry", entry), ("target", target), ("invalidation", invalidation), ("horizon", h["text"]))
                  if v is not None),
                *({"label": "user assumption", "what": k, "value": v, "asOf": now} for k, v in sorted(a.items())),
                {"label": "model output", "what": "feasible quantity", "value": sizing["feasibleQty"], "asOf": now,
                 "source": f"policy version {self.policy_version()}"},
            ]
            record = {
                "account": account, "symbol": symbol, "side": side, "currency": "USD", "createdAt": now,
                "policyVersion": self.policy_version(), "horizon": h, "benchmark": benchmark,
                "assumptions": {"entry": entry, "target": target, "invalidation": invalidation, "quantity": qty,
                                "budget": budget, "margin": margin, **a,
                                "note": "a price target is an assumption; the invalidation price is no guarantee of exit"},
                "quote": q, "accountSnapshot": snap, "sizing": sizing, "scenarios": scen,
                "beforeCosts": not fee, "costs": {"feeBps": snap["feeBps"], "label": "the paper account's fee"},
                "returns": returns, "financing": financing, "impact": impact, "comparison": compare,
                "evidence": evidence, "gaps": gaps,
                "reviewTriggers": [{"what": "thesis review", "at": review_at},
                                   *(({"what": "invalidation", "price": invalidation},) if invalidation else ()),
                                   *({"what": "review required", "rule": r} for r in sizing["reviewRequired"])],
                "rules": self.rules(now), "exceptions": self.exceptions(now),
                "decision": {"choice": body.get("decision") or "save", "rationale": body.get("rationale"),
                             "alternatives": [c["symbol"] for c in compare],
                             "offers": ["watch", "paper trade (a preview first)", "request review", "keep cash"],
                             "submits": False},
            }
            if record["decision"]["choice"] not in ("save", "watch", "paper_trade", "request_review", "keep_cash"):
                raise ValueError("decision: save, watch, paper_trade, request_review or keep_cash (an evaluation never "
                                 "submits an order)")
            with self.conn:
                eid = self.conn.execute("INSERT INTO desk_evaluations (account, created_at, policy, body) VALUES "
                                        "(?, ?, ?, ?)", (account, now, record["policyVersion"], _j(record))).lastrowid
                self._emit(account, "evaluation.saved", {"evaluation": f"e-{eid}", "symbol": symbol}, now)
            return self.evaluation(f"e-{eid}")

    def _returns(self, h: dict, price, target, a: dict) -> dict:
        out: dict = {"gaps": []}
        if price is None or target is None:
            return out
        out["holdingPeriod"] = {"years": h["years"], "sessions": h["sessions"], "pct": round((target / price - 1) * 100, 4)}
        if h["kind"] == "long":
            end = target
            dy = a.get("dividend_yield")
            if dy is not None:
                if a.get("reinvestment") == "reinvested":
                    end = target * (1 + float(dy)) ** h["years"]
                    out["distributions"] = "reinvested at the assumed yield"
                else:
                    out["distributions"] = "paid out: not in the price return"
            nominal = annualized(price, end, h["years"])
            out["annualized"] = round(nominal, 6)
            if a.get("inflation") is not None:
                out["real"] = round(real_return(nominal, float(a["inflation"])), 6)
                out["inflation"] = float(a["inflation"])
            else:
                out["gaps"].append("inflation: not given (the real return is unknown)")
            for k in ("fees", "financing", "tax"):
                out[k] = a.get(k, "not counted")
        return out

    def _impact(self, snap, symbol, side, price, qty, feasible, fee) -> dict:
        held = next((p for p in snap["positions"] if p["symbol"] == symbol), None)
        before_val = None if held is None else held["value"]
        out = {"cashBefore": snap["cash"], "borrowingBefore": 0.0, "positionBefore": 0 if held is None else held["qty"],
               "valueBefore": 0.0 if held is None else before_val}
        sign = 1 if side == "buy" else -1
        for name, n in (("requested", qty), ("feasible", feasible)):
            if n is None or price is None or (held is not None and before_val is None):
                out[name] = None
                continue
            cash = snap["cash"] - sign * n * price - n * price * fee
            value = (before_val or 0.0) + sign * n * price
            eq = snap["equity"] or 0
            out[name] = {"qty": n, "cashAfter": round(cash, 2), "borrowingAfter": round(max(0.0, -cash), 2),
                         "valueAfter": round(value, 2), "concentration": round(abs(value) / eq, 4) if eq else None}
        return out

    def evaluation(self, eid: str) -> dict:
        m = re.fullmatch(r"e-(\d+)", str(eid))
        row = m and self.conn.execute("SELECT id, body FROM desk_evaluations WHERE id=?", (int(m.group(1)),)).fetchone()
        if not row:
            raise LookupError(f"no evaluation {eid}")
        body = json.loads(row[1])
        now_v = self.policy_version()
        return {"id": f"e-{row[0]}", **body,
                "current": {"policyVersion": now_v, "policyChangedSince": now_v != body["policyVersion"],
                            "note": "as evaluated at its own time; evaluate again for the policy and prices now"}}

    def evaluations(self, account: str | None = None, limit: int = 50) -> list[dict]:
        rows = self.conn.execute("SELECT id, account, created_at, policy, body FROM desk_evaluations "
                                 + ("WHERE account=? " if account else "") + "ORDER BY id DESC LIMIT ?",
                                 ((account,) if account else ()) + (int(limit),)).fetchall()
        out = []
        for i, acct, at, pv, body in rows:
            b = json.loads(body)
            out.append({"id": f"e-{i}", "account": acct, "createdAt": at, "policyVersion": pv, "symbol": b["symbol"],
                        "side": b["side"], "horizon": b["horizon"]["text"], "quantity": b["assumptions"]["quantity"],
                        "feasibleQty": b["sizing"]["feasibleQty"], "binding": b["sizing"]["binding"],
                        "decision": b["decision"]["choice"]})
        return out

    # -- commitments (shared: two desks cannot spend the same dollars) ----------------------------------------------
    def reserve(self, body: dict) -> dict:
        with self.lock:
            now = self.now()
            account = str(body.get("account_id") or "")
            self._paper(account)
            amount = _num(body.get("amount"), "amount")
            if amount is None:
                raise ValueError("amount: needed")
            desk = str(body.get("desk") or "another desk")[:80]
            with self.conn:
                cid = self.conn.execute("INSERT INTO desk_commitments (account, desk, amount, note, created_at) VALUES "
                                        "(?, ?, ?, ?, ?)", (account, desk, amount, body.get("note"), now)).lastrowid
                self._invalidate(account, f"{desk} reserved ${amount:,.2f}", now)
                self._emit(account, "commitment.reserved", {"commitment": cid, "desk": desk, "amount": amount}, now)
            return {"id": cid, "account": account, "desk": desk, "amount": amount, "createdAt": now}

    def release(self, cid: int) -> dict:
        with self.lock:
            now = self.now()
            row = self.conn.execute("SELECT account FROM desk_commitments WHERE id=? AND released_at IS NULL",
                                    (int(cid),)).fetchone()
            if not row:
                raise LookupError(f"no open commitment {cid}")
            with self.conn:
                self.conn.execute("UPDATE desk_commitments SET released_at=? WHERE id=?", (now, int(cid)))
                self._invalidate(row[0], f"commitment {cid} released", now)
                self._emit(row[0], "commitment.released", {"commitment": int(cid)}, now)
            return {"id": int(cid), "releasedAt": now}

    # -- reviews --------------------------------------------------------------------------------------------------
    def review(self, body: dict) -> dict:
        """A proposed change or exception, routed to its owner: applied at once only within the requester's delegated
        authority (the rule's owner, or the role it is delegated to, inside the parent's limit)."""
        with self.lock:
            now = self.now()
            kind = body.get("kind") or "change"
            if kind not in ("change", "exception"):
                raise ValueError("kind: change or exception")
            rule = self.rule(str(body.get("rule_id") or ""), now)
            threshold = _num(body.get("threshold"), "threshold", positive=False)
            if threshold is None or threshold < 0:
                raise ValueError("threshold: a number, 0 or more, in the rule's own unit")
            by = str(body.get("requested_by") or "Client")
            rationale = str(body.get("rationale") or "").strip()
            if not rationale:
                raise ValueError("rationale: needed")
            if rule.get("broker"):
                raise NoAuthority(f"{rule['id']} is the broker's limit: it stays binding, and no role here changes it")
            parent = self.rule(rule["parent"], now) if rule.get("parent") else None
            tighter = rule.get("threshold") is not None and threshold <= rule["threshold"]
            within_parent = parent is None or parent.get("threshold") is None or threshold <= parent["threshold"]
            if by == rule["owner"] and within_parent:
                routed, applied = rule["owner"], kind == "change"
            elif by == rule.get("delegated_to") and (tighter or within_parent) and kind == "change":
                routed, applied = by, True  # inside the delegated authority: the supervisor's own call
            elif not within_parent:
                routed, applied = parent["owner"], False  # beyond the parent: the parent's owner decides
            else:
                routed, applied = rule["owner"], False
            expires = int(_num(body.get("expires_at"), "expires_at") or now + REVIEW_DAYS * DAY_MS)
            scope = body.get("scope") or {}
            record = {"kind": kind, "rule": rule["id"], "ruleVersion": rule["version"], "from": rule.get("threshold"),
                      "threshold": threshold, "unit": rule["unit"], "scope": scope, "requestedBy": by,
                      "routedTo": routed, "rationale": rationale, "effectiveFrom": now, "expiresAt": expires,
                      "tightens": tighter}
            with self.conn:
                rid = self.conn.execute("INSERT INTO desk_reviews (body, status, created_at, expires_at) VALUES "
                                        "(?, ?, ?, ?)", (_j(record), "pending", now, expires)).lastrowid
                self._emit(scope.get("account"), "review.requested", {"review": rid, "rule": rule["id"],
                                                                       "routedTo": routed}, now)
                if applied:
                    v = self._new_version(rule, threshold, now, f"{rule['id']} to {threshold:,} {rule['unit']} by {by}")
                    self.conn.execute("UPDATE desk_reviews SET status='applied', decided_at=?, decided_by=?, reason=? "
                                      "WHERE id=?", (now, by, f"within {by}'s delegated authority (policy {v})", rid))
            return self.review_of(rid)

    def decide(self, rid: int, body: dict) -> dict:
        with self.lock:
            now = self.now()
            self._sweep(now)
            r = self.review_of(rid)
            if r["status"] != "pending":
                raise Conflict(f"review {rid} is {r['status']}", review=r)
            by = str(body.get("by") or "")
            if by != r["routedTo"]:
                raise NoAuthority(f"review {rid} is {r['routedTo']}'s to decide, not {by or 'an unnamed role'}'s")
            decision = body.get("decision")
            reason = str(body.get("reason") or "").strip()
            if decision not in ("approve", "reject") or not reason:
                raise ValueError("decision: approve or reject, with a reason")
            with self.conn:
                status = "approved" if decision == "approve" else "rejected"
                self.conn.execute("UPDATE desk_reviews SET status=?, decided_at=?, decided_by=?, reason=? WHERE id=?",
                                  (status, now, by, reason, int(rid)))
                if status == "approved" and r["kind"] == "change":
                    self._new_version(self.rule(r["rule"], now), r["threshold"], now,
                                      f"{r['rule']} to {r['threshold']:,} {r['unit']}, approved by {by}")
                elif status == "approved":
                    v = self._bump(f"exception {rid} on {r['rule']} approved by {by}", now)
                    self._invalidate(None, f"policy version {v}: exception {rid}", now)
                self._emit((r.get("scope") or {}).get("account"), f"review.{status}",
                           {"review": int(rid), "rule": r["rule"], "by": by, "reason": reason}, now)
            return self.review_of(rid)

    def review_of(self, rid: int) -> dict:
        row = self.conn.execute("SELECT id, body, status, created_at, expires_at, decided_at, decided_by, reason FROM "
                                "desk_reviews WHERE id=?", (int(rid),)).fetchone()
        if not row:
            raise LookupError(f"no review {rid}")
        i, body, status, c, e, da, db, reason = row
        return {"id": i, **json.loads(body), "status": status, "createdAt": c, "expiresAt": e, "decidedAt": da,
                "decidedBy": db, "reason": reason}

    def reviews(self, limit: int = 50) -> list[dict]:
        self._sweep(self.now())
        return [self.review_of(i) for (i,) in self.conn.execute("SELECT id FROM desk_reviews ORDER BY id DESC LIMIT ?",
                                                                (int(limit),))]

    def _sweep(self, now: int) -> None:
        with self.conn:
            for (rid,) in self.conn.execute("SELECT id FROM desk_reviews WHERE status='pending' AND expires_at <= ?",
                                            (now,)).fetchall():
                self.conn.execute("UPDATE desk_reviews SET status='expired', decided_at=?, reason=? WHERE id=?",
                                  (now, "not decided before its expiry", rid))
                self._emit(None, "review.expired", {"review": rid}, now)
            for pid, acct in self.conn.execute("SELECT id, account FROM desk_previews WHERE status IN ('valid', "
                                               "'blocked', 'indeterminate') AND expires_at <= ?", (now,)).fetchall():
                self.conn.execute("UPDATE desk_previews SET status='expired' WHERE id=?", (pid,))
                self._emit(acct, "preview.expired", {"preview": f"p-{pid}"}, now)

    # -- previews and orders ----------------------------------------------------------------------------------------
    def _fingerprint(self, snap: dict, symbol: str, at: int) -> str:
        held = sum(p["qty"] for p in snap["positions"] if p["symbol"] == symbol)
        facts = {"available": snap["available"], "commitments": [c["id"] for c in snap["commitments"]],
                 "working": [o["id"] for o in snap["workingOrders"]], "held": held, "policy": self.policy_version(),
                 "exceptions": [e["review"] for e in self.exceptions(at)]}
        return hashlib.sha256(_j(facts).encode()).hexdigest()[:16]

    def _order_fields(self, body: dict) -> dict:
        symbol = str(body.get("symbol") or "").strip().upper()
        side = body.get("side")
        if not symbol or side not in ("buy", "sell"):
            raise ValueError("symbol and side (buy or sell): needed")
        qty = _num(body.get("quantity"), "quantity")
        if qty is None or qty != int(qty):
            raise ValueError("quantity: whole shares")
        price = _num(body.get("price"), "price")
        kind = "limit" if price is not None else "market"
        session = body.get("session") or "regular"
        if session != "regular":
            raise ValueError("session: the paper account fills in the regular session only")
        return {"account_id": str(body.get("account_id") or ""), "symbol": symbol, "side": side, "quantity": qty,
                "type": kind, "price": price, "session": session}

    def _preview_checks(self, f: dict, now: int) -> dict:
        snap = self.snapshot(f["account_id"], now)
        q = self.quote(f["symbol"], now)
        price = f["price"] if f["price"] is not None else q["price"]  # a limit bounds the cost; a market order needs a quote
        sizing = self.check(snap, f["symbol"], f["side"], f["quantity"], price, now)
        if sizing["executableQty"] is None:
            status = "indeterminate"
        elif sizing["failing"] or sizing["executableQty"] < f["quantity"]:
            status = "blocked"
        else:
            status = "valid"
        return {"snapshot": snap, "quote": q, "sizing": sizing, "status": status,
                "fingerprint": self._fingerprint(snap, f["symbol"], now)}

    def preview(self, body: dict) -> dict:
        with self.lock:
            now = self.now()
            self._sweep(now)
            f = self._order_fields(body)
            c = self._preview_checks(f, now)
            record = {"order": f, "quote": c["quote"], "sizing": c["sizing"], "fingerprint": c["fingerprint"],
                      "policyVersion": self.policy_version(), "available": c["snapshot"]["available"],
                      "evaluation": body.get("evaluation_id")}
            with self.conn:
                pid = self.conn.execute("INSERT INTO desk_previews (account, body, status, created_at, expires_at) "
                                        "VALUES (?, ?, ?, ?, ?)", (f["account_id"], _j(record), c["status"], now,
                                                                   now + PREVIEW_S * 1000)).lastrowid
                self._emit(f["account_id"], "preview.created", {"preview": f"p-{pid}", "status": c["status"]}, now)
            return self.preview_of(f"p-{pid}")

    def preview_of(self, pid: str) -> dict:
        m = re.fullmatch(r"p-(\d+)", str(pid))
        row = m and self.conn.execute("SELECT id, body, status, created_at, expires_at FROM desk_previews WHERE id=?",
                                      (int(m.group(1)),)).fetchone()
        if not row:
            raise LookupError(f"no preview {pid}")
        i, body, status, c, e = row
        return {"id": f"p-{i}", **json.loads(body), "status": status, "createdAt": c, "expiresAt": e}

    def _invalidate(self, account: str | None, why: str, at: int) -> None:
        rows = self.conn.execute("SELECT id, account FROM desk_previews WHERE status IN ('valid', 'blocked', "
                                 "'indeterminate')" + (" AND account=?" if account else ""),
                                 (account,) if account else ()).fetchall()
        for pid, acct in rows:
            self.conn.execute("UPDATE desk_previews SET status='invalidated' WHERE id=?", (pid,))
            self._emit(acct, "preview.invalidated", {"preview": f"p-{pid}", "why": why}, at)

    def order(self, body: dict, key: str | None) -> tuple[dict, bool]:
        """A paper order from a current preview: (the answer, whether it was placed by this request). Any account but
        a paper one is refused first: real orders are off. A retry under the same key returns the first answer."""
        account = str(body.get("account_id") or "")
        if not _PAPER_ID.match(account):
            raise RealOrdersOff(REAL_ORDERS_OFF)
        with self.lock:
            now = self.now()
            key = (key or body.get("idempotency_key") or "").strip()
            if not 0 < len(key) <= 48:
                raise ValueError("an idempotency key of 1 to 48 characters is needed (a retry reuses it)")
            f = self._order_fields(body)
            row = self.conn.execute("SELECT request, result FROM desk_orders WHERE key=?", (key,)).fetchone()
            if row is not None:
                if json.loads(row[0]) != {**f, "preview": body.get("preview_id")}:
                    raise Conflict(f"idempotency key {key} is another request's")
                return self._reconciled(key), False
            self._paper(account)
            if body.get("authorized") is not True:
                raise NoAuthority("an order needs the client's explicit authorization (authorized: true)")
            self._sweep(now)
            p = self.preview_of(str(body.get("preview_id") or ""))
            if (p["order"]["account_id"], p["order"]["symbol"], p["order"]["side"], p["order"]["quantity"],
                    p["order"]["price"]) != (account, f["symbol"], f["side"], f["quantity"], f["price"]):
                raise Conflict(f"preview {p['id']} is for another order")
            if p["status"] != "valid":
                raise Conflict(f"preview {p['id']} is {p['status']}: ask for a new one", preview=p)
            c = self._preview_checks(f, now)
            if c["fingerprint"] != p["fingerprint"] or c["status"] != "valid":
                with self.conn:
                    self.conn.execute("UPDATE desk_previews SET status='invalidated' WHERE id=?", (int(p["id"][2:]),))
                    self._emit(account, "preview.invalidated", {"preview": p["id"], "why": "what it checked changed"}, now)
                raise Conflict(f"preview {p['id']} is out of date: the feasible size is now {c['sizing']['feasibleQty']}",
                               feasibleQty=c["sizing"]["feasibleQty"], sizing=c["sizing"])
            request = {**f, "preview": p["id"]}
            try:
                placed = self.book.place(f["symbol"], "*", f["side"], f["quantity"], f["type"], limit=f["price"],
                                         tif="day", now_ms=now, client_id=f"v1:{key}")
                result = {"status": "accepted", "broker": "paper", "order": placed,
                          "orderStatus": PAPER_STATUS[placed["status"]]}
                status, oid = "accepted", placed["id"]
            except ValueError as e:  # the paper book's refusal: recorded, so a retry is answered the same
                result, status, oid = {"status": "rejected", "broker": "paper", "reason": str(e)}, "rejected", None
            with self.conn:
                self.conn.execute("INSERT INTO desk_orders (key, account, preview, request, result, paper_order, status, "
                                  "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (key, account, int(p["id"][2:]),
                                                                                  _j(request), _j(result), oid, status, now))
                self.conn.execute("UPDATE desk_previews SET status='used' WHERE id=?", (int(p["id"][2:]),))
                self._emit(account, f"order.{status}", {"key": key, "order": oid, "symbol": f["symbol"]}, now)
                self._invalidate(account, f"order {oid or key} {status}", now)
            return self._reconciled(key), True

    def _reconciled(self, key: str) -> dict:
        """The order's answer, its status read again from the paper book (an event for each change)."""
        account, result, oid, status = self.conn.execute(
            "SELECT account, result, paper_order, status FROM desk_orders WHERE key=?", (key,)).fetchone()
        result = json.loads(result)
        if oid is not None:
            try:
                o = self.book.order(oid)
            except LookupError:
                o = None
            if o is not None:
                now_status = PAPER_STATUS[o["status"]]
                result = {**result, "order": o, "orderStatus": now_status}
                if now_status != status and not (status == "accepted" and now_status == "open"):
                    with self.conn:
                        self.conn.execute("UPDATE desk_orders SET status=? WHERE key=?", (now_status, key))
                        self._emit(account, f"order.{now_status}", {"key": key, "order": oid}, self.now())
        return {"key": key, **result}

    # -- events ---------------------------------------------------------------------------------------------------
    def events(self, account: str | None = None, cursor: int = 0, limit: int = 200) -> dict:
        with self.lock:
            now = self.now()
            self._sweep(now)
            self.book.match(now)
            for (key,) in self.conn.execute("SELECT key FROM desk_orders WHERE paper_order IS NOT NULL AND status IN "
                                            "('accepted', 'open')").fetchall():
                self._reconciled(key)
            rows = self.conn.execute(
                "SELECT seq, account, kind, at, data FROM desk_events WHERE seq > ?"
                + (" AND (account=? OR account IS NULL)" if account else "") + " ORDER BY seq LIMIT ?",
                (int(cursor),) + ((account,) if account else ()) + (int(limit),)).fetchall()
            last = self.conn.execute("SELECT MAX(seq) FROM desk_events").fetchone()[0] or 0
            events = [{"seq": s, "account": a, "kind": k, "at": t, **json.loads(d)} for s, a, k, t, d in rows]
            nxt = events[-1]["seq"] if events else max(int(cursor), 0)
            return {"events": events, "cursor": nxt, "more": nxt < last,
                    "gap": int(cursor) > last}  # a cursor past the newest: the store was reset; reconcile from 0

    # -- opportunities --------------------------------------------------------------------------------------------
    def opportunities(self, symbols: list[str], benchmark: str = "SPY", horizon_text: str = "20_sessions",
                      asset_class: str | None = None, catalyst: str | None = None) -> dict:
        with self.lock:
            now = self.now()
            h = horizon(horizon_text, now)
            look = max(5, int(round(h["sessions"] or min(h["years"], 1) * 252)))
            bench = self.performance(benchmark, look) if benchmark.lower() != "cash" else None
            out = []
            for s in symbols:
                if asset_class and self.asset(s) != asset_class:
                    continue
                perf = self.performance(s, look)
                rel = (None if bench is None or perf["return"] is None or bench["return"] is None
                       else round(perf["return"] - bench["return"], 6))
                out.append({"symbol": s, "assetClass": self.asset(s), "quote": self.quote(s, now), "performance": perf,
                            "vsBenchmark": rel, "benchmarks": [benchmark, "cash"],
                            "evidence": [{"label": "observed", "what": "daily closes", "from": perf["from"],
                                          "to": perf["to"], "source": "the stored bars (Massive)"}],
                            "competingExplanations": [], "catalysts": [],
                            "gaps": ["catalysts, filings and estimates: not linked yet",
                                     "competing explanations: none recorded"]})
            return {"observedAt": now, "horizon": h, "benchmark": {"symbol": benchmark, "performance": bench},
                    "catalystFilter": catalyst, "opportunities": out,
                    "note": "catalyst filtering waits for the news and filings link" if catalyst else None}
