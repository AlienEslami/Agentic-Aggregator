# Paper Outputs

This folder contains the workbook and prompt artifacts used to prepare the paper results. The files were copied from the original analysis output folder and renamed to match the paper structure.

## Naming Convention

- `day_ahead/table_06/`: day-ahead strategy workbooks for Table 6.
- `real_time/profit_based/`: real-time outputs for the profit-based aggregator mode, corresponding to the paper's `Selfish` workflow runs.
- `real_time/operational_based/`: real-time outputs for the operational-based aggregator mode, corresponding to the paper's `Altruistic` workflow runs.
- `real_time/*/delay/`: delay scenarios `D_plus_30_beginning`, `D_minus_30_beginning`, `D_plus_30_end`, and `D_minus_30_end`.
- `real_time/*/energy/`: route-energy scenarios `E_plus_50` and `E_minus_50`.
- `real_time/*/price/`: price-shock scenarios `P_plus_25`, `P_plus_50`, `P_minus_25`, and `P_minus_50`.
- `real_time/*/combined/`: combined scenarios `C_Seq`, `C_All_5_48`, `C_All_5_25`, and `C_All_20_48`.
- `prompt_sensitivity/table_14/`: prompt-sensitivity workbooks for Table 14.
- `prompt_sensitivity/prompts/`: prompt template documents used for the prompt-sensitivity experiments and Appendix A.

## Mode Mapping

The original local folders used `Selfish` and `Altruistic`. The paper uses clearer coordination-mode language:

- `Selfish` -> `profit_based`
- `Altruistic` -> `operational_based`

## Manifest

`manifest.json` records every copied file, its original source path, its cleaned repository path, and its role in the paper output structure.

