from __future__ import annotations

import argparse
from pathlib import Path

from utils.aligned_utils import compute_aligned_window, compute_pre_light_post_windows
from utils.event_utils import (
    derive_light_on_off_from_intervals,
    read_light_intervals,
    resolve_event_file_path,
    resolve_interval_file_path,
    validate_event_file,
)
from utils.logging_utils import PipelineLogger
from utils.path_utils import ensure_dir, load_yaml, resolve_project_paths
from utils.table_utils import normalize_include_column, normalize_stim_schedule, read_table


REQUIRED_CONFIG_FIELDS = [
    ("project", "root_dir"),
    ("project", "file_id_column"),
    ("input", "pl2_dir"),
    ("input", "stim_schedule"),
    ("input", "unit_quality_table"),
    ("analysis", "mode"),
    ("neuroexplorer", "backend"),
    ("origin", "plot_style"),
    ("pptx", "output_file"),
]


def _aligned_cfg(config: dict) -> dict:
    return config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))


def _plotting_cfg(config: dict) -> dict:
    return config.get("plotting", config.get("neuroexplorer", {}).get("plotting", {}))


def _require_config_fields(config: dict) -> None:
    missing: list[str] = []
    for group, field in REQUIRED_CONFIG_FIELDS:
        if group not in config or field not in config[group]:
            missing.append(f"{group}.{field}")
    if missing:
        raise ValueError(f"Missing required config field(s): {', '.join(missing)}")


def _log_path_support(paths: dict, logger: PipelineLogger) -> None:
    root_dir = paths["root_dir"]
    has_space = " " in str(root_dir)
    has_non_ascii = any(ord(ch) > 127 for ch in str(root_dir))
    message = f"Resolved root path with pathlib. has_space={has_space}, has_non_ascii={has_non_ascii}"
    logger.log("validate_project", "*", str(root_dir), "", "success", message)


def validate_project(config: dict, logger: PipelineLogger) -> None:
    _require_config_fields(config)
    paths = resolve_project_paths(config)
    root_dir = paths["root_dir"]

    if not root_dir.exists():
        raise FileNotFoundError(f"Project root does not exist: {root_dir}")
    if not paths["pl2_dir"].exists():
        raise FileNotFoundError(f"PL2 directory does not exist: {paths['pl2_dir']}")
    if not paths["stim_schedule_path"].exists():
        raise FileNotFoundError(f"Stim schedule does not exist: {paths['stim_schedule_path']}")
    analysis_mode = config.get("analysis", {}).get("mode", "fullrate_aligned")
    unit_cfg = config.get("unit_table", {})
    allow_missing_unit_table = (
        analysis_mode in {"fullrate_aligned", "auto"}
        and unit_cfg.get("enabled", True)
        and unit_cfg.get("auto_build_if_missing", True)
    )
    if not paths["unit_quality_path"].exists() and not allow_missing_unit_table:
        raise FileNotFoundError(f"Unit quality table does not exist: {paths['unit_quality_path']}")

    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    if paths["unit_quality_path"].exists():
        unit_df = normalize_include_column(read_table(paths["unit_quality_path"]))
    else:
        unit_df = read_table(paths["stim_schedule_path"]).iloc[0:0].copy()
        unit_df[config["project"]["file_id_column"]] = ""
        unit_df["unit_id"] = ""
        unit_df["include"] = "yes"
        unit_df["include_bool"] = True
        logger.log(
            "validate_project",
            "*",
            "",
            str(paths["unit_quality_path"]),
            "warning",
            "unit_quality_table is missing but auto_build_if_missing=true; build_unit_table should create it before downstream export.",
        )

    if config["project"]["file_id_column"] not in unit_df.columns:
        raise ValueError(
            f"Unit quality table must include {config['project']['file_id_column']!r} to map units to files."
        )

    missing_pl2 = []
    for row in stim_df.itertuples(index=False):
        latest_scan_flag = "yes"
        if hasattr(row, "detected_in_latest_scan"):
            latest_scan_flag = str(getattr(row, "detected_in_latest_scan")).strip().lower() or "yes"
        pl2_path = paths["pl2_dir"] / str(row.pl2_file)
        if not pl2_path.exists():
            if latest_scan_flag in {"no", "false", "0"}:
                logger.log(
                    "validate_project",
                    str(getattr(row, config["project"]["file_id_column"])),
                    str(paths["stim_schedule_path"]),
                    "",
                    "warning",
                    f"PL2 file is missing for a stale stim_schedule row marked detected_in_latest_scan=no: {pl2_path}",
                )
            else:
                missing_pl2.append(str(pl2_path))
    if missing_pl2:
        raise FileNotFoundError("Missing PL2 file(s): " + "; ".join(missing_pl2))

    for output_dir_key in [
        "events_export_dir",
        "nex_psth_dir",
        "nex_fullrate_dir",
        "nex_raster_dir",
        "nex_aligned_rate_dir",
        "origin_output_dir",
        "figure_psth_dir",
        "figure_fullrate_dir",
        "figure_aligned_dir",
        "figure_prepost_dir",
        "figure_summary_dir",
        "pptx_dir",
        "logs_dir",
    ]:
        ensure_dir(paths[output_dir_key])
        logger.log("validate_project", "*", "", str(paths[output_dir_key]), "success", "Verified output directory.")

    events_cfg = config.get("neuroexplorer", {}).get("events", {})
    reference_event = events_cfg.get("reference_event", "Light_On")
    event_on_name = events_cfg.get("event_on_name", "Light_On")
    stimulus_input_mode = events_cfg.get("stimulus_input_mode", "event")
    if reference_event != event_on_name:
        logger.log(
            "validate_project",
            "*",
            "",
            "",
            "warning",
            "reference_event is not equal to event_on_name. PSTH shading logic assumes Light_On alignment.",
        )

    valid_backends = {"auto", "nex_package", "com_nexscript", "manual_csv"}
    if config["neuroexplorer"]["backend"] not in valid_backends:
        raise ValueError(f"Unsupported neuroexplorer.backend: {config['neuroexplorer']['backend']}")
    if analysis_mode not in {"neuroexplorer_psth", "fullrate_aligned", "auto"}:
        raise ValueError(f"Unsupported analysis.mode: {analysis_mode}")

    psth_cfg = config.get("neuroexplorer", {}).get("psth", {})
    if psth_cfg and psth_cfg.get("histogram_unit") != "Spikes per second":
        logger.log(
            "validate_project",
            "*",
            "",
            "",
            "warning",
            "PSTH histogram_unit is not 'Spikes per second'. This skill assumes Hz/spikes-per-second outputs by default.",
        )
    if config["neuroexplorer"]["fullrate"]["histogram_unit"] != "Spikes per second":
        logger.log(
            "validate_project",
            "*",
            "",
            "",
            "warning",
            "Full-rate histogram_unit is not 'Spikes per second'. This skill assumes Hz/spikes-per-second outputs by default.",
        )

    if psth_cfg and psth_cfg.get("light_band_start_s") != 0:
        logger.log(
            "validate_project",
            "*",
            "",
            "",
            "warning",
            "PSTH light band start is expected to be 0 for event-aligned PSTH shading.",
        )
    if psth_cfg and psth_cfg.get("light_band_end_mode") != "duration_s":
        logger.log(
            "validate_project",
            "*",
            "",
            "",
            "warning",
            "PSTH light band end mode is expected to be duration_s.",
        )
    if stimulus_input_mode not in {"event", "interval"}:
        raise ValueError(f"Unsupported stimulus_input_mode: {stimulus_input_mode}")
    if psth_cfg and psth_cfg.get("reference_source", "event") not in {"event", "interval_start"}:
        raise ValueError("Unsupported neuroexplorer.psth.reference_source")
    plotting_cfg = _plotting_cfg(config)
    aligned_cfg = _aligned_cfg(config)
    if plotting_cfg.get("light_band_source", "interval") not in {"event", "interval"}:
        raise ValueError("Unsupported neuroexplorer.plotting.light_band_source")
    if aligned_cfg["multi_trial_aggregation"] not in {"mean", "median", "keep_trials"}:
        raise ValueError("Unsupported aligned_rate.multi_trial_aggregation")
    if aligned_cfg.get("variable_duration_policy", "keep_trials") not in {"keep_trials", "align_by_light_on_and_pad", "error"}:
        raise ValueError("Unsupported aligned_rate.variable_duration_policy")
    if aligned_cfg["off_boundary_policy"] not in {"nearest", "interpolate", "error"}:
        raise ValueError("Unsupported aligned_rate.off_boundary_policy")
    if float(aligned_cfg["bin_width_s"]) <= 0:
        raise ValueError("aligned_rate.bin_width_s must be > 0.")
    if not config["neuroexplorer"]["fullrate"].get("template_name"):
        raise ValueError("neuroexplorer.fullrate.template_name is required for fullrate_aligned mode.")
    if aligned_cfg.get("window_mode", "configured_windows") not in {"configured_windows", "fixed", "light_duration_plus_margin"}:
        raise ValueError("aligned_rate.window_mode must be 'configured_windows', 'fixed', or 'light_duration_plus_margin'.")
    configured_window_keys = ["pre_window_s", "light_window_s", "post_window_s"]
    has_configured_windows = any(key in aligned_cfg for key in configured_window_keys)
    if has_configured_windows:
        missing = [key for key in configured_window_keys if key not in aligned_cfg]
        if missing:
            raise ValueError(f"Missing aligned_rate configured window key(s): {', '.join(missing)}")
        for key in configured_window_keys:
            values = aligned_cfg.get(key)
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError(f"aligned_rate.{key} must be a two-item list [start_s, end_s].")
            start_s, end_s = [float(v) for v in values]
            if start_s >= end_s:
                raise ValueError(f"aligned_rate.{key} start must be < end.")
        compute_pre_light_post_windows(1.0, aligned_cfg)
    elif aligned_cfg.get("post_window_mode", "after_light_off") not in {"fixed", "after_light_off"}:
        raise ValueError("aligned_rate.post_window_mode must be 'fixed' or 'after_light_off'.")
    if has_configured_windows:
        pass
    elif aligned_cfg.get("window_mode", "fixed") == "fixed":
        if float(aligned_cfg["x_min_s"]) >= 0:
            raise ValueError("aligned_rate.x_min_s must be < 0.")
    else:
        if float(aligned_cfg.get("pre_margin_s", 0)) <= 0:
            raise ValueError("aligned_rate.pre_margin_s must be > 0.")
        if float(aligned_cfg.get("post_margin_s", 0)) <= 0:
            raise ValueError("aligned_rate.post_margin_s must be > 0.")
        if not has_configured_windows:
            baseline_start, baseline_end = [float(v) for v in aligned_cfg["baseline_window_s"]]
            if baseline_start < -float(aligned_cfg["pre_margin_s"]) or baseline_end > 0:
                raise ValueError("aligned_rate.baseline_window_s must fall within [-pre_margin_s, 0].")
        if not has_configured_windows and aligned_cfg.get("post_window_mode", "after_light_off") == "after_light_off":
            post_after_start, post_after_end = [float(v) for v in aligned_cfg.get("post_window_after_light_s", [0, 60])]
            if post_after_start < 0 or post_after_end > float(aligned_cfg["post_margin_s"]):
                raise ValueError("aligned_rate.post_window_after_light_s must be within [0, post_margin_s].")

    light_stim_df = stim_df[stim_df["has_light_bool"]].copy()
    no_light_stim_df = stim_df[~stim_df["has_light_bool"]].copy()
    bad_rows = light_stim_df[light_stim_df["light_off_s"] <= light_stim_df["light_on_s"]]
    if not bad_rows.empty:
        raise ValueError("Found stim_schedule rows where light_off_s <= light_on_s.")
    if (light_stim_df["duration_s"] <= 0).any():
        raise ValueError("Found stim_schedule rows where duration_s <= 0.")
    for row in no_light_stim_df.itertuples(index=False):
        logger.log(
            "validate_project",
            str(getattr(row, config["project"]["file_id_column"])),
            str(paths["stim_schedule_path"]),
            "",
            "success",
            "Validated no-light control row: full-session outputs are allowed and aligned/event analyses are not required.",
        )

    logger.log(
        "validate_project",
        "*",
        str(paths["stim_schedule_path"]),
        "",
        "success",
        "Validated aligned light band semantics from configured pre/light/post windows and plotting light-band settings.",
    )
    logger.log(
        "validate_project",
        "*",
        str(paths["stim_schedule_path"]),
        "",
        "success",
        "Validated full-session light band semantics: light_on_s -> light_off_s for absolute-time plots.",
    )

    if analysis_mode != "fullrate_aligned" and stimulus_input_mode == "interval":
        interval_cfg = config.get("neuroexplorer", {}).get("interval", {})
        interval_name = interval_cfg.get(
            "interval_variable_name",
            events_cfg.get("interval_name", "Light_Interval"),
        )
        for file_id in light_stim_df[config["project"]["file_id_column"]].astype(str).unique():
            interval_path = resolve_interval_file_path(
                paths["events_export_dir"],
                file_id,
                interval_cfg.get("interval_csv_pattern", "{file_id}_Light_Interval.csv"),
            )
            if interval_path.exists():
                intervals = read_light_intervals(
                    interval_path,
                    interval_name=interval_name,
                    has_variable_name_first_line=interval_cfg.get("include_variable_name_first_line", True),
                    delimiter=interval_cfg.get("delimiter", ","),
                )
                derived_on, derived_off, _ = derive_light_on_off_from_intervals(intervals)
                if events_cfg.get("require_light_on", False):
                    on_path = resolve_event_file_path(paths["events_export_dir"], file_id, event_on_name, stimulus_input_mode)
                    if not on_path.exists():
                        logger.log(
                            "validate_project",
                            file_id,
                            "",
                            str(on_path),
                            "warning",
                            "Interval mode is active. Light_On event file is missing and may need manual creation/import for PSTH reference.",
                        )
                    else:
                        validate_event_file(on_path)
                logger.log(
                    "validate_project",
                    file_id,
                    str(interval_path),
                    "",
                    "success",
                    f"Validated interval CSV and derived Light_On/Light_Off successfully. n_intervals={len(intervals)}",
                )
            else:
                logger.log(
                    "validate_project",
                    file_id,
                    "",
                    str(interval_path),
                    "warning",
                    "Interval mode is active, but the interval CSV does not exist yet. Run prepare_events first.",
                )
    elif analysis_mode == "fullrate_aligned":
        logger.log(
            "validate_project",
            "*",
            str(paths["stim_schedule_path"]),
            "",
            "success",
            "analysis.mode=fullrate_aligned: Light_On / Light_Interval are not required for NeuroExplorer export; alignment will be reconstructed from stim_schedule.",
        )
        for row in light_stim_df.itertuples(index=False):
            duration = float(row.duration_s)
            window_info = compute_aligned_window(
                light_on_s=float(row.light_on_s),
                light_off_s=None if row.light_off_s is None else float(row.light_off_s),
                duration_s=duration,
                aligned_cfg=aligned_cfg,
            )
            if aligned_cfg.get("window_mode", "fixed") == "fixed" and float(window_info["aligned_x_max_s"]) <= duration:
                raise ValueError("aligned_rate.x_max_s must extend beyond the light duration.")
            if window_info["abs_start_s"] < 0:
                logger.log(
                    "validate_project",
                    str(getattr(row, config["project"]["file_id_column"])),
                    str(paths["stim_schedule_path"]),
                    "",
                    "warning",
                    "Aligned window extends before recording start; missing bins will be clipped or filled according to off_boundary_policy.",
                )
            logger.log(
                "validate_project",
                str(getattr(row, config["project"]["file_id_column"])),
                str(paths["stim_schedule_path"]),
                "",
                "success",
                f"Validated aligned fullrate window: absolute {window_info['abs_start_s']:g} to {window_info['abs_end_s']:g} s; aligned {window_info['aligned_x_min_s']:g} to {window_info['aligned_x_max_s']:g} s",
            )
    _log_path_support(paths, logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a PSTH/Origin/PPTX project.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        validate_project(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
