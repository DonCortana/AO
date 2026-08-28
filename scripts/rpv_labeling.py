"""CLI for the RPV labeling tool — export / validate / import.

    # 1. write unlabelled observations into a Sheet tab, with the dropdown
    python scripts/rpv_labeling.py export --sheet <id> --tab Labeling \
        --run-plan <uuid> [--run-plan <uuid> ...]

    # 2. after labeling, check without writing anything
    python scripts/rpv_labeling.py validate --sheet <id> --tab Labeling

    # 3. insert the validated rows (validate runs again first; all-or-nothing)
    python scripts/rpv_labeling.py import --sheet <id> --tab Labeling --commit

    # confirm the outcome_type vocabulary against the live check constraint
    python scripts/rpv_labeling.py verify-vocabulary

`import` without --commit is a dry run. Nothing is inserted unless every row
validates — see atlas.tools.rpv_labeling.import_from_sheet for why a partial
import is not a neutral state.
"""

from __future__ import annotations

import argparse
import sys

from atlas.db.client import get_db
from atlas.tools.rpv_labeling import (
    OUTCOME_TYPES,
    SHEET_LAST_COLUMN,
    export_to_sheet,
    import_from_sheet,
    read_values,
    validate,
    verify_vocabulary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="write unlabelled observations into a Sheet tab")
    export.add_argument("--sheet", required=True, help="spreadsheet ID")
    export.add_argument("--tab", required=True, help="tab title")
    export.add_argument(
        "--run-plan", action="append", required=True, dest="run_plans", help="repeatable"
    )

    check = sub.add_parser("validate", help="validate a labelled tab; writes nothing")
    check.add_argument("--sheet", required=True)
    check.add_argument("--tab", required=True)

    load = sub.add_parser("import", help="validate then insert")
    load.add_argument("--sheet", required=True)
    load.add_argument("--tab", required=True)
    load.add_argument("--commit", action="store_true", help="default is a dry run")

    sub.add_parser("verify-vocabulary", help="probe the live outcome_type check constraint")

    args = parser.parse_args()
    db = get_db()

    if args.command == "export":
        written = export_to_sheet(db, args.run_plans, args.sheet, args.tab)
        print(f"exported {written} unlabelled observation(s) to {args.tab!r}")
        if written == 0:
            print("nothing to label — every complete observation already has a row")
        return 0

    if args.command == "validate":
        report = validate(db, read_values(args.sheet, f"{args.tab}!A:{SHEET_LAST_COLUMN}"))
        print(report.render())
        return 0 if report.ok else 1

    if args.command == "import":
        report = import_from_sheet(db, args.sheet, args.tab, dry_run=not args.commit)
        print(report.render())
        if not report.ok:
            print("\nnothing inserted — fix the errors above and re-run")
            return 1
        if args.commit:
            print(f"\ninserted {len(report.valid)} recommendation row(s)")
        else:
            print(f"\nDRY RUN — {len(report.valid)} row(s) would be inserted; pass --commit")
        return 0

    if args.command == "verify-vocabulary":
        accepted = verify_vocabulary(db)
        for value in OUTCOME_TYPES:
            print(f"  {'OK      ' if accepted[value] else 'REJECTED'} {value}")
        rejected = [v for v, ok in accepted.items() if not ok]
        if rejected:
            print(f"\nlive check constraint rejects {rejected} — the vocabulary has drifted")
            return 1
        print("\nvocabulary matches the live check constraint")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
