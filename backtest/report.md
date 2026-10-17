# Weather backtest

Seasons 2022–2022 · 471 outdoor FBS games with weather · 942 team-games · generated 2026-10-17T23:45+00:00

Baseline: unders 47.9% (222-241, 95% CI 43.4–52.5) vs the closing total.

Effects are versus each team's own average in its other games that season (EPA per play; pass rate as a share of plays).

## Scoring vs the market by wind

| Level | Games | Pts vs closing total | Unders | Avg total pts |
|---|---|---|---|---|
| none | 276 | +0.3 | 45.8% (125-148, 95% CI 40.0–51.7) | 55.4 |
| mild | 147 | +0.3 | 51.0% (73-70, 95% CI 42.9–59.1) | 53.6 |
| moderate | 32 | +0.7 | 48.4% (15-16, 95% CI 32.0–65.2) | 55.2 |
| severe | 16 | -1.8 | 56.2% (9-7, 95% CI 33.2–76.9) ⚠ small | 49.6 |

## Scoring vs the market by precipitation

| Level | Games | Pts vs closing total | Unders | Avg total pts |
|---|---|---|---|---|
| dry | 425 | +0.5 | 46.9% (196-222, 95% CI 42.2–51.7) | 54.6 |
| rain:mild | 32 | -1.9 | 53.1% (17-15, 95% CI 36.4–69.1) | 54.3 |
| rain:moderate | 12 | -5.2 | 81.8% (9-2, 95% CI 52.3–94.9) ⚠ small | 51.0 |
| rain:severe | 1 | +32.5 | 0.0% (0-1, 95% CI 0.0–79.3) ⚠ small | 79.0 |
| snow:mild | 1 | +8.0 | 0.0% (0-1, 95% CI 0.0–79.3) ⚠ small | 70.0 |

## Scoring vs the market by temperature

| Level | Games | Pts vs closing total | Unders | Avg total pts |
|---|---|---|---|---|
| none | 439 | +0.3 | 48.0% (208-225, 95% CI 43.4–52.7) | 54.6 |
| moderate | 28 | -0.6 | 42.3% (11-15, 95% CI 25.5–61.1) ⚠ small | 56.1 |
| severe | 4 | -5.1 | 75.0% (3-1, 95% CI 30.1–95.4) ⚠ small | 43.8 |

## Scoring vs the market by overall passing-hostility level

| Level | Games | Pts vs closing total | Unders | Avg total pts |
|---|---|---|---|---|
| none | 268 | +0.4 | 45.3% (120-145, 95% CI 39.4–51.3) | 55.3 |
| mild | 150 | +0.0 | 51.0% (75-72, 95% CI 43.0–59.0) | 53.7 |
| significant | 49 | +0.2 | 53.2% (25-22, 95% CI 39.2–66.7) | 53.9 |
| extreme | 4 | +0.0 | 50.0% (2-2, 95% CI 15.0–85.0) ⚠ small | 49.8 |

## Style × wind: what changes for each offense

| Conditions | Style | Team-games | Δ pass rate | Δ off EPA | Δ pass EPA | Δ rush EPA | Δ turnovers |
|---|---|---|---|---|---|---|---|
| none | pass-heavy | 106 | -0.001 | -0.004 | -0.015 | +0.036 | +0.01 |
| none | balanced | 146 | +0.000 | -0.002 | -0.001 | -0.007 | -0.04 |
| none | run-heavy | 111 | +0.028 | -0.019 | -0.016 | -0.017 | -0.09 |
| mild | pass-heavy | 58 | -0.014 | -0.008 | +0.001 | -0.016 | -0.01 |
| mild | balanced | 82 | +0.002 | -0.005 | +0.006 | +0.006 | +0.11 |
| mild | run-heavy | 77 | +0.011 | -0.040 | -0.073 | -0.015 | +0.02 |
| moderate | pass-heavy | 17 ⚠ | -0.012 | +0.082 | +0.163 | +0.023 | -0.25 |
| moderate | balanced | 16 ⚠ | -0.024 | +0.002 | +0.028 | +0.024 | +0.45 |
| moderate | run-heavy | 13 ⚠ | +0.010 | -0.035 | -0.015 | -0.075 | -0.02 |
| severe | pass-heavy | 10 ⚠ | -0.088 | -0.056 | -0.157 | +0.016 | -0.46 |
| severe | balanced | 15 ⚠ | -0.012 | -0.125 | -0.171 | -0.030 | +0.08 |
| severe | run-heavy | 7 ⚠ | -0.026 | -0.080 | -0.122 | -0.119 | -0.56 |

## Style × precipitation

| Conditions | Style | Team-games | Δ pass rate | Δ off EPA | Δ pass EPA | Δ rush EPA | Δ turnovers |
|---|---|---|---|---|---|---|---|
| dry | pass-heavy | 176 | -0.003 | +0.010 | +0.013 | +0.027 | -0.11 |
| dry | balanced | 239 | +0.002 | -0.009 | -0.003 | -0.004 | +0.08 |
| dry | run-heavy | 195 | +0.018 | -0.027 | -0.026 | -0.023 | -0.06 |
| rain:mild | pass-heavy | 11 ⚠ | -0.082 | -0.104 | -0.154 | -0.097 | +1.09 |
| rain:mild | balanced | 15 ⚠ | -0.037 | +0.008 | +0.041 | -0.001 | -0.30 |
| rain:mild | run-heavy | 8 ⚠ | +0.061 | -0.068 | -0.294 | +0.042 | -0.34 |
| rain:moderate | pass-heavy | 3 ⚠ | -0.116 | -0.161 | -0.298 | -0.032 | +0.10 |
| rain:moderate | balanced | 4 ⚠ | -0.080 | -0.162 | -0.490 | +0.093 | -0.66 |
| rain:moderate | run-heavy | 5 ⚠ | -0.001 | -0.088 | -0.198 | -0.138 | +0.21 |
| snow:mild | pass-heavy | 1 ⚠ | -0.173 | -0.076 | -0.188 | -0.023 | -1.64 |
| snow:mild | balanced | 1 ⚠ | -0.063 | +0.132 | +0.350 | +0.005 | +0.25 |

## Style × overall level

| Conditions | Style | Team-games | Δ pass rate | Δ off EPA | Δ pass EPA | Δ rush EPA | Δ turnovers |
|---|---|---|---|---|---|---|---|
| none | pass-heavy | 102 | +0.004 | +0.001 | -0.005 | +0.039 | +0.02 |
| none | balanced | 145 | +0.001 | -0.003 | -0.004 | -0.007 | -0.04 |
| none | run-heavy | 110 | +0.028 | -0.018 | -0.016 | -0.013 | -0.09 |
| mild | pass-heavy | 62 | -0.022 | -0.016 | -0.017 | -0.017 | -0.03 |
| mild | balanced | 80 | +0.003 | -0.001 | +0.025 | +0.001 | +0.14 |
| mild | run-heavy | 75 | +0.013 | -0.041 | -0.069 | -0.017 | -0.03 |
| significant | pass-heavy | 25 ⚠ | -0.029 | +0.044 | +0.081 | +0.017 | -0.31 |
| significant | balanced | 31 | -0.016 | -0.043 | -0.060 | +0.014 | +0.23 |
| significant | run-heavy | 22 ⚠ | +0.004 | -0.048 | -0.036 | -0.103 | +0.02 |
| extreme | pass-heavy | 2 ⚠ | -0.174 | -0.130 | -0.418 | +0.066 | -0.55 |
| extreme | balanced | 3 ⚠ | -0.079 | -0.250 | -0.484 | -0.038 | -0.36 |
| extreme | run-heavy | 1 ⚠ | -0.124 | -0.114 | -0.657 | +0.113 | -1.50 |

## Do the app's rules work? (pregame profiles, closing lines)

| Label | Team-games | Δ off EPA | ATS vs closing spread |
|---|---|---|---|
| exposed | 104 | +0.012 | 51.5% (52-49, 95% CI 41.9–61.0) |
| neutral | 132 | -0.040 | 48.1% (62-67, 95% CI 39.6–56.6) |
| insulated | 65 | -0.040 | 48.4% (30-32, 95% CI 36.4–60.6) |
| unaffected | 357 | -0.006 | 50.3% (175-173, 95% CI 45.1–55.5) |
| unknown | 284 | +0.022 | 50.4% (141-139, 95% CI 44.5–56.2) |

- Style-edge side ATS: 46.6% (34-39, 95% CI 35.6–57.9)
- Games flagged "lean under": 53 games, unders 52.9% (27-24, 95% CI 39.5–65.9), +0.1 pts vs total
- Games with no lean: unders 47.3% (195-217, 95% CI 42.6–52.2)

## Regressions

**team_off_epa_delta** (n=658, R²=0.017)

| Term | Coef | SE | t |
|---|---|---|---|
| const | +0.0108 | 0.0181 | 0.6 |
| wind_mph | -0.0020 | 0.0018 | -1.1 |
| rain_in | -0.3930 | 0.1768 | -2.22 |
| snow_in | +0.0945 | 0.3298 | 0.29 |
| cold | -0.0532 | 0.0372 | -1.43 |
| pass_heavy | +0.0023 | 0.0321 | 0.07 |
| wind_x_pass_heavy | +0.0019 | 0.0033 | 0.58 |
| rain_x_pass_heavy | -0.2055 | 0.3561 | -0.58 |

**team_pass_rate_delta** (n=658, R²=0.0456)

| Term | Coef | SE | t |
|---|---|---|---|
| const | +0.0275 | 0.0089 | 3.08 |
| wind_mph | -0.0024 | 0.0009 | -2.66 |
| rain_in | -0.1906 | 0.0873 | -2.18 |
| snow_in | -0.2630 | 0.1627 | -1.62 |
| cold | -0.0303 | 0.0184 | -1.65 |
| pass_heavy | +0.0009 | 0.0158 | 0.06 |
| wind_x_pass_heavy | -0.0018 | 0.0016 | -1.12 |
| rain_x_pass_heavy | -0.2675 | 0.1757 | -1.52 |

**game_points_vs_total** (n=468, R²=0.0052)

| Term | Coef | SE | t |
|---|---|---|---|
| const | +1.3293 | 1.6545 | 0.8 |
| wind_mph | -0.0951 | 0.1721 | -0.55 |
| rain_in | -15.8135 | 13.1196 | -1.21 |
| snow_in | +18.8489 | 34.9437 | 0.54 |
| cold | -2.1838 | 3.9773 | -0.55 |

Reading it: `wind_x_pass_heavy` is the extra change per mph of wind for a fully pass-heavy offense (pass_heavy = +1 at the 100th percentile of pass rate, −1 at the 0th). |t| ≥ 2 is roughly significant.

## Stories vs data

| Game | Reported | Measured weather | Level | Score | vs total | Away Δ pass rate / pass EPA / rush EPA | Home Δ |
|---|---|---|---|---|---|---|---|
| Ohio State at Northwestern (2022-11-05) | Heavy rain; wind ~20 mph sustained, gusts reported 40-47 mph | 63.1F, wind 30.1 (g 59.1), precip 0.04", snow 0.0" | extreme | 21-7 | -27.0 | -0.05 / -0.77 / -0.10 | -0.27 / -0.39 / +0.06 |
| Notre Dame at NC State (2016) | Hurricane Matthew: torrential rain, gusty wind, waterlogged field | not found in data | | | | | |
| Iowa State at Kansas State (2023) | Heavy snow throughout; crews clearing yard lines during play | not found in data | | | | | |
| Army at Navy (2017) | Steady snow, snow-covered field (Philadelphia, neutral site) | not found in data | | | | | |
| Minnesota at Northwestern (2017) | Wind and sleet, muddy field | not found in data | | | | | |
| Iowa at Purdue (2022) | Gusty conditions (needs verification: which game and how windy) | not found in data | | | | | |
| Florida State at Boston College (2023) | Hurricane Lee forecast: wind and rain expected, no roof | not found in data | | | | | |
| Appalachian State at Troy (2023) | Sun Belt Championship forecast: 1-2 inches of rain | not found in data | | | | | |
| Vanderbilt at South Carolina (2024) | Rain, sloppy field (season unconfirmed) | not found in data | | | | | |

## Most extreme games in the sample

| Game | Weather | Score | vs total | Away (style, label, Δ off EPA) | Home |
|---|---|---|---|---|---|
| Maryland at Wisconsin (2022-11-05) | 59.3F, wind 27.0 (g 48.3), precip 0.3", snow 0.0" | 10-23 | -14.5 | balanced, neutral, -0.42 | run-heavy, insulated, -0.11 |
| Ohio State at Northwestern (2022-11-05) | 63.1F, wind 30.1 (g 59.1), precip 0.04", snow 0.0" | 21-7 | -27.0 | balanced, neutral, -0.39 | pass-heavy, exposed, -0.05 |
| Penn State at Indiana (2022-11-05) | 64.1F, wind 21.4 (g 54.1), precip 0.03", snow 0.0" | 45-14 | +9.0 | balanced, neutral, +0.06 | pass-heavy, exposed, -0.21 |
| UAlbany at Baylor (2022-09-03) | 85.1F, wind 7.7 (g 20.6), precip 0.32", snow 0.0" | 10-69 | +32.5 | None, unknown, — | None, unknown, +0.44 |
| Kentucky at Missouri (2022-11-05) | 43.6F, wind 18.2 (g 39.8), precip 0.01", snow 0.0" | 21-17 | -2.0 | balanced, neutral, +0.01 | run-heavy, neutral, -0.29 |
| Oregon at Colorado (2022-11-05) | 49.2F, wind 26.7 (g 61.3), precip 0.0", snow 0.0" | 49-10 | -3.5 | balanced, neutral, +0.11 | balanced, neutral, -0.11 |
| Kansas at Baylor (2022-10-22) | 76.4F, wind 20.4 (g 37.1), precip 0.0", snow 0.0" | 23-35 | +1.5 | run-heavy, neutral, -0.38 | balanced, insulated, -0.02 |
| Texas at Oklahoma State (2022-10-22) | 84.7F, wind 17.5 (g 35.6), precip 0.0", snow 0.0" | 34-41 | +16.5 | balanced, neutral, -0.24 | balanced, neutral, +0.12 |
| Clemson at Notre Dame (2022-11-05) | 56.8F, wind 24.1 (g 43.8), precip 0.0", snow 0.0" | 14-35 | +5.5 | balanced, insulated, -0.48 | run-heavy, neutral, +0.09 |
| Boston College at Notre Dame (2022-11-19) | 26.2F, wind 18.2 (g 35.6), precip 0.01", snow 0.06" | 0-44 | +2.0 | pass-heavy, exposed, -0.51 | run-heavy, insulated, +0.21 |
| Michigan State at Illinois (2022-11-05) | 61.2F, wind 27.5 (g 53.0), precip 0.0", snow 0.0" | 23-15 | -3.0 | pass-heavy, exposed, +0.01 | run-heavy, insulated, -0.17 |
| Indiana at Michigan State (2022-11-19) | 22.6F, wind 19.8 (g 41.2), precip 0.0", snow 0.0" | 39-31 | +22.8 | pass-heavy, exposed, +0.15 | pass-heavy, exposed, -0.02 |
| Syracuse at Pittsburgh (2022-11-05) | 77.0F, wind 17.6 (g 37.4), precip 0.01", snow 0.0" | 9-19 | -19.5 | balanced, neutral, -0.36 | balanced, insulated, -0.11 |
| Utah State at BYU (2022-09-30) | 72.1F, wind 8.9 (g 36.9), precip 0.0", snow 0.0" | 26-38 | +4.0 | balanced, neutral, -0.04 | pass-heavy, exposed, +0.07 |
| Cincinnati at SMU (2022-10-22) | 75.0F, wind 23.1 (g 38.5), precip 0.0", snow 0.0" | 29-27 | -3.5 | balanced, neutral, -0.10 | pass-heavy, exposed, -0.24 |
| Navy at Cincinnati (2022-11-05) | 71.6F, wind 16.7 (g 38.5), precip 0.0", snow 0.0" | 10-20 | -13.5 | run-heavy, insulated, +0.09 | pass-heavy, neutral, +0.14 |
| Memphis at SMU (2022-11-26) | 60.9F, wind 21.4 (g 34.7), precip 0.01", snow 0.0" | 31-34 | -3.5 | balanced, exposed, +0.10 | pass-heavy, exposed, +0.10 |
| Missouri at Kansas State (2022-09-10) | 62.2F, wind 13.1 (g 25.5), precip 0.42", snow 0.0" | 12-40 | -0.5 | None, unknown, -0.49 | None, unknown, -0.06 |
| Tennessee at Vanderbilt (2022-11-27) | 53.6F, wind 13.2 (g 23.3), precip 0.48", snow 0.0" | 56-0 | -8.0 | balanced, neutral, +0.13 | balanced, neutral, -0.42 |
| Georgia State at Army (2022-10-01) | 56.7F, wind 8.4 (g 21.5), precip 0.16", snow 0.0" | 31-14 | -9.0 | run-heavy, neutral, +0.31 | run-heavy, insulated, -0.35 |
| Georgia Tech at Pittsburgh (2022-10-02) | 52.9F, wind 11.0 (g 21.9), precip 0.2", snow 0.0" | 26-21 | +0.0 | balanced, neutral, +0.06 | run-heavy, insulated, -0.13 |
| Western Kentucky at Hawai'i (2022-09-04) | 79.3F, wind 11.9 (g 28.4), precip 0.05", snow 0.0" | 49-17 | -1.5 | None, unknown, +0.22 | None, unknown, -0.17 |
| Vanderbilt at Hawai'i (2022-08-28) | 82.8F, wind 12.2 (g 31.8), precip 0.0", snow 0.0" | 63-10 | +18.5 | None, unknown, +0.32 | None, unknown, -0.34 |
| Vanderbilt at Northern Illinois (2022-09-17) | 82.8F, wind 16.8 (g 32.2), precip 0.0", snow 0.0" | 38-28 | +7.5 | balanced, neutral, +0.36 | None, unknown, +0.08 |
| South Carolina State at South Carolina (2022-09-29) | 67.1F, wind 17.5 (g 31.5), precip 0.0", snow 0.0" | 10-50 | +3.5 | None, unknown, — | pass-heavy, exposed, +0.24 |

## Caveats

- Archive weather is reanalysis (about 10-30 km grid) and smooths stadium-level gusts and downpours; compare the Stories table with what was reported.
- Live picks use forecasts, which are noisier than the archive, so live performance will be somewhat weaker than this backtest.
- Closing lines already price forecast weather, so weather can move scoring a lot and still show little edge against the total.
- Rows marked ⚠ have under 30 samples; treat them as anecdotes.
