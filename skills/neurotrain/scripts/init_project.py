from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.logging_utils import PipelineLogger
from utils.path_utils import ensure_dir, load_yaml, save_yaml
from utils.table_utils import write_table


PROJECT_DIRS = [
    "00_raw_pl2",
    "01_sorting_info",
    "02_stim_events",
    "02_stim_events/exported_events",
    "03_nex_exports/fullrate",
    "03_nex_exports/aligned_rate",
    "03_nex_exports/psth",
    "03_nex_exports/raster",
    "04_origin_projects/templates",
    "04_origin_projects/opju_outputs",
    "05_exported_figures/fullrate",
    "05_exported_figures/aligned_rate",
    "05_exported_figures/prepost_summary",
    "05_exported_figures/summary",
    "06_pptx",
    "99_logs",
]

ROOT_PL2_LOG_NAME = "root_pl2_detected_files.txt"

STIM_COLUMNS = [
    "file_id",
    "pl2_file",
    "event_group",
    "has_light",
    "light_on_s",
    "duration_s",
    "light_off_s",
    "condition",
    "note",
    "file_index",
    "sorted_channels",
    "detected_in_latest_scan",
    "created_at",
    "updated_at",
]

UNIT_COLUMNS = [
    "file_id",
    "pl2_file",
    "unit_id",
    "unit_index",
    "channel",
    "original_name",
    "source_variable_type",
    "include",
    "exclusion_reason",
    "representative_unit",
    "duplicate_of",
    "note",
    "detected_in_latest_scan",
    "detected_by",
    "created_at",
    "updated_at",
]


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _project_readme_text(project_dir: Path) -> str:
    return f"""# NeuroTrain Event-Aligned Spike Train Analysis Project

## Project structure

- `00_raw_pl2/`: manually put source `.pl2` files here
- `01_sorting_info/unit_quality_table.xlsx`: auto-generated / manually curated unit inclusion table
- `02_stim_events/stim_schedule_master.xlsx`: auto-generated / manually curated stimulation schedule
- `03_nex_exports/fullrate/`: NeuroExplorer full-session numerical exports
- `03_nex_exports/aligned_rate/`: aligned-rate reconstructions and summary tables
- `05_exported_figures/`: exported figures
- `06_pptx/`: PowerPoint output

## Recommended `.pl2` filename rule

Use:

`sorted_<file_index>_<light_on_s>light<duration_s>_<sorted_channels>.pl2`

Example:

`sorted_01_200light25_1,5,9.pl2`

This will be parsed into:

- `file_id = 01`
- `light_on_s = 200`
- `duration_s = 25`
- `light_off_s = 225`
- `note = sorted channels: 1,5,9`

## Required NeuroExplorer template

Create `RateHist_FullSession` in NeuroExplorer:

- full-session rate histogram
- bin width = 1 s
- units = Spikes per second
- no `Light_On` required
- no `Light_Interval` required
- selected variables should be the sorted units you want to analyze

## Recommended workflow

```powershell
python run_pipeline.py --config config.yaml --module build_stim_schedule
python run_pipeline.py --config config.yaml --module build_unit_table
python run_pipeline.py --config config.yaml --module batch_gui_export_fullrate
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module build_pptx
```

Or run the full pipeline:

```powershell
python run_pipeline.py --config config.yaml
```

If your local checkout does not yet expose `batch_gui_export_fullrate`, use:

```powershell
python run_pipeline.py --config config.yaml --module neuroexplorer_export
```

## Raw `.pl2` placement

Move raw `.pl2` files into:

`00_raw_pl2/`

Recommended order:

1. Run `init_project` first
2. Manually move `sorted_*.pl2` into `00_raw_pl2/`
3. Run the pipeline

`init_project` does not automatically move, copy, or rename raw `.pl2` files.

## Default aligned window

- pre/light/post statistics windows are configured explicitly in `config.yaml`:
  - `aligned_rate.pre_window_s`
  - `aligned_rate.light_window_s`
  - `aligned_rate.post_window_s`
- default aligned window spans the union of those three windows
- alignment point = `light_on_s`
- default windows:
  - pre: `[-60, 0]`
  - light: `[5, 20]`
  - post: `[25, 85]`

## Manual review checklist

- Review `unit_quality_table.xlsx` and set `include = no` for units to exclude
- Mark duplicate units manually if needed
- Inspect the exported figures and PPTX

Project root:

`{project_dir.as_posix()}`
"""


def _example_stim_row(now: str) -> dict:
    return {
        "file_id": "01",
        "pl2_file": "sorted_01_200light25_1,5,9.pl2",
        "event_group": "200light25",
        "has_light": "yes",
        "light_on_s": 200,
        "duration_s": 25,
        "light_off_s": 225,
        "condition": "",
        "note": "sorted channels: 1,5,9",
        "file_index": "01",
        "sorted_channels": "1,5,9",
        "detected_in_latest_scan": "yes",
        "created_at": now,
        "updated_at": now,
    }


def _example_unit_row(now: str) -> dict:
    return {
        "file_id": "01",
        "pl2_file": "sorted_01_200light25_1,5,9.pl2",
        "unit_id": "unit01",
        "unit_index": 1,
        "channel": 1,
        "original_name": "SPK_SPKC01a",
        "source_variable_type": "NeuronNames",
        "include": "yes",
        "exclusion_reason": "",
        "representative_unit": "unit01",
        "duplicate_of": "",
        "note": "",
        "detected_in_latest_scan": "yes",
        "detected_by": "nex",
        "created_at": now,
        "updated_at": now,
    }


def _load_template_config() -> dict:
    template_path = Path(__file__).resolve().parents[1] / "config_template.yaml"
    return load_yaml(template_path)


def _root_pl2_policy(config: dict) -> dict:
    return (
        config.get("init_project", {}).get("raw_pl2_policy", {})
        or {
            "auto_move_from_project_root": False,
            "auto_copy_from_project_root": False,
            "detect_root_pl2": True,
            "root_pl2_glob": "sorted_*.pl2",
            "on_root_pl2_found": "warn_only",
            "max_files_to_list_in_console": 5,
            "write_full_file_list_to_log": True,
        }
    )


def build_init_config(
    *,
    project_dir: Path,
    bin_width: float,
    pre_margin: float,
    post_margin: float,
    file_id_format: str,
) -> dict:
    config = _load_template_config()
    project_root = project_dir.resolve().as_posix()
    config.setdefault("project", {})["root_dir"] = project_root
    config.setdefault("analysis", {})["mode"] = "auto"
    config.setdefault("input", {})["stim_schedule"] = "02_stim_events/stim_schedule_master.xlsx"
    config.setdefault("input", {})["unit_quality_table"] = "01_sorting_info/unit_quality_table.xlsx"
    config.setdefault("stim_schedule", {})["output_path"] = "02_stim_events/stim_schedule_master.xlsx"
    config.setdefault("unit_table", {})["output_path"] = "01_sorting_info/unit_quality_table.xlsx"
    config["stim_schedule"].setdefault("source", {})["pl2_dir"] = "00_raw_pl2"
    config["stim_schedule"].setdefault("file_id", {})["format"] = file_id_format
    config["unit_table"].setdefault("source", {})["open_pl2"] = True
    config.setdefault("neuroexplorer", {})["export_psth"] = False
    config["neuroexplorer"]["export_fullrate"] = True
    config["neuroexplorer"].setdefault("fullrate", {})["enabled"] = True
    config["neuroexplorer"]["fullrate"]["template_name"] = "RateHist_FullSession"
    config["neuroexplorer"]["fullrate"]["bin_width_s"] = bin_width
    config["neuroexplorer"]["fullrate"]["histogram_unit"] = "Spikes per second"
    config["neuroexplorer"]["fullrate"]["save_num_results"] = True
    config["neuroexplorer"]["fullrate"]["skip_if_template_missing"] = False
    config["neuroexplorer"]["fullrate"]["x_min_s"] = 0
    config["neuroexplorer"]["fullrate"]["x_max_s"] = None
    config["neuroexplorer"].pop("gui_batch_open", None)
    config.setdefault("aligned_rate", {})["enabled"] = True
    config["aligned_rate"].pop("window_mode", None)
    config["aligned_rate"].pop("pre_margin_s", None)
    config["aligned_rate"].pop("post_margin_s", None)
    config["aligned_rate"].pop("x_min_s", None)
    config["aligned_rate"].pop("x_max_s", None)
    config["aligned_rate"].pop("summary_window_mode", None)
    config["aligned_rate"].pop("baseline_window_s", None)
    config["aligned_rate"].pop("baseline_window_mode", None)
    config["aligned_rate"].pop("light_window_mode", None)
    config["aligned_rate"].pop("post_window_mode", None)
    config["aligned_rate"].pop("post_window_after_light_s", None)
    config["aligned_rate"]["pre_window_s"] = [-abs(pre_margin), 0]
    config["aligned_rate"]["light_window_s"] = [5, 20]
    config["aligned_rate"]["post_window_s"] = [25, 25 + post_margin]
    config["aligned_rate"]["align_to"] = "light_on_s"
    config["aligned_rate"]["bin_width_s"] = bin_width
    config["aligned_rate"]["multi_trial_aggregation"] = "mean"
    config["aligned_rate"]["variable_duration_policy"] = "keep_trials"
    config["aligned_rate"]["require_light_on_on_bin_boundary"] = False
    config["aligned_rate"]["off_boundary_policy"] = "nearest"
    config.setdefault("plotting", {})["psth_like_from_fullrate"] = True
    config["plotting"].pop("full_session_light_band_source", None)
    config["plotting"]["aligned_light_band_start_s"] = 0
    config["plotting"]["aligned_light_band_end_mode"] = "duration_s"
    config.setdefault("origin", {})["enabled"] = True
    config["origin"]["backend"] = "matplotlib_png"
    config["origin"]["save_opju"] = False
    config["origin"]["export_images"] = False
    config["origin"].setdefault("use_originpro", True)
    config["origin"]["opju_mode"] = "per_file"
    config["origin"].setdefault("max_graph_pages_per_opju", 80)
    config["origin"].setdefault("opju_generation_mode", "archive_existing_pngs")
    config["origin"].setdefault("opju_output_dir", "04_origin_projects/opju_outputs")
    config["origin"].setdefault("opju_filename", "{project_name}_fullrate_aligned.opju")
    config["origin"].setdefault("overwrite_opju", True)
    config["origin"].setdefault("require_opju_success", False)
    config["origin"].setdefault("close_origin_after_save", False)
    config["origin"].setdefault(
        "native",
        {
            "use_originpro": True,
            "manifest_path": "04_origin_projects/origin_input/origin_plot_manifest.xlsx",
            "opju_output_dir": "04_origin_projects/opju_outputs",
            "image_output_dir": "05_exported_figures_origin",
            "image_format": "png",
            "dpi": 300,
            "templates": {
                "fullrate": "04_origin_projects/templates/FullRate_template.otpu",
                "aligned_rate": "04_origin_projects/templates/AlignedRate_template.otpu",
                "prepost_summary": "04_origin_projects/templates/PreLightPost_template.otpu",
                "summary": "04_origin_projects/templates/Summary_template.otpu",
            },
            "template_creation": {
                "enabled": False,
                "seed_opju_path": "04_origin_projects/template_seed/origin_template_seed.opju",
                "auto_save_otpu": True,
                "fail_if_otpu_save_failed": False,
                "overwrite_templates": True,
                "templates": {
                    "fullrate": "04_origin_projects/templates/FullRate_template.otpu",
                    "aligned_rate": "04_origin_projects/templates/AlignedRate_template.otpu",
                    "prepost_summary": "04_origin_projects/templates/PreLightPost_template.otpu",
                },
                "style": {
                    "line_color": "#1F77B4",
                    "line_width_pt": 1.8,
                    "light_band_color": "#B7C9E8",
                    "light_band_transparency": 70,
                    "baseline_bar_color": "#9BA7B0",
                    "light_bar_color": "#F3C969",
                    "post_bar_color": "#8BC6A2",
                    "grid_color": "#B0B0B0",
                    "grid_transparency": 75,
                    "title_font_size": 24,
                    "axis_title_font_size": 20,
                    "tick_font_size": 16,
                },
            },
            "graph_pages": {
                "fullrate": True,
                "aligned_rate": True,
                "prepost_summary": True,
                "summary": True,
            },
        },
    )
    config["origin"].setdefault(
        "project_content",
        {
            "include_stim_schedule": True,
            "include_unit_quality_table": True,
            "include_fullrate_data": True,
            "include_aligned_rate_data": True,
            "include_prepost_summary_data": True,
            "include_graph_pages": True,
        },
    )
    config["origin"].setdefault(
        "graph_pages",
        {
            "fullrate": True,
            "aligned_rate": True,
            "prepost_summary": True,
            "summary": True,
        },
    )
    config.setdefault("pptx", {})["output_file"] = "06_pptx/PSTH_summary_auto.pptx"
    config.setdefault("run", {})["overwrite"] = True
    config["run"]["dry_run"] = False
    config["run"]["stop_on_error"] = False
    config["run"]["clean_outputs_before_run"] = False
    config["run"]["clean_fullrate"] = False
    config["run"].setdefault("modules", {})
    config["run"]["modules"].update(
        {
            "build_stim_schedule": True,
            "build_unit_table": True,
            "validate": True,
            "prepare_events": False,
            "neuroexplorer_export": True,
            "aligned_rate": True,
            "time_cluster_permutation": False,
            "export_figures": True,
            "origin_create_templates": False,
            "origin_native_plot": False,
            "origin_plot": False,
            "build_pptx": True,
        }
    )
    config.setdefault("init_project", {})["raw_pl2_policy"] = {
        "auto_move_from_project_root": False,
        "auto_copy_from_project_root": False,
        "detect_root_pl2": True,
        "root_pl2_glob": "sorted_*.pl2",
        "on_root_pl2_found": "warn_only",
        "max_files_to_list_in_console": 5,
        "write_full_file_list_to_log": True,
    }
    return config


def _write_if_allowed(path: Path, writer, *, force: bool, skipped_existing: list[str], created_files: list[str]) -> None:
    if path.exists() and not force:
        skipped_existing.append(str(path))
        return
    writer()
    created_files.append(str(path))


def _handle_root_pl2_detection(
    *,
    project_dir: Path,
    raw_pl2_dir: Path,
    config: dict,
    logs_dir: Path,
    logger: PipelineLogger,
) -> list[str]:
    policy = _root_pl2_policy(config)
    if not policy.get("detect_root_pl2", True):
        return []

    root_pl2_files = sorted(project_dir.glob(str(policy.get("root_pl2_glob", "sorted_*.pl2"))))
    if not root_pl2_files:
        return []

    listed_limit = max(int(policy.get("max_files_to_list_in_console", 5)), 0)
    listed_names = [path.name for path in root_pl2_files[:listed_limit]]
    listed_suffix = "" if len(root_pl2_files) <= listed_limit else f" ... (+{len(root_pl2_files) - listed_limit} more)"
    message = (
        f"Found sorted_*.pl2 files in project root; user must manually move them into 00_raw_pl2. "
        f"found={len(root_pl2_files)}"
    )
    logger.log(
        "init_project",
        "root_pl2_detected",
        project_dir.as_posix(),
        raw_pl2_dir.as_posix(),
        "warning" if policy.get("on_root_pl2_found", "warn_only") != "ignore" else "success",
        message,
    )

    if policy.get("write_full_file_list_to_log", True):
        file_list_path = logs_dir / ROOT_PL2_LOG_NAME
        file_list_path.write_text("\n".join(path.name for path in root_pl2_files) + "\n", encoding="utf-8")

    mode = str(policy.get("on_root_pl2_found", "warn_only")).strip().lower()
    if mode == "warn_only":
        print(
            f"Found {len(root_pl2_files)} sorted_*.pl2 files in project root. "
            "Auto-move is disabled. Please manually move raw .pl2 files into 00_raw_pl2."
        )
        if listed_names:
            print("Examples: " + ", ".join(listed_names) + listed_suffix)
    elif mode == "error":
        logger.save()
        raise RuntimeError(
            "Found sorted_*.pl2 files in project root. Auto-move is disabled. "
            "Please manually move raw .pl2 files into 00_raw_pl2 and rerun init_project."
        )
    return [path.name for path in root_pl2_files]


def initialize_project(
    *,
    project_dir: Path,
    force: bool = False,
    with_example: bool = False,
    config_preset: str = "fullrate_aligned",
    bin_width: float = 1.0,
    pre_margin: float = 60.0,
    post_margin: float = 60.0,
    file_id_format: str = "{file_index}",
) -> Path:
    if config_preset != "fullrate_aligned":
        raise ValueError(f"Unsupported config preset: {config_preset}")
    project_dir = Path(project_dir).expanduser().resolve()
    created_dirs: list[str] = []
    created_files: list[str] = []
    skipped_existing: list[str] = []

    for relative_dir in PROJECT_DIRS:
        target_dir = project_dir / relative_dir
        if not target_dir.exists():
            ensure_dir(target_dir)
            created_dirs.append(str(target_dir))
        else:
            ensure_dir(target_dir)

    logs_dir = project_dir / "99_logs"
    logger = PipelineLogger(logs_dir)
    now = _timestamp()

    try:
        config = build_init_config(
            project_dir=project_dir,
            bin_width=bin_width,
            pre_margin=pre_margin,
            post_margin=post_margin,
            file_id_format=file_id_format,
        )
        config_path = project_dir / "config.yaml"
        effective_config = load_yaml(config_path) if config_path.exists() and not force else config
        stim_path = project_dir / "02_stim_events" / "stim_schedule_master.xlsx"
        unit_path = project_dir / "01_sorting_info" / "unit_quality_table.xlsx"
        readme_path = project_dir / "README_project.md"

        _write_if_allowed(
            config_path,
            lambda: save_yaml(config, config_path),
            force=force,
            skipped_existing=skipped_existing,
            created_files=created_files,
        )

        stim_df = pd.DataFrame([_example_stim_row(now)] if with_example else [], columns=STIM_COLUMNS)
        unit_df = pd.DataFrame([_example_unit_row(now)] if with_example else [], columns=UNIT_COLUMNS)

        _write_if_allowed(
            stim_path,
            lambda: write_table(stim_df, stim_path),
            force=force,
            skipped_existing=skipped_existing,
            created_files=created_files,
        )
        _write_if_allowed(
            unit_path,
            lambda: write_table(unit_df, unit_path),
            force=force,
            skipped_existing=skipped_existing,
            created_files=created_files,
        )
        _write_if_allowed(
            readme_path,
            lambda: readme_path.write_text(_project_readme_text(project_dir), encoding="utf-8"),
            force=force,
            skipped_existing=skipped_existing,
            created_files=created_files,
        )

        parameter_record = {
            "module": "init_project",
            "project_dir": project_dir.as_posix(),
            "force": force,
            "with_example": with_example,
            "config_preset": config_preset,
            "bin_width": bin_width,
            "pre_margin": pre_margin,
            "post_margin": post_margin,
            "file_id_format": file_id_format,
            "created_at": now,
        }
        save_yaml(parameter_record, logs_dir / "parameter_record.yaml")
        if str(logs_dir / "parameter_record.yaml") not in created_files:
            created_files.append(str(logs_dir / "parameter_record.yaml"))

        _handle_root_pl2_detection(
            project_dir=project_dir,
            raw_pl2_dir=project_dir / "00_raw_pl2",
            config=effective_config,
            logs_dir=logs_dir,
            logger=logger,
        )

        logger.log(
            "init_project",
            "*",
            project_dir.as_posix(),
            str(config_path),
            "success",
            f"Initialized project scaffold. created_dirs={len(created_dirs)} created_files={len(created_files)} skipped_existing={len(skipped_existing)}",
        )
        return project_dir
    finally:
        logger.save()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a new NeuroTrain event-aligned spike train analysis project.")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--with-example", action="store_true")
    parser.add_argument("--config-preset", default="fullrate_aligned")
    parser.add_argument("--bin-width", type=float, default=1.0)
    parser.add_argument("--pre-margin", type=float, default=60.0)
    parser.add_argument("--post-margin", type=float, default=60.0)
    parser.add_argument("--file-id-format", default="{file_index}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_dir = initialize_project(
        project_dir=Path(args.project_dir),
        force=args.force,
        with_example=args.with_example,
        config_preset=args.config_preset,
        bin_width=args.bin_width,
        pre_margin=args.pre_margin,
        post_margin=args.post_margin,
        file_id_format=args.file_id_format,
    )
    print(f"Initialized project: {project_dir}")
    print("Next steps:")
    print("1. Put .pl2 files into 00_raw_pl2/")
    print("2. Prepare the NeuroExplorer template RateHist_FullSession")
    print("3. Run: python run_pipeline.py --config config.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
