# medicare-plan-finder

A Python client for the Medicare.gov Plan Finder ("compare drug plans") tool.

It drives a real Chrome window with Playwright and calls the Plan Finder's own JSON API from inside the page. See [API_NOTES.md](API_NOTES.md) for the endpoints and how they were found.

## Setup

Requires Google Chrome. A graphical display is needed because medicare.gov blocks headless Chrome.

```bash
uv sync
```

## Example

```bash
uv run examples/compare_drug_plans.py 30577 atorvastatin lisinopril
```

```python
from medicare_plan_finder import PDP, PlanFinder, Prescription

with PlanFinder() as pf:
    county = pf.counties("30577")[0]
    drug = pf.search_drugs("atorvastatin", 2027)[0]
    pkg = pf.drug_forms(drug["rxcui"], 2027)[0]["packages"][0]
    rx = [Prescription.per_days(pkg["ndc"], 30, days=30)]
    npis = [f["properties"]["npi"] for f in pf.pharmacies("30577")["pharmacies"]["features"][:3]]

    plans = pf.all_plans(zipcode="30577", fips=county["fips"], year=2027,
                         plan_type=PDP, prescriptions=rx, pharmacy_npis=npis)
    best = min(plans, key=lambda p: p["remaining_premium_and_drugs"])

    pf.plan_details(best)                                    # full benefits
    pf.drug_costs(plans=[best], prescriptions=rx, pharmacy_npis=npis)
    pf.drug_coverage(best, [pkg["ndc"]])                     # tier, prior auth, limits
```

`PlanFinder.request(method, endpoint, params, body)` calls any other endpoint listed in the notes.

## Caveats

- This uses an undocumented API that can change without notice.
- A ZIP code can cover several counties. Plans depend on the county FIPS code, so pick the right one from `counties()`.
- For bulk data, use CMS's official Part D formulary, pharmacy network and pricing public use files instead.
