"""CLI for the Layer B consumer-surface ingest tool — export / validate / import.

    # 1. write the capture template for one surface into a Sheet tab
    python scripts/consumer_ingest.py export --sheet <id> --tab Capture \
        --provider perplexity --replicates 3

    # 2. after capturing, check without writing anything
    python scripts/consumer_ingest.py validate --sheet <id> --tab Capture \
        --run-plan <uuid>

    # 3. write the observations and store the screenshots as evidence
    python scripts/consumer_ingest.py import --sheet <id> --tab Capture \
        --run-plan <uuid> --commit

`import` without --commit is a dry run. Nothing is written unless every row
validates — see atlas.tools.consumer_ingest.import_from_sheet for why a partial
ingest is not a neutral state.

Warnings never block. Methodology §8.3 makes n=3 a target rather than a floor,
so a short cell is reported and ingested; a cell with no captures at all is
reported too, because that is what drops the paired frame to n=9 and routes the
§8.4 gate to its fallback route.

`import --commit` uploads each screenshot to Drive before writing its evidence
row, so it needs GOOGLE_DRIVE_EVIDENCE_FOLDER_ID set (or --folder) and Drive
reachable. There is no offline or deferred-upload mode: an evidence row whose
storage_path points at nothing asserts that evidence can be produced on audit.
"""

from __future__ import annotations

import argparse
import sys

from atlas.db.client import get_db
from atlas.tools.consumer_ingest import (
    FROZEN_CORE_VERSION,
    PROVIDERS,
    SHEET_LAST_COLUMN,
    export_template,
    import_from_sheet,
    read_values,
    validate,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="write the capture template into a Sheet tab")
    export.add_argument("--sheet", required=True, help="spreadsheet ID")
    export.add_argument("--tab", required=True, help="tab title")
    export.add_argument("--provider", required=True, choices=PROVIDERS)
    export.add_argument(
        "--replicates",
        type=int,
        default=3,
        help="rows per prompt (default 3, the §8.3 target)",
    )
    export.add_argument("--prompt-set", default=FROZEN_CORE_VERSION)

    check = sub.add_parser("validate", help="validate a captured tab; writes nothing")
    check.add_argument("--sheet", required=True)
    check.add_argument("--tab", required=True)
    check.add_argument("--run-plan", required=True, dest="run_plan")
    check.add_argument("--prompt-set", default=FROZEN_CORE_VERSION)

    load = sub.add_parser("import", help="validate then write observations and evidence")
    load.add_argument("--sheet", required=True)
    load.add_argument("--tab", required=True)
    load.add_argument("--run-plan", required=True, dest="run_plan")
    load.add_argument("--prompt-set", default=FROZEN_CORE_VERSION)
    load.add_argument(
        "--folder", default=None, help="Drive folder ID; defaults to the configured one"
    )
    load.add_argument("--commit", action="store_true", help="default is a dry run")

    args = parser.parse_args()
    db = get_db()

    if args.command == "export":
        written = export_template(
            db,
            args.sheet,
            args.tab,
            provider=args.provider,
            replicate_count=args.replicates,
            prompt_set_version=args.prompt_set,
        )
        print(f"exported {written} capture row(s) for {args.provider!r} to {args.tab!r}")
        if written == 0:
            print(
                f"no prompts at version {args.prompt_set!r} — seed the prompt set first"
            )
            return 1
        return 0

    if args.command == "validate":
        values = read_values(args.sheet, f"{args.tab}!A:{SHEET_LAST_COLUMN}")
        report = validate(db, values, args.run_plan, prompt_set_version=args.prompt_set)
        print(report.render())
        return 0 if report.ok else 1

    if args.command == "import":
        report = import_from_sheet(
            db,
            args.sheet,
            args.tab,
            args.run_plan,
            dry_run=not args.commit,
            folder_id=args.folder,
            prompt_set_version=args.prompt_set,
        )
        print(report.render())
        if not report.ok:
            print("\nnothing written — fix the errors above and re-run")
            return 1
        if not args.commit:
            print(
                f"\nDRY RUN — {len(report.validation.valid)} capture(s) would be "
                "written; pass --commit"
            )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
