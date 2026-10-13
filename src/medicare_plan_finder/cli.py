"""Command line: compare Medicare plans and save every plan as JSON and/or CSV.

    medicare-plans compare --zip 30577 --county Stephens \\
        --drug atorvastatin:40:30 --drug lisinopril:10 -o plans.json --csv plans.csv
    medicare-plans csv plans.json            # convert a saved report to plans.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .client import LIS_LEVELS, LIS_NO_HELP, MA, PlanFinder
from .compare import PLAN_CATEGORIES, CompareError, DrugSpec, compare_plans
from .export import PLAN_TYPE_LABELS, write_csv


def _default_year() -> int:
    # From October on, Open Enrollment shops for next year's coverage.
    today = date.today()
    return today.year + 1 if today.month >= 10 else today.year


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medicare-plans", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser(
        "compare",
        help="search all plans and write them to JSON (and optionally CSV)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""drug formats:
  --drug NAME[:STRENGTH[:QTY[:DAYS]]]   e.g. atorvastatin, atorvastatin:40, lisinopril:10:60:90
  --ndc  NDC[:QTY[:DAYS]]               exact package code, e.g. 50090591500:30
  QTY is units per fill (default: the site's default); DAYS is 30/60/90/180/360 (default 30).""",
    )
    c.add_argument("--zip", required=True, dest="zipcode", help="5-digit ZIP code")
    c.add_argument("--county", help="county name or FIPS (needed when the ZIP spans several)")
    c.add_argument("--year", type=int, default=_default_year(), help="coverage year (default: %(default)s)")
    c.add_argument("--drug", action="append", default=[], metavar="SPEC", help="drug by name (repeatable)")
    c.add_argument("--ndc", action="append", default=[], metavar="SPEC", help="drug by NDC (repeatable)")
    c.add_argument("--pharmacy", action="append", default=[], metavar="NPI",
                   help="pharmacy NPI to price at (repeatable, up to 5)")
    c.add_argument("--nearest-pharmacies", type=int, default=3, metavar="N",
                   help="without --pharmacy, use the N nearest pharmacies (default: %(default)s)")
    c.add_argument("--type", action="append", dest="categories", choices=sorted(PLAN_CATEGORIES),
                   help="plan category: pdp = drug plans, ma = Medicare Advantage (repeatable; default pdp)")
    c.add_argument("--lis", default=LIS_NO_HELP, choices=LIS_LEVELS, help="Extra Help level (default: none)")
    c.add_argument("--details", action="store_true",
                   help="also fetch each plan's full details, per-drug costs and formulary info (slower)")
    c.add_argument("-o", "--output", type=Path, help="JSON output file (default: plans_<zip>_<year>.json)")
    c.add_argument("--csv", nargs="?", const=True, default=None, metavar="FILE",
                   help="also write a CSV, one row per plan (default name: the JSON name with .csv)")
    c.add_argument("--headless", action="store_true",
                   help="run Chrome headless (medicare.gov currently blocks this)")

    x = sub.add_parser("csv", help="convert a saved JSON report to CSV")
    x.add_argument("report", type=Path, help="JSON file written by `compare`")
    x.add_argument("-o", "--output", type=Path, help="CSV output file (default: same name with .csv)")
    return parser


def _print_summary(report: dict) -> None:
    plans = report["plans"]
    print(f"\n{report['plan_count']} plans, cheapest estimated yearly total first:\n")
    print(f"{'Plan':48} {'ID':18} {'Type':4} {'Premium/mo':>10} {'Drug ded.':>9} {'Est. year':>10} {'Stars':>5}")
    for p in plans:
        stars = (p.get("overall_star_rating") or {}).get("rating") or "-"
        premium = (p.get("partd_premium") or 0) + (p.get("partc_premium") or 0)
        kind = PLAN_TYPE_LABELS.get(p.get("plan_type"), "?")
        print(f"{p['name'][:48]:48} {p['plan_key']:18} {kind:4} {premium:>10.2f} "
              f"{p.get('drug_plan_deductible') or 0:>9.0f} "
              f"{p.get('remaining_premium_and_drugs') or 0:>10.2f} {stars:>5}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "csv":
        return _convert(args)
    return _compare(args)


def _convert(args) -> int:
    try:
        report = json.loads(args.report.read_text())
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"error: can't read {args.report}: {e}")
    output = args.output or args.report.with_suffix(".csv")
    n = write_csv(report, output)
    print(f"Saved {n} plans to {output}")
    return 0


def _compare(args) -> int:
    if len(args.pharmacy) > 5:
        sys.exit("error: at most 5 pharmacies")

    try:
        drugs = [DrugSpec.parse(s) for s in args.drug] + [DrugSpec.parse_ndc(s) for s in args.ndc]
    except ValueError as e:
        sys.exit(f"error: bad drug spec: {e}")
    output = args.output or Path(f"plans_{args.zipcode}_{args.year}.json")

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr)

    try:
        with PlanFinder(headless=args.headless) as pf:
            report = compare_plans(
                pf,
                zipcode=args.zipcode,
                year=args.year,
                county=args.county,
                drugs=drugs,
                pharmacy_npis=args.pharmacy,
                nearest_pharmacies=args.nearest_pharmacies,
                categories=args.categories or ["pdp"],
                lis=args.lis,
                include_details=args.details,
                progress=progress,
            )
    except CompareError as e:
        sys.exit(f"error: {e}")

    output.write_text(json.dumps(report, indent=2))
    _print_summary(report)
    if any(p.get("plan_type") == MA for p in report["plans"]):
        print("\nMA = Medicare Advantage with no drug coverage: its estimate is the full drug price.")
    print(f"\nSaved {report['plan_count']} plans to {output}")
    if args.csv:
        csv_path = output.with_suffix(".csv") if args.csv is True else Path(args.csv)
        write_csv(report, csv_path)
        print(f"Saved {report['plan_count']} plans to {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
