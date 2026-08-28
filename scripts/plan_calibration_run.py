"""CLI for the calibration run driver — plan one Layer A Frozen Core run.

    # dry run: every preflight check runs, nothing is written (default)
    python scripts/plan_calibration_run.py \
        --property <uuid> --market <uuid> \
        --prompt <uuid> --prompt <uuid> ...

    # write the run_plans row and the planned observations
    python scripts/plan_calibration_run.py ... --commit

Without --commit nothing is written, matching the idiom in
scripts/seed_calibration_property.py. The printed observation count is also
the number of provider calls the resume runner will make once it picks the
plan up, so read it before passing --commit.

Prompt version ids are passed in explicitly and never discovered by query —
§7 makes Frozen Core membership immutable between baseline and validation, so
the set is named by the operator who froze it (DESIGN §2).

This plans Layer A only. The §8.4 gate also needs Layer B run plan ids, which
nothing in this codebase can create yet (D-056).
"""

from __future__ import annotations

import argparse
import sys

from atlas.calibration.driver import (
    DEFAULT_PROVIDERS,
    DEFAULT_REPLICATE_COUNT,
    MIN_WINDOW_HOURS,
    PreflightError,
    plan_calibration_run,
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
        "--provider",
        action="append",
        default=None,
        dest="providers",
        help=f"override the provider list (default: {', '.join(DEFAULT_PROVIDERS)})",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=DEFAULT_REPLICATE_COUNT,
        help=f"replicates per prompt-platform-market (default {DEFAULT_REPLICATE_COUNT}, Methodology §6.1)",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=MIN_WINDOW_HOURS,
        help=f"run window written to the plan (default and minimum {MIN_WINDOW_HOURS}, §6.1)",
    )
    parser.add_argument(
        "--new-plan",
        action="store_true",
        help="plan a deliberate second baseline even if this property and "
        "prompt set already have a frozen_core run plan",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually write rows (default is a dry run that writes nothing)",
    )
    args = parser.parse_args()

    try:
        plan = plan_calibration_run(
            get_db(),
            property_id=args.property,
            prompt_version_ids=args.prompts,
            market_id=args.market,
            providers=args.providers,
            replicate_count=args.replicates,
            window_hours=args.window_hours,
            new_plan=args.new_plan,
            commit=args.commit,
        )
    except PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"prompt set version : {plan.prompt_set_version}")
    print(f"prompts            : {len(plan.prompt_version_ids)}")
    print(f"providers          : {', '.join(plan.providers)}")
    print(f"replicates         : {plan.replicate_count}")
    print(f"planned observations: {plan.planned_observations}")
    print(f"run plan           : {plan.run_plan_id or '(would be created)'}")
    print(f"reused existing    : {plan.reused}")
    if plan.window_start:
        print(f"window             : {plan.window_start} -> {plan.window_end}")
    for note in plan.notes:
        print(f"  note: {note}")

    if plan.committed:
        print(
            f"\nHand to the gate as api_run_plan_ids=['{plan.run_plan_id}'].\n"
            "Layer B consumer_run_plan_ids are still required and cannot be "
            "produced by this driver (D-056)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
