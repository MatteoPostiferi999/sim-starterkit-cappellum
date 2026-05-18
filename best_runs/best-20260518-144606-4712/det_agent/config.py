"""Tunable knobs — read once at module import.

Set via env vars to A/B different variants in parallel without forking code:
  DET_BASE_PRICE_SHIFT  baseline price multiplier shift           (default 0.05)
  DET_PREMIUM_EXTRA     extra mult for premium dishes              (default 0.04)
  DET_ELASTIC_EXTRA     extra mult for elastic dishes (negative)   (default -0.03)
  DET_WEEKEND_LIFT      weekend price lift                         (default 0.05)
  DET_END_GAME_LIFT     last-5-days price lift                     (default 0.06)
  DET_OVER_ORDER        baseline over-order factor for fill rates  (default 1.25)
  DET_OVER_ORDER_CRISIS over-order factor when supply crisis       (default 1.60)
  DET_BUFFER_FRESH      buffer days for short-shelf ingredients    (default 3)
  DET_BUFFER_DRY        buffer days for long-shelf ingredients     (default 5)
  DET_STAFF_DELTA       int added to all DOW staff defaults        (default 0)
  DET_MARKETING_MODE    off|low|default|heavy                      (default default)
  DET_HH_MODE           off|default|aggressive                     (default default)
  DET_CASH_RESERVE      EUR floor reserve                          (default 2000)
"""

from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _s(name: str, default: str) -> str:
    return os.getenv(name, default)


# Pricing
BASE_PRICE_SHIFT = _f("DET_BASE_PRICE_SHIFT", 0.05)
PREMIUM_EXTRA = _f("DET_PREMIUM_EXTRA", 0.04)
ELASTIC_EXTRA = _f("DET_ELASTIC_EXTRA", -0.03)
WEEKEND_LIFT = _f("DET_WEEKEND_LIFT", 0.05)
SLOW_DAY_CUT = _f("DET_SLOW_DAY_CUT", -0.04)
SUNNY_LIFT = _f("DET_SUNNY_LIFT", 0.02)
RAINY_CUT = _f("DET_RAINY_CUT", -0.02)
END_GAME_LIFT = _f("DET_END_GAME_LIFT", 0.06)
MID_GAME_LIFT = _f("DET_MID_GAME_LIFT", 0.03)
WALKOUT_DISCOUNT = _f("DET_WALKOUT_DISCOUNT", -0.04)
DECLINING_DISCOUNT = _f("DET_DECLINING_DISCOUNT", -0.05)
TOURIST_SURGE_LIFT = _f("DET_TOURIST_SURGE_LIFT", 0.08)
TOURIST_DROP_CUT = _f("DET_TOURIST_DROP_CUT", -0.05)
EXPIRY_DISCOUNT = _f("DET_EXPIRY_DISCOUNT", -0.03)
INFLATION_LIFT = _f("DET_INFLATION_LIFT", 0.05)

# Orders
OVER_ORDER = _f("DET_OVER_ORDER", 1.25)
OVER_ORDER_CRISIS = _f("DET_OVER_ORDER_CRISIS", 1.60)
BUFFER_FRESH = _i("DET_BUFFER_FRESH", 3)
BUFFER_DRY = _i("DET_BUFFER_DRY", 5)
CASH_RESERVE = _f("DET_CASH_RESERVE", 2000.0)

# Staff
STAFF_DELTA = _i("DET_STAFF_DELTA", 0)

# Promo
MARKETING_MODE = _s("DET_MARKETING_MODE", "default")
HH_MODE = _s("DET_HH_MODE", "default")

# Variant tag (purely for logging)
VARIANT = _s("DET_VARIANT", "v1")
