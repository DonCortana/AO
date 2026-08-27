"""Persistence for the §8.4 gate — write results, read the eligible list.

D-044 makes `calibration_results` the system of record for platform
eligibility, replacing the hand-typed argument D-036 required while no store
existed. This module is the only writer and the only reader.

Append-only by construction: there is no update and no delete function here,
matching `atlas.evidence.vault` and Operating System §7 ("Append-only;
versioned"). A re-run writes a new row under a new `calibration_run_id`;
`eligible_platforms` reads the most recent run, so a superseded result stays
visible instead of being overwritten.
"""

from __future__ import annotations

from atlas.calibration.types import CalibrationRun, PlatformGateResult, Verdict

_MIGRATION_0005 = "migrations/0005_surface_layer_and_calibration_results.sql"


def _row(run: CalibrationRun, result: PlatformGateResult) -> dict:
    agreement = result.agreement
    return {
        "calibration_run_id": run.calibration_run_id,
        "property_id": run.property_id,
        "market_id": run.market_id,
        "platform": result.platform,
        "prompt_set_version": run.prompt_set_version,
        "n_paired_units": agreement.n_paired_units,
        "raw_agreement": agreement.raw_agreement,
        "cohen_kappa": agreement.cohen_kappa,
        "kappa_prevalence_note": (
            f"[{agreement.kappa_stability.value}] {agreement.kappa_note}"
        ),
        "co_mention_count": agreement.co_mention_count,
        "spearman_rho": agreement.spearman_rho,
        "verdict": result.verdict.value,
        "pass_route": result.pass_route.value if result.pass_route else None,
        "reviewer": result.reviewer,
        "notes": result.notes,
    }


def write_calibration_run(db, run: CalibrationRun) -> list[dict]:
    """Insert one row per platform examined. Insert, never upsert — a repeat
    write under the same calibration_run_id must collide with the table's
    unique constraint rather than silently replace a stored gate result."""
    if not run.results:
        raise ValueError("refusing to write a calibration run with no platform results")

    rows = [_row(run, result) for result in run.results]
    try:
        return db.table("calibration_results").insert(rows).execute().data
    except Exception as exc:
        raise RuntimeError(
            f"could not write calibration_results. If the table does not exist, "
            f"apply {_MIGRATION_0005} (D-044). Underlying error: {exc}"
        ) from exc


def eligible_platforms(db, property_id: str, market_id: str) -> tuple[str, ...]:
    """The eligible-platform list for this property/market, from the latest
    calibration run (D-044).

    Raises rather than returning an empty tuple when no calibration exists:
    D-036's rule that AVS is never computed over an inferred or defaulted
    platform set is unchanged by moving the list into a table, and an empty
    result must not read as "no platforms eligible, score zero".
    """
    rows = (
        db.table("calibration_results")
        .select("platform,verdict,calibration_run_id,created_at")
        .eq("property_id", property_id)
        .eq("market_id", market_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    if not rows:
        raise ValueError(
            f"no calibration_results for property {property_id!r} / market "
            f"{market_id!r} — the §8.4 gate has not been run, so no AVS can be "
            "computed (D-036, D-044)"
        )

    # Latest run only. Rows are ordered newest-first, so the first row's run id
    # identifies the most recent cycle.
    latest_run_id = rows[0]["calibration_run_id"]
    latest = [r for r in rows if r["calibration_run_id"] == latest_run_id]

    eligible = tuple(
        sorted(r["platform"] for r in latest if r["verdict"] == Verdict.ELIGIBLE.value)
    )
    if not eligible:
        raise ValueError(
            f"calibration run {latest_run_id!r} found no eligible platform for "
            f"property {property_id!r} / market {market_id!r}. Every platform is "
            "evidence-only, so there is no AVS to report — this is an explicit "
            "'not measurable' result, never an AVS of 0 (§8.4)"
        )
    return eligible
