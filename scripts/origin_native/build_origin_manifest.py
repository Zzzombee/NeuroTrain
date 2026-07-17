from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from scripts.origin_native.labtalk_templates import safe_origin_name
from utils.aligned_utils import aligned_window_tag
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table, write_table
from utils.unit_selection import select_quality_table_cohort, write_cohort_metadata


MANIFEST_COLUMNS = [
    "graph_type",
    "file_id",
    "unit_id",
    "source_csv",
    "x_col",
    "y_col",
    "template_path",
    "graph_page_name",
    "light_band_start_s",
    "light_band_end_s",
    "x_min",
    "x_max",
    "output_image_path",
    "include",
    "notes",
]

EXTRA_COLUMNS = [
    "opju_group",
    "has_light",
    "image_format",
    "dpi",
]


def _origin_cfg(config: dict) -> dict:
    origin_cfg = dict(config.get("origin", {}))
    origin_cfg.setdefault("backend", "matplotlib_png")
    origin_cfg.setdefault("opju_mode", "per_file")
    origin_cfg.setdefault("max_graph_pages_per_opju", 80)
    origin_cfg.setdefault("require_opju_success", False)
    return origin_cfg


def _native_cfg(config: dict) -> dict:
    origin_cfg = _origin_cfg(config)
    native_cfg = dict(origin_cfg.get("native", {}))
    native_cfg.setdefault("manifest_path", "04_origin_projects/origin_input/origin_plot_manifest.xlsx")
    native_cfg.setdefault("opju_output_dir", "04_origin_projects/opju_outputs")
    native_cfg.setdefault("image_output_dir", "05_exported_figures_origin")
    native_cfg.setdefault("image_format", origin_cfg.get("export_format", "png"))
    native_cfg.setdefault("dpi", origin_cfg.get("dpi", 300))
    native_cfg.setdefault("templates", {})
    native_cfg.setdefault("graph_pages", origin_cfg.get("graph_pages", {}))
    return native_cfg


def _first_light_band(stim_sub: pd.DataFrame) -> tuple[float | str, float | str, str]:
    if stim_sub.empty or "has_light_bool" not in stim_sub.columns or not bool(stim_sub["has_light_bool"].any()):
        return "", "", "No light stimulation."
    light_rows = stim_sub[stim_sub["has_light_bool"]].copy()
    if light_rows.empty:
        return "", "", "No light stimulation."
    first = light_rows.iloc[0]
    notes = f"n_light_events={len(light_rows)}"
    return float(first["light_on_s"]), float(first["light_off_s"]), notes


def _duration_from_stim(stim_sub: pd.DataFrame) -> float | None:
    if stim_sub.empty or "has_light_bool" not in stim_sub.columns:
        return None
    light_rows = stim_sub[stim_sub["has_light_bool"]]
    if light_rows.empty:
        return None
    return float(light_rows["duration_s"].median())


def _read_axis_bounds(csv_path: Path, x_col: str, unit_id: str | None = None) -> tuple[float | str, float | str]:
    try:
        df = read_table(csv_path)
    except Exception:
        return "", ""
    if x_col not in df.columns or df.empty:
        return "", ""
    if unit_id and "unit_id" in df.columns:
        sub_df = df[df["unit_id"].astype(str) == str(unit_id)]
        if not sub_df.empty:
            df = sub_df
    values = pd.to_numeric(df[x_col], errors="coerce").dropna()
    if values.empty:
        return "", ""
    return float(values.min()), float(values.max())


def _path_str(path: Path) -> str:
    return str(Path(path))


def _image_path(paths: dict, native_cfg: dict, graph_type: str, file_id: str, unit_id: str, page_name: str) -> Path:
    image_format = str(native_cfg.get("image_format", "png")).lower().lstrip(".")
    output_root = paths["origin_native_image_output_dir"]
    suffix = image_format or "png"
    return output_root / graph_type / f"{safe_origin_name(page_name, max_len=120)}.{suffix}"


def _template(config: dict, native_cfg: dict, graph_type: str) -> str:
    raw_value = native_cfg.get("templates", {}).get(graph_type, "")
    if not raw_value:
        return ""
    candidate = Path(str(raw_value)).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    root_dir = Path(config["project"]["root_dir"]).expanduser().resolve()
    return str((root_dir / candidate).resolve())


def _graph_pages(native_cfg: dict) -> dict:
    return {
        "fullrate": True,
        "aligned_rate": True,
        "prepost_summary": True,
        "summary": True,
        **dict(native_cfg.get("graph_pages", {})),
    }


def _included_units(unit_df: pd.DataFrame, file_id: str) -> Iterable[str]:
    sub_df = unit_df[unit_df["file_id"].astype(str) == str(file_id)].copy()
    for unit_id in sub_df["unit_id"].astype(str).tolist():
        yield unit_id


def _append_row(
    rows: list[dict],
    *,
    config: dict,
    paths: dict,
    native_cfg: dict,
    graph_type: str,
    file_id: str,
    unit_id: str,
    source_csv: Path,
    x_col: str,
    y_col: str,
    light_band_start_s,
    light_band_end_s,
    x_min,
    x_max,
    notes: str,
) -> None:
    page_name = safe_origin_name(f"{file_id}_{unit_id}_{graph_type}")
    rows.append(
        {
            "graph_type": graph_type,
            "file_id": str(file_id),
            "unit_id": str(unit_id),
            "source_csv": _path_str(source_csv),
            "x_col": x_col,
            "y_col": y_col,
            "template_path": _template(config, native_cfg, graph_type),
            "graph_page_name": page_name,
            "light_band_start_s": light_band_start_s,
            "light_band_end_s": light_band_end_s,
            "x_min": x_min,
            "x_max": x_max,
            "output_image_path": _path_str(_image_path(paths, native_cfg, graph_type, str(file_id), str(unit_id), page_name)),
            "include": "yes",
            "notes": notes,
            "opju_group": str(file_id) if _origin_cfg(config).get("opju_mode", "per_file") == "per_file" else "project",
            "has_light": "yes" if light_band_start_s != "" else "no",
            "image_format": str(native_cfg.get("image_format", "png")).lower().lstrip("."),
            "dpi": native_cfg.get("dpi", 300),
        }
    )


def build_origin_manifest(config: dict, logger: PipelineLogger) -> pd.DataFrame:
    paths = resolve_project_paths(config)
    origin_cfg = _origin_cfg(config)
    native_cfg = _native_cfg(config)
    graph_pages = _graph_pages(native_cfg)
    aligned_cfg = config.get("aligned_rate", {})
    fullrate_bin = config.get("neuroexplorer", {}).get("fullrate", {}).get("bin_width_s", aligned_cfg.get("bin_width_s", 1))
    expected_fullrate_pattern = config.get("neuroexplorer", {}).get("export", {}).get(
        "expected_fullrate_pattern",
        "{file_id}_FullRate_bin{bin_width_s}s.csv",
    )

    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    unit_df, cohort = select_quality_table_cohort(
        config,
        module="origin_native_plot",
        logger=logger,
        duplicate_policy=config.get("unit_selection", {}).get("duplicate_policy", "keep_all"),
    )
    if not config.get("run", {}).get("dry_run", False):
        write_cohort_metadata(cohort, paths["origin_native_manifest_path"].parent)
    manifest_rows: list[dict] = []

    for file_id, stim_sub in stim_df.groupby(config["project"]["file_id_column"], sort=False):
        file_id = str(file_id)
        band_start, band_end, band_notes = _first_light_band(stim_sub)
        duration_s = _duration_from_stim(stim_sub)
        units = list(_included_units(unit_df, file_id))
        if not units:
            logger.log("origin_native_plot", file_id, str(paths["unit_quality_path"]), "", "warning", "No included units found for Origin native manifest.")
            continue

        fullrate_csv = paths["nex_fullrate_dir"] / expected_fullrate_pattern.format(file_id=file_id, bin_width_s=fullrate_bin)
        aligned_csv = paths["nex_aligned_rate_dir"] / f"{file_id}_LightAlignedRate_{aligned_window_tag(aligned_cfg)}_bin{float(aligned_cfg.get('bin_width_s', 1)):g}s.csv"
        prepost_csv = paths["nex_aligned_rate_dir"] / f"{file_id}_PreLightPostSummary.csv"

        for unit_id in units:
            if graph_pages.get("fullrate", True):
                if fullrate_csv.exists():
                    x_min, x_max = _read_axis_bounds(fullrate_csv, "time_bin_center_s", unit_id)
                    _append_row(
                        manifest_rows,
                        config=config,
                        paths=paths,
                        native_cfg=native_cfg,
                        graph_type="fullrate",
                        file_id=file_id,
                        unit_id=unit_id,
                        source_csv=fullrate_csv,
                        x_col="time_bin_center_s",
                        y_col="firing_rate_hz",
                        light_band_start_s=band_start,
                        light_band_end_s=band_end,
                        x_min=x_min,
                        x_max=x_max,
                        notes=band_notes,
                    )
                else:
                    logger.log("origin_native_plot", file_id, "", str(fullrate_csv), "warning", "Fullrate source CSV missing for Origin native manifest.")

            if graph_pages.get("aligned_rate", True) and duration_s is not None:
                if aligned_csv.exists():
                    x_min, x_max = _read_axis_bounds(aligned_csv, "aligned_time_s", unit_id)
                    _append_row(
                        manifest_rows,
                        config=config,
                        paths=paths,
                        native_cfg=native_cfg,
                        graph_type="aligned_rate",
                        file_id=file_id,
                        unit_id=unit_id,
                        source_csv=aligned_csv,
                        x_col="aligned_time_s",
                        y_col="firing_rate_hz",
                        light_band_start_s=0.0,
                        light_band_end_s=duration_s,
                        x_min=x_min,
                        x_max=x_max,
                        notes=f"aligned_window_tag={aligned_window_tag(aligned_cfg)}; {band_notes}",
                    )
                else:
                    logger.log("origin_native_plot", file_id, "", str(aligned_csv), "warning", "Aligned-rate source CSV missing for Origin native manifest.")

            if graph_pages.get("prepost_summary", True) and duration_s is not None:
                if prepost_csv.exists():
                    _append_row(
                        manifest_rows,
                        config=config,
                        paths=paths,
                        native_cfg=native_cfg,
                        graph_type="prepost_summary",
                        file_id=file_id,
                        unit_id=unit_id,
                        source_csv=prepost_csv,
                        x_col="window_label",
                        y_col="firing_rate_hz",
                        light_band_start_s="",
                        light_band_end_s="",
                        x_min="",
                        x_max="",
                        notes="Runner may need to reshape baseline_hz/light_hz/post_hz before plotting.",
                    )
                else:
                    logger.log("origin_native_plot", file_id, "", str(prepost_csv), "warning", "PreLightPost source CSV missing for Origin native manifest.")

        if graph_pages.get("summary", True) and prepost_csv.exists() and duration_s is not None:
            _append_row(
                manifest_rows,
                config=config,
                paths=paths,
                native_cfg=native_cfg,
                graph_type="summary",
                file_id=file_id,
                unit_id="all_units",
                source_csv=prepost_csv,
                x_col="unit_id",
                y_col="light_hz",
                light_band_start_s="",
                light_band_end_s="",
                x_min="",
                x_max="",
                notes="All included units summary graph.",
            )

    manifest_df = pd.DataFrame(manifest_rows)
    for column in MANIFEST_COLUMNS + EXTRA_COLUMNS:
        if column not in manifest_df.columns:
            manifest_df[column] = pd.NA
    manifest_df = manifest_df[MANIFEST_COLUMNS + EXTRA_COLUMNS]

    manifest_path = paths["origin_native_manifest_path"]
    if not config.get("run", {}).get("dry_run", False):
        write_table(manifest_df, manifest_path)
    logger.log(
        "origin_native_plot",
        "*",
        "",
        str(manifest_path),
        "success" if not manifest_df.empty else "warning",
        f"Built Origin native plot manifest. n_rows={len(manifest_df)}; backend={origin_cfg.get('backend')}",
        event="build_manifest",
        n_graph_pages=len(manifest_df),
    )
    return manifest_df
