from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table, write_table
from utils.unit_selection import select_unit_cohort, write_cohort_metadata


DEFAULTS = {
    "enabled": True,
    "output_dir": "03_nex_exports/time_cluster_aligned_rate",
    "window_s": None,
    "source_bin_width_s": None,
    "bin_width_s": None,
    "incomplete_target_bin_policy": "error",
    "require_light_on_on_bin_boundary": False,
    "off_boundary_policy": "nearest",
}


def time_cluster_aligned_rate_config(config: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update(config.get("time_cluster_aligned_rate", {}))
    if merged.get("source_bin_width_s") is None:
        source_width = config.get("neuroexplorer", {}).get("fullrate", {}).get("bin_width_s")
        if source_width is None:
            source_width = merged.get("bin_width_s")
        if source_width is None:
            raise ValueError(
                "time_cluster_aligned_rate.source_bin_width_s is null but no "
                "neuroexplorer.fullrate.bin_width_s fallback is available."
            )
        merged["source_bin_width_s"] = float(source_width)
    else:
        merged["source_bin_width_s"] = float(merged["source_bin_width_s"])
    if merged.get("bin_width_s") is None:
        merged["bin_width_s"] = float(merged["source_bin_width_s"])
    else:
        merged["bin_width_s"] = float(merged["bin_width_s"])
    if merged.get("window_s") is None:
        analysis_cfg = config.get("time_cluster_permutation", {})
        window = analysis_cfg.get("analysis_window_s")
        if window is None:
            baseline = analysis_cfg.get("baseline_window_s", [-60, 0])
            test = analysis_cfg.get("test_window_s", [0, 300])
            window = [min(float(baseline[0]), float(test[0])), max(float(baseline[1]), float(test[1]))]
        merged["window_s"] = window
    start_s, end_s = _window_pair(merged["window_s"], "time_cluster_aligned_rate.window_s")
    merged["window_s"] = [start_s, end_s]
    return merged


def _window_pair(value, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must contain exactly [start_s, end_s].")
    start_s, end_s = float(value[0]), float(value[1])
    if not math.isfinite(start_s) or not math.isfinite(end_s) or start_s >= end_s:
        raise ValueError(f"{name} must have finite bounds with start_s < end_s; got {value!r}.")
    return start_s, end_s


def _format_number(value: float) -> str:
    prefix = "m" if value < 0 else ""
    magnitude = abs(float(value))
    text = str(int(magnitude)) if magnitude.is_integer() else f"{magnitude:.6f}".rstrip("0").rstrip(".")
    return prefix + text


def time_cluster_window_tag(cfg: dict) -> str:
    start_s, end_s = [float(value) for value in cfg["window_s"]]
    return f"{_format_number(start_s)}to{_format_number(end_s)}"


def _output_path(paths: dict, file_id: str, cfg: dict) -> Path:
    bin_width_s = float(cfg["bin_width_s"])
    return paths["time_cluster_aligned_rate_dir"] / (
        f"{file_id}_TimeClusterAlignedRate_{time_cluster_window_tag(cfg)}_bin{bin_width_s:g}s.csv"
    )


def _validate_fullrate_intervals(
    fullrate_df: pd.DataFrame,
    target_bin_width_s: float,
    configured_source_bin_width_s: float,
) -> tuple[pd.DataFrame, float]:
    interval_columns = ["time_bin_start_s", "time_bin_end_s", "time_bin_center_s"]
    missing = [column for column in interval_columns if column not in fullrate_df.columns]
    if missing:
        raise ValueError(
            "Time-cluster reconstruction requires fullrate bin interval columns; "
            f"missing={missing}. Legacy aligned centers cannot be converted safely."
        )
    data = fullrate_df.copy()
    for column in interval_columns + ["firing_rate_hz"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[interval_columns].isna().any().any():
        raise ValueError("Fullrate bin start/end/center columns contain missing or non-numeric values.")
    starts = data["time_bin_start_s"].to_numpy(dtype=float)
    ends = data["time_bin_end_s"].to_numpy(dtype=float)
    centers = data["time_bin_center_s"].to_numpy(dtype=float)
    widths = ends - starts
    if np.any(widths <= 0):
        raise ValueError("Fullrate source intervals must have positive widths.")
    source_bin_width_s = float(np.median(widths))
    tolerance = max(1.0e-9, abs(source_bin_width_s) * 1.0e-6)
    if not np.allclose(widths, source_bin_width_s, rtol=1.0e-6, atol=tolerance):
        observed = sorted({round(float(value), 9) for value in widths})
        raise ValueError(f"Fullrate source interval widths are not uniform: {observed}.")
    if not np.allclose(centers, (starts + ends) / 2.0, rtol=1.0e-6, atol=tolerance):
        raise ValueError("Fullrate time_bin_center_s is inconsistent with the source bin start/end midpoint.")
    if not math.isclose(
        source_bin_width_s,
        configured_source_bin_width_s,
        rel_tol=1.0e-6,
        abs_tol=max(tolerance, abs(configured_source_bin_width_s) * 1.0e-6),
    ):
        raise ValueError(
            "Fullrate CSV interval width does not match time_cluster_aligned_rate.source_bin_width_s; "
            f"observed={source_bin_width_s:g}s, configured={configured_source_bin_width_s:g}s."
        )
    ratio = target_bin_width_s / source_bin_width_s
    rounded_ratio = round(ratio)
    if rounded_ratio < 1 or not math.isclose(ratio, rounded_ratio, rel_tol=1.0e-6, abs_tol=1.0e-6):
        raise ValueError(
            "time_cluster_aligned_rate.bin_width_s must be an integer multiple of the source fullrate "
            f"bin width; target={target_bin_width_s:g}s, source={source_bin_width_s:g}s."
        )
    return data, source_bin_width_s


def _resolve_alignment_boundary(
    fullrate_df: pd.DataFrame,
    light_on_s: float,
    cfg: dict,
    source_bin_width_s: float,
) -> dict:
    tolerance = max(1.0e-9, abs(source_bin_width_s) * 1.0e-6)
    boundaries = np.unique(
        np.concatenate(
            [
                fullrate_df["time_bin_start_s"].to_numpy(dtype=float),
                fullrate_df["time_bin_end_s"].to_numpy(dtype=float),
            ]
        )
    )
    if boundaries.size == 0:
        raise ValueError("Fullrate input contains no source bin boundaries.")
    distances = np.abs(boundaries - light_on_s)
    minimum_distance = float(np.min(distances))
    exact = minimum_distance <= tolerance
    policy = str(cfg.get("off_boundary_policy", "nearest")).strip().lower()
    require_exact = bool(cfg.get("require_light_on_on_bin_boundary", False))
    if policy not in {"nearest", "interpolate", "error"}:
        raise ValueError("time_cluster_aligned_rate.off_boundary_policy must be nearest, interpolate, or error.")
    if exact:
        boundary = float(np.min(boundaries[distances <= tolerance]))
        method = "exact_boundary"
    elif require_exact or policy == "error":
        raise ValueError(
            f"Stimulus onset {light_on_s:g} s is not a fullrate bin boundary; nearest boundary is "
            f"{minimum_distance:g} s away."
        )
    elif policy == "interpolate":
        raise ValueError(
            "time_cluster_aligned_rate.off_boundary_policy=interpolate cannot reconstruct exact bins from "
            "already averaged fullrate values without spike timestamps."
        )
    else:
        nearest = boundaries[np.isclose(distances, minimum_distance, rtol=0.0, atol=tolerance)]
        boundary = float(np.min(nearest))
        method = "nearest_boundary"
    offset = boundary - light_on_s
    return {
        "alignment_boundary_s": boundary,
        "alignment_offset_s": offset,
        "stimulus_time_aligned_s": -offset,
        "alignment_method": method,
        "alignment_exact": bool(exact),
        "off_boundary_policy": policy,
    }


def _aggregate_unit_to_target_bins(
    unit_df: pd.DataFrame,
    alignment_boundary_s: float,
    window_start_s: float,
    window_end_s: float,
    target_bin_width_s: float,
    source_bin_width_s: float,
    incomplete_target_bin_policy: str,
) -> pd.DataFrame:
    tolerance = max(1.0e-9, abs(source_bin_width_s) * 1.0e-6)
    unit = unit_df.sort_values("time_bin_start_s").copy()
    starts = unit["time_bin_start_s"].to_numpy(dtype=float)
    ends = unit["time_bin_end_s"].to_numpy(dtype=float)
    if len(unit) > 1 and not np.allclose(starts[1:], ends[:-1], rtol=0.0, atol=tolerance):
        raise ValueError(f"Fullrate source intervals are not contiguous for unit {unit_df['unit_id'].iloc[0]!r}.")

    first_k = math.ceil(window_start_s / target_bin_width_s - 0.5 - tolerance)
    stop_k = math.ceil(window_end_s / target_bin_width_s - 0.5 - tolerance)
    expected_source_bins = int(round(target_bin_width_s / source_bin_width_s))
    rows: list[dict] = []
    for bin_index in range(first_k, stop_k):
        aligned_start_s = bin_index * target_bin_width_s
        aligned_end_s = aligned_start_s + target_bin_width_s
        absolute_start_s = alignment_boundary_s + aligned_start_s
        absolute_end_s = alignment_boundary_s + aligned_end_s
        source = unit[
            (unit["time_bin_start_s"] >= absolute_start_s - tolerance)
            & (unit["time_bin_end_s"] <= absolute_end_s + tolerance)
        ].copy()
        complete = (
            len(source) == expected_source_bins
            and math.isclose(float(source["time_bin_start_s"].iloc[0]), absolute_start_s, abs_tol=tolerance)
            and math.isclose(float(source["time_bin_end_s"].iloc[-1]), absolute_end_s, abs_tol=tolerance)
            and math.isclose(
                float((source["time_bin_end_s"] - source["time_bin_start_s"]).sum()),
                target_bin_width_s,
                abs_tol=tolerance * expected_source_bins,
            )
        )
        if not complete:
            unit_id = str(unit_df["unit_id"].iloc[0])
            if incomplete_target_bin_policy == "error":
                raise ValueError(
                    f"Cannot exactly reconstruct target bin [{aligned_start_s:g}, {aligned_end_s:g}) s for "
                    f"unit {unit_id}: expected {expected_source_bins} contiguous source bins covering "
                    f"[{absolute_start_s:g}, {absolute_end_s:g}) s, found {len(source)}."
                )
            source_coverage_s = float(
                (source["time_bin_end_s"] - source["time_bin_start_s"]).sum()
            )
            rows.append(
                {
                    "aligned_bin_start_s": aligned_start_s,
                    "aligned_bin_end_s": aligned_end_s,
                    "aligned_time_s": (aligned_start_s + aligned_end_s) / 2.0,
                    "firing_rate_hz": float("nan"),
                    "source_bin_width_s": source_bin_width_s,
                    "target_bin_width_s": target_bin_width_s,
                    "n_source_bins": len(source),
                    "source_coverage_s": source_coverage_s,
                    "rebin_method": "incomplete_source_coverage_nan",
                }
            )
            continue
        rates = source["firing_rate_hz"].to_numpy(dtype=float)
        source_widths = (
            source["time_bin_end_s"].to_numpy(dtype=float)
            - source["time_bin_start_s"].to_numpy(dtype=float)
        )
        firing_rate_hz = float(np.average(rates, weights=source_widths)) if np.isfinite(rates).all() else float("nan")
        rows.append(
            {
                "aligned_bin_start_s": aligned_start_s,
                "aligned_bin_end_s": aligned_end_s,
                "aligned_time_s": (aligned_start_s + aligned_end_s) / 2.0,
                "firing_rate_hz": firing_rate_hz,
                "source_bin_width_s": source_bin_width_s,
                "target_bin_width_s": target_bin_width_s,
                "n_source_bins": len(source),
                "source_coverage_s": target_bin_width_s,
                "rebin_method": "identity" if expected_source_bins == 1 else "duration_weighted_exact_aggregation",
            }
        )
    return pd.DataFrame(rows)


def build_time_cluster_aligned_rate_for_file(
    config: dict,
    file_id: str,
    fullrate_df: pd.DataFrame,
    stim_sub: pd.DataFrame,
) -> pd.DataFrame:
    cfg = time_cluster_aligned_rate_config(config)
    bin_width_s = float(cfg["bin_width_s"])
    configured_source_bin_width_s = float(cfg["source_bin_width_s"])
    incomplete_target_bin_policy = str(cfg.get("incomplete_target_bin_policy", "error")).strip().lower()
    if incomplete_target_bin_policy not in {"error", "nan"}:
        raise ValueError("time_cluster_aligned_rate.incomplete_target_bin_policy must be error or nan.")
    window_start_s, window_end_s = [float(value) for value in cfg["window_s"]]
    tolerance = max(1.0e-9, abs(bin_width_s) * 1.0e-6)
    fullrate, source_bin_width_s = _validate_fullrate_intervals(
        fullrate_df,
        bin_width_s,
        configured_source_bin_width_s,
    )
    rows: list[dict] = []
    for trial_id, stim_row in enumerate(stim_sub.itertuples(index=False), start=1):
        light_on_s = float(stim_row.light_on_s)
        duration_s = None if pd.isna(stim_row.duration_s) else float(stim_row.duration_s)
        light_off_s = None if pd.isna(stim_row.light_off_s) else float(stim_row.light_off_s)
        if light_off_s is None and duration_s is None:
            raise ValueError("Each light trial requires light_off_s or duration_s.")
        if light_off_s is None:
            light_off_s = light_on_s + float(duration_s)
        if duration_s is None:
            duration_s = light_off_s - light_on_s
        alignment = _resolve_alignment_boundary(fullrate, light_on_s, cfg, source_bin_width_s)
        for unit_id, unit_df in fullrate.groupby("unit_id", sort=False):
            trial_df = _aggregate_unit_to_target_bins(
                unit_df,
                alignment["alignment_boundary_s"],
                window_start_s,
                window_end_s,
                bin_width_s,
                source_bin_width_s,
                incomplete_target_bin_policy,
            )
            trial_df[["aligned_bin_start_s", "aligned_bin_end_s", "aligned_time_s"]] = trial_df[
                ["aligned_bin_start_s", "aligned_bin_end_s", "aligned_time_s"]
            ].round(6)
            crossing_zero = (trial_df["aligned_bin_start_s"] < -tolerance) & (
                trial_df["aligned_bin_end_s"] > tolerance
            )
            if crossing_zero.any():
                raise ValueError("A reconstructed time-cluster bin crosses aligned 0 s.")
            for row in trial_df.itertuples(index=False):
                rows.append(
                    {
                        "file_id": file_id,
                        "unit_id": unit_id,
                        "trial_id": trial_id,
                        "light_on_s": light_on_s,
                        "light_off_s": light_off_s,
                        "duration_s": duration_s,
                        "window_start_s": window_start_s,
                        "window_end_s": window_end_s,
                        "aligned_bin_start_s": row.aligned_bin_start_s,
                        "aligned_bin_end_s": row.aligned_bin_end_s,
                        "aligned_time_s": row.aligned_time_s,
                        "firing_rate_hz": row.firing_rate_hz,
                        "source_bin_width_s": row.source_bin_width_s,
                        "target_bin_width_s": row.target_bin_width_s,
                        "n_source_bins": row.n_source_bins,
                        "source_coverage_s": row.source_coverage_s,
                        "rebin_method": row.rebin_method,
                        **alignment,
                        "aggregation": "keep_trials",
                    }
                )
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    return output.sort_values(["unit_id", "trial_id", "aligned_time_s"]).reset_index(drop=True)


def build_time_cluster_aligned_rate(config: dict, logger: PipelineLogger) -> None:
    paths = resolve_project_paths(config)
    cfg = time_cluster_aligned_rate_config(config)
    if not bool(cfg.get("enabled", True)):
        logger.log("time_cluster_aligned_rate", "*", "", "", "skipped", "time_cluster_aligned_rate.enabled=false")
        return
    stim_df = normalize_stim_schedule(
        read_table(paths["stim_schedule_path"]),
        file_id_column=config["project"]["file_id_column"],
    )
    configured_source_width = config.get("time_cluster_aligned_rate", {}).get("source_bin_width_s")
    fullrate_bin_width_value = (
        config["neuroexplorer"]["fullrate"]["bin_width_s"]
        if configured_source_width is None
        else configured_source_width
    )
    fullrate_cache: dict[str, pd.DataFrame] = {}
    discovered_frames: list[pd.DataFrame] = []
    for file_id in stim_df[config["project"]["file_id_column"]].astype(str).drop_duplicates():
        fullrate_path = paths["nex_fullrate_dir"] / config["neuroexplorer"]["export"][
            "expected_fullrate_pattern"
        ].format(file_id=file_id, bin_width_s=fullrate_bin_width_value)
        if not fullrate_path.exists():
            continue
        frame = read_table(fullrate_path).copy()
        if "unit_id" not in frame.columns:
            raise ValueError(f"Fullrate CSV is missing unit_id: {fullrate_path}")
        frame["file_id"] = str(file_id)
        fullrate_cache[str(file_id)] = frame
        discovered_frames.append(frame[["file_id", "unit_id"]])
    cohort = None
    if discovered_frames:
        cohort = select_unit_cohort(
            config,
            pd.concat(discovered_frames, ignore_index=True),
            module="time_cluster_aligned_rate",
            logger=logger,
            duplicate_policy=config.get("time_cluster_permutation", {}).get("duplicate_policy", "exclude_duplicates"),
        )
        if not config.get("run", {}).get("dry_run", False):
            write_cohort_metadata(cohort, paths["time_cluster_aligned_rate_dir"])
    for file_id, stim_sub in stim_df.groupby(config["project"]["file_id_column"], sort=False):
        file_id = str(file_id)
        has_light_values = (
            stim_sub["has_light"].astype(str).str.strip().str.lower().tolist()
            if "has_light" in stim_sub.columns
            else ["yes"]
        )
        if not any(value == "yes" for value in has_light_values):
            logger.log("time_cluster_aligned_rate", str(file_id), "", "", "skipped", "No light event; skipped.")
            continue
        fullrate_path = paths["nex_fullrate_dir"] / config["neuroexplorer"]["export"][
            "expected_fullrate_pattern"
        ].format(file_id=file_id, bin_width_s=fullrate_bin_width_value)
        output_path = _output_path(paths, str(file_id), cfg)
        if not fullrate_path.exists():
            logger.log(
                "time_cluster_aligned_rate",
                str(file_id),
                str(fullrate_path),
                str(output_path),
                "warning",
                "Fullrate CSV missing; cannot build time-cluster aligned input.",
            )
            continue
        output = build_time_cluster_aligned_rate_for_file(
            config,
            str(file_id),
            fullrate_cache[file_id] if file_id in fullrate_cache else read_table(fullrate_path),
            stim_sub,
        )
        if output.empty:
            logger.log(
                "time_cluster_aligned_rate",
                str(file_id),
                str(fullrate_path),
                str(output_path),
                "warning",
                "No boundary-aligned time-cluster rows were produced.",
            )
            continue
        if not config.get("run", {}).get("dry_run", False):
            write_table(output, output_path)
        alignment = output[
            ["trial_id", "light_on_s", "alignment_boundary_s", "alignment_offset_s", "stimulus_time_aligned_s", "alignment_method"]
        ].drop_duplicates()
        details = "; ".join(
            (
                f"trial={row.trial_id}, onset={float(row.light_on_s):g}s, "
                f"boundary={float(row.alignment_boundary_s):g}s, offset={float(row.alignment_offset_s):+g}s, "
                f"stimulus_aligned={float(row.stimulus_time_aligned_s):g}s, method={row.alignment_method}"
            )
            for row in alignment.itertuples(index=False)
        )
        incomplete_rows = int((output["rebin_method"] == "incomplete_source_coverage_nan").sum())
        logger.log(
            "time_cluster_aligned_rate",
            str(file_id),
            str(fullrate_path),
            str(output_path),
            "success",
            (
                f"Built dedicated boundary-aligned time-cluster input for all discovered Units. n_rows={len(output)}; "
                f"cohort_included={cohort.metadata['n_units_included'] if cohort is not None else 0}; "
                f"incomplete_rows_as_nan={incomplete_rows}; {details}"
            ),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build dedicated boundary-aligned inputs for time-cluster permutation from fullrate CSVs."
    )
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config).expanduser().resolve())
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        build_time_cluster_aligned_rate(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
