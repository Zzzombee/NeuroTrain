---
name: neurotrain
description: NeuroTrain: event-aligned spike train analysis workflow for fullrate, PSTH-like traces, raster plots, statistics, and presentation-ready outputs.
---

# NeuroTrain

Use this skill for Plexon/NeuroExplorer/OriginPro/PowerPoint batch workflows where:

- raw data are `.pl2` files with sorted spike variables,
- stimulation timing is encoded in `.pl2` filenames or a schedule table,
- no-light control files must be handled cleanly,
- NeuroExplorer full-session firing-rate outputs must be standardized,
- PSTH-like light-aligned rate traces are reconstructed from full-session rate bins,
- final figures and metadata must be summarized into a `.pptx`,
- existing `PreLightPostSummary` outputs must be aggregated into statistics-ready tables.

## Stable Default Workflow

Current default mode:

```yaml
analysis:
  mode: "auto"
```

`auto` currently prefers `fullrate_aligned`:

```text
.pl2 files
-> build stim_schedule_master from filenames
-> build unit_quality_table from NeuroExplorer NeuronNames
-> NeuroExplorer RateHist_FullSession
-> SaveNumResults full-session rate export
-> Python fullrate_aligned reconstruction
-> FullRate / AlignedRate / PreLightPost / Summary figures
-> PPTX
-> optional prelightpost_stats from existing 03_nex_exports
```

The older direct NeuroExplorer PSTH route is experimental fallback only.

## What This Skill Provides

- Project initialization with `config.yaml`, template tables, logs, and output directories.
- Automatic `stim_schedule_master` generation from light and no-light `.pl2` filenames.
- Automatic `unit_quality_table` generation from NeuroExplorer `NeuronNames`.
- Project validation for directory structure, config, light/no-light semantics, and aligned-window settings.
- Optional `Light_On` / `Light_Off` / `Light_Interval` helper file generation for manual or legacy workflows.
- NeuroExplorer `nex` package integration for `RateHist_FullSession` and `SaveNumResults`.
- Figure generation through matplotlib fallback, with optional OriginPro OPJU archive output.
- PPTX generation using `python-pptx`.
- Statistics-only export of all-unit pre/light/post values plus QC-filtered wide tables from existing `03_nex_exports/aligned_rate/*_PreLightPostSummary.csv`.
- Processing and error logs written to `99_logs`.

## Files To Reuse

- Main entry: `run_pipeline.py`
- Project init: `scripts/init_project.py`
- Stim schedule build: `scripts/build_stim_schedule_from_filenames.py`
- Unit table build: `scripts/build_unit_quality_table.py`
- Validation only: `validate_project.py`
- Event helper export only: `prepare_events.py`
- NeuroExplorer fullrate bridge: `export_from_neuroexplorer.py`
- Fullrate-aligned reconstruction: `scripts/build_aligned_rate_from_fullrate.py`
- Dedicated time-cluster reconstruction: `scripts/build_time_cluster_aligned_rate.py`
- Plotting: `plot_in_origin.py`
- Native OriginPro plotting: `origin_native_plot.py` / `scripts/origin_native_plot.py`
- Native OriginPro template seeding: `origin_create_templates.py` / `scripts/origin_native/create_origin_templates.py`
- Summary figure generation: `export_figures.py`
- PPTX build: `build_pptx.py`
- Pre/light/post statistics export: `scripts/build_prelightpost_statistics.py`
- Unit-level temporal cluster permutation: `scripts/time_cluster_permutation.py`
- Complete raw-spike raster project run: `raster_run.py`
- Existing raster CSV plotting only: `raster_plot.py`
- Command reference: `HELP.md`
- Detailed usage: `docs/usage.md`

## Expected Workflow

1. Initialize a project, or use an existing project with `config.yaml`.
2. Put raw `.pl2` files in `00_raw_pl2/`.
3. Use the default filename rules:
   - `sorted_<file_index>_<light_on_s>light<duration_s>_<sorted_channels>.pl2`
   - `sorted_<file_index>_nolight_<sorted_channels>.pl2`
4. Create the NeuroExplorer `RateHist_FullSession` template.
5. Run:

```powershell
python run_pipeline.py --config config.yaml
```

6. After inspecting aligned-rate results, optionally export statistics-only tables:

```powershell
python run_pipeline.py --config config.yaml --module prelightpost_stats
python raster_run.py --project-dir "D:\Data\my_ephys_project"
```

## Submodule Commands

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python run_pipeline.py --config config.yaml --module build_stim_schedule
python run_pipeline.py --config config.yaml --module build_unit_table
python run_pipeline.py --config config.yaml --module validate
python run_pipeline.py --config config.yaml --module prepare_events
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module time_cluster_aligned_rate
python run_pipeline.py --config config.yaml --module time_cluster_permutation
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module origin_create_templates
python run_pipeline.py --config config.yaml --module origin_native_plot
python run_pipeline.py --config config.yaml --module build_pptx
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

## Important Semantics

- Full-session shading is absolute-time:
  - `light_on_s -> light_off_s`
- Normal `aligned_rate` preserves the original fullrate-aligned definition: `aligned_time_s = time_bin_center_s - light_on_s`; 0 s may be a bin center. Its half-open PreLightPost window selection and output files remain unchanged.
- `time_cluster_aligned_rate` is a separate builder. Its bins are half-open intervals `[kΔ, (k+1)Δ)` with centers `(k+0.5)Δ`; 0 s is a bin boundary, never a center.
- Dedicated reconstruction uses real fullrate start/end/center columns. If onset is not a source-bin boundary, `time_cluster_aligned_rate.off_boundary_policy` controls handling and the actual boundary/offset is recorded; `nearest` may place the real stimulus away from aligned 0 s.
- A dedicated target width larger than the source width is supported only as an integer multiple with complete contiguous source coverage. Rates are duration-weighted; source/target widths, source-bin count, and rebin method are recorded. Partial target bins fail.
- `time_cluster_aligned_rate.source_bin_width_s` selects the dedicated source fullrate export independently from `neuroexplorer.fullrate.bin_width_s`; null preserves compatibility inheritance. This source is still a binned RateHist CSV, not raw spike timestamps.
- `time_cluster_aligned_rate.incomplete_target_bin_policy` defaults to `error`. `nan` may be used for unequal recording lengths: incomplete target intervals retain their true boundaries but receive missing firing rate plus explicit coverage/method metadata; never compute a target value from partial coverage.
- Do not use, copy, or rename normal `LightAlignedRate` files as time-cluster inputs. Rebuild `03_nex_exports/time_cluster_aligned_rate` whenever upgrading an existing project to this split layout.
- Mainline Pre/light/post windows are controlled by:
  - `aligned_rate.pre_window_s`
  - `aligned_rate.light_window_s`
  - `aligned_rate.post_window_s`
- Default Pre/light/post windows are:
  - baseline/pre: `-60 -> 0`
  - light statistics: `5 -> 20`
  - post: `25 -> 85`
- The aligned reconstruction/display span is the union of those windows, so the default tag is `pre60_post85`.
- No-light control files still export full-session rate, but skip aligned-rate and PreLightPost analysis.
- `prelightpost_stats` reads existing `03_nex_exports/aligned_rate` outputs only; it does not call NeuroExplorer, rebuild aligned-rate data, plot figures, or build PPTX.
- `prelightpost_stats` input: `03_nex_exports/aligned_rate/*_PreLightPostSummary.csv`.
- `prelightpost_stats` does not recompute firing-rate values; existing `PreLightPostSummary.csv` values are the numeric source of truth. It reads the same `aligned_rate.pre_window_s/light_window_s/post_window_s` config to fill or validate window metadata. Change window config only changes numeric values after rerunning `aligned_rate`.
- `prelightpost_stats` outputs:
  - `07_statistics/all_units_pre_light_post_wide.csv`
  - `07_statistics/all_units_pre_light_post_wide_qc.csv`
  - `07_statistics/all_units_pre_light_post_qc_excluded.csv`
  - `07_statistics/skipped_or_missing_prelightpost.csv`
- QC rule: keep rows where `max(pre_hz, light_hz, post_hz) >= 0.5 Hz`, at least one of `pre_hz` or `post_hz` is strictly greater than `0.5 Hz`, and `total_expected_spikes >= 10`.
- `pre_hz` is an alias of `baseline_hz`; `baseline_hz` remains in output.
- No-light files do not enter the pre/light/post QC table; they are recorded in `qc_excluded` and/or `skipped_or_missing`.
- `summary_by_file` and `summary_by_condition` CSV outputs are no longer generated.
- `unit_quality_table` is the only downstream Unit cohort source. Only literal `include: yes` is eligible; `no`, blank, other values, and missing rows are excluded.
- Unit discovery retains every matching NeuroExplorer `NeuronNames` variable from the PL2 as a candidate row. When `unit_table.filename_channel_selection.enabled: true`, channels parsed from the PL2 filename control `include`: every suffix Unit on a listed channel (for example, `SPK_SPKC15a` and `SPK_SPKC15b`) is included, while detected Units on unlisted channels remain in the table with `include: no` and an exclusion reason.
- Missing tables, unmatched analysis Units, or an empty eligible cohort must fail with an actionable message; never silently include all Units.
- Auto pipeline updates append new Units with default `include: yes` while `preserve_manual_edits: true` preserves manual `no`, duplicate, reason, representative, and note fields.
- Fullrate and aligned-rate source/intermediate CSVs retain all Units. Apply cohort selection at Summary/statistics/plot/PPTX/permutation entry points.
- Record discovered/included/excluded counts, reason/status counts, and the effective duplicate policy in logs or cohort metadata.
- `time_cluster_permutation` is opt-in and reads only `03_nex_exports/time_cluster_aligned_rate/*_TimeClusterAlignedRate_*.csv`; it does not inherit normal `aligned_rate` config or read `LightAlignedRate` CSVs. One `(file_id, unit_id)` is one exchange unit after within-unit trial averaging.
- Terminal-only normal branch: `python build_aligned_rate_from_fullrate.py --config config.yaml`.
- Terminal-only time-cluster branch: first `python build_time_cluster_aligned_rate.py --config config.yaml`, then `python time_cluster_permutation.py --config config.yaml`.
- Temporal cluster results are unit-level inferences. They do not model within-animal/session dependence, make each bin independently significant, or define exact physiological onset boundaries.
- Raw-spike raster is an independent branch. `raster_run.py` creates/reads project-level `raster_config.yaml`; it does not read the main `config.yaml` or run rate/PPTX/time-cluster modules.
- Raster NeuroExplorer export reads `NexVar.Timestamps()` for literal `include: yes` units, aligns schedule events at `t=0`, and writes deterministic long CSV inputs before plotting.
- Raster output includes per-unit figures and one project combined figure. Combined rows use one shared relative-time x-axis; per-trial stimulus durations all start at `t=0` and may end at different times.

## NeuroExplorer / Origin Policy

- The stable route does not require `Light_On` or `Light_Interval` inside NeuroExplorer.
- The stable NeuroExplorer template is `RateHist_FullSession`.
- Users can manually export CSV from NeuroExplorer and continue from plotting/PPTX.
- Users can skip OriginPro and rely on matplotlib PNG output.
- `export_figures` is the stable matplotlib QC/fallback plotting path.
- OriginPro OPJU archive output is optional and defaults to disabled; enable it with `origin.save_opju: true` and `origin.opju_generation_mode: archive_existing_pngs`.
- Native OriginPro plotting is opt-in through `origin_native_plot` and `origin.backend: "origin_native"` or `"both"`. It builds `04_origin_projects/origin_input/origin_plot_manifest.xlsx`, imports CSV data into OriginPro, creates editable graph pages, saves OPJU, and exports Origin-generated images.
- Native Origin OPJU grouping should default to `origin.opju_mode: "per_file"` to avoid OriginPro page/window limits.
- Native Origin template seeding is opt-in through `origin_create_templates`. It creates `04_origin_projects/template_seed/origin_template_seed.opju` and tries to save `FullRate_template.otpu`, `AlignedRate_template.otpu`, and `PreLightPost_template.otpu`.
- `.otpu` templates are style-only. Do not hard-code light duration in templates; `origin_native_plot` updates light-band start/end from manifest fields for each graph.
- `batch_gui_export_fullrate` is not a stable `run_pipeline.py` module in the current implementation.
- `export_figures` is the preferred plotting module name. `origin_plot` and `python_plot` are compatibility aliases for older configs and commands.

## Maintenance Rules

- Use the workspace root as the only development source directory for this skill.
- Treat the user-level Codex skill entry as a Windows junction to the project source, not as an independent editable copy.
- Make all future code, test, config-template, and documentation changes in the workspace root so the source and Codex user skill stay synchronized.
- `README.md` 和 `HELP.md` 必须使用中文编写；命令、路径、配置键、数据字段、文件名和其他技术标识保持原样。

## When To Read More

- For command-by-command terminal and agent usage, read `HELP.md`.
- For user-facing setup and troubleshooting, read `README.md` and `docs/usage.md`.
- For exact config fields and templates, inspect:
  - `config_template.yaml`
  - `stim_schedule_template.csv`
  - `unit_quality_template.csv`
