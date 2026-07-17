from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd
import yaml

from scripts.raster_pipeline import RasterConfigError, RasterInputError, load_raster_config, run_raster_pipeline


def _atomic_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
        temporary = Path(handle.name)
    temporary.replace(path)


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        temporary = Path(handle.name)
    temporary.replace(path)


def default_raster_project_config(project_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "paths": {
            "input_root": "03_nex_exports/raster_input",
            "output_root": "03_nex_exports",
            "spike_table_glob": "project_unit_train.csv",
            "event_table_glob": "alignment_events.csv",
            "output_subdir": "raster",
        },
        "project_export": {
            "project_root": str(project_dir.resolve()),
            "pl2_dir": "00_raw_pl2",
            "stim_schedule_table": "02_stim_events/stim_schedule_master.xlsx",
            "unit_quality_table": "01_sorting_info/unit_quality_table.xlsx",
            "include_value": "yes",
            "file_id_width": 2,
            "session_id_template": "session_{file_id}",
            "event_name": "Light_On",
            "schedule_columns": {
                "file_id": "file_id",
                "pl2_file": "pl2_file",
                "has_light": "has_light",
                "event_time": "light_on_s",
                "stimulus_duration": "duration_s",
            },
            "unit_columns": {
                "file_id": "file_id",
                "unit_id": "unit_id",
                "channel_id": "channel",
                "original_name": "original_name",
                "include": "include",
            },
        },
        "input": {
            "format": "neuroexplorer_long_csv",
            "delimiter": None,
            "encoding": "utf-8-sig",
            "time_unit": "seconds",
            "columns": {
                "session_id": "session_id",
                "unit_id": "unit_id",
                "channel_id": "channel_id",
                "spike_time": "timestamp",
                "event_name": "event_name",
                "event_time": "timestamp",
                "trial_id": "trial_id",
                "stimulus_duration": "stimulus_duration_s",
            },
        },
        "alignment": {
            "event_name": "Light_On",
            "window_s": [-60.0, 300.0],
            "boundary": "left_closed_right_open",
            "trial_order": "event_time",
            "minimum_inter_event_interval_s": None,
            "overlapping_windows": "allow",
            "missing_event": "error",
            "light_off_event_name": None,
            "fixed_stimulus_duration_s": None,
        },
        "trial_filter": {
            "first_trial": None,
            "last_trial": None,
            "include_trial_ids": None,
            "exclude_trial_ids": [],
        },
        "plot": {
            "formats": ["png"],
            "dpi": 300,
            "figsize_inches": [10.0, 6.0],
            "combined_width_inches": 12.0,
            "combined_row_height_inches": 0.45,
            "combined_min_height_inches": 4.0,
            "spike_color": "black",
            "spike_linewidth": 0.6,
            "spike_height_fraction": 0.8,
            "alignment_line_color": "red",
            "alignment_linewidth": 1.0,
            "show_alignment_line": True,
            "stimulus_band_color": "#B7C9E8",
            "stimulus_band_alpha": 0.25,
            "title_template": "{session_id} | {unit_id} | aligned to {event_name}",
            "combined_title": "Project raster | aligned to Light_On",
            "x_label": "Time from event (s)",
            "y_label": "Trial",
            "combined_y_label": "Session | Unit",
            "transparent_background": False,
        },
        "output": {
            "write_individual_figures": True,
            "write_combined_figure": True,
            "combined_filename": "project_combined_raster",
            "write_combined_row_map_csv": True,
            "write_trial_summary_csv": True,
            "write_unit_summary_csv": True,
            "write_exclusion_csv": True,
            "write_aligned_spikes_long_csv": False,
            "write_manifest_json": True,
            "overwrite": False,
        },
        "runtime": {
            "fail_on_empty_unit": False,
            "continue_on_unit_error": True,
            "log_level": "INFO",
        },
    }


def initialize_raster_project(project_dir: Path, *, force: bool = False) -> tuple[Path, bool]:
    project_dir = Path(project_dir).expanduser().resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    config_path = project_dir / "raster_config.yaml"
    if config_path.exists() and not force:
        return config_path, False
    (project_dir / "03_nex_exports" / "raster_input").mkdir(parents=True, exist_ok=True)
    _atomic_yaml(config_path, default_raster_project_config(project_dir))
    return config_path, True


def _resolve_project_path(project_root: Path, value: str, key: str) -> Path:
    if not str(value).strip():
        raise RasterConfigError(f"Config key project_export.{key} must be a non-empty path.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (project_root / path).resolve()


def _canonical_file_id(value: Any, width: int) -> str:
    text = str(value).strip()
    if not text or text.lower() == "nan":
        raise RasterInputError(f"Empty file_id encountered in project export table: {value!r}")
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        text = str(int(float(text)))
    return text.zfill(width) if text.isdigit() else text


def _require_table_columns(frame: pd.DataFrame, columns: list[str], path: Path) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise RasterInputError(f"Missing required columns {missing} in {path}. Available columns: {list(frame.columns)}")


def export_neuroexplorer_raster_inputs(
    config_path: Path,
    *,
    overwrite: bool = False,
    nex_module=None,
) -> dict[str, Any]:
    config_path = Path(config_path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    export_cfg = raw.get("project_export")
    if not isinstance(export_cfg, dict):
        raise RasterConfigError(
            "Config key project_export is required for automatic NeuroExplorer export. "
            "Run raster_run.py --project-dir <project> --force-init to create a complete config."
        )
    project_root = _resolve_project_path(config_path.parent, export_cfg.get("project_root", "."), "project_root")
    pl2_dir = _resolve_project_path(project_root, export_cfg.get("pl2_dir", ""), "pl2_dir")
    schedule_path = _resolve_project_path(project_root, export_cfg.get("stim_schedule_table", ""), "stim_schedule_table")
    unit_path = _resolve_project_path(project_root, export_cfg.get("unit_quality_table", ""), "unit_quality_table")
    input_root = _resolve_project_path(config_path.parent, raw["paths"]["input_root"], "paths.input_root")
    input_root.mkdir(parents=True, exist_ok=True)
    spike_path = input_root / str(raw["paths"]["spike_table_glob"])
    event_path = input_root / str(raw["paths"]["event_table_glob"])
    export_manifest_path = input_root / "neuroexplorer_export_manifest.json"
    if any("*" in path.name or "?" in path.name for path in [spike_path, event_path]):
        raise RasterConfigError(
            "Automatic export requires literal paths.spike_table_glob and paths.event_table_glob filenames, not wildcards."
        )
    conflicts = [path for path in [spike_path, event_path, export_manifest_path] if path.exists()]
    if conflicts and not overwrite:
        raise RasterInputError(f"Raster input export conflict with overwrite=false: {conflicts}")

    schedule = pd.read_excel(schedule_path)
    units = pd.read_excel(unit_path, dtype=str)
    schedule_columns = export_cfg.get("schedule_columns", {})
    unit_columns = export_cfg.get("unit_columns", {})
    _require_table_columns(schedule, list(schedule_columns.values()), schedule_path)
    _require_table_columns(units, list(unit_columns.values()), unit_path)
    width = int(export_cfg.get("file_id_width", 2))
    schedule["_file_id"] = schedule[schedule_columns["file_id"]].map(lambda value: _canonical_file_id(value, width))
    units["_file_id"] = units[unit_columns["file_id"]].map(lambda value: _canonical_file_id(value, width))
    include_value = str(export_cfg.get("include_value", "yes")).strip().lower()
    included = units[units[unit_columns["include"]].fillna("").astype(str).str.strip().str.lower() == include_value].copy()
    if included.empty:
        raise RasterInputError(f"No units match project_export.include_value={include_value!r} in {unit_path}.")

    if nex_module is None:
        try:
            import nex as nex_module
        except ImportError as exc:
            raise RasterInputError("The NeuroExplorer nex package is unavailable in this Python environment.") from exc

    spike_columns = ["session_id", "unit_id", "channel_id", "timestamp", "source_pl2", "original_name"]
    event_rows = []
    unit_exports = []
    exclusions = []
    with NamedTemporaryFile("w", encoding="utf-8-sig", newline="", delete=False, dir=input_root, suffix=".csv.tmp") as handle:
        temporary_spike_path = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=spike_columns)
        writer.writeheader()
        try:
            for file_id, file_units in included.groupby("_file_id", sort=True):
                schedule_rows = schedule[schedule["_file_id"] == file_id]
                if schedule_rows.empty:
                    raise RasterInputError(f"No stim schedule row for included file_id={file_id}.")
                schedule_row = schedule_rows.iloc[0]
                event_time = pd.to_numeric(pd.Series([schedule_row[schedule_columns["event_time"]]]), errors="coerce").iloc[0]
                has_light = str(schedule_row[schedule_columns["has_light"]]).strip().lower()
                if has_light != "yes" or pd.isna(event_time):
                    exclusions.extend(
                        {
                            "file_id": file_id,
                            "unit_id": str(row[unit_columns["unit_id"]]).strip(),
                            "reason": "no_alignment_event",
                        }
                        for _, row in file_units.iterrows()
                    )
                    continue
                duration = pd.to_numeric(
                    pd.Series([schedule_row[schedule_columns["stimulus_duration"]]]), errors="coerce"
                ).iloc[0]
                if pd.isna(duration) or not math.isfinite(float(duration)) or float(duration) <= 0:
                    raise RasterInputError(f"Invalid stimulus duration for file_id={file_id}: {duration!r}")
                pl2_path = pl2_dir / str(schedule_row[schedule_columns["pl2_file"]]).strip()
                if not pl2_path.exists():
                    raise RasterInputError(f"Missing PL2 for file_id={file_id}: {pl2_path}")
                session_id = str(export_cfg.get("session_id_template", "session_{file_id}")).format(file_id=file_id)
                doc = nex_module.OpenDocument(str(pl2_path))
                try:
                    neuron_vars = {variable.Name(): variable for variable in doc.NeuronVars()}
                    for _, unit_row in file_units.iterrows():
                        original_name = str(unit_row[unit_columns["original_name"]]).strip()
                        unit_id = str(unit_row[unit_columns["unit_id"]]).strip()
                        channel_id = str(unit_row[unit_columns["channel_id"]]).strip()
                        if original_name not in neuron_vars:
                            raise RasterInputError(
                                f"Missing neuron {original_name!r} in {pl2_path.name}; available={sorted(neuron_vars)}"
                            )
                        timestamps = neuron_vars[original_name].Timestamps()
                        for timestamp in timestamps:
                            writer.writerow(
                                {
                                    "session_id": session_id,
                                    "unit_id": unit_id,
                                    "channel_id": channel_id,
                                    "timestamp": repr(float(timestamp)),
                                    "source_pl2": pl2_path.name,
                                    "original_name": original_name,
                                }
                            )
                        unit_exports.append(
                            {
                                "file_id": file_id,
                                "session_id": session_id,
                                "unit_id": unit_id,
                                "original_name": original_name,
                                "source_pl2": pl2_path.name,
                                "n_spikes": len(timestamps),
                            }
                        )
                finally:
                    nex_module.CloseDocument(doc)
                event_rows.append(
                    {
                        "session_id": session_id,
                        "event_name": str(export_cfg.get("event_name", raw["alignment"]["event_name"])),
                        "timestamp": float(event_time),
                        "trial_id": f"{file_id}_trial0001",
                        "stimulus_duration_s": float(duration),
                        "source_pl2": pl2_path.name,
                    }
                )
        except Exception:
            handle.close()
            temporary_spike_path.unlink(missing_ok=True)
            raise
    if not unit_exports:
        temporary_spike_path.unlink(missing_ok=True)
        raise RasterInputError("No light-aligned include=yes units were exported.")
    temporary_spike_path.replace(spike_path)
    event_frame = pd.DataFrame(event_rows)
    with NamedTemporaryFile("w", encoding="utf-8-sig", newline="", delete=False, dir=input_root, suffix=".csv.tmp") as handle:
        event_frame.to_csv(handle, index=False)
        temporary_event_path = Path(handle.name)
    temporary_event_path.replace(event_path)
    manifest = {
        "config_path": str(config_path),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "spike_table": str(spike_path),
        "event_table": str(event_path),
        "time_unit": "seconds",
        "units": unit_exports,
        "exclusions": exclusions,
        "counts": {
            "sessions": len({row["session_id"] for row in unit_exports}),
            "units": len(unit_exports),
            "spikes": sum(row["n_spikes"] for row in unit_exports),
            "events": len(event_rows),
            "excluded_units": len(exclusions),
        },
    }
    _atomic_json(export_manifest_path, manifest)
    return manifest


def run_project_raster(
    project_dir: Path,
    *,
    init_only: bool = False,
    force_init: bool = False,
    skip_export: bool = False,
    validate_only: bool = False,
    overwrite: bool = False,
    nex_module=None,
) -> dict[str, Any]:
    config_path, created = initialize_raster_project(project_dir, force=force_init)
    if init_only:
        return {"config_path": str(config_path), "config_created": created, "init_only": True}
    export_summary = None
    if not skip_export:
        export_summary = export_neuroexplorer_raster_inputs(config_path, overwrite=overwrite, nex_module=nex_module)
    config = load_raster_config(config_path)
    if overwrite:
        config.output["overwrite"] = True
    raster_summary = run_raster_pipeline(config, validate_only=validate_only)
    return {
        "config_path": str(config_path),
        "config_created": created,
        "export": export_summary,
        "raster": raster_summary,
        "init_only": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize, export, and build a complete NeuroTrain raster project.")
    parser.add_argument("--project-dir", required=True, help="NeuroTrain project root.")
    parser.add_argument("--init-only", action="store_true", help="Create raster_config.yaml and input directory only.")
    parser.add_argument("--force-init", action="store_true", help="Replace raster_config.yaml with the current template.")
    parser.add_argument("--skip-export", action="store_true", help="Use existing raster input CSV files.")
    parser.add_argument("--validate-only", action="store_true", help="Validate exported/existing inputs without plotting.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing raster input and output files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = run_project_raster(
            Path(args.project_dir),
            init_only=args.init_only,
            force_init=args.force_init,
            skip_export=args.skip_export,
            validate_only=args.validate_only,
            overwrite=args.overwrite,
        )
    except (RasterConfigError, RasterInputError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if summary["init_only"]:
        print(f"Raster project initialized: config={summary['config_path']}; created={summary['config_created']}")
        return 0
    export_counts = (summary.get("export") or {}).get("counts", {})
    raster_counts = summary["raster"]
    print(
        "Raster project summary: "
        f"exported_units={export_counts.get('units', 'existing')}; "
        f"exported_spikes={export_counts.get('spikes', 'existing')}; "
        f"sessions={raster_counts.get('sessions', 0)}; "
        f"units={raster_counts.get('units_discovered', raster_counts.get('units', 0))}; "
        f"trials={raster_counts.get('trials', 0)}; "
        f"figures={raster_counts.get('figures_written', 0)}; "
        f"output_dir={raster_counts.get('output_dir')}; "
        f"validate_only={raster_counts.get('validate_only')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
