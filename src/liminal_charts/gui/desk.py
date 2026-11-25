"""The Trading Desk's client API, /v1/... (Monday list item 37; the user's design, Trading_Desk.docx, "Basic client API").

The Desk view of the page (static/desk.js) and any other client ask the same routes, under the same rules as the
rest of the charts' API: only requests addressed to this machine, only from this app's own pages or a program here
(web.from_this_app), every write with the app's write header. Kept in a module of its own: server.py registers it in
one line for reads and one for writes.

  GET  /v1/accounts                     the accounts: the paper one, and the brokers planned (none connected)
  GET  /v1/accounts/{id}/overview       balances, holdings, commitments, permissions, data freshness, market session
  GET  /v1/opportunities                ?universe=QQQ,AAPL&benchmark=SPY&horizon=20_sessions&asset_class=&catalyst=
  POST /v1/evaluations                  a saved evaluation and its policy results (no trading authority needed)
  GET  /v1/evaluations[/{id}]           the saved ones, or one as evaluated at its own time
  GET  /v1/constraints                  the rules in force, their owners and versions, the exceptions, the roles
  POST /v1/reviews                      a proposed change or exception, routed to its owner
  POST /v1/reviews/{id}/decision        {by, decision: approve | reject, reason}
  GET  /v1/reviews[/{id}]
  POST /v1/previews                     the current account checks for one order, expiring
  GET  /v1/previews/{id}
  POST /v1/orders                       paper accounts only (any other answers 403 "real orders are off": the user's
                                        rule); explicit authorization, a current preview, an idempotency key
                                        (Idempotency-Key header or idempotency_key)
  GET  /v1/orders/{key}                 the order placed under that key, its status read again
  POST /v1/commitments                  a reservation of cash by another desk (shared: two desks cannot spend the same)
  DELETE /v1/commitments/{id}
  GET  /v1/events                       ?account=&cursor=&limit=: every change after the cursor, in order
"""
from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

from ..trading.desk import Conflict, Desk

PREFIX = "/v1"


def desk_for(app) -> Desk:
    """The app's desk, over its paper account (the same store, connection and lock): made again when the paper account
    was (the app let go of its stores and opened them again)."""
    with app._paper_lock:
        book = app.paper
        d = getattr(app, "_desk", None)
        if d is None or d.book is not book:
            d = Desk(app._paper_store.conn, book, quote=lambda s: quote(app, s), closes=lambda s, n: closes(app, s, n),
                     asset=lambda t: app.window.asset(t), lock=app._paper_lock)
            app._desk = d
        return d


def quote(app, symbol: str) -> dict | None:
    """The newest stored minute bar's close, its time the bar's end: the price the paper account fills near."""
    try:
        if not app.window.handles("*", symbol):
            return None
        bars = app.window.page("*", symbol, "minute", 1, "extended", limit=1)["bars"]
        feeds = ", ".join(app.window.feeds("*", symbol))
    except (KeyError, ValueError, LookupError):  # a symbol not stored: no quote, not an error
        return None
    if not bars:
        return None
    return {"price": float(bars[-1]["close"]), "asOf": int(bars[-1]["timestamp"]) + 60_000,
            "source": f"the stored minute bars ({feeds})"}


def closes(app, symbol: str, n: int) -> list[tuple[int, float]] | None:
    try:
        if not app.window.handles("*", symbol):
            return None
        bars = app.window.page("*", symbol, "day", 1, "regular", limit=max(2, int(n)))["bars"]
    except (KeyError, ValueError, LookupError):
        return None
    return [(int(b["timestamp"]), float(b["close"])) for b in bars]


def answer(desk: Desk, method: str, path: str, query: dict, body: dict, headers=None) -> tuple[int, dict]:
    """(status, JSON) for one /v1 request: what the routes do, apart from the HTTP (tested on its own)."""
    parts = [p for p in path[len(PREFIX):].split("/") if p]
    q = {k: (v[-1] if isinstance(v, list) else v) for k, v in (query or {}).items()}
    if method == "GET":
        if parts == ["accounts"]:
            return 200, {"accounts": desk.accounts(), "observedAt": desk.now()}
        if len(parts) == 3 and parts[0] == "accounts" and parts[2] == "overview":
            return 200, desk.overview(parts[1])
        if parts == ["opportunities"]:
            universe = [s.strip().upper() for s in (q.get("universe") or q.get("symbols") or "QQQ,SPY").split(",")
                        if s.strip()][:50]
            return 200, desk.opportunities(universe, q.get("benchmark") or "SPY", q.get("horizon") or "20_sessions",
                                           q.get("asset_class") or None, q.get("catalyst") or None)
        if parts == ["evaluations"]:
            return 200, {"evaluations": desk.evaluations(q.get("account"), int(q.get("limit") or 50)),
                         "observedAt": desk.now()}
        if len(parts) == 2 and parts[0] == "evaluations":
            return 200, desk.evaluation(parts[1])
        if parts == ["constraints"]:
            from ..trading.desk import MEASURES, ROLES

            return 200, {"rules": desk.rules(), "exceptions": desk.exceptions(), "policyVersion": desk.policy_version(),
                         "roles": ROLES, "units": {k: m.unit for k, m in MEASURES.items()}, "observedAt": desk.now(),
                         "note": "one user explores every role; viewing one confers no authority over the broker"}
        if parts == ["reviews"]:
            return 200, {"reviews": desk.reviews(int(q.get("limit") or 50)), "observedAt": desk.now()}
        if len(parts) == 2 and parts[0] == "reviews":
            return 200, desk.review_of(_int(parts[1]))
        if len(parts) == 2 and parts[0] == "previews":
            return 200, desk.preview_of(parts[1])
        if len(parts) == 2 and parts[0] == "orders":
            with desk.lock:
                if not desk.conn.execute("SELECT 1 FROM desk_orders WHERE key=?", (parts[1],)).fetchone():
                    raise LookupError(f"no order under key {parts[1]}")
                return 200, desk._reconciled(parts[1])
        if parts == ["events"]:
            return 200, desk.events(q.get("account") or None, _int(q.get("cursor") or 0), _int(q.get("limit") or 200))
    if method == "POST":
        if parts == ["evaluations"]:
            return 201, desk.evaluate(body)
        if parts == ["reviews"]:
            return 201, desk.review(body)
        if len(parts) == 3 and parts[0] == "reviews" and parts[2] == "decision":
            return 200, desk.decide(_int(parts[1]), body)
        if parts == ["previews"]:
            return 201, desk.preview(body)
        if parts == ["orders"]:
            key = (headers or {}).get("Idempotency-Key") if headers is not None else None
            result, placed = desk.order(body, key)
            return (201 if placed else 200), result
        if parts == ["commitments"]:
            return 201, desk.reserve(body)
    if method == "DELETE" and len(parts) == 2 and parts[0] == "commitments":
        return 200, desk.release(_int(parts[1]))
    raise LookupError(f"no route {method} {path}")


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{v!r}: a whole number") from None


def serve(handler, app, method: str, raw_path: str, body: dict | None = None) -> None:
    """One /v1 request on the charts' server: the local-only and this-app checks for a read (a write has had them, and
    the write header, in the server's _write), then answered."""
    if method == "GET" and not handler._from_this_app():
        return
    url = urlparse(raw_path)
    try:
        status, data = answer(desk_for(app), method, url.path.rstrip("/"), parse_qs(url.query), body or {},
                              handler.headers)
        return handler._json(data, status)
    except Conflict as e:
        return handler._json(e.payload, HTTPStatus.CONFLICT)
    except PermissionError as e:  # real orders off; a role deciding what is not its own
        return handler._json({"error": str(e)}, HTTPStatus.FORBIDDEN)
    except (ValueError, json.JSONDecodeError) as e:
        return handler._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)
    except LookupError as e:
        return handler._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
    except ConnectionError:
        pass  # the page went away
    except Exception as e:  # noqa: BLE001 - answered, not dropped (the server's _failed)
        return handler._failed(e)
