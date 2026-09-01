"""CLI for Layer B run_plans creation — insert one consumer-surface run plan.

    # dry run: every preflight check runs, nothing is written (default)
    python scripts/create_consumer_run_plan.py \
        --property <uuid> --market <uuid> \
        --prompt <uuid> --prompt <uuid> ...

    # write the run_plans row
    python scripts/create_consumer_run_plan.py ... --commit

Insert-only, mirroring scripts/plan_calibration_run.py's --commit idiom, but
scoped to Layer B — see atlas.calibration.consumer_run_plan's module
docstring for what is and is not shared with the Layer A driver, and why.

This writes only the run_plans row. It plans no observations: those are
written directly by atlas.tools.consumer_ingest from a human capture sheet,
each carrying surface_layer='consumer'. window_start/window_end are always
null at insert (D-062) — the real window is only known after every
replicate has been captured, and this script does not perform that later
update.
"""

from __future__ import annotations

import argparse
import sys

from atlas.calibration.consumer_run_plan import (
    DEFAULT_REPLICATE_COUNT,
    ConsumerPreflightError,
    create_consumer_run_plan,
)
from atlas.db.client import get_db


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--property", required=True, help="properties.id (uuid)")
    parser.add_argument("--market", required=True, help="markets.id (uuid)")
    parser.add_argument(
        "--prompt",
        action="append",
        default=[],
        dest="prompts",
        metavar="UUID",
        help="a prompt_versions.id; repeat once per prompt in the frozen set",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=DEFAULT_REPLICATE_COUNT,
        help=f"replicate target written to run_plans.replicate_count "
        f"(default {DEFAULT_REPLICATE_COUNT}, §8.3 'target n=3')",
    )
    parser.add_argument(
        "--new-plan",
        action="store_true",
        help="plan a deliberate second Layer B run plan even if this property "
        "and prompt set already have one",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually write the row (default is a dry run that writes nothing)",
    )
    args = parser.parse_args()

    try:
        plan = create_consumer_run_plan(
            get_db(),
            property_id=args.property,
            prompt_version_ids=args.prompts,
            market_id=args.market,
            replicate_count=args.replicates,
            new_plan=args.new_plan,
            commit=args.commit,
        )
    except ConsumerPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"prompt set version : {plan.prompt_set_version}")
    print(f"prompts            : {len(plan.prompt_version_ids)}")
    print(f"replicates (target): {plan.replicate_count}")
    print(f"run plan           : {plan.run_plan_id or '(would be created)'}")
    print(f"reused existing    : {plan.reused}")
    for note in plan.notes:
        print(f"  note: {note}")

    if plan.committed:
        print(
            f"\nHand to the gate as consumer_run_plan_ids=['{plan.run_plan_id}'] "
            "once captures are ingested."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
