# medicare.gov drug plan comparison (Python)

Compare Medicare drug plans (Part D) and Medicare Advantage plans from Python or the command line, and save every plan as JSON.

It uses the same JSON API as the [Medicare.gov Plan Finder](https://www.medicare.gov/plan-compare/). Playwright drives a real Chrome window, and the API is called from inside that page, the same way the Plan Finder calls it. [API_NOTES.md](API_NOTES.md) lists the endpoints and how they were found.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Google Chrome
- A graphical display. medicare.gov returns `403 Access Denied` to headless Chrome, so a Chrome window opens while the tool runs.

```bash
git clone https://github.com/TheBengineer/medicare.gov-drug-plan-python.git
cd medicare.gov-drug-plan-python
uv sync
```

## Command line: compare plans and save JSON

```bash
uv run medicare-plans compare --zip 30577 --county Stephens \
    --drug atorvastatin:40 --drug lisinopril:10:60 \
    --type pdp --type ma --details -o plans.json
```

This prints a table sorted by estimated yearly cost (premiums + drugs) and writes every plan to `plans.json`:

```
Plan                                             ID                 Type Premium/mo Drug ded.  Est. year Stars
Wellcare Value Script (PDP)                      2027-S4802-145-0   PDP        5.30       700     106.80     3
Humana Basic Rx Plan (PDP)                       2027-S5884-135-0   PDP        6.30       700     147.60   4.5
...
Saved 53 plans to plans.json
```

| Option | Meaning |
|---|---|
| `--zip` | ZIP code (required) |
| `--county` | County name or FIPS code. Required when the ZIP covers more than one county; the error message lists the choices. |
| `--year` | Coverage year. Defaults to next year from October on, otherwise this year. |
| `--drug NAME[:STRENGTH[:QTY[:DAYS]]]` | A drug looked up by name. Examples: `atorvastatin`, `atorvastatin:40`, `lisinopril:10:60:90`. QTY is units per fill (defaults to the site's usual amount); DAYS is 30, 60, 90, 180 or 360 (default 30). Repeatable. |
| `--ndc NDC[:QTY[:DAYS]]` | A drug given by its exact package code (NDC). Repeatable. |
| `--pharmacy NPI` | Price drugs at this pharmacy. Repeatable, up to 5. |
| `--nearest-pharmacies N` | Without `--pharmacy`, use the N closest pharmacies (default 3). |
| `--type pdp` / `--type ma` | Which plans to include: `pdp` = stand-alone drug plans (default), `ma` = Medicare Advantage. Both can be given. |
| `--lis LEVEL` | Extra Help level, e.g. `LIS_LEVEL_1A`. Defaults to `LIS_NO_HELP`. |
| `--details` | Also save each plan's full benefits, per-drug costs at each pharmacy, and formulary info (tier, prior authorization, quantity limits). Slower and makes a much larger file. |
| `-o FILE` | Output path (default `plans_<zip>_<year>.json`) |

A full run with `--details` across 53 plans took about 30 seconds and produced a 4.8 MB file.

### JSON format

```jsonc
{
  "generated_at": "2026-10-13T14:30:00+00:00",
  "source": "https://www.medicare.gov/plan-compare/",
  "query": {
    "zipcode": "30577",
    "county": {"name": "Stephens", "fips": "13257", "state": "GA"},
    "year": 2027,
    "plan_categories": ["pdp", "ma"],
    "lis": "LIS_NO_HELP",
    "drugs": [{"query": "atorvastatin", "name": "Atorvastatin Calcium", "strength": "40 MG",
               "rxcui": "...", "ndc": "50090591500", "quantity": 30, "frequency": "FREQUENCY_30_DAYS"}],
    "pharmacies": [{"npi": "1114087541", "name": "JERRY WHITES PHARMACY", "address": "...", "distance_miles": 0.04}]
  },
  "plan_count": 53,
  "plans": [
    {
      "plan_key": "2027-S4802-145-0",          // year-contract-plan-segment
      "name": "Wellcare Value Script (PDP)",
      "plan_type": "PLAN_TYPE_PDP",            // PDP, MAPD (MA with drugs), MA (no drugs)
      "organization_name": "Wellcare",
      "partd_premium": 5.3, "partc_premium": 0,
      "drug_plan_deductible": 700,
      "annual_drugs_total": 43,
      "remaining_premium_and_drugs": 106.8,    // estimated yearly cost; sort key
      "overall_star_rating": {"rating": 3},
      "pharmacies": [{"npi": "1114087541", "in_network": true}],
      // ...all other fields from the Plan Finder search...

      // only with --details:
      "details": {...},        // full benefits, phone numbers, address, cost sharing
      "drug_costs": [...],     // per pharmacy -> per drug: full_cost, coverage_reason, estimated_yearly_total, ...
      "drug_coverage": [...]   // per NDC: tier, on_formulary, prior_auth, step_therapy, quantity limits
    }
  ]
}
```

Plans of type `PLAN_TYPE_MA` don't cover drugs, so their estimated cost is the full retail drug price. The summary table labels them `MA`.

## Python

```python
from medicare_plan_finder import DrugSpec, PlanFinder, compare_plans

with PlanFinder() as pf:
    report = compare_plans(
        pf,
        zipcode="30577",
        county="Stephens",
        year=2027,
        drugs=[DrugSpec.parse("atorvastatin:40"), DrugSpec.parse("lisinopril:10:60")],
        categories=["pdp", "ma"],
        include_details=True,
    )

cheapest = report["plans"][0]
```

Individual endpoints are also available:

```python
from medicare_plan_finder import PDP, PlanFinder, Prescription

with PlanFinder() as pf:
    county = pf.counties("30577")[0]                     # {name, fips, state}
    drug = pf.search_drugs("atorvastatin", 2027)[0]      # {rxcui, name, ...}
    pkg = pf.drug_forms(drug["rxcui"], 2027)[0]["packages"][0]
    rx = [Prescription.per_days(pkg["ndc"], 30, days=30)]
    npis = [f["properties"]["npi"] for f in pf.pharmacies("30577")["pharmacies"]["features"][:3]]

    plans = pf.all_plans(zipcode="30577", fips=county["fips"], year=2027,
                         plan_type=PDP, prescriptions=rx, pharmacy_npis=npis)
    best = min(plans, key=lambda p: p["remaining_premium_and_drugs"])

    pf.plan_details(best)
    pf.drug_costs(plans=[best], prescriptions=rx, pharmacy_npis=npis)
    pf.drug_coverage(best, [pkg["ndc"]])
```

`PlanFinder.request(method, endpoint, params, body)` calls any other endpoint listed in [API_NOTES.md](API_NOTES.md).

## Caveats

- This relies on an undocumented API that can change or be blocked without notice. Results are estimates, the same as on the Plan Finder; confirm with the plan before enrolling.
- Drug names are matched to the first search result, and to the first strength unless you give one. Check `query.drugs` in the output, or use `--ndc` for an exact package.
- Before Open Enrollment opens (October 15), some details for next year's plans may not be published yet.
- For bulk or repeated data collection, use CMS's official Part D formulary, pharmacy network and pricing public use files.
