"""Guard: migration files must be contiguously numbered, with no duplicates.

Two branches that both add "the next migration" will both call it 0010. Merged,
one of them silently never applies — the directory looks fine, the numbers look
fine, and a column simply is not there. That is the same class of drift that
left README.md asserting 0005 was unapplied while the live project had run it.

Run by CI on every pull request.
"""

from __future__ import annotations

import pathlib
import re
import sys

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def main() -> int:
    problems: list[str] = []
    seen: dict[int, list[str]] = {}

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = PATTERN.match(path.name)
        if not match:
            problems.append(
                f"{path.name}: does not match NNNN_lower_snake_case.sql"
            )
            continue
        seen.setdefault(int(match.group(1)), []).append(path.name)

    for number, names in sorted(seen.items()):
        if len(names) > 1:
            problems.append(f"migration number {number:04d} used by {len(names)} files: {names}")

    if seen:
        numbers = sorted(seen)
        if numbers[0] != 1:
            problems.append(f"migrations must start at 0001, found {numbers[0]:04d}")
        expected = list(range(numbers[0], numbers[-1] + 1))
        missing = sorted(set(expected) - set(numbers))
        if missing:
            problems.append(
                "gap in migration numbering: "
                + ", ".join(f"{n:04d}" for n in missing)
                + " missing"
            )
    else:
        problems.append(f"no migrations found in {MIGRATIONS_DIR}")

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    print(f"OK: {len(seen)} migrations, contiguous 0001..{max(seen):04d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
