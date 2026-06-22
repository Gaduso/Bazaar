"""Pure functions for computing margin-flip opportunities from bazaar data.

Hypixel Bazaar terminology (from the player's perspective):
    sellPrice  - price the player can SELL items at (top of the buy-order book).
                 You compete with this number when placing a sell offer; your
                 buy ORDER, conversely, must sit at or just above sellPrice to
                 be next in line to fill.
    buyPrice   - price the player can BUY items at (top of the sell-offer book).
                 You compete with this number when placing a buy order; your
                 sell OFFER, conversely, must sit at or just below buyPrice.

Margin-flip strategy ("buy order + sell offer" trading):
    1. Place a BUY ORDER one tick above sellPrice -> fills near sellPrice.
    2. Place a SELL OFFER one tick below buyPrice -> fills near buyPrice.
    3. Profit per unit = buyPrice * (1 - tax) - sellPrice
       (Hypixel charges a 1% tax on the sale side only.)

Liquidity (volume) is just as important as the spread: a wide margin on an
illiquid item means your orders never fill. We expose buyMovingWeek and
sellMovingWeek so callers can require both sides to be active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


TAX_RATE: float = 0.01  # 1% sale tax on Hypixel Bazaar
HOURS_PER_WEEK: int = 7 * 24
DAYS_PER_WEEK: int = 7

# Daily NPC sell-cap planning constants (see the planner section below).
DAILY_NPC_SELL_CAP: float = 500_000_000.0  # Hypixel NPC sell limit, coins/day
DEFAULT_CAPTURE_FACTOR: float = 0.3        # share of weekly buy-flow you win


@dataclass(frozen=True)
class FlipOpportunity:
    """A single flip candidate, derived from one product's quick_status."""

    product_id: str

    # Reference prices from the API (see module docstring).
    buy_price: float   # what you pay when placing a buy order (top sell offer)
    sell_price: float  # what you receive when placing a sell offer (top buy order)

    # Profit metrics, after 1% sale tax.
    margin: float          # coins of profit per unit flipped
    roi: float             # margin / sell_price * 100 (percent)

    # Order-book snapshots.
    buy_volume: int        # items currently sitting in sell offers (you fill these via buy orders)
    sell_volume: int       # items currently sitting in buy orders (you fill these via sell offers)

    # 7-day flow. Both sides matter: your buy order needs people to instant-sell
    # into it, and your sell offer needs people to instant-buy from it.
    buy_moving_week: int
    sell_moving_week: int

    # Throughput estimates. items_per_hour is the slower side of the book
    # divided by 168, i.e. the realistic ceiling for a single flipper before
    # competition is considered.
    items_per_hour: float
    profit_per_hour: float

    # NPC flip ("buy on bazaar, sell to the NPC merchant"). The NPC sell price
    # is fixed per item and supplied by the Hypixel items resource; it is None
    # for items the NPC won't buy. NPC sales are tax free, so
    # npc_margin = npc_sell_price - npc_buy_price, where npc_buy_price is your
    # acquisition cost. How you acquire the item is selectable (npc_buy_method):
    #   "buy_order"  -> you place a buy order and wait; cost = sell_price.
    #   "instant_buy"-> you take the lowest sell offer now; cost = buy_price.
    npc_sell_price: float | None = None
    # Acquisition cost per unit actually used for the npc_* metrics above
    # (either sell_price or buy_price, per npc_buy_method).
    npc_buy_price: float | None = None
    npc_margin: float | None = None
    npc_roi: float | None = None
    # Combined margin*liquidity score: NPC margin scaled by how fast you can
    # acquire the item (buy side of the book). Surfaces items that are both
    # profitable and high-volume.
    npc_profit_per_hour: float | None = None
    # Profit per coin of NPC *revenue* (npc_margin / npc_sell_price). This is the
    # density to rank by when the binding constraint is the daily NPC sell cap
    # (Hypixel limits NPC sales to 500M coins/day): every coin of NPC revenue is
    # the scarce resource, so the most cap-efficient items are those with the
    # highest profit per revenue coin, not the highest absolute margin.
    npc_profit_per_revenue: float | None = None
    # Realistic units you can acquire and flip to the NPC per day:
    # buyMovingWeek/7 * capture_factor, then capped so a single item's NPC
    # revenue can't exceed the daily sell cap on its own.
    npc_daily_units: float | None = None
    # Potential profit per day from this item alone, at the given capture_factor
    # (npc_daily_units * npc_margin). This is the per-item ceiling; the portfolio
    # ceiling under the shared 500M cap comes from plan_daily_npc().
    npc_daily_profit: float | None = None


def calculate_flip(
    product_id: str,
    product_data: dict[str, Any],
    tax_rate: float = TAX_RATE,
    npc_sell_price: float | None = None,
    capture_factor: float = DEFAULT_CAPTURE_FACTOR,
    revenue_cap: float = DAILY_NPC_SELL_CAP,
    npc_buy_method: str = "buy_order",
) -> FlipOpportunity:
    """Build a FlipOpportunity for one product entry from the bazaar payload.

    ``npc_buy_method`` selects how the NPC-flip acquisition cost is priced:
    ``"buy_order"`` (default) uses ``sell_price`` (place a buy order and wait),
    ``"instant_buy"`` uses ``buy_price`` (take the lowest sell offer now).
    """
    quick = product_data.get("quick_status", {}) or {}

    # IMPORTANT: quick_status.buyPrice / sellPrice are *weighted averages* across
    # the order book, not the live top-of-book prices, so they can sit thousands
    # of coins away from what the in-game bazaar shows and inflate the spread.
    # The real top-of-book lives in the summary arrays:
    #   sell_summary[0] = highest BUY order  -> where your buy order sits / you
    #                     instant-sell into (in-game "Sell Instantly" price)
    #   buy_summary[0]  = lowest SELL offer  -> what you pay to instant-buy /
    #                     where your sell offer sits (in-game "Buy Instantly")
    def _top_price(summary: Any) -> float:
        if summary:
            return float(summary[0].get("pricePerUnit", 0.0) or 0.0)
        return 0.0

    sell_price = _top_price(product_data.get("sell_summary"))
    buy_price = _top_price(product_data.get("buy_summary"))

    # Fall back to the weighted averages only if a side of the book is empty.
    if sell_price <= 0:
        sell_price = float(quick.get("sellPrice", 0.0) or 0.0)
    if buy_price <= 0:
        buy_price = float(quick.get("buyPrice", 0.0) or 0.0)

    # Margin trade: sell at buy_price (after 1% tax), buy at sell_price.
    margin = buy_price * (1.0 - tax_rate) - sell_price
    roi = (margin / sell_price * 100.0) if sell_price > 0 else 0.0

    buy_volume = int(quick.get("buyVolume", 0) or 0)
    sell_volume = int(quick.get("sellVolume", 0) or 0)
    buy_moving_week = int(quick.get("buyMovingWeek", 0) or 0)
    sell_moving_week = int(quick.get("sellMovingWeek", 0) or 0)

    # Slowest side caps your throughput.
    hourly_buy = buy_moving_week / HOURS_PER_WEEK
    hourly_sell = sell_moving_week / HOURS_PER_WEEK
    items_per_hour = min(hourly_buy, hourly_sell)
    profit_per_hour = items_per_hour * margin

    # NPC flip metrics — only meaningful when the NPC buys the item.
    npc_buy_price: float | None = None
    npc_margin: float | None = None
    npc_roi: float | None = None
    npc_profit_per_hour: float | None = None
    npc_profit_per_revenue: float | None = None
    npc_daily_units: float | None = None
    npc_daily_profit: float | None = None
    if npc_sell_price is not None:
        # Acquisition cost depends on how you buy: instant-buy hits the lowest
        # sell offer (buy_price), a buy order fills near the top buy order
        # (sell_price).
        npc_buy_price = buy_price if npc_buy_method == "instant_buy" else sell_price
        npc_margin = npc_sell_price - npc_buy_price
        npc_roi = (npc_margin / npc_buy_price * 100.0) if npc_buy_price > 0 else 0.0
        npc_profit_per_hour = npc_margin * hourly_buy
        npc_profit_per_revenue = (
            npc_margin / npc_sell_price if npc_sell_price > 0 else 0.0
        )
        # Liquidity-bounded daily throughput, capped so one item can't claim more
        # than the entire daily NPC sell cap by itself.
        daily_units = (buy_moving_week / DAYS_PER_WEEK) * capture_factor
        if npc_sell_price > 0:
            daily_units = min(daily_units, revenue_cap / npc_sell_price)
        npc_daily_units = daily_units
        npc_daily_profit = daily_units * npc_margin

    return FlipOpportunity(
        product_id=product_id,
        buy_price=buy_price,
        sell_price=sell_price,
        margin=margin,
        roi=roi,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_moving_week=buy_moving_week,
        sell_moving_week=sell_moving_week,
        items_per_hour=items_per_hour,
        profit_per_hour=profit_per_hour,
        npc_sell_price=npc_sell_price,
        npc_buy_price=npc_buy_price,
        npc_margin=npc_margin,
        npc_roi=npc_roi,
        npc_profit_per_hour=npc_profit_per_hour,
        npc_profit_per_revenue=npc_profit_per_revenue,
        npc_daily_units=npc_daily_units,
        npc_daily_profit=npc_daily_profit,
    )


def calculate_all_flips(
    api_data: dict[str, Any],
    tax_rate: float = TAX_RATE,
    npc_sell_prices: dict[str, float] | None = None,
    capture_factor: float = DEFAULT_CAPTURE_FACTOR,
    revenue_cap: float = DAILY_NPC_SELL_CAP,
    npc_buy_method: str = "buy_order",
) -> list[FlipOpportunity]:
    """Compute a FlipOpportunity for every product in a bazaar payload.

    npc_sell_prices maps product_id -> fixed NPC sell price. Products absent
    from the map are treated as not NPC-sellable (npc_* fields stay None).

    capture_factor and revenue_cap feed the per-item npc_daily_units /
    npc_daily_profit estimates (see calculate_flip).
    """
    products = api_data.get("products", {}) or {}
    npc = npc_sell_prices or {}
    return [
        calculate_flip(
            pid, pdata, tax_rate, npc.get(pid),
            capture_factor=capture_factor, revenue_cap=revenue_cap,
            npc_buy_method=npc_buy_method,
        )
        for pid, pdata in products.items()
    ]


def filter_flips(
    flips: Iterable[FlipOpportunity],
    *,
    min_margin: float = 0.0,
    min_roi: float = 0.0,
    min_buy_moving_week: int = 0,
    min_sell_moving_week: int = 0,
    min_buy_volume: int = 0,
    min_sell_volume: int = 0,
    min_npc_margin: float | None = None,
    max_buy_price: float | None = None,
    name_query: str = "",
    blacklist: set[str] | None = None,
) -> list[FlipOpportunity]:
    """Keep only liquid, profitable flips.

    Both sides of the order book must clear the volume thresholds: a flip
    isn't real if there are no sellers (your buy order won't fill) or no
    buyers (your sell offer won't fill).

    When min_npc_margin is set, only NPC-sellable items whose npc_margin clears
    the threshold are kept (items the NPC won't buy are dropped).

    max_buy_price caps the per-unit acquisition cost (instant-buy price), acting
    as a budget filter so you only see items you can afford.

    name_query is a case-insensitive substring filter against product_id.
    Whitespace is treated as ``_`` so "enchanted lapis" matches
    ENCHANTED_LAPIS_LAZULI.

    blacklist is a set of product IDs to drop entirely (user-hidden items).
    """
    needle = name_query.strip().lower().replace(" ", "_")
    bl = blacklist or frozenset()
    return [
        f
        for f in flips
        if f.product_id not in bl
        and f.margin >= min_margin
        and f.roi >= min_roi
        and f.buy_moving_week >= min_buy_moving_week
        and f.sell_moving_week >= min_sell_moving_week
        and f.buy_volume >= min_buy_volume
        and f.sell_volume >= min_sell_volume
        and (
            min_npc_margin is None
            or (f.npc_margin is not None and f.npc_margin >= min_npc_margin)
        )
        and (max_buy_price is None or f.buy_price <= max_buy_price)
        and (not needle or needle in f.product_id.lower())
    ]


def sort_flips(
    flips: Iterable[FlipOpportunity],
    key: str = "profit_per_hour",
    reverse: bool = True,
) -> list[FlipOpportunity]:
    """Stable sort flips by any FlipOpportunity attribute.

    Missing values (e.g. npc_* on non-NPC items) sort last in both directions.
    """
    def sort_value(f: FlipOpportunity) -> float:
        value = getattr(f, key, None)
        if value is None:
            return float("-inf") if reverse else float("inf")
        return value

    return sorted(flips, key=sort_value, reverse=reverse)


# ---------------------------------------------------------------------------
# Daily NPC sell-cap planner
# ---------------------------------------------------------------------------
#
# Hypixel caps how much you can sell to NPC merchants at 500M coins of *revenue*
# per day. That cap — not coins spent or hours played — is the binding
# constraint, which turns "what should I flip to the NPC?" into a fractional
# knapsack problem:
#
#   * Each coin of NPC revenue is the scarce resource.
#   * The value density of an item is therefore profit per revenue coin
#     (npc_margin / npc_sell_price), NOT its absolute margin.
#   * Each item can only absorb so much revenue per day, because you can only
#     acquire roughly buyMovingWeek/7 units before you run out of cheap supply.
#
# Greedily filling the revenue budget from the highest-density item down is the
# optimal solution to fractional knapsack with per-item caps.

@dataclass(frozen=True)
class NpcAllocation:
    """One line of a daily NPC plan: how many of an item to flip to the NPC."""

    product_id: str
    units: float            # units to buy on bazaar and sell to the NPC per day
    buy_price: float        # acquisition cost per unit (your buy order = sell_price)
    npc_sell_price: float   # NPC payout per unit
    npc_margin: float       # profit per unit
    revenue: float          # units * npc_sell_price — counts against the daily cap
    cost: float             # units * buy_price — capital you must deploy
    profit: float           # units * npc_margin
    profit_per_revenue: float


@dataclass(frozen=True)
class NpcPlan:
    """Result of allocating the daily NPC sell cap across items for max profit."""

    revenue_cap: float
    capture_factor: float
    allocations: list[NpcAllocation]
    total_revenue: float
    total_cost: float
    total_profit: float
    revenue_utilization: float   # total_revenue / revenue_cap (0..1)
    limited_by: str              # "revenue_cap" or "liquidity"


def plan_daily_npc(
    flips: Iterable[FlipOpportunity],
    *,
    revenue_cap: float = DAILY_NPC_SELL_CAP,
    capture_factor: float = DEFAULT_CAPTURE_FACTOR,
    min_npc_margin: float = 0.0,
) -> NpcPlan:
    """Greedily allocate the daily NPC sell cap across items to maximise profit.

    Ranks NPC-sellable items by profit per coin of NPC revenue
    (``npc_margin / npc_sell_price``) and fills ``revenue_cap`` from the top down,
    capping each item at the units you can realistically acquire per day
    (``buyMovingWeek / 7 * capture_factor``).

    ``capture_factor`` (0..1) is the share of the weekly instant-sell flow you
    expect to win against competing buy orders.

    ``limited_by`` tells you which wall you hit:
        "revenue_cap" — you filled the 500M cap; item *selection* is what matters.
        "liquidity"   — you ran out of cheap, NPC-profitable supply before the cap;
                        there simply aren't enough good flips to use the full cap.
    """
    candidates = [
        f
        for f in flips
        if f.npc_margin is not None
        and f.npc_margin > min_npc_margin
        and f.npc_sell_price is not None
        and f.npc_sell_price > 0
        and (f.npc_buy_price if f.npc_buy_price is not None else f.sell_price) > 0
    ]
    # Highest profit-per-revenue-coin first (fractional knapsack density order).
    candidates.sort(
        key=lambda f: f.npc_margin / f.npc_sell_price,  # type: ignore[operator]
        reverse=True,
    )

    remaining = revenue_cap
    allocations: list[NpcAllocation] = []
    hit_cap = False
    for f in candidates:
        if remaining <= 0:
            hit_cap = True
            break
        assert f.npc_sell_price is not None and f.npc_margin is not None
        daily_units = (f.buy_moving_week / DAYS_PER_WEEK) * capture_factor
        if daily_units <= 0:
            continue
        units = min(daily_units, remaining / f.npc_sell_price)
        if units <= 0:
            continue
        revenue = units * f.npc_sell_price
        # Acquisition cost mirrors the buy method chosen upstream (buy order vs
        # instant buy); fall back to sell_price for safety if it wasn't set.
        acquire_price = f.npc_buy_price if f.npc_buy_price is not None else f.sell_price
        allocations.append(
            NpcAllocation(
                product_id=f.product_id,
                units=units,
                buy_price=acquire_price,
                npc_sell_price=f.npc_sell_price,
                npc_margin=f.npc_margin,
                revenue=revenue,
                cost=units * acquire_price,
                profit=units * f.npc_margin,
                profit_per_revenue=f.npc_margin / f.npc_sell_price,
            )
        )
        remaining -= revenue

    total_revenue = sum(a.revenue for a in allocations)
    total_cost = sum(a.cost for a in allocations)
    total_profit = sum(a.profit for a in allocations)
    return NpcPlan(
        revenue_cap=revenue_cap,
        capture_factor=capture_factor,
        allocations=allocations,
        total_revenue=total_revenue,
        total_cost=total_cost,
        total_profit=total_profit,
        revenue_utilization=(total_revenue / revenue_cap) if revenue_cap > 0 else 0.0,
        limited_by="revenue_cap" if (hit_cap or remaining <= 1e-6) else "liquidity",
    )
