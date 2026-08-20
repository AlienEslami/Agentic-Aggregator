"""Day-ahead smart charging with V2G and no agentic layer.

This is the benchmark Reviewer 2 identified as missing from the day-ahead
comparison: the same MILP, the same fleet and price data, V2G enabled, and
tariffs set by a fixed rule instead of by a pricing agent.  It separates three
effects that were previously conflated by the S1/S2 to S3/S4 progression:
optimization value, V2G value, and agentic-coordination value.

Two tariff policies are supported.

``passthrough``
    Buy and sell tariffs equal the spot price.  The aggregator takes no margin,
    so the run isolates the value of V2G itself.  A zero spread makes the MILP
    highly degenerate — shifting energy between equally priced intervals costs
    nothing but round-trip losses — so proving optimality is slow.  Raise
    ``DA_SOLVER_TIME_LIMIT`` or keep the default gap when using it.

``fixed_margin``
    Buy and sell tariffs are constant multiples of the spot price, either taken
    from the aggregator tariff workbook or supplied explicitly.  This is the
    regulated-band variant: a published, non-adaptive tariff.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyomo.environ as pyo

from app import build_dataframes, extract_scalars, solvePTO
from generate_benchmark_files import build_input_data
from run_no_v2g_optimization import compute_bus_power_kw
from scenario_summary import (
    append_agent_reasoning,
    append_day_ahead_summary,
    build_agent_reasoning_row,
    build_day_ahead_summary_row,
)


SCENARIO = 'smart_charging_v2g_no_agents'


def spot_price_rows(input_data: dict) -> list[dict]:
    return input_data.get('grid_prices') or input_data.get('prices') or []


def spot_value(row: dict):
    return row.get(
        'spot_market',
        row.get('Spot Market', row.get('price', row.get('Price'))),
    )


def mean_multiplier(input_data: dict, key: str, default: float) -> float:
    """Average multiplier implied by the tariff workbook, if one was supplied."""

    tariffs = input_data.get('tariffs') or []
    ratios = []
    for tariff, price_row in zip(tariffs, spot_price_rows(input_data)):
        spot = spot_value(price_row)
        value = tariff.get(key)
        if spot in (None, '', 0) or value in (None, ''):
            continue
        ratios.append(float(value) / float(spot))
    if not ratios:
        return default
    return sum(ratios) / len(ratios)


def attach_nonagentic_tariffs(
    input_data: dict,
    *,
    policy: str,
    buy_multiplier: float | None,
    sell_multiplier: float | None,
) -> tuple[dict, dict]:
    if policy == 'passthrough':
        buy_factor = 1.0
        sell_factor = 1.0
    else:
        buy_factor = (
            buy_multiplier
            if buy_multiplier is not None
            else mean_multiplier(input_data, 'buy_tariff', 1.05)
        )
        sell_factor = (
            sell_multiplier
            if sell_multiplier is not None
            else mean_multiplier(input_data, 'sell_tariff', 0.80)
        )
    # Passthrough deliberately sets both sides to the spot price: the
    # aggregator takes no margin.  Round-trip efficiency is below one, so a
    # zero-spread tariff still makes cycling strictly lossy and the optimum
    # stays well defined.  A regulated band, in contrast, is only meaningful
    # when the buy side is strictly above the sell side.
    if policy == 'fixed_margin' and buy_factor <= sell_factor:
        raise SystemExit(
            'The buy multiplier must exceed the sell multiplier under '
            f'fixed_margin; received buy={buy_factor} sell={sell_factor}'
        )

    tariffs = []
    for row in spot_price_rows(input_data):
        if row is None:
            continue
        spot = spot_value(row)
        if spot in (None, ''):
            continue
        tariffs.append({
            'time': row.get('time', row.get('Time', row.get('timestep'))),
            'buy_tariff': float(spot) * buy_factor,
            'sell_tariff': float(spot) * sell_factor,
        })
    input_data['tariffs'] = tariffs
    input_data['v2g_enabled'] = True
    policy_record = {
        'tariff_policy': policy,
        'buy_multiplier': buy_factor,
        'sell_multiplier': sell_factor,
        'agentic_layer': 'absent',
        'tariff_is_time_invariant_multiple_of_spot': True,
    }
    return input_data, policy_record


def summarize_solution(sc: dict, model, policy_record: dict) -> dict:
    t_steps = sc['T_steps']
    total_pto_buy_cost = sum(
        sc['S_buy'][t - 1] * pyo.value(model.w_buy[t]) for t in range(1, t_steps + 1)
    )
    total_pto_sell_rev = sum(
        sc['S_sell'][t - 1] * pyo.value(model.w_sell[t]) for t in range(1, t_steps + 1)
    )
    energy = [
        [float(pyo.value(model.e[k, t])) for t in range(1, t_steps + 1)]
        for k in range(1, sc['k_count'] + 1)
    ]
    return {
        'scenario': SCENARIO,
        'optimization_mode': sc['optimization_mode'],
        'timestep_minutes': sc['timestep_minutes'],
        'v2g_enabled': sc['v2g_enabled'],
        'pricing_policy': policy_record,
        'avg_grid_price': sc['avg_P'],
        'avg_buy_price': sc['avg_S_buy'],
        'avg_sell_price': sc['avg_S_sell'],
        'pto_daily_cost': total_pto_buy_cost - total_pto_sell_rev,
        'total_buy_cost': total_pto_buy_cost,
        'total_sell_revenue': total_pto_sell_rev,
        'total_kwh_bought': sum(
            pyo.value(model.w_buy[t]) for t in range(1, t_steps + 1)
        ),
        'total_kwh_sold': sum(
            pyo.value(model.w_sell[t]) for t in range(1, t_steps + 1)
        ),
        'w_buy': [float(pyo.value(model.w_buy[t])) for t in range(1, t_steps + 1)],
        'w_sell': [float(pyo.value(model.w_sell[t])) for t in range(1, t_steps + 1)],
        'energy': energy,
        'power_kw': compute_bus_power_kw(energy, sc['timestep_minutes']),
        'S_buy': sc['S_buy'],
        'S_sell': sc['S_sell'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='data/inputs/case_study_inputs.xlsx')
    parser.add_argument('--spot-prices-file', default='')
    parser.add_argument(
        '--tariffs-file',
        default='',
        help=(
            'Optional aggregator tariff workbook. Only used to derive the mean '
            'multipliers of the fixed_margin policy.'
        ),
    )
    parser.add_argument(
        '--tariff-policy',
        choices=('passthrough', 'fixed_margin'),
        default='fixed_margin',
        help=(
            'fixed_margin applies constant multipliers, the regulated-band '
            'variant, and is the default because it solves quickly; '
            'passthrough sets both tariffs to the spot price, which isolates '
            'the value of V2G but is numerically degenerate.'
        ),
    )
    parser.add_argument('--buy-multiplier', type=float, default=None)
    parser.add_argument('--sell-multiplier', type=float, default=None)
    parser.add_argument(
        '--output', default='results/nonagentic_v2g_result.json'
    )
    parser.add_argument('--summary-workbook', default='')
    parser.add_argument('--reasoning-source', default='')
    parser.add_argument('--reasoning-text', default='')
    args = parser.parse_args()

    if args.tariff_policy == 'passthrough' and (
        args.buy_multiplier is not None or args.sell_multiplier is not None
    ):
        raise SystemExit(
            'Multiplier overrides require --tariff-policy fixed_margin'
        )

    input_path = Path(args.input)
    spot_prices_path = Path(args.spot_prices_file) if args.spot_prices_file else None
    tariffs_path = Path(args.tariffs_file) if args.tariffs_file else None
    summary_workbook = (
        Path(args.summary_workbook) if args.summary_workbook else input_path
    )

    input_data, _ = build_input_data(
        input_path, spot_prices_path=spot_prices_path, tariffs_path=tariffs_path
    )
    input_data, policy_record = attach_nonagentic_tariffs(
        input_data,
        policy=args.tariff_policy,
        buy_multiplier=args.buy_multiplier,
        sell_multiplier=args.sell_multiplier,
    )

    data = build_dataframes(input_data)
    sc = extract_scalars(
        data, price_guidance={}, optimization_mode='day_ahead', current_timestep=1
    )
    model = solvePTO(sc)
    if model is None:
        raise RuntimeError(
            'The non-agentic V2G optimization returned no usable solution. '
            'With --tariff-policy passthrough this is usually the solver time '
            'limit rather than infeasibility: the zero-spread tariff is '
            'degenerate. Raise DA_SOLVER_TIME_LIMIT or relax DA_SOLVER_MIP_GAP.'
        )

    result = summarize_solution(sc, model, policy_record)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + '\n', newline='\n')

    summary_row = build_day_ahead_summary_row(
        sc,
        model,
        SCENARIO,
        input_workbook=input_path,
        spot_prices_file=spot_prices_path,
        tariffs_file=tariffs_path,
    )
    append_day_ahead_summary(summary_workbook, summary_row)
    reasoning_row = build_agent_reasoning_row(
        sc,
        model,
        summary_row['scenario'],
        input_workbook=input_path,
        spot_prices_file=spot_prices_path,
        tariffs_file=tariffs_path,
        reasoning_source=args.reasoning_source or 'nonagentic_fixed_tariff_rule',
        reasoning_text=(
            args.reasoning_text
            or (
                'No agent was consulted. Tariffs follow the '
                f"{policy_record['tariff_policy']} rule with buy multiplier "
                f"{policy_record['buy_multiplier']:.4f} and sell multiplier "
                f"{policy_record['sell_multiplier']:.4f}."
            )
        ),
    )
    append_agent_reasoning(summary_workbook, reasoning_row)

    print(f"Saved non-agentic V2G result to {output_path}")
    print(f"Tariff policy: {policy_record['tariff_policy']}")
    print(f"Total kWh bought: {result['total_kwh_bought']:.4f}")
    print(f"Total kWh sold: {result['total_kwh_sold']:.4f}")
    print(f"PTO daily cost: {result['pto_daily_cost']:.4f}")


if __name__ == '__main__':
    main()
