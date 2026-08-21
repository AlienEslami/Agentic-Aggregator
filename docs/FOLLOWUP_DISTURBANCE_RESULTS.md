# Follow-up disturbance experiments

## Primary-case screening

The original clustered-late-return setting is not included in the primary or
reviewer-facing result tables because every trigger method violated the realized
battery reserve. It remains archived as a calibration audit trail. The decision
to replace it was made from deterministic feasibility results before the Agent
was evaluated on the replacement.

The replacement, `aw_recoverable_clustered_late_returns`, delays Buses 3, 4,
and 7 by 60, 45, and 60 minutes. All 16 runs are operationally feasible: one
oracle, numerical, and text-rule run per mode plus five Agent repetitions per
mode. Every run has zero reserve shortfall and minimum observed SOC of 20%.

## Chained three-day experiment

The selected persistent disturbance is a temporary charger-cooling derating.
Chargers 6, 7, and 8 are reduced from 200 kW to 100 kW beginning at timestep 31
(15:00) on day 1, remain derated throughout day 2, and return to 200 kW at
timestep 31 on day 3. This is operationally plausible as a temporary cooling or
thermal-management restriction and has a clear recovery time.

The experiment consists of three daily 48-timestep optimizations. It is not a
single 144-step perfect-foresight solve. The terminal realized energy of every
bus is copied exactly to the next day's initial physical state. Every trigger
method faces the same hidden physical event; only its information channel
differs. A matched no-derating control performs scheduled replanning at each
daily handover using the same carried physical state, so the disturbance's
cross-day effect can be separated from ordinary daily replanning.

| Condition | Mode | Method | Three-day episodes | Feasible | PTO cost (mean) | Aggregator revenue (mean) |
|---|---|---|---:|---:|---:|---:|
| Derating | Selfish | Trigger Agent | 5 | 5 | 382.135957 | 59.999193 |
| Derating | Selfish | Oracle | 1 | 1 | 382.135957 | 59.999193 |
| Derating | Selfish | Numerical | 1 | 1 | 382.135957 | 59.999193 |
| Derating | Selfish | Text rule | 1 | 1 | 382.135957 | 59.999193 |
| No derating | Selfish | Scheduled daily replan | 1 | 1 | 403.798815 | 73.198665 |
| Derating | Altruistic | Trigger Agent | 5 | 5 | 334.348522 | 35.598691 |
| Derating | Altruistic | Oracle | 1 | 1 | 334.348522 | 35.598691 |
| Derating | Altruistic | Numerical | 1 | 1 | 334.348522 | 35.598691 |
| Derating | Altruistic | Text rule | 1 | 1 | 334.348522 | 35.598691 |
| No derating | Altruistic | Scheduled daily replan | 1 | 1 | 338.718234 | 41.343294 |

All 18 three-day episodes and all 54 daily runs are operationally feasible.
Maximum reserve shortfall and internal day-to-day carryover error are both 0
kWh. The first recorded settlement differs from the unrounded carried state by
at most 0.0000492 kWh because observations are written to four decimal places.
All 120 optimizer attempts use Gurobi; 118 meet the configured 2% MIP-gap target.
The two attempts for the selfish no-derating day-3 control reach the 300-second
limit with feasible incumbents at 5.52% and 5.48% gaps. Therefore the nominal
selfish economic deltas below are approximate sensitivity results, while the
physical feasibility and state-continuity conclusions do not depend on their
optimality. A targeted 900-second-per-attempt sensitivity rerun also reaches
the time limit, at 5.50% and 5.42% gaps. Despite tripling day-3 workflow time
from 613.38 to 1813.83 seconds, it retains the identical charging/discharging
schedule and identical three-day economics to numerical precision. This shows
that the reported incumbent is stable to the longer limit, but it does not turn
the result into a 2%-gap solution.

The Trigger Agent received no false numerical energy-event signal before the
maintenance notice: all ten repetitions skipped the unconfirmed warning at
timestep 22 and reoptimized on the confirmed text at timestep 24, before the
physical cap at timestep 31. The interval-energy comparison now uses the active
real-time plan, rather than a clock-incompatible forecast workbook. All four
trigger methods then obtained identical derating schedules and economics, so
this case tests text interpretation and multi-day execution rather than
economic dominance.

Relative to scheduled no-derating daily replanning, the derating reduces
selfish three-day PTO cost by EUR 21.662858, revenue by EUR 13.199472, buying by
400 kWh, and selling by 194.445360 kWh. In altruistic mode it reduces PTO cost
by EUR 4.369712 and revenue by EUR 5.744603, reduces buying by 49.999928 kWh,
and increases selling by 5.554863 kWh. Terminal-SOC differences grow across
the three days, directly demonstrating cross-day propagation. These are
disturbance-versus-nominal effects, not Agent-versus-baseline gains.

The ten Trigger-Agent episodes used 956,239 tokens in 80 successful API
requests, with an indexed approximate API cost of USD 0.150846. This case
supports claims of multi-day state continuity, feasibility, and robust
advance-text interpretation. It should not be used as evidence that the Agent
economically dominates every baseline.

Primary files:

- `results/revision/recoverable_cluster_v1/matrix_runs.csv`
- `results/revision/multiday_charger_derating_v1/multiday_episodes.csv`
- `results/revision/multiday_charger_derating_v1/multiday_days.csv`
- `results/revision/multiday_charger_derating_v1/multiday_method_summary.csv`
- `results/revision/multiday_charger_derating_v1/multiday_episode_effects.csv`
- `results/revision/multiday_charger_derating_v1/multiday_daily_effects.csv`
- `results/revision/multiday_charger_derating_v1/multiday_solver_audit.csv`
- `results/revision/multiday_charger_derating_v1/multiday_solver_audit_summary.csv`
- `results/revision/multiday_charger_derating_v1/multiday_manifest.json`
- `results/revision/multiday_nominal_selfish_900s_v1/multiday_episodes.csv`
- `results/revision/multiday_nominal_selfish_900s_v1/multiday_solver_audit.csv`
- `results/revision/multiday_nominal_selfish_900s_v1/multiday_manifest.json`
