"""Hypixel Skyblock Bazaar flipper package.

Public API:
    BazaarAPI            - cached HTTP client for the Hypixel Bazaar endpoint
    FlipOpportunity      - dataclass describing a single flip
    calculate_flip       - build a FlipOpportunity for one product
    calculate_all_flips  - build flips for every product in an API payload
    filter_flips         - drop flips that don't meet liquidity / profit thresholds
    sort_flips           - stable sort by any FlipOpportunity field
    TAX_RATE             - default sale tax (1%)
"""

from .api import BazaarAPI, ItemsAPI
from .blacklist import Blacklist
from .calculator import (
    DAILY_NPC_SELL_CAP,
    DEFAULT_BAZAAR_CAPTURE_FACTOR,
    DEFAULT_CAPTURE_FACTOR,
    SPREAD_SUSPECT_RATIO,
    TAX_RATE,
    FlipOpportunity,
    NpcAllocation,
    NpcPlan,
    calculate_all_flips,
    calculate_flip,
    filter_flips,
    plan_daily_npc,
    sort_flips,
)

__all__ = [
    "BazaarAPI",
    "ItemsAPI",
    "Blacklist",
    "FlipOpportunity",
    "NpcAllocation",
    "NpcPlan",
    "TAX_RATE",
    "DAILY_NPC_SELL_CAP",
    "DEFAULT_CAPTURE_FACTOR",
    "DEFAULT_BAZAAR_CAPTURE_FACTOR",
    "SPREAD_SUSPECT_RATIO",
    "calculate_flip",
    "calculate_all_flips",
    "filter_flips",
    "plan_daily_npc",
    "sort_flips",
]
