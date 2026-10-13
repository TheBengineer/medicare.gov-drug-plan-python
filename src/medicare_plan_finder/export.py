"""Flatten a compare_plans() report into one CSV row per plan."""

from __future__ import annotations

import csv
from pathlib import Path

PLAN_TYPE_LABELS = {
    "PLAN_TYPE_PDP": "PDP",
    "PLAN_TYPE_MAPD": "MAPD",
    "PLAN_TYPE_MA": "MA",
}

# (column, function of the plan dict)
_PLAN_COLUMNS = [
    ("plan_key", lambda p: p.get("plan_key")),
    ("name", lambda p: p.get("name")),
    ("organization", lambda p: p.get("organization_name")),
    ("plan_type", lambda p: PLAN_TYPE_LABELS.get(p.get("plan_type"), p.get("plan_type"))),
    ("covers_drugs", lambda p: p.get("plan_type") != "PLAN_TYPE_MA"),
    ("estimated_yearly_total", lambda p: p.get("remaining_premium_and_drugs")),
    ("estimated_yearly_total_retail", lambda p: p.get("remaining_premium_and_drugs_retail")),
    ("estimated_yearly_total_mail_order", lambda p: p.get("remaining_premium_and_drugs_mail_order")),
    ("yearly_drug_cost", lambda p: p.get("annual_drugs_total")),
    ("yearly_premium_remaining", lambda p: p.get("total_remaining_premium")),
    ("monthly_premium", lambda p: p.get("calculated_monthly_premium")),
    ("part_c_premium", lambda p: p.get("partc_premium")),
    ("part_d_premium", lambda p: p.get("partd_premium")),
    ("part_b_premium_reduction", lambda p: p.get("partb_premium_reduction")),
    ("drug_deductible", lambda p: p.get("drug_plan_deductible")),
    ("health_deductible", lambda p: p.get("annual_deductible")),
    ("max_out_of_pocket", lambda p: p.get("maximum_oopc")),
    ("star_rating", lambda p: (p.get("overall_star_rating") or {}).get("rating")),
    ("in_network_pharmacies", lambda p: sum(1 for ph in p.get("pharmacies") or [] if ph.get("in_network"))),
    ("supports_mail_order", lambda p: not p.get("does_not_support_mail_order")),
    ("contract_id", lambda p: p.get("contract_id")),
    ("plan_id", lambda p: p.get("plan_id")),
    ("segment_id", lambda p: p.get("segment_id")),
    ("year", lambda p: p.get("contract_year")),
    ("website", lambda p: p.get("url")),
]


def _drug_label(drug: dict) -> str:
    name = drug.get("name") or drug.get("ndc")
    return f"{name} {drug['strength']}" if drug.get("strength") else name


def _drug_columns(plan: dict, drug: dict) -> dict:
    """Per-drug values; only present when the report was made with include_details."""
    ndc = drug["ndc"]
    cov = next((c for c in plan.get("drug_coverage") or [] if c.get("ndc") == ndc), {})
    costs = [
        dc
        for pharmacy in plan.get("drug_costs") or []
        for dc in pharmacy.get("drug_costs") or []
        if dc.get("ndc") == ndc
    ]
    yearly = [c["estimated_yearly_total"] for c in costs if c.get("estimated_yearly_total") is not None]
    return {
        "covered": any(c.get("covered") for c in costs) if costs else cov.get("on_formulary"),
        "tier": cov.get("tier"),
        "prior_auth": cov.get("prior_auth"),
        "step_therapy": cov.get("step_therapy"),
        "quantity_limit": cov.get("quantity_limit"),
        "lowest_yearly_cost": min(yearly) if yearly else None,
    }


def report_rows(report: dict) -> tuple[list[str], list[dict]]:
    """Column names and one row per plan, in the report's order (cheapest first)."""
    drugs = report.get("query", {}).get("drugs", [])
    has_details = any("drug_coverage" in p or "drug_costs" in p for p in report["plans"])

    header = ["rank"] + [name for name, _ in _PLAN_COLUMNS]
    drug_fields = ["covered", "tier", "prior_auth", "step_therapy", "quantity_limit", "lowest_yearly_cost"]
    if has_details:
        for d in drugs:
            header += [f"{_drug_label(d)}: {f}" for f in drug_fields]

    rows = []
    for rank, plan in enumerate(report["plans"], 1):
        row = {"rank": rank}
        row.update({name: get(plan) for name, get in _PLAN_COLUMNS})
        if has_details:
            for d in drugs:
                values = _drug_columns(plan, d)
                row.update({f"{_drug_label(d)}: {f}": values[f] for f in drug_fields})
        rows.append({k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()})
    return header, rows


def write_csv(report: dict, path: Path | str) -> int:
    """Write the report as CSV; returns the number of plan rows written."""
    header, rows = report_rows(report)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
