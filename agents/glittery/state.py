"""Parse server observation into a typed WorldState dataclass.

All field access in the rest of the agent goes through this. Defensive — every
field uses .get() with a sane default so a missing field can never raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import REP_RANK, WALKOUT_RANK


@dataclass
class InventoryItem:
    ingredient: str
    total_kg: float
    shelf_life_days: int
    batches: list[dict] = field(default_factory=list)  # [{quantity_kg, expires_in_days}]

    def fresh_stock(self, min_expiry_days: int = 1) -> float:
        """Stock that won't expire in the next `min_expiry_days` days."""
        return sum(b["quantity_kg"] for b in self.batches if b["expires_in_days"] > min_expiry_days)

    def expiring_soon(self, within_days: int = 2) -> float:
        return sum(b["quantity_kg"] for b in self.batches if b["expires_in_days"] <= within_days)


@dataclass
class WorldState:
    # Identifiers
    day: int = 1
    day_of_week: str = "Monday"
    days_remaining: int = 30

    # Cash & costs
    cash: float = 0.0
    yesterday_revenue: float = 0.0
    yesterday_total_costs: float = 0.0
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    # Inventory & supply
    inventory: dict[str, InventoryItem] = field(default_factory=dict)
    pending_by_ingredient: dict[str, float] = field(default_factory=dict)
    pending_by_supplier: dict[str, list[dict]] = field(default_factory=dict)
    delivery_history: list[dict] = field(default_factory=list)
    supplier_catalog: dict[str, dict] = field(default_factory=dict)  # name -> raw entry

    # Operations
    staff_level: int = 8
    active_menu: list[str] = field(default_factory=list)
    menu_book: dict[str, dict] = field(default_factory=dict)  # name -> recipe entry

    # Service yesterday
    service: dict[str, Any] = field(default_factory=dict)
    total_covers_yesterday: int = 0
    walkout_band: str = "None"
    walkout_rank: int = 0
    kitchen_bottleneck_hours: list = field(default_factory=list)
    table_utilization_peak: float = 0.0
    substitution_count: int = 0
    dishes_unavailable_at: dict = field(default_factory=dict)
    peak_wait_minutes: float = 0.0
    avg_wait_minutes: float = 0.0
    hourly_covers: list[int] = field(default_factory=list)

    # Reputation & reviews
    reputation_band: str = "Very Good"
    reputation_rank: int = REP_RANK["Very Good"]
    customer_trend: str = "Stable"
    recent_reviews: list[dict] = field(default_factory=list)

    # Environment
    weather_today: str = "sunny"
    weather_forecast: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    # Notes (raw — decoded in memory.py)
    notes_raw: str = ""

    # Reference back to raw observation for any field we forgot
    raw: dict = field(default_factory=dict)


def parse(observation: dict, day: int) -> WorldState:
    """Build a WorldState from a server observation. Defensive: never raises."""
    obs = observation or {}

    inv: dict[str, InventoryItem] = {}
    for item in obs.get("inventory", []) or []:
        ing = item.get("ingredient")
        if not ing:
            continue
        inv[ing] = InventoryItem(
            ingredient=ing,
            total_kg=float(item.get("total_kg", 0.0)),
            shelf_life_days=int(item.get("shelf_life_days", 7)),
            batches=list(item.get("batches", []) or []),
        )

    pending_by_ing: dict[str, float] = {}
    pending_by_sup: dict[str, list[dict]] = {}
    for po in obs.get("pending_orders", []) or []:
        ing = po.get("ingredient")
        sup = po.get("supplier")
        qty = float(po.get("quantity_kg", 0.0))
        if ing:
            pending_by_ing[ing] = pending_by_ing.get(ing, 0.0) + qty
        if sup:
            pending_by_sup.setdefault(sup, []).append(po)

    supplier_catalog = {}
    for s in obs.get("supplier_catalog", []) or []:
        name = s.get("name")
        if name:
            supplier_catalog[name] = s

    menu_book = {}
    for r in obs.get("menu_book", []) or []:
        name = r.get("name")
        if name:
            menu_book[name] = r

    service = obs.get("service_summary") or {}
    walkout = service.get("walkout_band", "None")
    rep_band = obs.get("reputation_band", "Very Good")

    return WorldState(
        day=int(obs.get("day", day) or day),
        day_of_week=obs.get("day_of_week", "Monday"),
        days_remaining=int(obs.get("days_remaining", 30 - day + 1) or 0),

        cash=float(obs.get("cash", 0.0)),
        yesterday_revenue=float(obs.get("yesterday_revenue", 0.0)),
        yesterday_total_costs=float(obs.get("yesterday_total_costs", 0.0)),
        cost_breakdown=dict(obs.get("cost_breakdown") or {}),

        inventory=inv,
        pending_by_ingredient=pending_by_ing,
        pending_by_supplier=pending_by_sup,
        delivery_history=list(obs.get("delivery_history") or []),
        supplier_catalog=supplier_catalog,

        staff_level=int(obs.get("staff_level", 8) or 8),
        active_menu=list(obs.get("active_menu") or []),
        menu_book=menu_book,

        service=service,
        total_covers_yesterday=int(service.get("total_covers", 0) or 0),
        walkout_band=walkout,
        walkout_rank=WALKOUT_RANK.get(walkout, 0),
        kitchen_bottleneck_hours=list(service.get("kitchen_bottleneck_hours") or []),
        table_utilization_peak=float(service.get("table_utilization_peak", 0.0) or 0.0),
        substitution_count=int(service.get("substitution_count", 0) or 0),
        dishes_unavailable_at=dict(service.get("dishes_unavailable_at") or {}),
        peak_wait_minutes=float(service.get("peak_wait_minutes", 0.0) or 0.0),
        avg_wait_minutes=float(service.get("avg_wait_minutes", 0.0) or 0.0),
        hourly_covers=list(service.get("hourly_covers") or []),

        reputation_band=rep_band,
        reputation_rank=REP_RANK.get(rep_band, REP_RANK["Very Good"]),
        customer_trend=obs.get("customer_trend", "Stable"),
        recent_reviews=list(obs.get("recent_reviews") or []),

        weather_today=obs.get("weather_today", "sunny"),
        weather_forecast=list(obs.get("weather_forecast") or []),
        alerts=list(obs.get("alerts") or []),

        notes_raw=obs.get("notes", "") or "",
        raw=obs,
    )


def base_price(state: WorldState, dish: str) -> float | None:
    """Resolve a dish's base price from the menu_book."""
    entry = state.menu_book.get(dish)
    return float(entry["base_price"]) if entry and "base_price" in entry else None


def current_price(state: WorldState, dish: str) -> float | None:
    entry = state.menu_book.get(dish)
    return float(entry["current_price"]) if entry and "current_price" in entry else None
