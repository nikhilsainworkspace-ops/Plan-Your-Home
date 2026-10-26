"""Pure, testable planning calculations. Rates are user inputs, never market claims."""
from __future__ import annotations

import math

DEFAULT_CATEGORIES = [
    ("Site preparation", 5.0), ("Foundation", 10.0), ("Structure", 25.0),
    ("Masonry and roofing", 15.0), ("Plumbing and electrical", 10.0),
    ("Doors and windows", 8.0), ("Flooring", 8.0), ("Painting and finishing", 7.0),
    ("Interior work", 8.0), ("External works", 4.0),
]


def positive_number(value, label: str, maximum: float = 1_000_000_000) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if not math.isfinite(number) or number < 0 or number > maximum:
        raise ValueError(f"{label} must be a finite number between 0 and {maximum:g}.")
    return number


def room_area(width_m: float, depth_m: float) -> float:
    width = positive_number(width_m, "Width", 100)
    depth = positive_number(depth_m, "Depth", 100)
    if width <= 0 or depth <= 0:
        raise ValueError("Room dimensions must be greater than zero.")
    return round(width * depth, 2)


def estimate_cost(area_m2: float, cost_per_m2: float, contingency_pct: float, category_percentages=None) -> dict:
    area = positive_number(area_m2, "Built-up area", 10_000_000)
    rate = positive_number(cost_per_m2, "Cost per square metre", 100_000_000)
    contingency = positive_number(contingency_pct, "Contingency", 100)
    base = area * rate
    categories = category_percentages if category_percentages is not None else DEFAULT_CATEGORIES
    percent_sum = sum(positive_number(pct, "Category percentage", 100) for _, pct in categories)
    if categories and abs(percent_sum - 100.0) > 0.05:
        raise ValueError("Estimate categories must add up to 100%.")
    lines = [{"category": label, "percent": pct, "amount": round(base * pct / 100, 2)} for label, pct in categories]
    return {
        "area_m2": area,
        "rate_per_m2": rate,
        "base_cost": round(base, 2),
        "contingency_pct": contingency,
        "contingency_amount": round(base * contingency / 100, 2),
        "total": round(base * (1 + contingency / 100), 2),
        "lines": lines,
    }


def to_display_length(metres: float, units: str) -> float:
    return round(metres * 3.280839895 if units == "imperial" else metres, 2)


def area_from_plot(length_m: float, width_m: float) -> float:
    length = positive_number(length_m, "Plot length", 10000)
    width = positive_number(width_m, "Plot width", 10000)
    return round(length * width, 2)
