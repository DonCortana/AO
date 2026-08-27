"""Methodology §8.4 calibration gate.

Compares Layer A (§8.1 controlled API benchmark) against Layer B (§8.3
human-initiated consumer-surface capture) per platform, and produces the
eligible-platform list that AVS is averaged over (§4.3).

Governing decisions: D-042 (Google AI is structurally evidence-only),
D-043 (surface_layer separation), D-044 (calibration_results is the system of
record), D-045 (the prompt-platform cell is the unit of analysis).
"""

from atlas.calibration.gate import evaluate_platform
from atlas.calibration.run import run_gate
from atlas.calibration.store import eligible_platforms, write_calibration_run
from atlas.calibration.types import CalibrationRun, PassRoute, Verdict

__all__ = [
    "CalibrationRun",
    "PassRoute",
    "Verdict",
    "eligible_platforms",
    "evaluate_platform",
    "run_gate",
    "write_calibration_run",
]
