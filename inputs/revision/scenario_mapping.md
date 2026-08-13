# Revision notice scenario mapping

The controlled notice dataset maps directly to the current case-study inputs. Route block and bus identifiers are identical in the eight-row `Trips` sheet; charger identifiers are identical in the eight-row `Chargers` sheet. Every sequence has onset, unchanged persistence, material severity-change, recovery, and stable post-recovery records, plus explicit, indirect, and operational/coreferential wording variants. The frozen v2 dataset contains 120 Trigger decisions; stable records are informational SKIP cases.

| Scenario | Existing input assets | Optimizer updates |
|---|---|---|
| `svc_route4_detour` | trip/route block 4, bus 4 | +25 min trip timing; 1.10 route-energy multiplier |
| `svc_route6_closure` | trip/route block 6, bus 6 | +20 min trip timing; 1.08 route-energy multiplier |
| `svc_route2_substitution` | trip/route block 2, bus 2 | +15 min trip timing; 1.12 route-energy multiplier |
| `chg_2_fault` | charger 2 | remove charger 2 during the fault; restore to 200 kW at recovery |
| `chg_5_derating` | charger 5 | derate to 75 kW; restore to 200 kW |
| `chg_7_fault` | charger 7 | remove charger 7 during the fault; restore to 200 kW |
| `combined_bus3_charger3` | bus/route block 3 and charger 3 | +30 min, 1.12 route-energy multiplier, and charger 3 unavailable |
| `combined_bus8_charger8` | bus/route block 8 and charger 8 | +20 min and charger 8 derated to 50 kW |

The files are generated deterministically by `scripts/build_revision_notice_dataset.py`. `trigger_dataset_manifest.json` records the version, method-input exclusions, and SHA-256 hashes. Freeze the generated files, parser, memory rule, prompt, and model settings before any paid Agent evaluation. Any wording revision after freezing must receive a new dataset version.
