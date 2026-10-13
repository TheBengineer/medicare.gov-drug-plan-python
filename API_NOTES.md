# Medicare Plan Finder — observed API (captured 2026-10-13, FE version 2.93.0-hotfix)

Base: `https://www.medicare.gov/api/v1/data/plan-compare`

## Request signing (from the frontend bundle, function `B1`)
Each request carries:
- `Fe-Ver: 2.93.0-hotfix`
- `X-Date: <RFC1123 GMT date>`
- `Authorization: hmac username="oc-mct-frontend", algorithm="hmac-sha256", headers="x-date @request-target host", signature="<b64>"`

The signature is HMAC-SHA256 with key `PLACEHOLDER` (the literal runtime value of `window.config.HMAC_SECRET`) over:
```
x-date: <X-Date>
@request-target: <method lowercase> <path+query>
host: www.medicare.gov
```
I reproduced a captured signature in Python and it matched. From inside the browser, though, requests succeed even with no signature and no cookies.

## Access notes
- Akamai Bot Manager is active (`/BQkW5T/...` sensor POSTs, `_abck`/`bm_sz` cookies). A plain `curl` got a 403 on the static JS.
- A Python request to the API (with requests or curl_cffi, signed) reaches the gateway but gets a **401 "No API key found in request"**. The same call from the page works. I didn't find out why.
- From headless Chrome launched by Playwright, API calls get a **403 Access Denied**. A normal visible Chrome window works. That's why the client defaults to `headless=False`.
- The URL id `2027-S4802-082-0` is `<year>-<contract_id>-<plan_id>-<segment_id>`. The API itself uses path segments or plan-ref objects, not this string.

## Workflow / endpoints
| Step | Call |
|---|---|
| ZIP → counties | `GET /geography/counties?zipcode=30577` → `{counties:[{name,fips,state}]}` |
| Drug autocomplete | `GET /drugs/autocomplete?name=Ascomp&year=2027` → `{drugs:[{rxcui,name,generic,is_generic,generics,...}]}` |
| Drug forms/NDCs | `GET /drugs/{rxcui}/forms?year=2027` → `{dosages:[{rxcui,form,strength,packages:[{ndc,default_quantity_30,default_quantity_90,...}]}]}` |
| Drugs A–Z | `GET /drugs/list/...` |
| Pharmacy search | `GET /geography/pharmacies/address?address=&count=10&name=&page=0&radius_miles=25&zipcode=30577` → GeoJSON with `npi` |
| **Plan search** | `POST /plans/search?plan_type=PLAN_TYPE_PDP&page=0&year=2027&fips=13257&sort_order=ANNUAL_TOTAL&snp_type=SNP_TYPE_NOT_SNP&zip=30577` |
| Plan detail | `GET /plan/{year}/{contract_id}/{plan_id}/{segment_id}?lis=...` → `{plan_card:{...}}` |
| Formulary info | `POST /drugs/info/{year}/{contract_id}/{plan_id}/{segment_id}` `{ndcs:[...]}` → tier, prior_auth, step_therapy, quantity limits |
| Pharmacy network | `POST /pharmacies/status-summary` `{plans:[plan ref]}` |
| Drug cost breakdown | `POST /drugs/cost` `{npis, prescriptions, lis, plans, full_year, retailOnly}`; `plans` is a list of `{contract_id, contract_year, plan_id, segment_id}` objects |
| Other | `plans/premiumranges`, `plans/omstarratings?fips&year`, `geography/plan/pharmacies`, `drugs/info/{year}` `{ndcs}`, medigap/*, pap/* |

`plan_type` values: `PLAN_TYPE_PDP`, `PLAN_TYPE_MA`, `PLAN_TYPE_MAPD`. `sort_order`: `ANNUAL_TOTAL`, `MONTHLY_PREMIUM`, ... `page` is 0-based.

Plan search body (captured):
```json
{"npis":["1114087541"],
 "prescriptions":[{"ndc":"51991007405","quantity":120,"frequency":"FREQUENCY_30_DAYS"}],
 "lis":"LIS_LEVEL_1A_DN",
 "starRatings":[], "organizationNames":[]}
```
The response has `plans[]` with `id`, `name`, `contract_id`/`plan_id`/`segment_id`, `partd_premium`, `drug_plan_deductible`, `annual_drugs_total`, `remaining_premium_and_drugs` (plus `_retail` and `_mail_order`), `overall_star_rating`, `pharmacies[{npi,in_network}]`, `lis` levels, and more.
