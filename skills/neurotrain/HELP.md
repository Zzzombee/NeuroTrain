# NeuroTrain HELP

This document lists the current terminal commands and Codex/agent instruction patterns for the `neurotrain` skill.

Current stable workflow:

```text
.pl2 files
-> build stim_schedule_master from filenames
-> build unit_quality_table from NeuroExplorer NeuronNames
-> NeuroExplorer RateHist_FullSession
-> SaveNumResults full-session rate export
-> Python fullrate_aligned reconstruction
-> FullRate / AlignedRate / PreLightPost / Summary figures
-> PPTX
```

Recommended analysis mode:

```yaml
analysis:
  mode: "auto"
```

In `auto`, the skill prefers `fullrate_aligned`. The older direct NeuroExplorer PSTH path is experimental fallback.

## 1. Initialize A Project

Terminal:

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project"
```

Useful options:

```powershell
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --force
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --with-example
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --pre-margin 60 --post-margin 60 --bin-width 1
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --file-id-format "{file_index}"
```

Agent instruction examples:

```text
请使用 neurotrain skill，在 D:\Data\my_ephys_project 初始化一个新项目。
```

```text
请为 D:\Data\my_ephys_project 初始化该 pipeline 项目，生成示例模板，pre-margin=60，post-margin=60。
```

Important behavior:

- `init_project` creates directories and template files only.
- It does not move, copy, rename, or delete raw `.pl2` files.
- If root-level `sorted_*.pl2` files are found, it warns and writes `99_logs/root_pl2_detected_files.txt`.
- Users must manually place raw files in `00_raw_pl2/`.

Manual move example:

```powershell
Move-Item ".\sorted_*.pl2" ".\00_raw_pl2\"
```

## 2. Build stim_schedule_master

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module build_stim_schedule
python scripts/build_stim_schedule_from_filenames.py --config config.yaml
```

Input:

```text
00_raw_pl2/*.pl2
```

Supported filename rules:

```text
sorted_<file_index>_<light_on_s>light<duration_s>_<sorted_channels>.pl2
sorted_<file_index>_nolight_<sorted_channels>.pl2
```

Examples:

```text
sorted_01_200light25_1,5,9.pl2
sorted_02_nolight_1,5,9.pl2
sorted_03_120.5light15_2,4.pl2
```

Output:

```text
02_stim_events/stim_schedule_master.xlsx
```

For a light file, the generated row includes:

```text
file_id = 01
event_group = 200light25
has_light = yes
light_on_s = 200
duration_s = 25
light_off_s = 225
note = sorted channels: 1,5,9
```

For a no-light control file:

```text
file_id = 02
event_group = nolight
has_light = no
light_on_s = blank
duration_s = blank
light_off_s = blank
condition = no_light
note = sorted channels: 1,5,9
```

Agent instruction examples:

```text
请根据 00_raw_pl2 中的 .pl2 文件名自动生成或更新 stim_schedule_master。
```

```text
请只运行 build_stim_schedule 模块。
```

## 3. Build unit_quality_table

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module build_unit_table
python scripts/build_unit_quality_table.py --config config.yaml
```

Output:

```text
01_sorting_info/unit_quality_table.xlsx
```

Behavior:

- Reads NeuroExplorer `NeuronNames` from each `.pl2`.
- Creates `unit01`, `unit02`, ... per file.
- Defaults `include=yes`.
- Preserves manual edits to `include`, `exclusion_reason`, `representative_unit`, `duplicate_of`, and `note`.
- Does not require `Light_On` or `Light_Interval`.
- No-light files still get unit rows normally.

Agent instruction examples:

```text
请根据当前 .pl2 文件自动生成或更新 unit_quality_table，保留已有人工修改。
```

```text
请只运行 build_unit_table。
```

## 4. Validate Project

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module validate
python validate_project.py --config config.yaml
```

Validation includes:

- Project directory structure
- Required config fields
- `stim_schedule_master`
- `unit_quality_table`
- light/no-light semantics
- fullrate_aligned window settings

No-light behavior:

- `has_light=no` rows do not require `light_on_s`, `duration_s`, or `light_off_s`.
- Full-session rate export is still valid.
- Aligned analysis and PreLightPost are skipped.

Agent instruction example:

```text
请验证这个项目的 config、目录结构、stim_schedule 和 unit_quality_table。
```

## 5. Prepare Event Helper Files

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module prepare_events
python prepare_events.py --config config.yaml
```

Outputs for light files:

```text
02_stim_events/exported_events/{file_id}_Light_On.txt
02_stim_events/exported_events/{file_id}_Light_Off.txt
02_stim_events/exported_events/{file_id}_Light_Interval.csv
```

Current stable `fullrate_aligned` does not require these files. They remain useful for manual NeuroExplorer workflows and legacy PSTH mode.

No-light files:

- No `Light_On`, `Light_Off`, or `Light_Interval` helper files are generated.
- The module logs a skipped status for those files.

Agent instruction example:

```text
请生成 NeuroExplorer 手动导入用的 Light_On / Light_Off / Light_Interval 辅助文件。
```

## 6. Export Full-Session Rate From NeuroExplorer

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python export_from_neuroexplorer.py --config config.yaml
```

Stable route:

- Opens or attaches to the `.pl2` document through the configured NeuroExplorer backend.
- Applies `RateHist_FullSession`.
- Uses `nex.SaveNumResults` when available.
- Normalizes raw full-session rate output to CSV.

Outputs:

```text
03_nex_exports/fullrate/{file_id}_FullRate_bin1s_raw.txt
03_nex_exports/fullrate/{file_id}_FullRate_bin1s.csv
```

It does not call `Light_On`, `Light_Interval`, or `PSTH_LightOn` in `fullrate_aligned`.

Agent instruction examples:

```text
请运行 NeuroExplorer full-session rate 导出，只走 fullrate_aligned 路径。
```

```text
请只导出 full-session rate，不要跑 PSTH。
```

## 7. Build Aligned Rate From Fullrate

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module aligned_rate
python build_aligned_rate_from_fullrate.py --config config.yaml
```

Default configured windows:

```text
aligned_rate.pre_window_s   = [-60, 0]
aligned_rate.light_window_s = [5, 20]
aligned_rate.post_window_s  = [25, 85]
```

The aligned reconstruction/display span is the union of those windows:

```text
aligned window = [-60, 85]
output tag = pre60_post85
light band shown on aligned plots = [0, duration_s]
```

Pre / light / post summary windows:

```text
baseline = -60 to 0 s
light = 5 to 20 s
post = 25 to 85 s
```

Outputs:

```text
03_nex_exports/aligned_rate/{file_id}_LightAlignedRate_pre60_post85_bin1s.csv
03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv
```

`aligned_rate` writes the numeric `baseline_hz`, `light_hz`, and `post_hz` values plus the window columns into `PreLightPostSummary.csv`.

No-light files:

- True aligned-rate output is skipped.
- Pre/light/post summary is skipped.
- The log records `No light event; aligned rate skipped.`

Agent instruction example:

```text
请根据 fullrate CSV 和 stim_schedule 重建 aligned rate，并输出 Pre/light/post summary。
```

## 8. Plot Figures

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module export_figures
python plot_in_origin.py --config config.yaml
```

Stable outputs are matplotlib PNG figures. OriginPro OPJU archive generation is optional when `origin.save_opju: true` and remains a fallback/archive path.

Outputs:

```text
05_exported_figures/fullrate/{file_id}_{unit_id}_FullRate.png
05_exported_figures/aligned_rate/{file_id}_{unit_id}_AlignedRate_pre60_post85.png
05_exported_figures/prepost_summary/{file_id}_{unit_id}_PreLightPost.png
05_exported_figures/summary/{file_id}_Summary_pre60_post85.png
```

No-light output:

```text
05_exported_figures/fullrate/{file_id}_{unit_id}_FullRate.png
05_exported_figures/aligned_rate/{file_id}_{unit_id}_AlignedRate_no_light_skipped.png
05_exported_figures/prepost_summary/{file_id}_{unit_id}_PreLightPost_no_light_skipped.png
05_exported_figures/summary/{file_id}_Summary_no_light.png
```

No-light fullrate figures do not draw a light band.

Agent instruction examples:

```text
请根据当前 fullrate 和 aligned_rate 数据出图。
```

```text
请只重跑 export_figures。
```

## 8b. Native OriginPro Template Seeding

Use this before native Origin plotting when `.otpu` templates do not exist. It creates seed graphs from existing CSV outputs, saves a seed OPJU, and probes whether OriginPro can automatically save `.otpu` templates.

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module origin_create_templates
python origin_create_templates.py --config config.yaml
```

Outputs:

```text
04_origin_projects/template_seed/origin_template_seed.opju
04_origin_projects/templates/FullRate_template.otpu
04_origin_projects/templates/AlignedRate_template.otpu
04_origin_projects/templates/PreLightPost_template.otpu
99_logs/origin_template_creation_probe.txt
```

If automatic `.otpu` export is unavailable, open `origin_template_seed.opju` in OriginPro and manually use `Save Template As...` for each seed graph.

Template rule:

```text
.otpu files are style-only. Do not hard-code duration_s.
LightBand start/end comes from origin_plot_manifest.xlsx per graph row.
```

Agent instruction examples:

```text
请使用 neurotrain skill，为当前项目生成 OriginPro native 三张模板 seed 图和 .otpu。
```

```text
请只运行 origin_create_templates，不要重跑 NeuroExplorer。
```

## 8c. Native OriginPro Plotting

This is the editable Origin graph path. It imports CSV/numerical outputs directly into OriginPro, creates workbooks and graph pages, applies `.otpu` templates when available, saves `.opju`, and exports images from OriginPro. It is separate from `export_figures`.

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module origin_native_plot
python origin_native_plot.py --config config.yaml
```

Minimal opt-in config:

```yaml
origin:
  enabled: true
  backend: "origin_native"  # matplotlib_png | origin_native | both
  save_opju: true
  export_images: true
  opju_mode: "per_file"
  max_graph_pages_per_opju: 80
  require_opju_success: false
  native:
    use_originpro: true
    manifest_path: "04_origin_projects/origin_input/origin_plot_manifest.xlsx"
    opju_output_dir: "04_origin_projects/opju_outputs"
    image_output_dir: "05_exported_figures_origin"
    image_format: "png"
    dpi: 300
    templates:
      fullrate: "04_origin_projects/templates/FullRate_template.otpu"
      aligned_rate: "04_origin_projects/templates/AlignedRate_template.otpu"
      prepost_summary: "04_origin_projects/templates/PreLightPost_template.otpu"
      summary: "04_origin_projects/templates/Summary_template.otpu"
```

Native manifest:

```text
04_origin_projects/origin_input/origin_plot_manifest.xlsx
```

Manifest row fields:

```text
graph_type
file_id
unit_id
source_csv
x_col
y_col
template_path
graph_page_name
light_band_start_s
light_band_end_s
x_min
x_max
output_image_path
include
notes
```

Default OPJU grouping is `per_file` to avoid OriginPro page/window limits. If OriginPro or the `originpro` Python package is unavailable, the module logs a warning and does not block matplotlib PNG, PPTX, or statistics outputs unless `origin.require_opju_success: true`.

Agent instruction examples:

```text
请使用 neurotrain skill，为当前项目生成 Origin native manifest 并用 OriginPro 原生作图。
```

```text
请只运行 origin_native_plot，不要重跑 NeuroExplorer 或 matplotlib。
```

## 9. Build PPTX

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module build_pptx
python build_pptx.py --config config.yaml
```

Output:

```text
06_pptx/PSTH_summary_auto.pptx
```

Light-file slide panels:

```text
Panel A: Full-session rate
Panel B: Light-aligned rate
Panel C: Pre / light / post
```

No-light slide panels:

```text
Panel A: Full-session rate, no light shading
Panel B: No light event; aligned analysis skipped
Panel C: Pre/light/post summary not applicable
```

Metadata includes:

```text
analysis_mode
has_light
event_group
aligned_window
summary_window_mode
baseline_window
light_window
post_window
```

Agent instruction example:

```text
请基于当前图像和 summary 生成 PPTX。
```

## 10. Build Pre / Light / Post Statistics

This module is statistics-only. It reads existing outputs from `03_nex_exports/aligned_rate/` and does not call NeuroExplorer, export fullrate, rebuild aligned-rate CSV, plot figures, or build PPTX.

Relationship to PPTX/PreLightPost figures:

```text
config.yaml aligned_rate.pre_window_s/light_window_s/post_window_s
-> aligned_rate module computes PreLightPostSummary.csv
-> export_figures/PPTX display those PreLightPost values
-> prelightpost_stats aggregates existing PreLightPostSummary.csv values and uses the same window config to fill/validate window metadata
```

If you edit the window config, rerun `aligned_rate` before running `prelightpost_stats`; otherwise firing-rate statistics will still reflect the old summary CSV values.

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module prelightpost_stats
python scripts/build_prelightpost_statistics.py --config config.yaml
```

Primary input:

```text
03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv
03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary_no_light_skipped.csv
```

Optional metadata inputs:

```text
02_stim_events/stim_schedule_master.xlsx
01_sorting_info/unit_quality_table.xlsx
```

Outputs:

```text
07_statistics/all_units_pre_light_post_wide.csv
07_statistics/all_units_pre_light_post_wide_qc.csv
07_statistics/all_units_pre_light_post_qc_excluded.csv
07_statistics/skipped_or_missing_prelightpost.csv
07_statistics/all_units_pre_light_post_statistics.xlsx
```

Optional long table:

- one row per `file_id + unit_id + trial_id + phase`
- `phase` is `baseline`, `light`, or `post`
- suitable for R, Prism, and statistical modeling

Wide table:

- one row per `file_id + unit_id + trial_id`
- suitable for Excel inspection

QC-filtered wide table:

- appends `pre_hz`, window durations, expected spike counts, and activity filter status fields
- keeps rows where `max(pre_hz, light_hz, post_hz) >= 0.5 Hz`
- keeps rows where `total_expected_spikes >= 10`
- excluded rows are written to `all_units_pre_light_post_qc_excluded.csv`

Derived metrics:

```text
delta_light_minus_baseline = light_hz - baseline_hz
delta_post_minus_baseline = post_hz - baseline_hz
delta_post_minus_light = post_hz - light_hz
ratio_light_to_baseline = light_hz / baseline_hz
ratio_post_to_baseline = post_hz / baseline_hz
percent_change_light_vs_baseline
percent_change_post_vs_baseline
```

If `baseline_hz` is zero or missing, ratio and percent-change values are blank instead of raising an error.

No-light behavior:

- no-light files do not produce baseline/light/post values
- `{file_id}_PreLightPostSummary_no_light_skipped.csv` is recorded as `reason = no_light_control`
- no-light rows do not enter `all_units_pre_light_post_wide_qc.csv`
- missing summary files for light rows are recorded as `reason = missing_summary_file`

Config:

```yaml
statistics:
  enabled: true
  output_dir: "07_statistics"
  prelightpost:
    enabled: true
    input_dir: "03_nex_exports/aligned_rate"
    input_pattern: "*_PreLightPostSummary.csv"
    include_only_unit_quality_include_yes: true
    exclude_duplicate_units: false
    duplicate_policy: "keep_all"  # keep_all | keep_representative_only | exclude_duplicates
    include_trial_rows: true
    include_aggregated_rows: true
    preferred_aggregation: "trial"  # trial | mean | median | aggregated | all
    output_wide_csv: "all_units_pre_light_post_wide.csv"
    output_wide_qc_csv: "all_units_pre_light_post_wide_qc.csv"
    output_qc_excluded_csv: "all_units_pre_light_post_qc_excluded.csv"
    output_excel: "all_units_pre_light_post_statistics.xlsx"
    output_long_csv: null
    output_summary_by_file: false
    output_summary_by_condition: false
    activity_filter:
      enabled: true
      min_max_window_hz: 0.5
      min_total_expected_spikes: 10
      clean_table_suffix: "_qc"
    compute_derived_metrics: true
    fail_on_missing_light_summary: false
```

In the full auto workflow this module is disabled by default:

```yaml
run:
  modules:
    prelightpost_stats: false
```

Agent instruction examples:

```text
Only read existing 03_nex_exports PreLightPostSummary outputs and build all-unit statistics tables.
```

```text
Run only prelightpost_stats; do not call NeuroExplorer, plotting, or PPTX generation.
```

## 11. Full Auto Pipeline

Terminal:

```powershell
python run_pipeline.py --config config.yaml
```

Current full order:

```text
build_stim_schedule
build_unit_table
validate
prepare_events
neuroexplorer_export
aligned_rate
export_figures
origin_create_templates
origin_native_plot
build_pptx
```

The first two steps run automatically before validation when enabled in config.

`prelightpost_stats` is not run in full auto unless `run.modules.prelightpost_stats: true`.

Agent instruction examples:

```text
请对这个项目跑一遍完整 auto 流程。
```

```text
请清理历史输出后，重跑完整 pipeline。
```

## 12. Runtime Flags

Dry run:

```powershell
python run_pipeline.py --config config.yaml --dry-run
```

Overwrite flag:

```powershell
python run_pipeline.py --config config.yaml --overwrite
```

Run one module:

```powershell
python run_pipeline.py --config config.yaml --module build_stim_schedule
python run_pipeline.py --config config.yaml --module build_unit_table
python run_pipeline.py --config config.yaml --module validate
python run_pipeline.py --config config.yaml --module prepare_events
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module origin_create_templates
python run_pipeline.py --config config.yaml --module origin_native_plot
python run_pipeline.py --config config.yaml --module build_pptx
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

## 13. Important Config Defaults

```yaml
analysis:
  mode: "auto"

aligned_rate:
  pre_window_s:
    - -60
    - 0
  light_window_s:
    - 5
    - 20
  post_window_s:
    - 25
    - 85

neuroexplorer:
  export_psth: false
  export_fullrate: true
  fullrate:
    template_name: "RateHist_FullSession"

statistics:
  output_dir: "07_statistics"
  prelightpost:
    preferred_aggregation: "trial"
    duplicate_policy: "keep_all"
```

Mainline window rule:

```text
Only aligned_rate.pre_window_s / light_window_s / post_window_s control PreLightPost numeric windows.
Do not add summary_window_mode, baseline_window_s, post_window_mode, or post_window_after_light_s to new configs.
```

`init_project` raw-file policy:

```yaml
init_project:
  raw_pl2_policy:
    auto_move_from_project_root: false
    auto_copy_from_project_root: false
    detect_root_pl2: true
    on_root_pl2_found: "warn_only"
```

## 14. Quick FAQ / Self-Check

Q: PPTX Pre/light/post panel and prelightpost_stats disagree.

A: Check whether `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv` was regenerated after editing `aligned_rate.pre_window_s/light_window_s/post_window_s`. Run:

```powershell
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module build_pptx
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

Q: Statistics still show old windows.

A: `prelightpost_stats` reads existing Summary CSV values. If the Summary CSV contains old window columns, the module logs a warning and keeps those existing values. Rerun `aligned_rate` to regenerate the numeric summary with the current config.

Q: Where is the one true PreLightPost window setting?

A: In `config.yaml`:

```yaml
aligned_rate:
  pre_window_s: [-60, 0]
  light_window_s: [5, 20]
  post_window_s: [25, 85]
```

Q: Should `prepare_events` be enabled for the stable route?

A: No. Default `run.modules.prepare_events: false`. The fullrate-aligned route uses `stim_schedule_master.xlsx` and does not require NeuroExplorer `Light_On` or `Light_Interval` variables.

Q: Why is the aligned output named `pre60_post85`?

A: The tag is derived from the union of the three configured windows: minimum start `-60`, maximum end `85`.

## 15. OriginPro OPJU

When enabled:

```yaml
origin:
  use_originpro: true
  save_opju: true
  opju_output_dir: "04_origin_projects/opju_outputs"
  opju_filename: "{project_name}_fullrate_aligned.opju"
  require_opju_success: false
```

OPJU generation is disabled by default. The OPJU is an optional archive. PPTX generation reads PNG files from `05_exported_figures/` and does not require OPJU unless `origin.require_opju_success: true`.

## 16. Experimental / Legacy Features

These remain in the repo for debugging and local experiments, but are not the stable default route:

```text
analysis.mode: neuroexplorer_psth
automatic Light_On / Light_Interval creation
nex AddInterval / AddTimestamp probes
empty NexVar creation probes
clone NexVar probes
GUI automation fallback
```

Smoke-test scripts:

```powershell
python scripts/smoke_tests/test_nex_create_interval_event.py --config config.yaml --file-id 07 --active-doc
python scripts/smoke_tests/test_nex_create_var_objects.py --config config.yaml --file-id 07 --active-doc
python scripts/smoke_tests/test_nex_clone_var_objects.py --config config.yaml --active-doc
```

## 17. Not Currently A Stable Module

`batch_gui_export_fullrate` is present in some planning/config notes, but the current `run_pipeline.py` module list does not expose it as a runnable stable module.

Use:

```powershell
python run_pipeline.py --config config.yaml --module neuroexplorer_export
```

instead of:

```powershell
python run_pipeline.py --config config.yaml --module batch_gui_export_fullrate
```
