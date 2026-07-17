from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from utils.aligned_utils import aligned_window_tag, compute_aligned_window, compute_pre_light_post_windows
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table, write_table
from utils.unit_selection import filter_to_included_units, select_unit_cohort, write_cohort_metadata


def _aligned_cfg(config: dict) -> dict:
    return config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))


def _aligned_rate_path(paths: dict, file_id: str, bin_width_s: float, aligned_cfg: dict) -> Path:
    return paths["nex_aligned_rate_dir"] / f"{file_id}_LightAlignedRate_{aligned_window_tag(aligned_cfg)}_bin{bin_width_s:g}s.csv"


def _summary_path(paths: dict, file_id: str) -> Path:
    return paths["nex_aligned_rate_dir"] / f"{file_id}_PreLightPostSummary.csv"


def _window_mean(data: pd.DataFrame, start_s: float, end_s: float, column: str) -> float:
    window = data[(data[column] >= start_s) & (data[column] < end_s)]
    if window.empty:
        return float("nan")
    return float(window["firing_rate_hz"].mean())


def build_aligned_rate_for_file(config: dict, file_id: str, fullrate_df: pd.DataFrame, stim_sub: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    aligned_cfg = _aligned_cfg(config)
    aggregation_mode = aligned_cfg.get("multi_trial_aggregation", "mean")
    variable_duration_policy = aligned_cfg.get("variable_duration_policy", "keep_trials")
    bin_width_s = float(aligned_cfg["bin_width_s"])

    trial_specs: list[dict] = []
    for trial_idx, stim_row in enumerate(stim_sub.itertuples(index=False), start=1):
        duration_value = None if pd.isna(stim_row.duration_s) else float(stim_row.duration_s)
        light_off_value = None if pd.isna(stim_row.light_off_s) else float(stim_row.light_off_s)
        window_info = compute_aligned_window(
            light_on_s=float(stim_row.light_on_s),
            light_off_s=light_off_value,
            duration_s=duration_value,
            aligned_cfg=aligned_cfg,
        )
        summary_windows = compute_pre_light_post_windows(window_info["duration_s"], aligned_cfg)
        trial_specs.append(
            {
                "trial_id": trial_idx,
                **window_info,
                **summary_windows,
            }
        )

    unique_durations = sorted({round(spec["duration_s"], 6) for spec in trial_specs})
    durations_vary = len(unique_durations) > 1
    if durations_vary and variable_duration_policy == "error":
        raise ValueError("Light durations vary within this file and aligned_rate.variable_duration_policy=error.")
    effective_aggregation_mode = aggregation_mode
    if durations_vary and variable_duration_policy == "keep_trials":
        effective_aggregation_mode = "keep_trials"

    aligned_rows: list[dict] = []
    summary_rows: list[dict] = []
    for unit_id, unit_df in fullrate_df.groupby("unit_id", sort=False):
        unit_df = unit_df.sort_values("time_bin_center_s").copy()
        trial_frames = []
        for trial_spec in trial_specs:
            trial_df = unit_df[
                (unit_df["time_bin_center_s"] >= trial_spec["abs_start_s"])
                & (unit_df["time_bin_center_s"] <= trial_spec["abs_end_s"])
            ].copy()
            if trial_df.empty:
                continue
            trial_df["aligned_time_s"] = (trial_df["time_bin_center_s"] - trial_spec["light_on_s"]).round(6)
            trial_df["trial_id"] = trial_spec["trial_id"]
            trial_df["light_on_s"] = trial_spec["light_on_s"]
            trial_df["light_off_s"] = trial_spec["light_off_s"]
            trial_df["duration_s"] = trial_spec["duration_s"]
            trial_df["aligned_x_min_s"] = trial_spec["aligned_x_min_s"]
            trial_df["aligned_x_max_s"] = trial_spec["aligned_x_max_s"]
            trial_df["pre_margin_s"] = trial_spec["pre_margin_s"]
            trial_df["post_margin_s"] = trial_spec["post_margin_s"]
            trial_df["window_mode"] = trial_spec["window_mode"]
            trial_df["aggregation"] = "keep_trials"
            trial_df["file_id"] = file_id
            trial_frames.append(trial_df)

            baseline_hz = _window_mean(trial_df, trial_spec["baseline_window_start_s"], trial_spec["baseline_window_end_s"], "aligned_time_s")
            light_hz = _window_mean(trial_df, trial_spec["light_window_start_s"], trial_spec["light_window_end_s"], "aligned_time_s")
            post_hz = _window_mean(trial_df, trial_spec["post_window_start_s"], trial_spec["post_window_end_s"], "aligned_time_s")
            summary_rows.append(
                {
                    "file_id": file_id,
                    "unit_id": unit_id,
                    "trial_id": trial_spec["trial_id"],
                    "baseline_hz": baseline_hz,
                    "light_hz": light_hz,
                    "post_hz": post_hz,
                    "delta_light_minus_baseline": light_hz - baseline_hz if pd.notna(light_hz) and pd.notna(baseline_hz) else float("nan"),
                    "ratio_light_to_baseline": light_hz / baseline_hz if pd.notna(light_hz) and pd.notna(baseline_hz) and baseline_hz != 0 else float("nan"),
                    "duration_s": trial_spec["duration_s"],
                    "light_on_s": trial_spec["light_on_s"],
                    "light_off_s": trial_spec["light_off_s"],
                    "aligned_x_min_s": trial_spec["aligned_x_min_s"],
                    "aligned_x_max_s": trial_spec["aligned_x_max_s"],
                    "pre_margin_s": trial_spec["pre_margin_s"],
                    "post_margin_s": trial_spec["post_margin_s"],
                    "window_mode": trial_spec["window_mode"],
                    "summary_window_mode": trial_spec["summary_window_mode"],
                    "baseline_window_start_s": trial_spec["baseline_window_start_s"],
                    "baseline_window_end_s": trial_spec["baseline_window_end_s"],
                    "light_window_start_s": trial_spec["light_window_start_s"],
                    "light_window_end_s": trial_spec["light_window_end_s"],
                    "post_window_start_s": trial_spec["post_window_start_s"],
                    "post_window_end_s": trial_spec["post_window_end_s"],
                    "aggregation": "trial",
                }
            )

        if not trial_frames:
            continue
        unit_trials = pd.concat(trial_frames, ignore_index=True)
        if effective_aggregation_mode == "keep_trials":
            aligned_rows.extend(unit_trials.to_dict("records"))
        else:
            grouped = unit_trials.groupby(["file_id", "unit_id", "aligned_time_s"], as_index=False)
            agg_fn = "median" if effective_aggregation_mode == "median" else "mean"
            agg_df = grouped["firing_rate_hz"].agg(agg_fn)
            summary_duration = float(unit_trials["duration_s"].median())
            summary_window = compute_aligned_window(
                light_on_s=float(unit_trials["light_on_s"].min()),
                light_off_s=float(unit_trials["light_on_s"].min()) + summary_duration,
                duration_s=summary_duration,
                aligned_cfg=aligned_cfg,
            )
            agg_df["trial_id"] = "aggregated"
            agg_df["light_on_s"] = float(stim_sub["light_on_s"].min())
            agg_df["light_off_s"] = float(agg_df["light_on_s"].iloc[0] + summary_duration)
            agg_df["duration_s"] = summary_duration
            agg_df["aligned_x_min_s"] = summary_window["aligned_x_min_s"]
            agg_df["aligned_x_max_s"] = summary_window["aligned_x_max_s"]
            agg_df["pre_margin_s"] = summary_window["pre_margin_s"]
            agg_df["post_margin_s"] = summary_window["post_margin_s"]
            agg_df["window_mode"] = summary_window["window_mode"]
            agg_df["aggregation"] = effective_aggregation_mode
            aligned_rows.extend(agg_df.to_dict("records"))

            unit_summary = pd.DataFrame([row for row in summary_rows if row["unit_id"] == unit_id and row["file_id"] == file_id])
            if not unit_summary.empty:
                reducer = unit_summary[["baseline_hz", "light_hz", "post_hz", "delta_light_minus_baseline", "ratio_light_to_baseline"]].median if effective_aggregation_mode == "median" else unit_summary[["baseline_hz", "light_hz", "post_hz", "delta_light_minus_baseline", "ratio_light_to_baseline"]].mean
                values = reducer(numeric_only=True)
                agg_summary_windows = compute_pre_light_post_windows(summary_duration, aligned_cfg)
                summary_rows.append(
                    {
                        "file_id": file_id,
                        "unit_id": unit_id,
                        "trial_id": "aggregated",
                        "baseline_hz": float(values["baseline_hz"]),
                        "light_hz": float(values["light_hz"]),
                        "post_hz": float(values["post_hz"]),
                        "delta_light_minus_baseline": float(values["delta_light_minus_baseline"]),
                        "ratio_light_to_baseline": float(values["ratio_light_to_baseline"]),
                        "duration_s": summary_duration,
                        "light_on_s": float(unit_summary["light_on_s"].min()),
                        "light_off_s": float(unit_summary["light_on_s"].min()) + summary_duration,
                        "aligned_x_min_s": summary_window["aligned_x_min_s"],
                        "aligned_x_max_s": summary_window["aligned_x_max_s"],
                        "pre_margin_s": summary_window["pre_margin_s"],
                        "post_margin_s": summary_window["post_margin_s"],
                        "window_mode": summary_window["window_mode"],
                        **agg_summary_windows,
                        "aggregation": effective_aggregation_mode,
                    }
                )

    aligned_df = pd.DataFrame(aligned_rows)
    if not aligned_df.empty:
        required_cols = [
            "file_id",
            "unit_id",
            "trial_id",
            "light_on_s",
            "light_off_s",
            "duration_s",
            "aligned_x_min_s",
            "aligned_x_max_s",
            "pre_margin_s",
            "post_margin_s",
            "window_mode",
            "aligned_time_s",
            "firing_rate_hz",
            "aggregation",
        ]
        for col in required_cols:
            if col not in aligned_df.columns:
                aligned_df[col] = pd.NA
        aligned_df = aligned_df[required_cols].sort_values(["unit_id", "trial_id", "aligned_time_s"]).reset_index(drop=True)

    summary_df = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        summary_df = summary_df.sort_values(["unit_id", "trial_id"]).reset_index(drop=True)
    return aligned_df, summary_df


def build_aligned_rate_from_fullrate(config: dict, logger: PipelineLogger) -> None:
    paths = resolve_project_paths(config)
    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    aligned_cfg = _aligned_cfg(config)
    if not aligned_cfg.get("enabled", True):
        logger.log("build_aligned_rate_from_fullrate", "*", "", "", "skipped", "aligned_rate.enabled=false")
        return

    bin_width_s = float(aligned_cfg["bin_width_s"])
    fullrate_cache: dict[str, pd.DataFrame] = {}
    discovered_frames: list[pd.DataFrame] = []
    for file_id in stim_df[config["project"]["file_id_column"]].astype(str).drop_duplicates():
        fullrate_csv = paths["nex_fullrate_dir"] / config["neuroexplorer"]["export"]["expected_fullrate_pattern"].format(
            file_id=file_id,
            bin_width_s=config["neuroexplorer"]["fullrate"]["bin_width_s"],
        )
        if not fullrate_csv.exists():
            continue
        frame = read_table(fullrate_csv)
        if "unit_id" not in frame.columns:
            raise ValueError(f"Fullrate CSV is missing unit_id: {fullrate_csv}")
        frame = frame.copy()
        frame["file_id"] = str(file_id)
        fullrate_cache[str(file_id)] = frame
        discovered_frames.append(frame[["file_id", "unit_id"]])
    cohort = None
    if discovered_frames:
        cohort = select_unit_cohort(
            config,
            pd.concat(discovered_frames, ignore_index=True),
            module="build_aligned_rate_from_fullrate",
            logger=logger,
            duplicate_policy=config.get("unit_selection", {}).get("duplicate_policy", "keep_all"),
        )
        if not config["run"]["dry_run"]:
            write_cohort_metadata(cohort, paths["nex_aligned_rate_dir"])

    for file_id, stim_sub in stim_df.groupby(config["project"]["file_id_column"], sort=False):
        file_id = str(file_id)
        has_light_values = stim_sub["has_light"].astype(str).str.strip().str.lower().tolist() if "has_light" in stim_sub.columns else ["yes"]
        has_light = any(value == "yes" for value in has_light_values)
        fullrate_csv = paths["nex_fullrate_dir"] / config["neuroexplorer"]["export"]["expected_fullrate_pattern"].format(
            file_id=file_id,
            bin_width_s=config["neuroexplorer"]["fullrate"]["bin_width_s"],
        )
        if not fullrate_csv.exists():
            logger.log(
                "build_aligned_rate_from_fullrate",
                str(file_id),
                "",
                str(fullrate_csv),
                "warning",
                "Fullrate CSV missing; cannot build aligned rate for this file.",
            )
            continue
        if not has_light:
            status_df = pd.DataFrame(
                [
                    {
                        "file_id": str(file_id),
                        "analysis_status": "no_light_skipped",
                        "has_light": "no",
                        "event_group": "nolight",
                        "message": "No light event; aligned analysis skipped.",
                    }
                ]
            )
            if not config["run"]["dry_run"]:
                write_table(status_df, paths["nex_aligned_rate_dir"] / f"{file_id}_LightAlignedRate_no_light_skipped.csv")
                write_table(status_df, paths["nex_aligned_rate_dir"] / f"{file_id}_PreLightPostSummary_no_light_skipped.csv")
            logger.log(
                "build_aligned_rate_from_fullrate",
                str(file_id),
                str(fullrate_csv),
                "",
                "skipped",
                "No light event; aligned rate skipped.",
            )
            continue
        fullrate_df = fullrate_cache.get(file_id)
        if fullrate_df is None:
            fullrate_df = read_table(fullrate_csv)
            fullrate_df["file_id"] = file_id
        trial_windows = []
        for stim_row in stim_sub.itertuples(index=False):
            trial_window = compute_aligned_window(
                light_on_s=float(stim_row.light_on_s),
                light_off_s=None if pd.isna(stim_row.light_off_s) else float(stim_row.light_off_s),
                duration_s=None if pd.isna(stim_row.duration_s) else float(stim_row.duration_s),
                aligned_cfg=aligned_cfg,
            )
            trial_windows.append(trial_window)
            if trial_window["abs_start_s"] < 0:
                logger.log(
                    "build_aligned_rate_from_fullrate",
                    str(file_id),
                    str(fullrate_csv),
                    "",
                    "warning",
                    "Aligned window extends before recording start; missing bins will be clipped or filled according to off_boundary_policy.",
                )
        duration_set = sorted({round(window["duration_s"], 6) for window in trial_windows})
        if len(duration_set) > 1:
            logger.log(
                "build_aligned_rate_from_fullrate",
                str(file_id),
                str(fullrate_csv),
                "",
                "warning",
                f"Multiple light durations detected for this file ({min(duration_set):g} to {max(duration_set):g} s). variable_duration_policy={aligned_cfg.get('variable_duration_policy', 'keep_trials')}",
            )
        aligned_df, summary_df = build_aligned_rate_for_file(config, str(file_id), fullrate_df, stim_sub)
        aligned_path = _aligned_rate_path(paths, str(file_id), bin_width_s, aligned_cfg)
        summary_path = _summary_path(paths, str(file_id))
        if aligned_df.empty:
            logger.log(
                "build_aligned_rate_from_fullrate",
                str(file_id),
                str(fullrate_csv),
                str(aligned_path),
                "warning",
                "No aligned-rate rows were produced from fullrate CSV and stim schedule.",
            )
            continue
        if not config["run"]["dry_run"]:
            write_table(aligned_df, aligned_path)
            selected_summary = filter_to_included_units(summary_df, cohort) if cohort is not None else summary_df
            write_table(selected_summary, summary_path)
        logger.log(
            "build_aligned_rate_from_fullrate",
            str(file_id),
            str(fullrate_csv),
            str(aligned_path),
            "success",
            (
                "Built complete aligned-rate intermediate and cohort-filtered pre/light/post summary. "
                f"window_mode={aligned_cfg.get('window_mode', 'configured_windows')}; tag={aligned_window_tag(aligned_cfg)}; "
                f"n_aligned_rows_all_units={len(aligned_df)}; n_summary_rows_included_units="
                f"{len(filter_to_included_units(summary_df, cohort)) if cohort is not None else len(summary_df)}"
            ),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build light-aligned rate histograms from full-session rate CSV exports.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        build_aligned_rate_from_fullrate(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
