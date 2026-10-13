"""Python client for the Medicare.gov Plan Finder API.

The API is called from inside a real Chrome page via Playwright, the same way
the Plan Finder frontend calls it. See API_NOTES.md for the endpoint map.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Iterable
from urllib.parse import urlencode

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

ORIGIN = "https://www.medicare.gov"
APP_URL = f"{ORIGIN}/plan-compare/"
API_BASE = "/api/v1/data/plan-compare"

# Plan types
PDP = "PLAN_TYPE_PDP"  # stand-alone Part D drug plan
MA = "PLAN_TYPE_MA"  # Medicare Advantage without drug coverage
MAPD = "PLAN_TYPE_MAPD"  # Medicare Advantage with drug coverage

# Sort orders
SORT_ANNUAL_TOTAL = "ANNUAL_TOTAL"
SORT_MONTHLY_PREMIUM = "MONTHLY_PREMIUM"
SORT_DRUG_DEDUCTIBLE = "DRUG_PLAN_DEDUCTIBLE"

# Extra Help (low-income subsidy) levels
LIS_NO_HELP = "LIS_NO_HELP"
LIS_LEVELS = (
    "LIS_LEVEL_1A",
    "LIS_LEVEL_1A_DN",
    "LIS_LEVEL_1B",
    "LIS_LEVEL_1C",
    "LIS_LEVEL_2",
    "LIS_LEVEL_3",
    LIS_NO_HELP,
)

# Refill frequencies
FREQUENCIES = {
    30: "FREQUENCY_30_DAYS",
    60: "FREQUENCY_60_DAYS",
    90: "FREQUENCY_90_DAYS",
    180: "FREQUENCY_180_DAYS",
    360: "FREQUENCY_360_DAYS",
}

# Runs inside the page. Signs the request the same way the frontend does (B1 in
# the app bundle), then returns {status, body} so errors surface in Python.
_FETCH_JS = """
async ({method, path, body}) => {
  const secret = (window.config && window.config.HMAC_SECRET) || null;
  const headers = {"Fe-Ver": (window.config && window.config.FE_VERSION) || ""};
  if (secret) {
    const xDate = new Date().toUTCString();
    const enc = new TextEncoder();
    const msg = [`x-date: ${xDate}`, `@request-target: ${method.toLowerCase()} ${path}`,
                 `host: ${location.host}`].join("\\n");
    const key = await crypto.subtle.importKey("raw", enc.encode(secret),
        {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
    const sig = btoa(String.fromCodePoint(...new Uint8Array(
        await crypto.subtle.sign("HMAC", key, enc.encode(msg)))));
    headers["X-Date"] = xDate;
    headers["Authorization"] = `hmac username="oc-mct-frontend", algorithm="hmac-sha256", ` +
        `headers="x-date @request-target host", signature="${sig}"`;
  }
  const init = {method, headers, credentials: "same-origin"};
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  return {status: res.status, body: await res.text()};
}
"""


class PlanFinderError(RuntimeError):
    def __init__(self, status: int, path: str, body: str):
        super().__init__(f"HTTP {status} for {path}: {body[:300]}")
        self.status = status
        self.path = path
        self.body = body


@dataclass
class Prescription:
    """One drug in the search, identified by its NDC package code."""

    ndc: str
    quantity: int
    frequency: str = FREQUENCIES[30]

    @classmethod
    def per_days(cls, ndc: str, quantity: int, days: int = 30) -> "Prescription":
        return cls(ndc=ndc, quantity=quantity, frequency=FREQUENCIES[days])


class PlanFinder:
    """Medicare Plan Finder client backed by a Playwright-driven Chrome page.

    Usage:
        with PlanFinder() as pf:
            county = pf.counties("30577")[0]
            ...

    Opens a visible Chrome window by default: medicare.gov answers headless
    Chrome's API calls with 403 Access Denied.
    """

    def __init__(
        self,
        *,
        headless: bool = False,
        channel: str | None = "chrome",
        timeout_ms: int = 60_000,
    ):
        self._headless = headless
        self._channel = channel
        self._timeout_ms = timeout_ms
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "PlanFinder":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._headless, channel=self._channel
        )
        self._page = self._browser.new_page()
        self._page.set_default_timeout(self._timeout_ms)
        self._page.goto(APP_URL, wait_until="domcontentloaded")
        # The app sets window.config once its main bundle runs.
        self._page.wait_for_function("() => window.config && window.config.MCTAPI_URI")
        return self

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._browser = self._pw = self._page = None

    def __enter__(self) -> "PlanFinder":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | Iterable[tuple[str, Any]] | None = None,
        body: Any = None,
    ) -> Any:
        """Call any Plan Finder endpoint, e.g. request("GET", "/geography/counties", {...})."""
        if self._page is None:
            raise RuntimeError("PlanFinder not started; use start() or a with-block")
        path = API_BASE + endpoint
        if params:
            path += "?" + urlencode(params, doseq=True)
        res = self._page.evaluate(
            _FETCH_JS, {"method": method.upper(), "path": path, "body": body}
        )
        if res["status"] >= 400:
            raise PlanFinderError(res["status"], path, res["body"])
        return json.loads(res["body"])

    def _get(self, endpoint: str, params=None) -> Any:
        return self.request("GET", endpoint, params)

    def _post(self, endpoint: str, body: Any, params=None) -> Any:
        return self.request("POST", endpoint, params, body)

    # -- geography ---------------------------------------------------------

    def counties(self, zipcode: str) -> list[dict]:
        """Counties for a ZIP: [{name, fips, state}]."""
        try:
            return self._get("/geography/counties", {"zipcode": zipcode})["counties"]
        except PlanFinderError as e:
            if e.status == 404:
                return []
            raise

    def pharmacies(
        self,
        zipcode: str,
        *,
        radius_miles: int = 5,
        name: str = "",
        address: str = "",
        count: int = 10,
        page: int = 0,
    ) -> dict:
        """Pharmacies near a ZIP. Returns GeoJSON; NPIs are in features[].properties.npi."""
        return self._get(
            "/geography/pharmacies/address",
            {
                "address": address,
                "count": count,
                "name": name,
                "page": page,
                "radius_miles": radius_miles,
                "zipcode": zipcode,
            },
        )

    # -- drugs -------------------------------------------------------------

    def search_drugs(self, name: str, year: int) -> list[dict]:
        """Drug name autocomplete: [{rxcui, name, generic, is_generic, generics, ...}]."""
        return self._get("/drugs/autocomplete", {"name": name, "year": year})["drugs"]

    def drug_forms(self, rxcui: str, year: int) -> list[dict]:
        """Dosages for a drug, each with packages[] carrying the NDC and default quantities."""
        return self._get(f"/drugs/{rxcui}/forms", {"year": year})["dosages"]

    # -- plans -------------------------------------------------------------

    def search_plans(
        self,
        *,
        zipcode: str,
        fips: str,
        year: int,
        prescriptions: Iterable[Prescription] = (),
        pharmacy_npis: Iterable[str] = (),
        plan_type: str | Iterable[str] = PDP,
        lis: str = LIS_NO_HELP,
        sort_order: str = SORT_ANNUAL_TOTAL,
        page: int = 0,
        star_ratings: Iterable[float] = (),
        organization_names: Iterable[str] = (),
        snp_type: str | None = "SNP_TYPE_NOT_SNP",
    ) -> dict:
        """One page of plan search results: {plans, total_results, search_summary}."""
        plan_types = [plan_type] if isinstance(plan_type, str) else list(plan_type)
        params: list[tuple[str, Any]] = [("plan_type", t) for t in plan_types]
        params += [("page", page), ("year", year), ("fips", fips), ("sort_order", sort_order)]
        if snp_type:
            params.append(("snp_type", snp_type))
        params.append(("zip", zipcode))
        body = {
            "npis": list(pharmacy_npis),
            "prescriptions": [asdict(p) for p in prescriptions],
            "lis": lis,
            "starRatings": list(star_ratings),
            "organizationNames": list(organization_names),
        }
        return self._post("/plans/search", body, params)

    def all_plans(self, **kwargs) -> list[dict]:
        """search_plans() across every page, returned as one list."""
        plans: list[dict] = []
        page = 0
        while True:
            res = self.search_plans(page=page, **kwargs)
            batch = res.get("plans") or []
            plans.extend(batch)
            if not batch or len(plans) >= res.get("total_results", 0):
                return plans
            page += 1

    def drug_costs(
        self,
        *,
        plans: Iterable[dict | str],
        prescriptions: Iterable[Prescription],
        pharmacy_npis: Iterable[str] = (),
        lis: str = LIS_NO_HELP,
        full_year: bool = False,
        retail_only: bool = False,
    ) -> list[dict]:
        """Per-plan, per-pharmacy, per-drug costs: [{plan, costs: [{npi, drug_costs}]}].

        plans are search results or "<year>-<contract>-<plan>-<segment>" ids.
        """
        return self._post(
            "/drugs/cost",
            {
                "npis": list(pharmacy_npis),
                "prescriptions": [asdict(p) for p in prescriptions],
                "lis": lis,
                "plans": [plan_ref(p) for p in plans],
                "full_year": full_year,
                "retailOnly": retail_only,
            },
        )["plans"]

    def plan_details(self, plan: dict | str, *, lis: str = LIS_NO_HELP) -> dict:
        """Full plan details (benefits, phones, address, tiers, ...)."""
        r = plan_ref(plan)
        path = f"/plan/{r['contract_year']}/{r['contract_id']}/{r['plan_id']}/{r['segment_id']}"
        return self._get(path, {"lis": lis})["plan_card"]

    def drug_coverage(self, plan: dict | str, ndcs: Iterable[str]) -> list[dict]:
        """Formulary info per NDC for a plan: tier, prior_auth, step_therapy, quantity limits."""
        r = plan_ref(plan)
        path = f"/drugs/info/{r['contract_year']}/{r['contract_id']}/{r['plan_id']}/{r['segment_id']}"
        return self._post(path, {"ndcs": list(ndcs)})["drugInfoList"]


def plan_ref(plan: dict | str) -> dict:
    """Normalize a search result or "<year>-<contract>-<plan>-<segment>" id to the API's plan ref."""
    if isinstance(plan, str):
        year, contract_id, plan_id, segment_id = plan.split("-")
    else:
        year, contract_id = plan["contract_year"], plan["contract_id"]
        plan_id, segment_id = plan["plan_id"], plan["segment_id"]
    return {
        "contract_id": contract_id,
        "contract_year": str(year),
        "plan_id": plan_id,
        "segment_id": str(segment_id),
    }


def plan_key(plan: dict) -> str:
    """The "<year>-<contract>-<plan>-<segment>" id the Plan Finder uses in its URLs."""
    return f"{plan['contract_year']}-{plan['contract_id']}-{plan['plan_id']}-{plan['segment_id']}"
