from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.aligned_utils import compute_pre_light_post_windows
from utils.file_id_utils import canonicalize_file_id
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_path, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, parse_bool, read_table, write_table
from utils.unit_selection import load_unit_quality_table, select_unit_cohort, write_cohort_metadata


SUMMARY_RE = re.compile(r"^(?P<file_id>.+)_PreLightPostSummary(?:_no_light_skipped)?\.csv$", re.IGNORECASE)
STATUS_COLUMNS = ["file_id", "pl2_file", "has_light", "condition", "event_group", "reason", "message"]
CORE_SUMMARY_COLUMNS = [
    "file_id",
    "unit_id",
    "trial_id",
    "baseline_hz",
    "light_hz",
    "post_hz",
    "delta_light_minus_baseline",
    "ratio_light_to_baseline",
    "duration_s",
    "light_on_s",
    "light_off_s",
    "aligned_x_min_s",
    "aligned_x_max_s",
    "pre_margin_s",
    "post_margin_s",
    "window_mode",
    "summary_window_mode",
    "baseline_window_start_s",
    "baseline_window_end_s",
    "light_window_start_s",
    "light_window_end_s",
    "post_window_start_s",
    "post_window_end_s",
    "aggregation",
]
STIM_METADATA_COLUMNS = ["pl2_file", "event_group", "has_light", "condition", "note", "file_index", "sorted_channels"]
UNIT_METADATA_COLUMNS = [
    "original_name",
    "channel",
    "include",
    "exclusion_reason",
    "representative_unit",
    "duplicate_of",
    "unit_note",
]
WIDE_COLUMNS = [
    "file_id",
    "pl2_file",
    "condition",
    "event_group",
    "has_light",
    "unit_id",
    "original_name",
    "channel",
    "trial_id",
    "aggregation",
    "baseline_hz",
    "light_hz",
    "post_hz",
    "delta_light_minus_baseline",
    "delta_post_minus_baseline",
    "delta_post_minus_light",
    "ratio_light_to_baseline",
    "ratio_post_to_baseline",
    "percent_change_light_vs_baseline",
    "percent_change_post_vs_baseline",
    "duration_s",
    "light_on_s",
    "light_off_s",
    "baseline_window_start_s",
    "baseline_window_end_s",
    "light_window_start_s",
    "light_window_end_s",
    "post_window_start_s",
    "post_window_end_s",
    "summary_window_mode",
    "source_summary_file",
]
LONG_COLUMNS = [
    "file_id",
    "pl2_file",
    "condition",
    "event_group",
    "has_light",
    "unit_id",
    "original_name",
    "channel",
    "trial_id",
    "aggregation",
    "phase",
    "phase_start_s",
    "phase_end_s",
    "phase_duration_s",
    "firing_rate_hz",
    "baseline_hz",
    "light_hz",
    "post_hz",
    "delta_light_minus_baseline",
    "delta_post_minus_baseline",
    "delta_post_minus_light",
    "ratio_light_to_baseline",
    "ratio_post_to_baseline",
    "duration_s",
    "light_on_s",
    "light_off_s",
    "summary_window_mode",
    "source_summary_file",
]
QC_COLUMNS = [
    "pre_hz",
    "max_window_hz",
    "pre_duration_s",
    "light_duration_s",
    "post_duration_s",
    "pre_expected_spikes",
    "light_expected_spikes",
    "post_expected_spikes",
    "total_expected_spikes",
    "activity_filter_pass",
    "activity_filter_reason",
]


def _stats_cfg(config: dict) -> dict:
    return config.get("statistics", {}).get("prelightpost", {})


def _aligned_cfg(config: dict) -> dict:
    return config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))


def _output_dir(config: dict, paths: dict) -> Path:
    statistics_cfg = config.get("statistics", {})
    return resolve_path(paths["root_dir"], statistics_cfg.get("output_dir", "07_statistics"))


def _prelightpost_input_dir(config: dict, paths: dict) -> Path:
    cfg = _stats_cfg(config)
    return resolve_path(paths["root_dir"], cfg.get("input_dir", "03_nex_exports/aligned_rate"))


def _normal_summary_files(config: dict, input_dir: Path) -> list[Path]:
    pattern = _stats_cfg(config).get("input_pattern", "*_PreLightPostSummary.csv")
    return sorted(path for path in input_dir.glob(pattern) if "_no_light_skipped" not in path.name)


def _skipped_summary_files(input_dir: Path) -> list[Path]:
    return sorted(input_dir.glob("*_PreLightPostSummary_no_light_skipped.csv"))


def _file_id_from_summary_path(path: Path, config: dict) -> str:
    match = SUMMARY_RE.match(path.name)
    raw_file_id = match.group("file_id") if match else path.stem.split("_PreLightPostSummary")[0]
    return canonicalize_file_id(raw_file_id, None, config)


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _read_optional_stim(config: dict, paths: dict, logger: PipelineLogger) -> pd.DataFrame:
    path = paths["stim_schedule_path"]
    if not path.exists():
        return _empty_frame(["file_id", *STIM_METADATA_COLUMNS, "has_light_bool"])
    try:
        df = normalize_stim_schedule(read_table(path), file_id_column=config["project"]["file_id_column"])
        df["file_id"] = [canonicalize_file_id(str(row.file_id), row.pl2_file, config) for row in df.itertuples(index=False)]
        if "has_light_bool" not in df.columns:
            df["has_light_bool"] = df["has_light"].map(parse_bool)
        else:
            df["has_light_bool"] = df["has_light_bool"].map(parse_bool)
        keep_cols = ["file_id", *STIM_METADATA_COLUMNS, "has_light_bool"]
        for column in keep_cols:
            if column not in df.columns:
                df[column] = ""
        return df[keep_cols].drop_duplicates(subset=["file_id"], keep="last")
    except Exception as exc:
        logger.log("prelightpost_stats", "*", str(path), "", "warning", "stim_schedule_master read failed; continuing without stim metadata.", exc)
        return _empty_frame(["file_id", *STIM_METADATA_COLUMNS, "has_light_bool"])


def _read_optional_unit_table(config: dict, paths: dict, logger: PipelineLogger) -> pd.DataFrame:
    path = paths["unit_quality_path"]
    try:
        df = load_unit_quality_table(config)
        df["include_bool"] = df["include"].map(
            lambda value: str(value).strip().lower() == "yes" if not pd.isna(value) else False
        )
        if "pl2_file" not in df.columns:
            df["pl2_file"] = ""
        df["file_id"] = [canonicalize_file_id(str(row.file_id), row.pl2_file, config) for row in df.itertuples(index=False)]
        if "note" in df.columns:
            df["unit_note"] = df["note"]
        else:
            df["unit_note"] = ""
        keep_cols = ["file_id", "unit_id", *UNIT_METADATA_COLUMNS, "include_bool"]
        for column in keep_cols:
            if column not in df.columns:
                df[column] = ""
        return df[keep_cols].drop_duplicates(subset=["file_id", "unit_id"], keep="last")
    except Exception as exc:
        logger.log("prelightpost_stats", "*", str(path), "", "failed", "unit_quality_table is required and could not be validated.", exc)
        raise


def _read_summary_file(path: Path, file_id: str, logger: PipelineLogger) -> pd.DataFrame:
    try:
        df = read_table(path).copy()
    except Exception as exc:
        logger.log("prelightpost_stats", file_id, str(path), "", "failed", "Failed to read PreLightPostSummary CSV.", exc)
        return _empty_frame(CORE_SUMMARY_COLUMNS)
    for column in CORE_SUMMARY_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
    df["file_id"] = [canonicalize_file_id(str(value), None, None) if pd.notna(value) and str(value).strip() else file_id for value in df["file_id"]]
    df["file_id"] = df["file_id"].map(lambda value: canonicalize_file_id(str(value), None, None))
    df["source_summary_file"] = str(path)
    return df[[*CORE_SUMMARY_COLUMNS, "source_summary_file"]]


def _row_is_aggregated(row: pd.Series) -> bool:
    trial_id = str(row.get("trial_id", "")).strip().lower()
    aggregation = str(row.get("aggregation", "")).strip().lower()
    return trial_id == "aggregated" or aggregation in {"mean", "median", "aggregated"}


def _filter_rows_by_aggregation(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    cfg = _stats_cfg(config)
    include_trial = bool(cfg.get("include_trial_rows", True))
    include_aggregated = bool(cfg.get("include_aggregated_rows", True))
    preferred = str(cfg.get("preferred_aggregation", "trial")).strip().lower()
    if df.empty:
        return df
    aggregated_mask = df.apply(_row_is_aggregated, axis=1)
    trial_mask = ~aggregated_mask
    if preferred == "all":
        mask = (trial_mask & include_trial) | (aggregated_mask & include_aggregated)
    elif preferred == "trial":
        mask = trial_mask & include_trial
    elif preferred == "aggregated":
        mask = aggregated_mask & include_aggregated
    elif preferred in {"mean", "median"}:
        mask = df["aggregation"].astype(str).str.lower().eq(preferred) & include_aggregated
    else:
        mask = (trial_mask & include_trial) | (aggregated_mask & include_aggregated)
    return df[mask].copy()


def _safe_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = df.copy()
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _compute_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    result = _safe_numeric(df, ["baseline_hz", "light_hz", "post_hz"])
    result["delta_light_minus_baseline"] = result["light_hz"] - result["baseline_hz"]
    result["delta_post_minus_baseline"] = result["post_hz"] - result["baseline_hz"]
    result["delta_post_minus_light"] = result["post_hz"] - result["light_hz"]
    baseline_nonzero = result["baseline_hz"].notna() & (result["baseline_hz"] != 0)
    result["ratio_light_to_baseline"] = pd.NA
    result["ratio_post_to_baseline"] = pd.NA
    result["percent_change_light_vs_baseline"] = pd.NA
    result["percent_change_post_vs_baseline"] = pd.NA
    result.loc[baseline_nonzero, "ratio_light_to_baseline"] = result.loc[baseline_nonzero, "light_hz"] / result.loc[baseline_nonzero, "baseline_hz"]
    result.loc[baseline_nonzero, "ratio_post_to_baseline"] = result.loc[baseline_nonzero, "post_hz"] / result.loc[baseline_nonzero, "baseline_hz"]
    result.loc[baseline_nonzero, "percent_change_light_vs_baseline"] = 100 * result.loc[baseline_nonzero, "delta_light_minus_baseline"] / result.loc[baseline_nonzero, "baseline_hz"]
    result.loc[baseline_nonzero, "percent_change_post_vs_baseline"] = 100 * result.loc[baseline_nonzero, "delta_post_minus_baseline"] / result.loc[baseline_nonzero, "baseline_hz"]
    return result


def _apply_configured_window_metadata(df: pd.DataFrame, config: dict, logger: PipelineLogger) -> pd.DataFrame:
    if df.empty:
        return df
    aligned_cfg = _aligned_cfg(config)
    try:
        configured = compute_pre_light_post_windows(1.0, aligned_cfg)
    except Exception as exc:
        logger.log(
            "prelightpost_stats",
            "*",
            "",
            "",
            "warning",
            "Could not resolve configured pre/light/post windows; using only window metadata present in PreLightPostSummary.csv.",
            exc,
        )
        return df

    result = df.copy()
    window_columns = {
        "baseline_window_start_s": configured["baseline_window_start_s"],
        "baseline_window_end_s": configured["baseline_window_end_s"],
        "light_window_start_s": configured["light_window_start_s"],
        "light_window_end_s": configured["light_window_end_s"],
        "post_window_start_s": configured["post_window_start_s"],
        "post_window_end_s": configured["post_window_end_s"],
        "summary_window_mode": configured["summary_window_mode"],
    }
    filled_count = 0
    mismatch_columns: set[str] = set()
    for column, configured_value in window_columns.items():
        if column not in result.columns:
            result[column] = pd.NA
        missing_mask = result[column].isna() | result[column].astype(str).str.strip().eq("")
        if missing_mask.any():
            result.loc[missing_mask, column] = configured_value
            filled_count += int(missing_mask.sum())
        if column == "summary_window_mode":
            nonmissing = result[column].dropna().astype(str).str.strip()
            if not nonmissing.empty and not nonmissing.eq(str(configured_value)).all():
                mismatch_columns.add(column)
        else:
            numeric = pd.to_numeric(result[column], errors="coerce")
            nonmissing_mask = numeric.notna()
            if nonmissing_mask.any() and not (numeric[nonmissing_mask].round(6) == round(float(configured_value), 6)).all():
                mismatch_columns.add(column)
    if filled_count:
        logger.log(
            "prelightpost_stats",
            "*",
            "",
            "",
            "success",
            f"Filled missing PreLightPostSummary window metadata from aligned_rate pre/light/post config. n_cells_filled={filled_count}",
        )
    if mismatch_columns:
        logger.log(
            "prelightpost_stats",
            "*",
            "",
            "",
            "warning",
            "Existing PreLightPostSummary window metadata differs from current aligned_rate pre/light/post config; statistics use existing summary values. Rerun aligned_rate to regenerate values with the current window config. mismatched_window_columns="
            + ",".join(sorted(mismatch_columns)),
        )
    return result


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = df.copy()
    for column in columns:
        if column not in result.columns:
            result[column] = pd.NA
    return result[columns]


def _apply_unit_filters(wide_df: pd.DataFrame, unit_df: pd.DataFrame, config: dict, skipped_rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = _stats_cfg(config)
    if unit_df.empty:
        return wide_df, _empty_frame(WIDE_COLUMNS)
    result = wide_df.merge(unit_df, on=["file_id", "unit_id"], how="left")
    excluded_frames: list[pd.DataFrame] = []
    if not bool(cfg.get("include_only_unit_quality_include_yes", True)):
        raise ValueError(
            "statistics.prelightpost.include_only_unit_quality_include_yes cannot be false; "
            "unit_quality_table is the mandatory Unit cohort source."
        )
    include_only = True
    if include_only:
        missing_unit = result["include_bool"].isna()
        if missing_unit.any():
            missing = result.loc[missing_unit, ["file_id", "unit_id"]].drop_duplicates().to_dict("records")
            raise ValueError(
                f"prelightpost_stats: unit_quality_table does not match summary Unit(s): {missing}. "
                "Run build_unit_table, review include values, and rerun."
            )
        include_mask = result["include_bool"].map(bool)
        excluded = result[~include_mask]
        if not excluded.empty:
            excluded = excluded.copy()
            excluded["activity_filter_reason"] = "excluded_by_unit_quality_table"
            excluded_frames.append(_ensure_columns(excluded, [*WIDE_COLUMNS, "activity_filter_reason"]))
        for row in excluded.itertuples(index=False):
            skipped_rows.append(
                {
                    "file_id": str(row.file_id),
                    "pl2_file": getattr(row, "pl2_file", ""),
                    "has_light": getattr(row, "has_light", ""),
                    "condition": getattr(row, "condition", ""),
                    "event_group": getattr(row, "event_group", ""),
                    "unit_id": getattr(row, "unit_id", ""),
                    "reason": "excluded_by_unit_quality_table",
                    "message": "Unit excluded by unit_quality_table include=no.",
                }
            )
        result = result[include_mask].copy()
    duplicate_policy = str(cfg.get("duplicate_policy", "keep_all")).strip().lower()
    if duplicate_policy in {"keep_representative_only", "exclude_duplicates"}:
        duplicate_of = result.get("duplicate_of", pd.Series("", index=result.index)).fillna("").astype(str).str.strip()
        representative = result.get("representative_unit", pd.Series("", index=result.index)).fillna("").astype(str).str.strip()
        unit_id = result["unit_id"].fillna("").astype(str).str.strip()
        if duplicate_policy == "exclude_duplicates":
            keep_mask = duplicate_of.eq("")
        else:
            keep_mask = duplicate_of.eq("") | representative.eq(unit_id)
        excluded = result[~keep_mask]
        if not excluded.empty:
            excluded = excluded.copy()
            excluded["activity_filter_reason"] = "duplicate_excluded"
            excluded_frames.append(_ensure_columns(excluded, [*WIDE_COLUMNS, "activity_filter_reason"]))
        for row in excluded.itertuples(index=False):
            skipped_rows.append(
                {
                    "file_id": str(row.file_id),
                    "pl2_file": getattr(row, "pl2_file", ""),
                    "has_light": getattr(row, "has_light", ""),
                    "condition": getattr(row, "condition", ""),
                    "event_group": getattr(row, "event_group", ""),
                    "unit_id": getattr(row, "unit_id", ""),
                    "reason": "duplicate_excluded",
                    "message": "Unit excluded by duplicate unit policy.",
                }
            )
        result = result[keep_mask].copy()
    if "include_bool" in result.columns:
        result = result.drop(columns=["include_bool"])
    excluded_df = pd.concat(excluded_frames, ignore_index=True, sort=False) if excluded_frames else _empty_frame([*WIDE_COLUMNS, "activity_filter_reason"])
    return result, excluded_df


def _merge_metadata(summary_df: pd.DataFrame, stim_df: pd.DataFrame) -> pd.DataFrame:
    result = summary_df.copy()
    if not stim_df.empty:
        result = result.merge(stim_df.drop(columns=["has_light_bool"], errors="ignore"), on="file_id", how="left", suffixes=("", "_stim"))
    for column in STIM_METADATA_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    return result


def _build_long_table(wide_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    phase_specs = [
        ("baseline", "baseline_hz", "baseline_window_start_s", "baseline_window_end_s"),
        ("light", "light_hz", "light_window_start_s", "light_window_end_s"),
        ("post", "post_hz", "post_window_start_s", "post_window_end_s"),
    ]
    for row in wide_df.to_dict("records"):
        for phase, value_col, start_col, end_col in phase_specs:
            phase_row = {column: row.get(column, pd.NA) for column in LONG_COLUMNS if column in row}
            start = pd.to_numeric(pd.Series([row.get(start_col)]), errors="coerce").iloc[0]
            end = pd.to_numeric(pd.Series([row.get(end_col)]), errors="coerce").iloc[0]
            phase_row.update(
                {
                    "phase": phase,
                    "phase_start_s": start,
                    "phase_end_s": end,
                    "phase_duration_s": end - start if pd.notna(start) and pd.notna(end) else pd.NA,
                    "firing_rate_hz": row.get(value_col, pd.NA),
                }
            )
            rows.append(phase_row)
    long_df = pd.DataFrame(rows)
    for column in LONG_COLUMNS:
        if column not in long_df.columns:
            long_df[column] = pd.NA
    return long_df[LONG_COLUMNS]


def _sem(series: pd.Series) -> float | pd.NA:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) <= 1:
        return pd.NA
    return float(values.std(ddof=1) / (len(values) ** 0.5))


def _phase_summary(long_df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if long_df.empty:
        return _empty_frame([*group_cols, "phase", "n_units", "n_trials", "mean_firing_rate_hz", "sem_firing_rate_hz", "median_firing_rate_hz", "sd_firing_rate_hz"])
    grouped = long_df.groupby([*group_cols, "phase"], dropna=False)
    summary = grouped.agg(
        n_units=("unit_id", "nunique"),
        n_trials=("trial_id", "nunique"),
        mean_firing_rate_hz=("firing_rate_hz", "mean"),
        median_firing_rate_hz=("firing_rate_hz", "median"),
        sd_firing_rate_hz=("firing_rate_hz", "std"),
    ).reset_index()
    summary["sem_firing_rate_hz"] = grouped["firing_rate_hz"].apply(_sem).reset_index(drop=True)
    return summary


def _delta_summary(wide_df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    columns = [
        *group_cols,
        "n_files",
        "n_units",
        "mean_baseline_hz",
        "mean_light_hz",
        "mean_post_hz",
        "mean_delta_light_minus_baseline",
        "sem_delta_light_minus_baseline",
        "mean_delta_post_minus_baseline",
        "sem_delta_post_minus_baseline",
    ]
    if wide_df.empty:
        return _empty_frame(columns)
    grouped = wide_df.groupby(group_cols, dropna=False)
    summary = grouped.agg(
        n_files=("file_id", "nunique"),
        n_units=("unit_id", "nunique"),
        mean_baseline_hz=("baseline_hz", "mean"),
        mean_light_hz=("light_hz", "mean"),
        mean_post_hz=("post_hz", "mean"),
        mean_delta_light_minus_baseline=("delta_light_minus_baseline", "mean"),
        mean_delta_post_minus_baseline=("delta_post_minus_baseline", "mean"),
    ).reset_index()
    summary["sem_delta_light_minus_baseline"] = grouped["delta_light_minus_baseline"].apply(_sem).reset_index(drop=True)
    summary["sem_delta_post_minus_baseline"] = grouped["delta_post_minus_baseline"].apply(_sem).reset_index(drop=True)
    return summary[columns]


def _build_group_summaries(wide_df: pd.DataFrame, long_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_file_phase = _phase_summary(long_df, ["file_id", "condition", "event_group", "has_light"])
    by_file_delta = _delta_summary(wide_df, ["file_id", "condition", "event_group", "has_light"])
    by_file_delta["phase"] = "delta"
    by_file = pd.concat([by_file_phase, by_file_delta], ignore_index=True, sort=False)
    by_condition_phase = _phase_summary(long_df, ["condition"])
    by_condition_phase["n_files"] = long_df.groupby(["condition", "phase"], dropna=False)["file_id"].nunique().reset_index(drop=True) if not long_df.empty else pd.Series(dtype="Int64")
    by_condition_delta = _delta_summary(wide_df, ["condition"])
    by_condition_delta["phase"] = "delta"
    by_condition = pd.concat([by_condition_phase, by_condition_delta], ignore_index=True, sort=False)
    return by_file, by_condition


def _expected_light_file_ids(stim_df: pd.DataFrame) -> set[str]:
    if stim_df.empty or "has_light_bool" not in stim_df.columns:
        return set()
    return set(stim_df[stim_df["has_light_bool"]]["file_id"].astype(str).tolist())


def _build_skipped_records(
    *,
    config: dict,
    stim_df: pd.DataFrame,
    normal_file_ids: set[str],
    skipped_files: list[Path],
) -> list[dict]:
    records: list[dict] = []
    skipped_file_ids = {_file_id_from_summary_path(path, config) for path in skipped_files}
    if not stim_df.empty:
        for row in stim_df.to_dict("records"):
            file_id = str(row.get("file_id", ""))
            has_light = str(row.get("has_light", "")).strip().lower()
            event_group = str(row.get("event_group", "")).strip().lower()
            if has_light == "no" or event_group in {"nolight", "no_light"}:
                if file_id in skipped_file_ids or file_id not in normal_file_ids:
                    records.append(
                        {
                            "file_id": file_id,
                            "pl2_file": row.get("pl2_file", ""),
                            "has_light": "no",
                            "condition": row.get("condition", ""),
                            "event_group": row.get("event_group", "nolight"),
                            "reason": "no_light_control",
                            "message": "No light event; pre/light/post statistics not applicable.",
                        }
                    )
            elif file_id not in normal_file_ids:
                records.append(
                    {
                        "file_id": file_id,
                        "pl2_file": row.get("pl2_file", ""),
                        "has_light": "yes",
                        "condition": row.get("condition", ""),
                        "event_group": row.get("event_group", ""),
                        "reason": "missing_summary_file",
                        "message": "Expected PreLightPostSummary.csv not found.",
                    }
                )
    for file_id in sorted(skipped_file_ids - set(stim_df["file_id"].astype(str).tolist() if not stim_df.empty else [])):
        records.append(
            {
                "file_id": file_id,
                "pl2_file": "",
                "has_light": "no",
                "condition": "",
                "event_group": "nolight",
                "reason": "no_light_control",
                "message": "No light event; pre/light/post statistics not applicable.",
            }
        )
    return records


def _activity_filter_cfg(config: dict) -> dict:
    cfg = _stats_cfg(config).get("activity_filter", {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "min_max_window_hz": float(cfg.get("min_max_window_hz", 0.5)),
        "min_total_expected_spikes": float(cfg.get("min_total_expected_spikes", 10)),
    }


def _duration_from_window(df: pd.DataFrame, start_col: str, end_col: str) -> pd.Series:
    start = pd.to_numeric(df.get(start_col, pd.Series(pd.NA, index=df.index)), errors="coerce")
    end = pd.to_numeric(df.get(end_col, pd.Series(pd.NA, index=df.index)), errors="coerce")
    duration = end - start
    fallback = pd.to_numeric(df.get("duration_s", pd.Series(pd.NA, index=df.index)), errors="coerce")
    return duration.where(duration.notna(), fallback)


def _no_light_mask(df: pd.DataFrame) -> pd.Series:
    has_light = df.get("has_light", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    event_group = df.get("event_group", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    return has_light.isin({"no", "false", "0"}) | event_group.isin({"nolight", "no_light"})


def _append_activity_qc(
    df: pd.DataFrame,
    config: dict,
    logger: PipelineLogger,
    *,
    reason_override: str | None = None,
) -> pd.DataFrame:
    result = _ensure_columns(df, WIDE_COLUMNS) if not df.empty else _empty_frame(WIDE_COLUMNS)
    for column in ["baseline_hz", "light_hz", "post_hz", "duration_s"]:
        if column not in result.columns:
            logger.log("prelightpost_stats", "*", "", "", "warning", f"Required column missing for QC: {column}")
    result = result.copy()
    result["pre_hz"] = pd.to_numeric(result["baseline_hz"], errors="coerce")
    result["light_hz"] = pd.to_numeric(result["light_hz"], errors="coerce")
    result["post_hz"] = pd.to_numeric(result["post_hz"], errors="coerce")
    result["max_window_hz"] = result[["pre_hz", "light_hz", "post_hz"]].max(axis=1, skipna=True)
    result["pre_duration_s"] = _duration_from_window(result, "baseline_window_start_s", "baseline_window_end_s")
    result["light_duration_s"] = _duration_from_window(result, "light_window_start_s", "light_window_end_s")
    result["post_duration_s"] = _duration_from_window(result, "post_window_start_s", "post_window_end_s")
    result["pre_expected_spikes"] = result["pre_hz"] * result["pre_duration_s"]
    result["light_expected_spikes"] = result["light_hz"] * result["light_duration_s"]
    result["post_expected_spikes"] = result["post_hz"] * result["post_duration_s"]
    result["total_expected_spikes"] = result[["pre_expected_spikes", "light_expected_spikes", "post_expected_spikes"]].sum(axis=1, min_count=3)

    filter_cfg = _activity_filter_cfg(config)
    min_max = filter_cfg["min_max_window_hz"]
    min_spikes = filter_cfg["min_total_expected_spikes"]
    reasons: list[str] = []
    passes: list[str] = []
    no_light = _no_light_mask(result)
    missing_required = result[["pre_hz", "light_hz", "post_hz", "pre_duration_s", "light_duration_s", "post_duration_s", "total_expected_spikes"]].isna().any(axis=1)
    if reason_override is None and missing_required.any():
        logger.log("prelightpost_stats", "*", "", "", "warning", f"QC missing required rate or duration values in {int(missing_required.sum())} wide row(s).")
    for idx, row in result.iterrows():
        row_reasons: list[str] = []
        if reason_override:
            row_reasons = [reason_override]
        elif bool(no_light.loc[idx]):
            row_reasons = ["no_light_control"]
        elif bool(missing_required.loc[idx]):
            row_reasons = ["missing_required_values"]
        else:
            if float(row["max_window_hz"]) < min_max:
                row_reasons.append("low_max_window_hz")
            if float(row["total_expected_spikes"]) < min_spikes:
                row_reasons.append("low_total_expected_spikes")
        passes.append("yes" if not row_reasons else "no")
        reasons.append(";".join(row_reasons) if row_reasons else "pass")
    result["activity_filter_pass"] = passes
    result["activity_filter_reason"] = reasons
    return result[[*WIDE_COLUMNS, *QC_COLUMNS]]


def _qc_rows_from_skipped(skipped_rows: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for row in skipped_rows:
        reason = str(row.get("reason", ""))
        if reason != "no_light_control":
            continue
        record = {column: pd.NA for column in WIDE_COLUMNS}
        for column in ["file_id", "pl2_file", "condition", "event_group", "has_light"]:
            record[column] = row.get(column, pd.NA)
        record["activity_filter_reason"] = "no_light_control"
        rows.append(record)
    return pd.DataFrame(rows) if rows else _empty_frame([*WIDE_COLUMNS, "activity_filter_reason"])


def _write_table_or_preserve_identical_locked(df: pd.DataFrame, path: Path) -> None:
    try:
        write_table(df, path)
        return
    except PermissionError:
        if path.suffix.lower() != ".csv" or not path.exists():
            raise
        existing = path.read_text(encoding="utf-8-sig")
        expected = df.to_csv(index=False)
        if existing.lstrip("\ufeff").replace("\r\n", "\n") == expected.replace("\r\n", "\n"):
            return
        raise


def _write_outputs(
    *,
    config: dict,
    logger: PipelineLogger,
    output_dir: Path,
    wide_df: pd.DataFrame,
    wide_qc_df: pd.DataFrame,
    qc_excluded_df: pd.DataFrame,
    long_df: pd.DataFrame,
    skipped_df: pd.DataFrame,
) -> dict[str, Path]:
    cfg = _stats_cfg(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    long_name = cfg.get("output_long_csv", "all_units_pre_light_post_long.csv")
    excel_name = cfg.get("output_excel", "all_units_pre_light_post_statistics.xlsx")
    paths = {
        "long": output_dir / long_name if long_name else None,
        "wide": output_dir / cfg.get("output_wide_csv", "all_units_pre_light_post_wide.csv"),
        "wide_qc": output_dir / cfg.get("output_wide_qc_csv", "all_units_pre_light_post_wide_qc.csv"),
        "qc_excluded": output_dir / cfg.get("output_qc_excluded_csv", "all_units_pre_light_post_qc_excluded.csv"),
        "skipped": output_dir / "skipped_or_missing_prelightpost.csv",
        "excel": output_dir / excel_name if excel_name else None,
    }
    legacy_outputs = [
        output_dir / "all_units_pre_light_post_summary_by_file.csv",
        output_dir / "all_units_pre_light_post_summary_by_condition.csv",
    ]
    for path in legacy_outputs:
        if path.exists():
            path.unlink()
    try:
        if paths["long"] is not None:
            _write_table_or_preserve_identical_locked(long_df, paths["long"])
        _write_table_or_preserve_identical_locked(wide_df, paths["wide"])
        _write_table_or_preserve_identical_locked(wide_qc_df, paths["wide_qc"])
        _write_table_or_preserve_identical_locked(qc_excluded_df, paths["qc_excluded"])
        _write_table_or_preserve_identical_locked(skipped_df, paths["skipped"])
    except Exception as exc:
        logger.log("prelightpost_stats", "*", str(output_dir), str(output_dir), "failed", "Failed to write prelightpost statistics CSV output.", exc)
        raise
    if paths["excel"] is not None:
        try:
            with pd.ExcelWriter(paths["excel"]) as writer:
                wide_df.to_excel(writer, sheet_name="wide", index=False)
                wide_qc_df.to_excel(writer, sheet_name="wide_qc", index=False)
                qc_excluded_df.to_excel(writer, sheet_name="qc_excluded", index=False)
                skipped_df.to_excel(writer, sheet_name="skipped_or_missing", index=False)
                if paths["long"] is not None:
                    long_df.to_excel(writer, sheet_name="long", index=False)
        except Exception as exc:
            logger.log("prelightpost_stats", "*", str(output_dir), str(paths["excel"]), "failed", "Excel write failed for prelightpost statistics.", exc)
            raise
    return paths


def build_prelightpost_statistics(config: dict, logger: PipelineLogger) -> Path:
    paths = resolve_project_paths(config)
    cfg = _stats_cfg(config)
    output_dir = _output_dir(config, paths)
    input_dir = _prelightpost_input_dir(config, paths)
    stim_df = _read_optional_stim(config, paths, logger)
    unit_df = _read_optional_unit_table(config, paths, logger)

    normal_files = _normal_summary_files(config, input_dir)
    skipped_files = _skipped_summary_files(input_dir)
    loaded_frames = []
    for path in normal_files:
        file_id = _file_id_from_summary_path(path, config)
        loaded_frames.append(_read_summary_file(path, file_id, logger))
    summary_df = pd.concat(loaded_frames, ignore_index=True) if loaded_frames else _empty_frame([*CORE_SUMMARY_COLUMNS, "source_summary_file"])
    summary_df = _filter_rows_by_aggregation(summary_df, config)
    cohort = None
    if not summary_df.empty:
        cohort = select_unit_cohort(
            config,
            summary_df[["file_id", "unit_id"]],
            module="prelightpost_stats",
            logger=logger,
            duplicate_policy=cfg.get("duplicate_policy", "keep_all"),
        )
        if not config.get("run", {}).get("dry_run", False):
            write_cohort_metadata(cohort, output_dir)
    normal_file_ids = set(summary_df["file_id"].astype(str).tolist()) if not summary_df.empty else set()
    skipped_rows = _build_skipped_records(config=config, stim_df=stim_df, normal_file_ids=normal_file_ids, skipped_files=skipped_files)
    if cfg.get("fail_on_missing_light_summary", False):
        missing_light = [row for row in skipped_rows if row.get("reason") == "missing_summary_file"]
        if missing_light:
            raise FileNotFoundError(f"Missing PreLightPostSummary files for light file_id(s): {[row['file_id'] for row in missing_light]}")

    wide_df = _merge_metadata(summary_df, stim_df)
    wide_df = _apply_configured_window_metadata(wide_df, config, logger)
    wide_df = _compute_derived_metrics(wide_df) if cfg.get("compute_derived_metrics", True) else wide_df
    wide_df, unit_excluded_df = _apply_unit_filters(wide_df, unit_df, config, skipped_rows)
    for column in WIDE_COLUMNS:
        if column not in wide_df.columns:
            wide_df[column] = pd.NA
    wide_df = wide_df[WIDE_COLUMNS]
    long_df = _build_long_table(wide_df)
    wide_with_qc = _append_activity_qc(wide_df, config, logger)
    filter_cfg = _activity_filter_cfg(config)
    if filter_cfg["enabled"]:
        wide_qc_df = wide_with_qc[wide_with_qc["activity_filter_pass"].eq("yes")].copy()
        activity_excluded_df = wide_with_qc[wide_with_qc["activity_filter_pass"].eq("no")].copy()
    else:
        wide_qc_df = wide_with_qc.copy()
        activity_excluded_df = _empty_frame([*WIDE_COLUMNS, *QC_COLUMNS])
    unit_excluded_qc = _append_activity_qc(
        unit_excluded_df.drop(columns=[column for column in QC_COLUMNS if column in unit_excluded_df.columns], errors="ignore"),
        config,
        logger,
        reason_override=unit_excluded_df["activity_filter_reason"].iloc[0] if len(unit_excluded_df["activity_filter_reason"].dropna().unique()) == 1 else None,
    ) if not unit_excluded_df.empty else _empty_frame([*WIDE_COLUMNS, *QC_COLUMNS])
    if not unit_excluded_df.empty and len(unit_excluded_df["activity_filter_reason"].dropna().unique()) > 1:
        unit_excluded_qc = pd.concat(
            [
                _append_activity_qc(group.drop(columns=[column for column in QC_COLUMNS if column in group.columns], errors="ignore"), config, logger, reason_override=str(reason))
                for reason, group in unit_excluded_df.groupby("activity_filter_reason", dropna=False)
            ],
            ignore_index=True,
            sort=False,
        )
    skipped_no_light_qc = _append_activity_qc(_qc_rows_from_skipped(skipped_rows), config, logger, reason_override="no_light_control")
    excluded_parts = [df for df in [activity_excluded_df, unit_excluded_qc, skipped_no_light_qc] if not df.empty]
    if excluded_parts:
        qc_excluded_df = pd.DataFrame([record for df in excluded_parts for record in df.to_dict("records")])
    else:
        qc_excluded_df = _empty_frame([*WIDE_COLUMNS, *QC_COLUMNS])
    qc_excluded_df = _ensure_columns(qc_excluded_df, [*WIDE_COLUMNS, *QC_COLUMNS])
    skipped_df = pd.DataFrame(skipped_rows)
    for column in STATUS_COLUMNS:
        if column not in skipped_df.columns:
            skipped_df[column] = pd.NA
    output_paths = _write_outputs(
        config=config,
        logger=logger,
        output_dir=output_dir,
        wide_df=wide_df,
        wide_qc_df=wide_qc_df,
        qc_excluded_df=qc_excluded_df,
        long_df=long_df,
        skipped_df=skipped_df,
    )
    message = (
        "Built pre/light/post wide table and QC-filtered wide table. "
        f"QC rule: max(pre_hz, light_hz, post_hz) >= {filter_cfg['min_max_window_hz']:g} Hz "
        f"and total_expected_spikes >= {filter_cfg['min_total_expected_spikes']:g}."
    )
    logger.log(
        "prelightpost_stats",
        "*",
        str(input_dir),
        str(output_dir),
        "success",
        message,
        n_wide_rows_raw=len(wide_df),
        n_wide_rows_qc_pass=len(wide_qc_df),
        n_wide_rows_qc_excluded=len(qc_excluded_df),
        min_max_window_hz=filter_cfg["min_max_window_hz"],
        min_total_expected_spikes=filter_cfg["min_total_expected_spikes"],
        output_wide=str(output_paths["wide"]),
        output_wide_qc=str(output_paths["wide_qc"]),
        output_qc_excluded=str(output_paths["qc_excluded"]),
    )
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build all-unit pre/light/post statistics tables from existing aligned-rate exports.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        build_prelightpost_statistics(config=config, logger=logger)
        return 0
    except Exception as exc:
        logger.log("prelightpost_stats", "*", str(Path(args.config).resolve()), "", "failed", "prelightpost statistics terminated with an exception.", exc)
        return 1
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
