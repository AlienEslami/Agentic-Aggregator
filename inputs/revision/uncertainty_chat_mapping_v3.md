# Uncertainty and driver-chat benchmark v3

The v3 benchmark is a deterministic, fully synthetic evaluation set. It contains
no real driver messages, names, identifiers, or personal data. Eight physical
event lifecycles are paired across four information formats:

1. `clean`: a concise formal operational notice;
2. `single_message`: one informal field message;
3. `driver_chat`: a fragmented driver/dispatcher/maintenance exchange;
4. `uncertain_chat`: the same decision-relevant content with conflicting source
   order, stale telemetry, corrections, and irrelevant conversation.

Each lifecycle contains `warning`, `onset`, `persistence`, `severity_change`,
`recovery`, and `stable` phases. The warning requires confirmation and does not
change optimizer inputs. Confirmed onset, verified severity change, and recovery
require reoptimization. Unchanged persistence and stable post-recovery chatter
do not.

| Scenario | Split | Physical assets | Uncertainty represented |
|---|---|---|---|
| `v3_route4_detour` | development | bus/route 4 | delay and energy ranges; field/telemetry conflict |
| `v3_route6_closure` | test | bus/route 6 | word-form ranges; stale numerical feed |
| `v3_bus2_energy_sensor` | test | bus 2 | fluctuating energy estimate and sensor doubt |
| `v3_charger2_isolation` | development | charger 2 | conditional isolation warning before confirmed fault |
| `v3_charger5_thermal` | development | charger 5 | uncertain power-capacity range |
| `v3_charger7_relay` | test | charger 7 | possible sensor error followed by field confirmation |
| `v3_bus3_charger3` | development | bus 3 and charger 3 | combined delay, energy, and availability uncertainty |
| `v3_bus8_charger8` | test | bus 8 and charger 8 | combined delay and derating correction |

The split unit is the entire physical-event lifecycle. No wording variant from a
test event appears in development. Test chats also use a separate conversational
lexical family, including word-form numbers and authority-dependent corrections.

## Frozen uncertainty-to-optimizer policy

- Delay range: select the upper bound.
- Energy multiplier range: select the upper bound.
- Charger-power range: select the lower bound.
- Suspected charger fault: request confirmation; apply no outage update.
- Confirmed charger fault: mark the charger unavailable.
- Persistence: retain the previously selected values but do not reoptimize.
- Recovery: restore 0-minute delay, 1.0 energy multiplier, 200 kW charger power,
  and clear charger unavailability where applicable.

The Agent and rule-text comparator receive exactly the same public fields:
`notice_id`, `event_id`, `source_type`, `report_timestep`, and `text`. Canonical
truth, scenario ID, wording variant, split, and uncertainty-case labels are used
only after the decision for scoring.
