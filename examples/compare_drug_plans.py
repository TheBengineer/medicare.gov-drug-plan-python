"""Compare Part D drug plans for a ZIP code, a drug list, and nearby pharmacies.

    uv run examples/compare_drug_plans.py 30577 atorvastatin lisinopril
"""

import sys

from medicare_plan_finder import PDP, PlanFinder, Prescription, plan_key

YEAR = 2027


def main() -> None:
    zipcode, *drug_names = sys.argv[1:] or ["30577", "atorvastatin"]

    with PlanFinder() as pf:
        county = pf.counties(zipcode)[0]
        print(f"ZIP {zipcode}: {county['name']} County, {county['state']} (FIPS {county['fips']})")

        prescriptions = []
        for name in drug_names:
            drug = pf.search_drugs(name, YEAR)[0]
            dosage = pf.drug_forms(drug["rxcui"], YEAR)[0]
            package = dosage["packages"][0]
            qty = package["default_quantity_30"] or 30
            prescriptions.append(Prescription.per_days(package["ndc"], qty, 30))
            print(f"  drug: {dosage['name']} {dosage['strength']} {dosage['strength_units']} "
                  f"(NDC {package['ndc']}, {qty}/30 days)")

        pharmacies = pf.pharmacies(zipcode, radius_miles=5, count=3)["pharmacies"]["features"]
        npis = [p["properties"]["npi"] for p in pharmacies]
        for p in pharmacies:
            print(f"  pharmacy: {p['properties']['name']} (NPI {p['properties']['npi']})")

        plans = pf.all_plans(
            zipcode=zipcode,
            fips=county["fips"],
            year=YEAR,
            plan_type=PDP,
            prescriptions=prescriptions,
            pharmacy_npis=npis,
        )

    print(f"\n{len(plans)} drug plans, cheapest estimated total (premium + drugs) first:\n")
    print(f"{'Plan':45} {'ID':18} {'Premium':>8} {'Deduct':>7} {'Est. total':>11} {'Stars':>5}")
    for p in sorted(plans, key=lambda p: p["remaining_premium_and_drugs"]):
        print(f"{p['name'][:45]:45} {plan_key(p):18} {p['partd_premium']:>8.2f} "
              f"{p['drug_plan_deductible']:>7.0f} {p['remaining_premium_and_drugs']:>11.2f} "
              f"{p['overall_star_rating']['rating']:>5}")


if __name__ == "__main__":
    main()
