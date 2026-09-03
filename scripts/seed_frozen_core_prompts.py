"""Seed the Samujana Frozen Core prompt set into `prompt_versions`.

The step scripts/seed_calibration_property.py deliberately left open: "Seed
prompts in a separate step, once the text is agreed."

    python scripts/seed_frozen_core_prompts.py            # dry run (default)
    python scripts/seed_frozen_core_prompts.py --commit

Prompt text is **parsed out of the source document**, never retyped here.
docs/frozen-core-prompts-samujana-DRAFT.md is the artifact that was reviewed
and agreed; a hand-copied constant in this file would be a second, divergent
copy of an instrument that §7 makes immutable the moment a baseline runs. The
parser reads the `### <id> · <notes>` headings under each `## Tier <X>` and
takes the blockquote line beneath as the prompt text.

Idempotent: a row is matched on (version, prompt_text) and reused rather than
duplicated, so re-running writes nothing new.

§7 immutability: once a baseline runs against these rows, intent, entity, tier
and set membership are locked. Changing any of them requires a major
prompt-set version and a new baseline — not an UPDATE to these rows.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from atlas.db.client import get_db

SOURCE_DOC = Path(__file__).resolve().parents[1] / "docs" / "frozen-core-prompts-samujana-DRAFT.md"

# Agreed version string. §7: this is the prompt-set identity carried to the
# gate, so it is stated once, here, and never derived.
PROMPT_SET_VERSION = "frozen-core-samujana-v2"

SET_TYPE = "frozen_core"

# Samujana's TH/en market row (markets.id), Execution Plan §4 primary
# market/language for the first calibration.
MARKET_ID = "2d4854b9-5589-44a5-886b-c895e99c7b95"

# Methodology §6.3: hold-out is a Benchmark Set concept with no meaning in a
# Frozen Core instrument. The driver's preflight rejects a flagged row.
IS_HOLDOUT = False

EXPECTED_COUNT = 11

_TIER_RE = re.compile(r"^##\s+Tier\s+([ABCD])\b")
_PROMPT_RE = re.compile(r"^###\s+([ABCD]\d)\s*·")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")


def parse_prompts(path: Path) -> list[dict]:
    """Extract (label, intent_tier, prompt_text) from the source document.

    Stops at the `## Classification summary` section — everything below it is
    tables *about* the prompts, not the prompts themselves.
    """
    prompts: list[dict] = []
    tier: str | None = None
    label: str | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Classification summary"):
            break

        tier_match = _TIER_RE.match(line)
        if tier_match:
            tier = tier_match.group(1)
            continue

        prompt_match = _PROMPT_RE.match(line)
        if prompt_match:
            label = prompt_match.group(1)
            continue

        if label is None:
            continue

        quote_match = _QUOTE_RE.match(line)
        if quote_match and quote_match.group(1).strip():
            if tier is None:
                raise SystemExit(f"prompt {label} appears before any '## Tier X' heading")
            prompts.append(
                {"label": label, "intent_tier": tier, "prompt_text": quote_match.group(1).strip()}
            )
            label = None

    return prompts


def seed(*, commit: bool) -> list[str]:
    prompts = parse_prompts(SOURCE_DOC)

    print(f"Source     : {SOURCE_DOC}")
    print(f"Version    : {PROMPT_SET_VERSION}")
    print(f"Set type   : {SET_TYPE}")
    print(f"Market id  : {MARKET_ID}")
    print(f"Parsed     : {len(prompts)} prompt(s)  ({'COMMIT' if commit else 'DRY RUN'})\n")

    if len(prompts) != EXPECTED_COUNT:
        raise SystemExit(
            f"parsed {len(prompts)} prompts from {SOURCE_DOC.name}, expected "
            f"{EXPECTED_COUNT}. Refusing to seed a partially-parsed Frozen Core "
            "set — §7 freezes set membership, so a short set is not a fixable "
            "mistake after a baseline runs."
        )

    labels = [p["label"] for p in prompts]
    if len(set(labels)) != len(labels):
        raise SystemExit(f"duplicate prompt labels parsed: {labels}")

    db = get_db()
    written: list[str] = []

    for prompt in prompts:
        existing = (
            db.table("prompt_versions")
            .select("id")
            .eq("version", PROMPT_SET_VERSION)
            .eq("prompt_text", prompt["prompt_text"])
            .execute()
            .data
        )
        if existing:
            print(f"  {prompt['label']} (tier {prompt['intent_tier']}): exists -> {existing[0]['id']}")
            written.append(existing[0]["id"])
            continue

        payload = {
            "set_type": SET_TYPE,
            "version": PROMPT_SET_VERSION,
            "prompt_text": prompt["prompt_text"],
            "intent_tier": prompt["intent_tier"],
            "market_id": MARKET_ID,
            "is_holdout": IS_HOLDOUT,
        }
        if not commit:
            print(f"  {prompt['label']} (tier {prompt['intent_tier']}): WOULD CREATE {prompt['prompt_text'][:60]!r}...")
            continue

        created = db.table("prompt_versions").insert(payload).execute().data[0]["id"]
        print(f"  {prompt['label']} (tier {prompt['intent_tier']}): created -> {created}")
        written.append(created)

    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually write rows (default is a dry run that writes nothing)",
    )
    args = parser.parse_args()
    ids = seed(commit=args.commit)
    if args.commit:
        print("\nprompt_version_ids:")
        for i in ids:
            print(f"  {i}")
    sys.exit(0)
