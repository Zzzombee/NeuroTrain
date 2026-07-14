# NeuroTrain

NeuroTrain: event-aligned spike train analysis workflow for fullrate, PSTH-like traces, raster plots, statistics, and presentation-ready outputs.

The current stable workflow is:

`.pl2` -> NeuroExplorer `RateHist_FullSession` -> `SaveNumResults` full-session rate -> Python light-aligned rate reconstruction -> figures -> PPTX.

## Development source of truth

Maintain this skill only in the workspace root. The user-level Codex `neurotrain` skill path should be a Windows junction pointing to this project directory. Do not edit or develop against a separate user-directory copy. All future code, test, config-template, and documentation changes should be made in the workspace root so the source and Codex skill stay synchronized.

## Recommended workflow

1. Prepare `.pl2` files.
2. Auto-build or update `stim_schedule_master` from `.pl2` filenames, or edit it manually if needed.
3. Auto-build or update `unit_quality_table` from `.pl2` neuron names.
4. Create the NeuroExplorer template `RateHist_FullSession`.
5. Run the `fullrate_aligned` pipeline.
6. Inspect `FullRate`, `AlignedRate`, and `PreLightPost` figures.
7. Inspect the PPTX.
8. Optionally inspect the OriginPro OPJU archive.

## Default mode

The default config now uses:

- `analysis.mode: auto`
- `auto` prefers `fullrate_aligned` first
- `neuroexplorer.export_fullrate: true`
- aligned/pre-light/light/post windows are controlled by `aligned_rate.pre_window_s`, `aligned_rate.light_window_s`, and `aligned_rate.post_window_s`

This mode:

- does not require `Light_On` inside NeuroExplorer
- does not require `Light_Interval` inside NeuroExplorer
- uses `stim_schedule_master` as the alignment source
- produces a PSTH-like aligned rate view from full-session rate bins
- uses the union of configured windows as the aligned display/reconstruction window:
  - pre: `-60` to `0`
  - light statistics: `5` to `20`
  - post: `25` to `85`
  - aligned span/tag: `pre60_post85`
  - light band: `0` to `duration_s`
- writes those exact window bounds into `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv`

It is not a spike-timestamp PSTH. Time precision is limited by the full-session rate bin width.

The active pre/light/post window config is:

```yaml
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
```

These three fields are the only mainline parameters controlling the PreLightPost numeric windows. Legacy fields such as `summary_window_mode`, `baseline_window_s`, `post_window_mode`, and `post_window_after_light_s` are compatibility-only and should not be used in new project configs.

## Clean Config Reference

The default `config_template.yaml` keeps only the stable fullrate-aligned workflow settings. Mainline projects should edit these fields, not legacy PSTH/event-helper settings.

```yaml
analysis:
  mode: "auto"
```

- `analysis.mode`: workflow selector. `auto` resolves to `fullrate_aligned` when full-session exports or no-light controls are present.

```yaml
project:
  root_dir: "E:/example/project"
  file_id_column: "file_id"
```

- `project.root_dir`: absolute project folder.
- `project.file_id_column`: table column used to join stim schedule, unit table, exports, figures, PPTX, and statistics.

```yaml
input:
  pl2_dir: "00_raw_pl2"
  stim_schedule: "02_stim_events/stim_schedule_master.xlsx"
  unit_quality_table: "01_sorting_info/unit_quality_table.xlsx"
```

- `input.pl2_dir`: source `.pl2` folder relative to `root_dir`.
- `input.stim_schedule`: stimulation schedule table.
- `input.unit_quality_table`: unit inclusion/QC table.

```yaml
stim_schedule:
  auto_build_from_filenames: true
  update_existing: true
  preserve_manual_edits: true
  output_path: "02_stim_events/stim_schedule_master.xlsx"
  source:
    pl2_dir: "00_raw_pl2"
    file_glob: "*.pl2"
  filename_parser:
    enabled: true
    pattern_name: "sorted_index_light_channels_or_no_light"
    regex: "^sorted_(?P<file_index>\\d+)_(?P<light_on>\\d+(?:\\.\\d+)?)light(?P<duration>\\d+(?:\\.\\d+)?)_(?P<channels>[0-9,]+)\\.pl2$"
    no_light_regex: "^sorted_(?P<file_index>\\d+)_nolight_(?P<channels>[0-9,]+)\\.pl2$"
    case_sensitive: false
  file_id:
    format: "{file_index}"
    zero_pad: 2
```

- `stim_schedule.auto_build_from_filenames`: build/update schedule from `.pl2` filenames.
- `stim_schedule.update_existing`: refresh existing schedule rows on pipeline runs.
- `stim_schedule.preserve_manual_edits`: keep manually edited `condition` and `note` values.
- `stim_schedule.output_path`: schedule output table.
- `stim_schedule.source.pl2_dir`: folder scanned for raw files.
- `stim_schedule.source.file_glob`: file pattern used during scan.
- `stim_schedule.filename_parser.regex`: light-file naming rule.
- `stim_schedule.filename_parser.no_light_regex`: no-light control naming rule.
- `stim_schedule.file_id.format`: canonical `file_id`; default is the two-digit `file_index`, e.g. `01`.
- `stim_schedule.file_id.zero_pad`: pads `file_index` to two digits.

```yaml
unit_table:
  enabled: true
  auto_build_if_missing: true
  update_existing: true
  preserve_manual_edits: true
  source:
    backend: "nex"
    open_pl2: true
    fallback_to_existing_fullrate_exports: true
```

- `unit_table.enabled`: enables unit table generation/update.
- `unit_table.auto_build_if_missing`: creates `unit_quality_table` if missing.
- `unit_table.update_existing`: refreshes the table while preserving manual fields.
- `unit_table.preserve_manual_edits`: keeps `include`, duplicate annotations, exclusion reason, representative unit, and notes.
- `unit_table.source.backend`: unit scanning backend. `nex` reads NeuroExplorer neuron names.
- `unit_table.source.open_pl2`: opens each `.pl2` during scan.
- `unit_table.source.fallback_to_existing_fullrate_exports`: if PL2 scanning fails, read `unit_id` from existing fullrate CSVs.

```yaml
neuroexplorer:
  enabled: true
  backend: "nex_package"
  use_existing_csv_if_available: true
  export_fullrate: true
  fullrate:
    template_name: "RateHist_FullSession"
    bin_width_s: 1
    histogram_unit: "Spikes per second"
  export:
    output_fullrate_dir: "03_nex_exports/fullrate"
    output_aligned_rate_dir: "03_nex_exports/aligned_rate"
    expected_fullrate_pattern: "{file_id}_FullRate_bin{bin_width_s}s.csv"
```

- `neuroexplorer.enabled`: controls NeuroExplorer export stage.
- `neuroexplorer.backend`: export backend.
- `neuroexplorer.use_existing_csv_if_available`: skip NeuroExplorer when expected CSVs already exist.
- `neuroexplorer.export_fullrate`: stable route requires full-session rate export.
- `neuroexplorer.fullrate.template_name`: NeuroExplorer analysis template name.
- `neuroexplorer.fullrate.bin_width_s`: full-session rate bin width in seconds.
- `neuroexplorer.fullrate.histogram_unit`: expected rate unit.
- `neuroexplorer.export.output_fullrate_dir`: fullrate CSV output folder.
- `neuroexplorer.export.output_aligned_rate_dir`: Python reconstructed aligned-rate output folder.
- `neuroexplorer.export.expected_fullrate_pattern`: canonical fullrate CSV filename pattern.

```yaml
aligned_rate:
  enabled: true
  pre_window_s:
    - -60
    - 0
  light_window_s:
    - 5
    - 20
  post_window_s:
    - 25
    - 85
  align_to: "light_on_s"
  bin_width_s: 1
  multi_trial_aggregation: "mean"
  variable_duration_policy: "keep_trials"
  require_light_on_on_bin_boundary: false
  off_boundary_policy: "nearest"
```

- `aligned_rate.enabled`: builds Python aligned-rate CSVs from fullrate CSVs.
- `aligned_rate.pre_window_s`: baseline/pre window relative to light onset. This is the only mainline baseline window setting.
- `aligned_rate.light_window_s`: light-response statistics window relative to light onset. This is the only mainline light statistics window setting.
- `aligned_rate.post_window_s`: post-light statistics window relative to light onset. This is the only mainline post window setting.
- `aligned_rate.align_to`: alignment source column; stable route uses `light_on_s` from `stim_schedule_master`.
- `aligned_rate.bin_width_s`: expected aligned-rate bin width.
- `aligned_rate.multi_trial_aggregation`: aggregated trace method for multi-trial files.
- `aligned_rate.variable_duration_policy`: behavior when multiple light durations exist in one file.
- `aligned_rate.require_light_on_on_bin_boundary`: whether light onset must fall exactly on fullrate bin centers.
- `aligned_rate.off_boundary_policy`: nearest/interpolate/error policy for off-boundary alignment.

The aligned-rate CSV tag is derived from the union of the three windows. With the default windows above, outputs use `pre60_post85`.

```yaml
statistics:
  enabled: true
  output_dir: "07_statistics"
  prelightpost:
    input_dir: "03_nex_exports/aligned_rate"
    input_pattern: "*_PreLightPostSummary.csv"
    output_wide_csv: "all_units_pre_light_post_wide.csv"
    output_wide_qc_csv: "all_units_pre_light_post_wide_qc.csv"
```

- `statistics.enabled`: enables statistics module configuration.
- `statistics.output_dir`: statistics output folder.
- `statistics.prelightpost.input_dir`: existing Summary CSV input folder.
- `statistics.prelightpost.input_pattern`: normal Summary CSV pattern.
- `statistics.prelightpost.output_wide_csv`: raw all-unit table.
- `statistics.prelightpost.output_wide_qc_csv`: activity-QC-passing table.
- `statistics.prelightpost.output_qc_excluded_csv`: excluded/no-light/missing rows.
- `statistics.prelightpost.activity_filter.min_max_window_hz`: minimum activity threshold.
- `statistics.prelightpost.activity_filter.min_total_expected_spikes`: minimum expected spike count threshold.

`prelightpost_stats` does not call NeuroExplorer or recompute aligned traces. It reads existing `PreLightPostSummary.csv` values and uses the same `aligned_rate.pre_window_s/light_window_s/post_window_s` configuration to fill missing window metadata and to compute QC durations when old summaries lack window columns.

```yaml
run:
  modules:
    prepare_events: false
    prelightpost_stats: false
```

- `run.modules.prepare_events`: disabled by default because the stable fullrate-aligned route does not require NeuroExplorer event variables.
- `run.modules.prelightpost_stats`: disabled by default so statistics are run explicitly after reviewing aligned outputs.

## OriginPro outputs

There are now two separate Origin-related paths.

### Matplotlib PNG + OPJU archive

`export_figures` remains the stable QC/fallback plotting path. It creates PNG figures in `05_exported_figures/`. If `origin.save_opju: true`, it can also archive those existing CSV/PNG outputs into an OPJU. This archive mode does not create final graphs natively from Origin data; it imports the already generated outputs.

Enable the archive mode with:

```yaml
origin:
  backend: "matplotlib_png"
  use_originpro: true
  save_opju: true
  opju_generation_mode: "archive_existing_pngs"
  opju_output_dir: "04_origin_projects/opju_outputs"
  opju_filename: "{project_name}_fullrate_aligned.opju"
  overwrite_opju: true
  require_opju_success: false
```

The archive imports the configured source tables and generated CSV outputs into workbooks:

- `stim_schedule_master`
- `unit_quality_table`
- `fullrate_all`
- `aligned_rate_all`
- `prepost_summary_all`

It also attempts to add graph pages for generated fullrate, aligned-rate, pre/light/post, and summary PNG outputs.

### Native OriginPro plotting

`origin_native_plot` is the new editable OriginPro path. It builds a manifest from pipeline CSV outputs, imports those CSVs into OriginPro workbooks, creates graph pages from data, applies `.otpu` templates when available, saves editable `.opju` projects, and optionally exports images from OriginPro.

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module origin_native_plot
python origin_native_plot.py --config config.yaml
```

Recommended opt-in config:

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

Manifest output:

`04_origin_projects/origin_input/origin_plot_manifest.xlsx`

Each manifest row describes one native Origin graph, including `graph_type`, `file_id`, `unit_id`, `source_csv`, `x_col`, `y_col`, `template_path`, `graph_page_name`, light-band bounds, x-axis bounds, and `output_image_path`.

Default native OPJU grouping is `per_file` to avoid OriginPro graph page/window limits. Keep `require_opju_success: false` unless Origin output is mandatory; matplotlib PNGs and PPTX remain independent.

### Native OriginPro template seeding

Use `origin_create_templates` to create seed graphs that mimic the current Python figure style and to probe whether your OriginPro/originpro API can save `.otpu` templates automatically.

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

If automatic `.otpu` export is not supported by the current OriginPro API, the module still saves `origin_template_seed.opju`. Open that OPJU in OriginPro and manually run `Save Template As...` for the FullRate, AlignedRate, and PreLightPost seed graphs.

`.otpu` templates are style-only. Do not hard-code `duration_s` into the template. The `LightBand` start/end is dynamic: `origin_native_plot` reads `light_band_start_s` and `light_band_end_s` from `origin_plot_manifest.xlsx` for each graph.

Template creation config:

```yaml
origin:
  native:
    template_creation:
      enabled: true
      seed_opju_path: "04_origin_projects/template_seed/origin_template_seed.opju"
      auto_save_otpu: true
      fail_if_otpu_save_failed: false
      overwrite_templates: true
```

If the OPJU is not generated:

1. Check that OriginPro is installed.
2. Check that Python can `import originpro`.
3. For native plotting, check that `config.yaml` has `origin.backend: "origin_native"` or `"both"`.
4. Check that `config.yaml` has `origin.save_opju: true` and/or `origin.export_images: true`.
5. Review `99_logs/error_log.xlsx`.

If OriginPro or the `originpro` Python package is unavailable, matplotlib PNG figures and PPTX generation continue. Native Origin logs: `OriginPro unavailable; native Origin plotting skipped.`

## Auto mode

`analysis.mode: auto` is now the recommended default.

Order of operations:

1. try `RateHist_FullSession`
2. export full-session numerical results
3. rebuild aligned traces in Python
4. only if that path fails, retry the experimental `neuroexplorer_psth` route

## Required NeuroExplorer template

Create and save:

- `RateHist_FullSession`

Recommended template settings:

- full recording from `t=0`
- `Bin = 1`
- `Histogram Units = Spikes per second`
- no reference event

## Main commands

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python run_pipeline.py --config config.yaml
python scripts/build_stim_schedule_from_filenames.py --config config.yaml
python run_pipeline.py --config config.yaml --module build_stim_schedule
python scripts/build_unit_quality_table.py --config config.yaml
python run_pipeline.py --config config.yaml --module build_unit_table
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module origin_create_templates
python run_pipeline.py --config config.yaml --module origin_native_plot
python run_pipeline.py --config config.yaml --module build_pptx
```

## Initialize a new project

Create a new project scaffold:

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
```

Optional:

```powershell
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --force
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --with-example --pre-margin 60 --post-margin 60 --bin-width 1
```

Then:

1. Put `.pl2` files into `00_raw_pl2/`
2. Confirm filenames follow `sorted_01_200light25_1,5,9.pl2`
3. Prepare the NeuroExplorer template `RateHist_FullSession`
4. Run the full pipeline

If you run `init_project` in a folder that already contains `sorted_*.pl2`, the skill will only warn. It will not move, copy, rename, or delete raw `.pl2` files automatically.

Move files manually if needed:

```powershell
Move-Item ".\\sorted_*.pl2" ".\\00_raw_pl2\\"
```

## Automatic stim_schedule_master generation from .pl2 filenames

The skill can scan `00_raw_pl2/` and build or update:

- `02_stim_events/stim_schedule_master.xlsx`

Default filename rule:

- `sorted_<file_index>_<light_on_s>light<duration_s>_<sorted_channels>.pl2`
- `sorted_<file_index>_nolight_<sorted_channels>.pl2`

Example:

- `sorted_01_200light25_1,5,9.pl2`

Generated fields:

- `file_id = 01`
- `pl2_file = sorted_01_200light25_1,5,9.pl2`
- `event_group = 200light25`
- `light_on_s = 200`
- `duration_s = 25`
- `light_off_s = 225`
- `condition = ""`
- `note = sorted channels: 1,5,9`

No-light control example:

- `sorted_02_nolight_1,5,9.pl2`

Generated fields:

- `file_id = 02`
- `event_group = nolight`
- `has_light = no`
- `light_on_s = ""`
- `duration_s = ""`
- `light_off_s = ""`
- `condition = no_light`
- `note = sorted channels: 1,5,9`

Behavior:

- supports decimal onset/duration values such as `sorted_03_120.5light15_2,4.pl2`
- preserves manual `condition` and `note` edits in an existing schedule
- adds newly detected `.pl2` files
- keeps old rows that are no longer detected and marks them as `detected_in_latest_scan=no`
- logs non-matching filenames as `warning/skipped`

## Automatic unit_quality_table generation

The skill can now scan each `.pl2` file for neuron variable names and build or update:

- `01_sorting_info/unit_quality_table.xlsx`

Behavior:

- reads `NeuronNames` from each `.pl2` with the `nex` backend
- assigns `unit01`, `unit02`, ... within each file
- defaults `include=yes`
- preserves manual edits in `include`, `exclusion_reason`, `duplicate_of`, `representative_unit`, and `note`
- appends newly detected units
- keeps old rows that are no longer detected and marks them as `detected_in_latest_scan=no`

This removes the need to manually copy sorted unit names out of NeuroExplorer for most projects.

## No-light control files

Use:

- `sorted_<file_index>_nolight_<sorted_channels>.pl2`

Behavior:

- full-session rate is still exported
- no real aligned-rate analysis is generated
- no pre/light/post summary is computed
- aligned and pre/post panels are replaced with no-light placeholders
- PPTX metadata marks the file as `has_light: no`

## Key outputs

- `03_nex_exports/fullrate/{file_id}_FullRate_bin1s.csv`
- `03_nex_exports/aligned_rate/{file_id}_LightAlignedRate_pre60_post85_bin1s.csv`
- `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv`
- `07_statistics/all_units_pre_light_post_wide.csv`
- `07_statistics/all_units_pre_light_post_wide_qc.csv`
- `07_statistics/all_units_pre_light_post_qc_excluded.csv`
- `05_exported_figures/fullrate/{file_id}_{unit_id}_FullRate.png`
- `05_exported_figures/aligned_rate/{file_id}_{unit_id}_AlignedRate_pre60_post85.png`
- `05_exported_figures/prepost_summary/{file_id}_{unit_id}_PreLightPost.png`
- `05_exported_figures/summary/{file_id}_Summary_pre60_post85.png`
- `06_pptx/PSTH_summary_auto.pptx`

## PreLightPost statistics QC

Run only the statistics module after aligned-rate CSVs already exist:

```powershell
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

This module reads `03_nex_exports/aligned_rate/*_PreLightPostSummary.csv` only. It does not call NeuroExplorer, generate figures, or build PPTX files.

The statistics module does not recompute firing-rate values. Its numeric source of truth is the existing `PreLightPostSummary.csv`. Those CSVs are produced by `aligned_rate` using the shared `aligned_rate.pre_window_s`, `aligned_rate.light_window_s`, and `aligned_rate.post_window_s` settings, and PPTX/PreLightPost figures display the same summary values and window metadata. `prelightpost_stats` reads the same window config to fill missing window metadata in older Summary CSVs and to warn when existing Summary CSV window metadata differs from the current config. If you change window settings, rerun `aligned_rate` before rerunning `prelightpost_stats`.

The raw wide table is written to `07_statistics/all_units_pre_light_post_wide.csv`. The QC-filtered table is written to `07_statistics/all_units_pre_light_post_wide_qc.csv`, and excluded rows are written to `07_statistics/all_units_pre_light_post_qc_excluded.csv`.

QC keeps rows where `max(pre_hz, light_hz, post_hz) >= 0.5 Hz` and `total_expected_spikes >= 10`. `pre_hz` is an alias of `baseline_hz`; `baseline_hz` remains in the raw columns. No-light controls do not enter `wide_qc` and are recorded in excluded/skipped outputs. `summary_by_file` and `summary_by_condition` outputs are no longer generated.

## Experimental / legacy modes

These are no longer the recommended path and are kept isolated from the default workflow:

- `analysis.mode: neuroexplorer_psth`
- NeuroExplorer `Light_On` / `Light_Interval` auto-creation
- `nex.AddInterval` / `nex.AddTimestamp` probes
- empty `NexVar` creation probes
- clone-`NexVar` workaround probes
- GUI automation fallback

These scripts remain under:

- `scripts/smoke_tests/`

Use them only for debugging or local experimentation.
