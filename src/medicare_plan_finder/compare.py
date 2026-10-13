"""Run a full plan comparison and collect the results as one JSON-ready dict."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from .client import (
    FREQUENCIES,
    LIS_NO_HELP,
    MA,
    MAPD,
    PDP,
    SORT_ANNUAL_TOTAL,
    PlanFinder,
    Prescription,
    plan_key,
)

# Searches the site runs for each plan category.
PLAN_CATEGORIES = {
    "pdp": [PDP],  # stand-alone drug plans
    "ma": [MA, MAPD],  # Medicare Advantage, with or without drug coverage
}

# Plans per /drugs/cost request.
_COST_BATCH = 10


class CompareError(ValueError):
    pass


@dataclass
class DrugSpec:
    """A drug to price: by name (with optional strength), or by exact NDC."""

    name: str | None = None
    ndc: str | None = None
    strength: str | None = None
    quantity: int | None = None
    days: int = 30

    @classmethod
    def parse(cls, spec: str) -> "DrugSpec":
        """Parse "NAME[:STRENGTH[:QTY[:DAYS]]]", e.g. "atorvastatin:40:30" or "lisinopril::60"."""
        name, strength, qty, days = (spec.split(":") + [""] * 4)[:4]
        return cls(
            name=name.strip(),
            strength=strength.strip() or None,
            quantity=int(qty) if qty else None,
            days=int(days) if days else 30,
        )

    @classmethod
    def parse_ndc(cls, spec: str) -> "DrugSpec":
        """Parse "NDC[:QTY[:DAYS]]"."""
        ndc, qty, days = (spec.split(":") + [""] * 3)[:3]
        return cls(
            ndc=ndc.strip(),
            quantity=int(qty) if qty else None,
            days=int(days) if days else 30,
        )


def _strength_matches(dosage: dict, strength: str) -> bool:
    want = strength.lower().replace(" ", "")
    have = f"{dosage['strength']}{dosage['strength_units']}".lower().replace(" ", "")
    return have == want or dosage["strength"].lower() == want


def resolve_drug(pf: PlanFinder, spec: DrugSpec, year: int) -> dict:
    """Turn a DrugSpec into a concrete package: {name, rxcui, strength, ndc, quantity, frequency}."""
    if spec.days not in FREQUENCIES:
        raise CompareError(f"days must be one of {sorted(FREQUENCIES)}, got {spec.days}")
    if spec.ndc:
        return {
            "query": spec.ndc,
            "name": None,
            "rxcui": None,
            "strength": None,
            "ndc": spec.ndc,
            "quantity": spec.quantity or 30,
            "frequency": FREQUENCIES[spec.days],
        }

    matches = pf.search_drugs(spec.name, year)
    if not matches:
        raise CompareError(f"No drug found matching {spec.name!r}")
    exact = [d for d in matches if d["name"].lower() == spec.name.lower()]
    drug = (exact or matches)[0]

    dosages = pf.drug_forms(drug["rxcui"], year)
    if spec.strength:
        chosen = [d for d in dosages if _strength_matches(d, spec.strength)]
        if not chosen:
            options = ", ".join(sorted({f"{d['strength']} {d['strength_units']}" for d in dosages}))
            raise CompareError(
                f"{drug['name']}: no strength {spec.strength!r}. Available: {options}"
            )
        dosage = chosen[0]
    else:
        dosage = dosages[0]

    package = dosage["packages"][0]
    default_qty = package.get(f"default_quantity_{spec.days}") or package.get("default_quantity_30")
    return {
        "query": spec.name,
        "name": dosage["name"],
        "rxcui": dosage["rxcui"],
        "form": dosage["form"],
        "strength": f"{dosage['strength']} {dosage['strength_units']}",
        "ndc": package["ndc"],
        "quantity": spec.quantity or default_qty or 30,
        "frequency": FREQUENCIES[spec.days],
    }


def pick_county(counties: list[dict], zipcode: str, county: str | None) -> dict:
    """Choose the county by name or FIPS; required when a ZIP spans several counties."""
    if not counties:
        raise CompareError(f"No counties found for ZIP {zipcode}")
    if county:
        for c in counties:
            if county.lower() in (c["fips"], c["name"].lower()):
                return c
    elif len(counties) == 1:
        return counties[0]
    options = ", ".join(f"{c['name']} ({c['fips']})" for c in counties)
    raise CompareError(
        f"ZIP {zipcode} spans several counties; choose one by name or FIPS: {options}"
    )


def compare_plans(
    pf: PlanFinder,
    *,
    zipcode: str,
    year: int,
    drugs: Iterable[DrugSpec],
    county: str | None = None,
    pharmacy_npis: Iterable[str] = (),
    nearest_pharmacies: int = 3,
    categories: Iterable[str] = ("pdp",),
    lis: str = LIS_NO_HELP,
    include_details: bool = False,
    progress: Callable[[str], None] = lambda msg: None,
) -> dict:
    """Search every plan for a ZIP, drug list and pharmacies; return a JSON-ready report.

    Plans are sorted by estimated yearly cost (premiums + drugs). With
    include_details, each plan also gets its full details, per-drug costs at
    each pharmacy, and formulary info (tier, prior auth, limits) for each drug.
    """
    chosen_county = pick_county(pf.counties(zipcode), zipcode, county)
    progress(f"County: {chosen_county['name']}, {chosen_county['state']} ({chosen_county['fips']})")

    resolved = [resolve_drug(pf, spec, year) for spec in drugs]
    for d in resolved:
        progress(f"Drug: {d['name'] or d['ndc']} {d['strength'] or ''} (NDC {d['ndc']}, "
                 f"{d['quantity']} per {d['frequency']})")
    prescriptions = [
        Prescription(ndc=d["ndc"], quantity=d["quantity"], frequency=d["frequency"])
        for d in resolved
    ]

    npis = list(pharmacy_npis)
    pharmacies: list[dict] = []
    if npis:
        pharmacies = [{"npi": n} for n in npis]
    elif nearest_pharmacies > 0:
        found = pf.pharmacies(zipcode, radius_miles=25, count=nearest_pharmacies)
        for f in found["pharmacies"]["features"][:nearest_pharmacies]:
            props = f["properties"]
            pharmacies.append({
                "npi": props["npi"],
                "name": props["name"],
                "address": f"{props['street']}, {props['city']}, {props['state']} {props['zipcode']}",
                "phone": props["phone"],
                "distance_miles": round(props["distance_miles"], 2),
            })
        npis = [p["npi"] for p in pharmacies]
    for p in pharmacies:
        progress(f"Pharmacy: {p.get('name', '')} (NPI {p['npi']})")

    categories = list(categories)
    plans: list[dict] = []
    for cat in categories:
        if cat not in PLAN_CATEGORIES:
            raise CompareError(f"Unknown plan category {cat!r}; use {sorted(PLAN_CATEGORIES)}")
        found = pf.all_plans(
            zipcode=zipcode,
            fips=chosen_county["fips"],
            year=year,
            plan_type=PLAN_CATEGORIES[cat],
            prescriptions=prescriptions,
            pharmacy_npis=npis,
            lis=lis,
            sort_order=SORT_ANNUAL_TOTAL,
            snp_type="SNP_TYPE_NOT_SNP" if cat == "pdp" else None,
        )
        progress(f"Found {len(found)} {cat.upper()} plans")
        plans.extend(found)

    for p in plans:
        p["plan_key"] = plan_key(p)
    plans.sort(key=lambda p: p.get("remaining_premium_and_drugs") or 0)

    if include_details and plans:
        _add_details(pf, plans, prescriptions, npis, lis, progress)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "https://www.medicare.gov/plan-compare/",
        "query": {
            "zipcode": zipcode,
            "county": chosen_county,
            "year": year,
            "plan_categories": categories,
            "lis": lis,
            "drugs": resolved,
            "pharmacies": pharmacies,
        },
        "plan_count": len(plans),
        "plans": plans,
    }


def _add_details(pf, plans, prescriptions, npis, lis, progress) -> None:
    ndcs = [p.ndc for p in prescriptions]
    for i, plan in enumerate(plans, 1):
        progress(f"Details {i}/{len(plans)}: {plan['name']}")
        plan["details"] = pf.plan_details(plan, lis=lis)
        plan["drug_coverage"] = pf.drug_coverage(plan, ndcs) if ndcs else []

    if not prescriptions:
        return
    by_key = {p["plan_key"]: p for p in plans}
    for start in range(0, len(plans), _COST_BATCH):
        batch = plans[start : start + _COST_BATCH]
        for entry in pf.drug_costs(
            plans=batch, prescriptions=prescriptions, pharmacy_npis=npis, lis=lis
        ):
            by_key[plan_key(entry["plan"])]["drug_costs"] = entry["costs"]


__all__ = ["CompareError", "DrugSpec", "PLAN_CATEGORIES", "compare_plans", "resolve_drug"]
