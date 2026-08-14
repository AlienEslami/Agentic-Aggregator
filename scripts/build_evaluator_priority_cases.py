from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "inputs" / "revision" / "advance_warning_notices_v1.json"
OUTPUT = ROOT / "inputs" / "revision" / "evaluator_priority_notices_v1.json"


AMENDMENTS = {
    "AW-ROUTE6-CONFIRM": {
        "text": (
            " Operator request: because of the warning history, keep some extra "
            "charge available in Bus 6 tonight if possible."
        ),
        "canonical_priority": {
            "priority_id": "OP-RESERVE-BUS6",
            "objective": "preserve_bus_reserve",
            "affected_buses": [6],
            "timestep_start": 37,
            "timestep_end": 48,
            "target_value": 0.30,
            "target_unit": "soc_fraction",
            "priority_level": "soft",
            "default_policy_applied": True,
            "evidence": ["frozen_operator_policy_v1"],
        },
    },
    "AW-BANK-CONFIRM": {
        "text": (
            " Depot operator: if feasible, front-load at least 100 kWh of site "
            "charging between now and 03:30 before the isolation begins."
        ),
        "canonical_priority": {
            "priority_id": "OP-FRONTLOAD-BANK",
            "objective": "frontload_site_charging",
            "affected_buses": [],
            "timestep_start": 6,
            "timestep_end": 8,
            "target_value": 100.0,
            "target_unit": "kwh",
            "priority_level": "soft",
            "default_policy_applied": False,
            "evidence": ["explicit_operator_target_v1"],
        },
    },
    "AW-COMBINED-CONFIRM": {
        "text": (
            " Operator request: keep some extra charge available in Bus 8 tonight "
            "if possible because the combined disruption leaves little recovery time."
        ),
        "canonical_priority": {
            "priority_id": "OP-RESERVE-BUS8",
            "objective": "preserve_bus_reserve",
            "affected_buses": [8],
            "timestep_start": 37,
            "timestep_end": 48,
            "target_value": 0.30,
            "target_unit": "soc_fraction",
            "priority_level": "soft",
            "default_policy_applied": True,
            "evidence": ["frozen_operator_policy_v1"],
        },
    },
    "AW-COMBINED-CORRECT": {
        "text": (
            " The Bus 8 extra-reserve request remains active and unchanged."
        ),
        "canonical_priority": {
            "priority_id": "OP-RESERVE-BUS8",
            "objective": "preserve_bus_reserve",
            "affected_buses": [8],
            "timestep_start": 37,
            "timestep_end": 48,
            "target_value": 0.30,
            "target_unit": "soc_fraction",
            "priority_level": "soft",
            "default_policy_applied": True,
            "evidence": ["frozen_operator_policy_v1"],
        },
    },
}


def main() -> int:
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    for row in rows:
        amendment = AMENDMENTS.get(row["notice_id"])
        if amendment is None:
            continue
        row["text"] += amendment["text"]
        row["canonical_priority"] = amendment["canonical_priority"]
        row["benchmark_split"] = "controlled_evaluator_ablation_v1"
    OUTPUT.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(AMENDMENTS)} priority-bearing notices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
