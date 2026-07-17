from __future__ import annotations

import copy
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def save_yaml(data: dict, path: Path) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_path(root_dir: Path, raw_value: str) -> Path:
    candidate = Path(raw_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (root_dir / candidate).resolve()


def resolve_project_paths(config: dict) -> dict:
    root_dir = Path(config["project"]["root_dir"]).expanduser().resolve()
    neuro_cfg = config.get("neuroexplorer", {})
    export_cfg = neuro_cfg.get("export", {})
    origin_cfg = config.get("origin", {})
    origin_native_cfg = origin_cfg.get("native", {})
    unit_table_cfg = config.get("unit_table", {})
    stim_cfg = config.get("stim_schedule", {})
    statistics_cfg = config.get("statistics", {})
    time_cluster_aligned_cfg = config.get("time_cluster_aligned_rate", {})
    nex_psth_dir = resolve_path(root_dir, export_cfg.get("output_psth_dir", "03_nex_exports/psth"))
    nex_fullrate_dir = resolve_path(root_dir, export_cfg.get("output_fullrate_dir", "03_nex_exports/fullrate"))
    nex_raster_dir = resolve_path(root_dir, export_cfg.get("output_raster_dir", "03_nex_exports/raster"))
    nex_aligned_rate_dir = resolve_path(root_dir, export_cfg.get("output_aligned_rate_dir", "03_nex_exports/aligned_rate"))
    time_cluster_aligned_rate_dir = resolve_path(
        root_dir,
        time_cluster_aligned_cfg.get("output_dir", "03_nex_exports/time_cluster_aligned_rate"),
    )
    pptx_output_path = resolve_path(root_dir, config["pptx"]["output_file"])
    unit_quality_path = resolve_path(root_dir, unit_table_cfg.get("output_path", config["input"]["unit_quality_table"]))
    logs_dir = ensure_dir(root_dir / "99_logs")
    return {
        "root_dir": root_dir,
        "pl2_dir": resolve_path(root_dir, config["input"]["pl2_dir"]),
        "stim_schedule_path": resolve_path(root_dir, stim_cfg.get("output_path", config["input"]["stim_schedule"])),
        "unit_quality_path": unit_quality_path,
        "events_export_dir": ensure_dir(root_dir / "02_stim_events" / "exported_events"),
        "nex_psth_dir": ensure_dir(nex_psth_dir),
        "nex_fullrate_dir": ensure_dir(nex_fullrate_dir),
        "nex_raster_dir": ensure_dir(nex_raster_dir),
        "nex_aligned_rate_dir": ensure_dir(nex_aligned_rate_dir),
        "time_cluster_aligned_rate_dir": ensure_dir(time_cluster_aligned_rate_dir),
        "origin_template_psth": resolve_path(root_dir, origin_cfg.get("template_psth", "04_origin_projects/templates/PSTH_template.otpu")),
        "origin_template_fullrate": resolve_path(root_dir, origin_cfg.get("template_fullrate", "04_origin_projects/templates/FullRate_template.otpu")),
        "origin_output_dir": ensure_dir(resolve_path(root_dir, origin_cfg.get("opju_output_dir", "04_origin_projects/opju_outputs"))),
        "origin_native_manifest_path": resolve_path(
            root_dir,
            origin_native_cfg.get("manifest_path", "04_origin_projects/origin_input/origin_plot_manifest.xlsx"),
        ),
        "origin_native_opju_output_dir": ensure_dir(
            resolve_path(root_dir, origin_native_cfg.get("opju_output_dir", origin_cfg.get("opju_output_dir", "04_origin_projects/opju_outputs")))
        ),
        "origin_native_image_output_dir": ensure_dir(
            resolve_path(root_dir, origin_native_cfg.get("image_output_dir", "05_exported_figures_origin"))
        ),
        "figure_psth_dir": ensure_dir(root_dir / "05_exported_figures" / "psth"),
        "figure_fullrate_dir": ensure_dir(root_dir / "05_exported_figures" / "fullrate"),
        "figure_aligned_dir": ensure_dir(root_dir / "05_exported_figures" / "aligned_rate"),
        "figure_prepost_dir": ensure_dir(root_dir / "05_exported_figures" / "prepost_summary"),
        "figure_summary_dir": ensure_dir(root_dir / "05_exported_figures" / "summary"),
        "statistics_dir": ensure_dir(resolve_path(root_dir, statistics_cfg.get("output_dir", "07_statistics"))),
        "pptx_output_path": pptx_output_path,
        "pptx_dir": ensure_dir(pptx_output_path.parent),
        "logs_dir": logs_dir,
        "neuroexplorer_api_dump_path": logs_dir / "neuroexplorer_nex_api_dump.txt",
        "neuroexplorer_interval_event_probe_path": logs_dir / "nex_interval_event_creation_probe.txt",
        "neuroexplorer_var_object_probe_path": logs_dir / "nex_var_object_creation_probe.txt",
        "neuroexplorer_var_clone_probe_path": logs_dir / "nex_var_clone_probe.txt",
    }


def apply_runtime_overrides(config: dict, dry_run: bool = False, overwrite: bool = False) -> dict:
    patched = copy.deepcopy(config)
    if dry_run:
        patched.setdefault("run", {})["dry_run"] = True
    if overwrite:
        patched.setdefault("run", {})["overwrite"] = True
    return patched
