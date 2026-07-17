# Project Structure

## Skill source layout

The only maintained source directory for NeuroTrain is this workspace root. The user-level Codex `neurotrain` skill directory should remain a Windows junction to this project source. Do not treat the user-level path as an independent development copy; make all code, test, config-template, and documentation changes in the workspace root.

## Generated analysis project folders

Stable project folders:

```text
00_raw_pl2/                         raw PL2 files placed manually
01_sorting_info/                    unit_quality_table.xlsx
02_stim_events/                     stim_schedule_master.xlsx and optional exported event helpers
03_nex_exports/fullrate/            NeuroExplorer full-session RateHist CSV
03_nex_exports/aligned_rate/        original aligned-rate CSV and PreLightPost summaries
03_nex_exports/time_cluster_aligned_rate/ dedicated boundary-aligned permutation inputs
05_exported_figures/                Python/matplotlib QC figures
06_pptx/                            PPTX summaries
07_statistics/                      review-ready statistics tables
99_logs/                            processing and error logs
```

Each analysis branch writes `unit_cohort.csv` and `unit_cohort_metadata.json` in its output directory. These files record every discovered Unit, the strict `include: yes` decision, exclusion reasons/status counts, and the effective duplicate policy. Fullrate and aligned-rate intermediate CSVs retain all Units.

Paused/experimental:

```text
04_origin_projects/
```

`04_origin_projects/` may exist in older projects or experimental runs, but it is not part of the v0.2.0-managed stable workflow.
