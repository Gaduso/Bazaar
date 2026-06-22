"""CLI entry point: ``python -m flipper`` prints the top flips to the terminal.

Useful for quick spot-checks without running the web UI.
"""

from __future__ import annotations

import argparse

from .api import BazaarAPI
from .calculator import calculate_all_flips, filter_flips, sort_flips


def main() -> None:
    parser = argparse.ArgumentParser(description="Print top Hypixel Bazaar flips.")
    parser.add_argument("--min-margin", type=float, default=100.0, help="Minimum profit/unit (default 100)")
    parser.add_argument("--min-volume", type=int, default=50_000, help="Minimum 7-day volume on each side (default 50k)")
    parser.add_argument("--limit", type=int, default=25, help="Number of rows to print (default 25)")
    parser.add_argument("--sort", default="profit_per_hour",
                        help="Sort key: profit_per_hour, margin, roi, items_per_hour, ...")
    args = parser.parse_args()

    api = BazaarAPI()
    data = api.fetch()
    flips = calculate_all_flips(data)
    flips = filter_flips(
        flips,
        min_margin=args.min_margin,
        min_buy_moving_week=args.min_volume,
        min_sell_moving_week=args.min_volume,
    )
    flips = sort_flips(flips, key=args.sort)

    header = f"{'Product':<35} {'Buy@':>10} {'Sell@':>10} {'Margin':>10} {'ROI%':>7} {'Items/h':>10} {'Profit/h':>14}"
    print(header)
    print("-" * len(header))
    for f in flips[: args.limit]:
        name = f.product_id[:34]
        print(
            f"{name:<35} "
            f"{f.sell_price:>10.2f} "
            f"{f.buy_price:>10.2f} "
            f"{f.margin:>10.2f} "
            f"{f.roi:>6.1f}% "
            f"{f.items_per_hour:>10,.0f} "
            f"{f.profit_per_hour:>14,.0f}"
        )


if __name__ == "__main__":
    main()
