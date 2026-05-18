"""Parse the raw observation dict into a typed State dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from .constants import REP_RANK, WALKOUT_RANK


@dataclass
class InvItem:
    ingredient: str
    total_kg: float
    shelf_life_days: int
    batches: list[dict]  # [{quantity_kg, expires_in_days}]

    def expiring_within(self, n: int) -> float:
        return sum(b["quantity_kg"] for b in self.batches
                   if b.get("expires_in_days", 99) <= n)


@dataclass
class State:
    day: int
    day_of_week: str
    days_remaining: int
    cash: float
    yesterday_revenue: float
    yesterday_total_costs: float
    cost_breakdown: dict
    inventory: dict[str, InvItem]
    pending_orders: list[dict]
    pending_by_ingredient: dict[str, float]
    delivery_history: list[dict]
    supplier_catalog: list[dict]
    menu_book: dict[str, dict]
    active_menu: list[str]
    staff_level: int
    reputation_band: str
    reputation_rank: int
    recent_reviews: list[dict]
    customer_trend: str
    weather_today: str
    weather_forecast: list[str]
    alerts: list[str]
    service_summary: dict
    notes_raw: str
    walkout_band: str
    walkout_rank: int
    table_util_peak: float
    bottleneck_hours: list[int]
    total_covers_yesterday: int

    @property
    def fresh_inventory_kg(self) -> dict[str, float]:
        """Stock NOT expiring in <=1 day (treat near-expiry as zero)."""
        return {ing: max(0.0, item.total_kg - item.expiring_within(1))
                for ing, item in self.inventory.items()}


def parse(observation: dict, day: int) -> State:
    inv_map: dict[str, InvItem] = {}
    for item in observation.get("inventory", []) or []:
        inv_map[item["ingredient"]] = InvItem(
            ingredient=item["ingredient"],
            total_kg=float(item.get("total_kg", 0)),
            shelf_life_days=int(item.get("shelf_life_days", 0)),
            batches=list(item.get("batches", []) or []),
        )

    pending_by_ing: dict[str, float] = {}
    for po in observation.get("pending_orders", []) or []:
        pending_by_ing[po["ingredient"]] = pending_by_ing.get(po["ingredient"], 0) + float(po.get("quantity_kg", 0))

    menu_book_list = observation.get("menu_book", []) or []
    menu_book = {entry["name"]: entry for entry in menu_book_list}

    svc = observation.get("service_summary", {}) or {}
    walkout = svc.get("walkout_band", "None")

    rep = observation.get("reputation_band", "Very Good")

    return State(
        day=day,
        day_of_week=observation.get("day_of_week", "Monday"),
        days_remaining=int(observation.get("days_remaining", 30 - day + 1)),
        cash=float(observation.get("cash", 0)),
        yesterday_revenue=float(observation.get("yesterday_revenue", 0) or 0),
        yesterday_total_costs=float(observation.get("yesterday_total_costs", 0) or 0),
        cost_breakdown=dict(observation.get("cost_breakdown", {}) or {}),
        inventory=inv_map,
        pending_orders=list(observation.get("pending_orders", []) or []),
        pending_by_ingredient=pending_by_ing,
        delivery_history=list(observation.get("delivery_history", []) or []),
        supplier_catalog=list(observation.get("supplier_catalog", []) or []),
        menu_book=menu_book,
        active_menu=list(observation.get("active_menu", []) or []),
        staff_level=int(observation.get("staff_level", 8) or 8),
        reputation_band=rep,
        reputation_rank=REP_RANK.get(rep, 3),
        recent_reviews=list(observation.get("recent_reviews", []) or []),
        customer_trend=observation.get("customer_trend", "Stable"),
        weather_today=observation.get("weather_today", "cloudy"),
        weather_forecast=list(observation.get("weather_forecast", []) or []),
        alerts=list(observation.get("alerts", []) or []),
        service_summary=svc,
        notes_raw=observation.get("notes", "") or "",
        walkout_band=walkout,
        walkout_rank=WALKOUT_RANK.get(walkout, 0),
        table_util_peak=float(svc.get("table_utilization_peak", 0) or 0),
        bottleneck_hours=list(svc.get("kitchen_bottleneck_hours", []) or []),
        total_covers_yesterday=int(svc.get("total_covers", 0) or 0),
    )
