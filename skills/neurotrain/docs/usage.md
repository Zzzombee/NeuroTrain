# Usage Guide

## What this skill does

Recommended mode:

- `analysis.mode: auto`

Optional compatibility mode:

- `analysis.mode: fullrate_aligned`
- behavior: force only the stable full-session export and aligned reconstruction route

Stable path:

1. Export full-session firing rate from NeuroExplorer with `RateHist_FullSession`.
2. Rebuild a light-aligned rate histogram in Python from `stim_schedule_master`.
3. Plot:
   - full-session rate
   - aligned rate over the union of configured pre/light/post windows
   - pre/light/post summary using:
     - `aligned_rate.pre_window_s = [-60, 0]`
     - `aligned_rate.light_window_s = [5, 20]`
     - `aligned_rate.post_window_s = [25, 85]`
4. Build a PPTX.
5. Save one optional OriginPro OPJU archive for project-level review.

## Required inputs

- `00_raw_pl2/`
- `01_sorting_info/unit_quality_table.xlsx` or `.csv`
- `02_stim_events/stim_schedule_master.xlsx` or `.csv`

`stim_schedule_master` must provide:

- `file_id`
- `pl2_file`
- `has_light`
- `light_on_s`
- `duration_s`
- `light_off_s`

If `light_off_s` is blank, the pipeline computes:

- `light_off_s = light_on_s + duration_s`

For no-light control files:

- use filename `sorted_<file_index>_nolight_<sorted_channels>.pl2`
- `event_group = nolight`
- `has_light = no`
- `light_on_s / duration_s / light_off_s` remain blank
- full-session export still runs
- aligned-rate and pre/light/post analysis are skipped

## Recommended config

Use [config_template.yaml](../config_template.yaml) and keep:

- `analysis.mode: auto`
- `aligned_rate.pre_window_s: [-60, 0]`
- `aligned_rate.light_window_s: [5, 20]`
- `aligned_rate.post_window_s: [25, 85]`
- `neuroexplorer.fullrate.template_name: RateHist_FullSession`

## Run

Initialize a new project:

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --with-example
```

`init_project` only creates the scaffold and template files. If the project root already contains `sorted_*.pl2`, the skill will warn but will not move, copy, rename, or delete those raw files.

Move them manually into `00_raw_pl2/` if needed:

```powershell
Move-Item ".\\sorted_*.pl2" ".\\00_raw_pl2\\"
```

Then run the pipeline:

```powershell
python run_pipeline.py --config config.yaml
```

Build or update `unit_quality_table` only:

```powershell
python scripts/build_stim_schedule_from_filenames.py --config config.yaml
python run_pipeline.py --config config.yaml --module build_stim_schedule
python scripts/build_unit_quality_table.py --config config.yaml
python run_pipeline.py --config config.yaml --module build_unit_table
```

Or module-by-module:

```powershell
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module prelightpost_stats
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module origin_create_templates
python run_pipeline.py --config config.yaml --module origin_native_plot
python run_pipeline.py --config config.yaml --module build_pptx
```

## Output checks

Check these files:

- `02_stim_events/stim_schedule_master.xlsx` or `.csv`
- `01_sorting_info/unit_quality_table.xlsx` or `.csv`
- `03_nex_exports/fullrate/{file_id}_FullRate_bin1s.csv`
- `03_nex_exports/aligned_rate/{file_id}_LightAlignedRate_pre60_post85_bin1s.csv`
- `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv`
- `07_statistics/all_units_pre_light_post_wide.csv`
- `07_statistics/all_units_pre_light_post_wide_qc.csv`
- `07_statistics/all_units_pre_light_post_qc_excluded.csv`
- `05_exported_figures/fullrate/`
- `05_exported_figures/aligned_rate/`
- `05_exported_figures/prepost_summary/`
- `05_exported_figures/summary/`
- `04_origin_projects/origin_input/origin_plot_manifest.xlsx`
- `05_exported_figures_origin/`
- `04_origin_projects/opju_outputs/{project_name}_fullrate_aligned.opju`
- `06_pptx/PSTH_summary_auto.pptx`

## PreLightPost statistics-only export

Use this when `03_nex_exports/aligned_rate/*_PreLightPostSummary.csv` already exists and you only need statistics tables:

```powershell
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

The module reads existing aligned-rate summary CSVs and writes:

- `07_statistics/all_units_pre_light_post_wide.csv`
- `07_statistics/all_units_pre_light_post_wide_qc.csv`
- `07_statistics/all_units_pre_light_post_qc_excluded.csv`
- `07_statistics/skipped_or_missing_prelightpost.csv`

QC keeps rows where `max(pre_hz, light_hz, post_hz) >= 0.5 Hz` and `total_expected_spikes >= 10`. Expected spike counts are computed from each window's firing rate times its window duration, falling back to `duration_s` if window bounds are missing. No-light files are excluded from `wide_qc` and recorded in excluded/skipped outputs. `summary_by_file` and `summary_by_condition` CSVs and Excel sheets are not produced.

`prelightpost_stats` reads the same `aligned_rate.pre_window_s/light_window_s/post_window_s` config to fill or validate window metadata, but it does not recompute firing-rate values. It aggregates the existing `PreLightPostSummary.csv` values. If you change the windows, rerun `aligned_rate` first.

## OriginPro plotting paths

### Stable matplotlib/QC path

`export_figures` creates PNG figures in `05_exported_figures/`. This remains the stable fallback and the default PPTX image source.

### Legacy OPJU archive

When `origin.save_opju: true` and `origin.opju_generation_mode: archive_existing_pngs`, `export_figures` can archive existing CSV/PNG outputs into:

`04_origin_projects/opju_outputs/{project_name}_fullrate_aligned.opju`

This archive mode imports generated matplotlib PNGs; it is not the recommended editable Origin graph workflow.

### Native OriginPro plotting

`origin_native_plot` imports CSV data directly into OriginPro, creates editable graph pages, applies `.otpu` templates when available, saves OPJU, and exports images from OriginPro.

If `.otpu` templates do not exist yet, run template seeding first:

```powershell
python run_pipeline.py --config config.yaml --module origin_create_templates
python origin_create_templates.py --config config.yaml
```

Template seeding creates:

```text
04_origin_projects/template_seed/origin_template_seed.opju
04_origin_projects/templates/FullRate_template.otpu
04_origin_projects/templates/AlignedRate_template.otpu
04_origin_projects/templates/PreLightPost_template.otpu
99_logs/origin_template_creation_probe.txt
```

If automatic `.otpu` saving fails, open `origin_template_seed.opju` and manually use `Save Template As...`. Templates should contain style only; `LightBand` start/end is updated by `origin_native_plot` from the manifest for each graph.

Terminal:

```powershell
python run_pipeline.py --config config.yaml --module origin_native_plot
python origin_native_plot.py --config config.yaml
```

Opt-in config:

```yaml
origin:
  enabled: true
  backend: "origin_native"
  use_originpro: true
  save_opju: true
  export_images: true
  opju_mode: "per_file"
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

Each manifest row describes one native graph: graph type, file/unit, source CSV, x/y columns, template path, graph page name, light-band bounds, x-axis bounds, and Origin-exported image path.

Default `opju_mode: per_file` avoids OriginPro page/window limits. If OriginPro is unavailable, `origin_native_plot` logs a warning and does not block `export_figures`, `build_pptx`, or statistics modules. `origin_plot` remains available only as a compatibility alias for `export_figures`.

## Experimental / legacy

`neuroexplorer_psth`, automatic `Light_On` creation, interval/event `NexVar` probes, clone-object probes, and GUI automation are retained only for debugging and legacy use.
