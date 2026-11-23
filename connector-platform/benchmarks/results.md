# AI Layer Evaluation Results

## Summary Metrics

| Metric | Value |
| --- | --- |
| Total Scenarios | 24 |
| Generation First-Attempt Pass Rate | 60.0% (5 positive specs) |
| Generation Pass@3 Rate | 100.0% |
| Repair Success Rate (within 3 retries + full sync) | 100.0% (17 drift scenarios) |
| Validation Gate Catch Rate (adversarial/broken configs) | 100.0% (2 cases) |
| Mean Attempts per Scenario | 1.5 |
| Mean Latency per Scenario | 171.12 ms |

## Scenario Breakdown

| ID | Kind | Service | Description | Expected | Accepted | Attempts | Synced Rows | Latency (ms) | Status |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |
| `gen_01_paystub_openapi` | generate | paystub | openapi | True | True | 1 | 40/40 | 135.48 | PASS |
| `gen_02_gitlike_openapi` | generate | gitlike | openapi | True | True | 2 | 35/35 | 143.90 | PASS |
| `gen_03_sheetsy_markdown` | generate | sheetsy | markdown | True | True | 1 | 30/30 | 116.63 | PASS |
| `gen_04_paystub_markdown_retry` | generate | paystub | markdown | True | True | 2 | 40/40 | 136.31 | PASS |
| `gen_05_gitlike_markdown` | generate | gitlike | markdown | True | True | 1 | 35/35 | 114.94 | PASS |
| `gen_06_adversarial_bad_auth` | generate | paystub | markdown | False | False | 4 | 0/0 | 143.95 | PASS |
| `rep_07_paystub_rename_amount` | repair | paystub | Rename field amount -> amount_cents | True | True | 1 | 40/40 | 171.40 | PASS |
| `rep_08_paystub_retype_amount_string` | repair | paystub | Breaking type change amount integer -> string | True | True | 1 | 40/40 | 159.12 | PASS |
| `rep_09_paystub_remove_currency` | repair | paystub | Remove currency field from response | True | True | 1 | 40/40 | 174.25 | PASS |
| `rep_10_paystub_add_fee_cents` | repair | paystub | Additive field fee_cents introduced | True | True | 1 | 40/40 | 197.42 | PASS |
| `rep_11_paystub_records_path_move` | repair | paystub | Move records list from data -> payload.charges | True | True | 2 | 40/40 | 214.21 | PASS |
| `rep_12_paystub_cursor_param_rename` | repair | paystub | Rename query params starting_after -> after, limit -> per_page | True | True | 1 | 40/40 | 196.57 | PASS |
| `rep_13_paystub_combined_rename_and_nest` | repair | paystub | Rename amount -> gross_amount and move data -> result.charges | True | True | 2 | 40/40 | 199.81 | PASS |
| `rep_14_gitlike_rename_stars` | repair | gitlike | Rename stars -> stargazers_count | True | True | 1 | 35/35 | 190.50 | PASS |
| `rep_15_gitlike_retype_private` | repair | gitlike | Breaking type change private boolean -> string | True | True | 1 | 35/35 | 192.36 | PASS |
| `rep_16_gitlike_add_language` | repair | gitlike | Additive field language introduced | True | True | 1 | 35/35 | 197.11 | PASS |
| `rep_17_gitlike_remove_updated_at` | repair | gitlike | Remove updated_at field | True | True | 1 | 35/35 | 190.39 | PASS |
| `rep_18_gitlike_records_path_move` | repair | gitlike | Move records list from result.items -> data.repositories | True | True | 2 | 35/35 | 190.09 | PASS |
| `rep_19_gitlike_offset_param_rename` | repair | gitlike | Rename offset -> skip and limit -> count | True | True | 1 | 35/35 | 177.39 | PASS |
| `rep_20_sheetsy_rename_title` | repair | sheetsy | Rename title -> sheet_title | True | True | 1 | 30/30 | 183.90 | PASS |
| `rep_21_sheetsy_retype_row_count` | repair | sheetsy | Breaking type change row_count integer -> string | True | True | 1 | 30/30 | 172.00 | PASS |
| `rep_22_sheetsy_records_path_move` | repair | sheetsy | Move records list from rows -> data.sheets | True | True | 2 | 30/30 | 177.44 | PASS |
| `rep_23_sheetsy_add_and_remove` | repair | sheetsy | Add owner string and remove title field | True | True | 1 | 30/30 | 163.33 | PASS |
| `rep_24_adversarial_missing_pk` | repair | paystub | Broken mutation removes primary key id (must be rejected by gate) | False | False | 4 | 0/0 | 168.29 | PASS |
