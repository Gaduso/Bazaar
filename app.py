"""Flask web overlay for the bazaar flipper.

Run with:
    pip install -r requirements.txt
    python app.py

Then open http://127.0.0.1:5000 in your browser.
"""

from __future__ import annotations

from dataclasses import asdict

from flask import Flask, jsonify, render_template, request

from flipper import (
    DAILY_NPC_SELL_CAP,
    DEFAULT_BAZAAR_CAPTURE_FACTOR,
    DEFAULT_CAPTURE_FACTOR,
    BazaarAPI,
    Blacklist,
    ItemsAPI,
    calculate_all_flips,
    filter_flips,
    plan_daily_npc,
    sort_flips,
)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
bazaar = BazaarAPI(cache_ttl_seconds=30)
items = ItemsAPI(cache_ttl_seconds=3600)  # NPC prices are static; cache long.
blacklist = Blacklist()  # file-backed; persists across restarts.

# Whitelist of fields the client is allowed to sort by. Anything else falls
# back to profit_per_hour. This keeps the API tight and avoids reflective
# attribute lookups on user input.
SORTABLE_FIELDS = {
    "profit_per_hour",
    "effective_profit_per_hour",
    "margin",
    "roi",
    "items_per_hour",
    "buy_moving_week",
    "sell_moving_week",
    "buy_price",
    "sell_price",
    "npc_sell_price",
    "npc_margin",
    "npc_roi",
    "npc_profit_per_hour",
    "npc_profit_per_revenue",
    "npc_daily_profit",
    "npc_daily_units",
}

# Default sort key per trading mode. "bazaar" = margin flips (buy order + sell
# offer); "npc" = buy on bazaar, sell to the NPC merchant under the daily cap.
DEFAULT_SORT_BY_MODE = {
    "bazaar": "effective_profit_per_hour",
    "npc": "npc_profit_per_revenue",
}

# How to price the NPC-flip acquisition cost: "buy_order" (place an order and
# wait, cost = sell_price) or "instant_buy" (take the lowest sell offer now,
# cost = buy_price). Anything else falls back to the default.
NPC_BUY_METHODS = {"buy_order", "instant_buy"}
DEFAULT_NPC_BUY_METHOD = "buy_order"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _query_float(name: str, default: float) -> float:
    raw = request.args.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _query_int(name: str, default: int) -> int:
    raw = request.args.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/flips")
def api_flips():
    mode = request.args.get("mode", "bazaar")
    if mode not in DEFAULT_SORT_BY_MODE:
        mode = "bazaar"

    sort_key = request.args.get("sort", "")
    if sort_key not in SORTABLE_FIELDS:
        sort_key = DEFAULT_SORT_BY_MODE[mode]

    min_margin = _query_float("min_margin", 0.0)
    min_roi = _query_float("min_roi", 0.0)
    min_buy_volume = _query_int("min_buy_volume", 0)
    min_sell_volume = _query_int("min_sell_volume", 0)
    # Only apply an NPC-margin floor (and drop non-NPC items) when the param is
    # actually present, so the default view still shows regular flips.
    raw_min_npc = request.args.get("min_npc_margin")
    min_npc_margin = (
        _query_float("min_npc_margin", 0.0)
        if raw_min_npc not in (None, "")
        else None
    )
    # NPC mode only makes sense for NPC-sellable items, so apply a 0-floor (which
    # drops non-NPC products) even when the user didn't set an explicit minimum.
    if mode == "npc" and min_npc_margin is None:
        min_npc_margin = 0.0
    raw_max_buy = request.args.get("max_buy_price")
    max_buy_price = (
        _query_float("max_buy_price", 0.0)
        if raw_max_buy not in (None, "")
        else None
    )
    name_query = request.args.get("name", "")
    limit = max(1, min(_query_int("limit", 50), 500))

    # Per-item potential-daily-profit estimates react to these (NPC mode only).
    capture_factor = _query_float("capture_factor", DEFAULT_CAPTURE_FACTOR)
    revenue_cap = _query_float("revenue_cap", DAILY_NPC_SELL_CAP)
    npc_buy_method = request.args.get("npc_buy_method", DEFAULT_NPC_BUY_METHOD)
    if npc_buy_method not in NPC_BUY_METHODS:
        npc_buy_method = DEFAULT_NPC_BUY_METHOD
    # Bazaar-mode competition factor feeding effective_profit_per_hour.
    bazaar_capture_factor = _query_float(
        "bazaar_capture_factor", DEFAULT_BAZAAR_CAPTURE_FACTOR
    )

    data = bazaar.fetch()
    npc_prices = items.npc_sell_prices()
    flips = calculate_all_flips(
        data,
        npc_sell_prices=npc_prices,
        capture_factor=capture_factor,
        revenue_cap=revenue_cap,
        npc_buy_method=npc_buy_method,
        bazaar_capture_factor=bazaar_capture_factor,
    )
    flips = filter_flips(
        flips,
        min_margin=min_margin,
        min_roi=min_roi,
        min_buy_moving_week=min_buy_volume,
        min_sell_moving_week=min_sell_volume,
        min_npc_margin=min_npc_margin,
        max_buy_price=max_buy_price,
        name_query=name_query,
        blacklist=blacklist.as_set(),
    )
    flips = sort_flips(flips, key=sort_key)

    return jsonify({
        "mode": mode,
        "flips": [asdict(f) for f in flips[:limit]],
        "total_matching": len(flips),
        "last_updated": data.get("lastUpdated"),
        "cache_age_seconds": bazaar.cache_age_seconds(),
    })


@app.route("/api/plan")
def api_plan():
    """Daily NPC plan: allocate the 500M sell cap across items for max profit.

    Solves the fractional-knapsack problem described in flipper.calculator: rank
    NPC-sellable items by profit per coin of NPC revenue and greedily fill the
    daily cap, capping each item at the units you can realistically acquire.
    """
    revenue_cap = _query_float("revenue_cap", DAILY_NPC_SELL_CAP)
    capture_factor = _query_float("capture_factor", DEFAULT_CAPTURE_FACTOR)
    min_npc_margin = _query_float("min_npc_margin", 0.0)
    min_buy_volume = _query_int("min_buy_volume", 0)
    limit = max(1, min(_query_int("limit", 100), 1000))
    npc_buy_method = request.args.get("npc_buy_method", DEFAULT_NPC_BUY_METHOD)
    if npc_buy_method not in NPC_BUY_METHODS:
        npc_buy_method = DEFAULT_NPC_BUY_METHOD

    data = bazaar.fetch()
    npc_prices = items.npc_sell_prices()
    flips = calculate_all_flips(
        data, npc_sell_prices=npc_prices, npc_buy_method=npc_buy_method,
    )
    # Drop blacklisted items and (optionally) thinly traded ones before planning,
    # so they don't claim a slice of the cap.
    bl = blacklist.as_set()
    flips = [
        f
        for f in flips
        if f.product_id not in bl
        and (not min_buy_volume or f.buy_moving_week >= min_buy_volume)
    ]

    plan = plan_daily_npc(
        flips,
        revenue_cap=revenue_cap,
        capture_factor=capture_factor,
        min_npc_margin=min_npc_margin,
    )

    return jsonify({
        "revenue_cap": plan.revenue_cap,
        "capture_factor": plan.capture_factor,
        "total_revenue": plan.total_revenue,
        "total_cost": plan.total_cost,
        "total_profit": plan.total_profit,
        "revenue_utilization": plan.revenue_utilization,
        "limited_by": plan.limited_by,
        "count": len(plan.allocations),
        "allocations": [asdict(a) for a in plan.allocations[:limit]],
        "last_updated": data.get("lastUpdated"),
    })


@app.route("/api/blacklist", methods=["GET", "POST"])
def api_blacklist():
    """List the blacklist (GET) or add an item to it (POST {"product_id": ...})."""
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        product_id = str(payload.get("product_id", "")).strip()
        if not product_id:
            return jsonify({"error": "product_id required"}), 400
        blacklist.add(product_id)
    return jsonify({"blacklist": blacklist.all()})


@app.route("/api/blacklist/<path:product_id>", methods=["DELETE"])
def api_blacklist_delete(product_id: str):
    """Remove a single item from the blacklist."""
    blacklist.remove(product_id)
    return jsonify({"blacklist": blacklist.all()})


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "cache_age_seconds": bazaar.cache_age_seconds()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
