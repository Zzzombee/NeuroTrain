from __future__ import annotations

from pathlib import Path

import pandas as pd
from io import StringIO


TRUE_VALUES = {"1", "true", "yes", "y", "include"}
FALSE_VALUES = {"0", "false", "no", "n", "exclude", ""}


def read_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
        return _normalize_identifier_columns(df)
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
        return _normalize_identifier_columns(df)
    raise ValueError(f"Unsupported table format: {path}")


def write_table(df: pd.DataFrame, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = _normalize_identifier_columns(df)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False)
    elif path.suffix.lower() in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
    else:
        raise ValueError(f"Unsupported output table format: {path}")


def parse_bool(value) -> bool:
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError(f"Cannot parse boolean value from {value!r}")


def normalize_include_column(df: pd.DataFrame) -> pd.DataFrame:
    required = {"unit_id", "include"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required unit quality columns: {sorted(missing)}")
    result = df.copy()
    if "file_id" in result.columns:
        result["file_id"] = result["file_id"].map(_normalize_file_id_cell)
    # Downstream Unit eligibility is intentionally stricter than general boolean
    # parsing: only the literal reviewed value "yes" is eligible.
    result["include_bool"] = result["include"].map(
        lambda value: False if pd.isna(value) else str(value).strip().lower() == "yes"
    )
    for column in ["file_id", "channel", "original_name", "exclusion_reason", "representative_unit", "duplicate_of", "note"]:
        if column not in result.columns:
            result[column] = ""
    return result


def normalize_stim_schedule(df: pd.DataFrame, file_id_column: str = "file_id") -> pd.DataFrame:
    required = {file_id_column, "pl2_file"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required stim schedule columns: {sorted(missing)}")
    result = df.copy()
    result[file_id_column] = result[file_id_column].map(_normalize_file_id_cell)
    for column in ["event_group", "has_light", "light_on_s", "duration_s", "light_off_s", "condition", "note"]:
        if column not in result.columns:
            result[column] = ""
    has_light_series = result["has_light"].astype(str).str.strip().str.lower()
    if (has_light_series == "").all():
        inferred = result["event_group"].astype(str).str.strip().str.lower().ne("nolight")
        has_light_series = inferred.map({True: "yes", False: "no"})
    result["has_light"] = has_light_series.map(lambda value: "no" if value in {"no", "false", "0", "nolight"} else "yes")
    result["has_light_bool"] = result["has_light"] == "yes"
    result["light_on_s"] = pd.to_numeric(result["light_on_s"], errors="coerce")
    result["duration_s"] = pd.to_numeric(result["duration_s"], errors="coerce")
    light_off_numeric = pd.to_numeric(result["light_off_s"], errors="coerce")
    light_mask = result["has_light"] == "yes"
    if result.loc[light_mask, "light_on_s"].isna().any():
        raise ValueError("Stim schedule rows with has_light=yes must include light_on_s.")
    if result.loc[light_mask, "duration_s"].isna().any():
        raise ValueError("Stim schedule rows with has_light=yes must include duration_s.")
    result["light_off_s"] = light_off_numeric
    result.loc[light_mask, "light_off_s"] = result.loc[light_mask, "light_off_s"].fillna(
        result.loc[light_mask, "light_on_s"] + result.loc[light_mask, "duration_s"]
    )
    result.loc[~light_mask, ["light_on_s", "duration_s", "light_off_s"]] = pd.NA
    return result


def _normalize_file_id_cell(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit() and len(text) == 1:
        return text.zfill(2)
    return text


def _normalize_identifier_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in ["file_id", "file_index"]:
        if column in result.columns:
            result[column] = result[column].map(_normalize_file_id_cell)
    return result


def _infer_time_column(df: pd.DataFrame, candidates: list[str]) -> str:
    lowered = {column.lower(): column for column in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return df.columns[0]


def _infer_bin_width(centers: pd.Series) -> float:
    unique_times = sorted(pd.Series(centers).dropna().astype(float).unique().tolist())
    if len(unique_times) > 1:
        return float(unique_times[1] - unique_times[0])
    return 1.0


def convert_wide_psth_to_long(df: pd.DataFrame, file_id: str) -> pd.DataFrame:
    time_column = _infer_time_column(
        df,
        ["bin_center_s", "bin_center", "time_s", "time", "x"],
    )
    wide_df = df.copy()
    wide_df[time_column] = pd.to_numeric(wide_df[time_column], errors="raise")
    long_df = wide_df.melt(id_vars=[time_column], var_name="unit_id", value_name="firing_rate_hz")
    long_df["firing_rate_hz"] = pd.to_numeric(long_df["firing_rate_hz"], errors="coerce")
    long_df = long_df.dropna(subset=["firing_rate_hz"]).copy()
    bin_width = _infer_bin_width(long_df[time_column])
    long_df["file_id"] = file_id
    long_df["bin_center_s"] = long_df[time_column]
    long_df["bin_start_s"] = long_df["bin_center_s"] - bin_width / 2
    long_df["bin_end_s"] = long_df["bin_center_s"] + bin_width / 2
    long_df["spike_count"] = pd.NA
    long_df["n_events"] = pd.NA
    return long_df[
        ["file_id", "unit_id", "bin_start_s", "bin_end_s", "bin_center_s", "firing_rate_hz", "spike_count", "n_events"]
    ]


def convert_wide_fullrate_to_long(df: pd.DataFrame, file_id: str) -> pd.DataFrame:
    time_column = _infer_time_column(
        df,
        ["time_bin_center_s", "bin_center_s", "time_s", "time", "x"],
    )
    wide_df = df.copy()
    wide_df[time_column] = pd.to_numeric(wide_df[time_column], errors="raise")
    long_df = wide_df.melt(id_vars=[time_column], var_name="unit_id", value_name="firing_rate_hz")
    long_df["firing_rate_hz"] = pd.to_numeric(long_df["firing_rate_hz"], errors="coerce")
    long_df = long_df.dropna(subset=["firing_rate_hz"]).copy()
    bin_width = _infer_bin_width(long_df[time_column])
    long_df["file_id"] = file_id
    long_df["time_bin_center_s"] = long_df[time_column]
    long_df["time_bin_start_s"] = long_df["time_bin_center_s"] - bin_width / 2
    long_df["time_bin_end_s"] = long_df["time_bin_center_s"] + bin_width / 2
    long_df["spike_count"] = pd.NA
    return long_df[
        ["file_id", "unit_id", "time_bin_start_s", "time_bin_end_s", "time_bin_center_s", "firing_rate_hz", "spike_count"]
    ]


def convert_rate_export_to_long(df: pd.DataFrame, file_id: str, kind: str) -> pd.DataFrame:
    """
    Normalize either a long-table export or a simple wide-table export into the pipeline format.
    """

    if kind == "psth":
        canonical_cols = {
            "file_id",
            "unit_id",
            "bin_start_s",
            "bin_end_s",
            "bin_center_s",
            "firing_rate_hz",
            "spike_count",
            "n_events",
        }
        time_start = "bin_start_s"
        time_end = "bin_end_s"
        time_center = "bin_center_s"
    else:
        canonical_cols = {
            "file_id",
            "unit_id",
            "time_bin_start_s",
            "time_bin_end_s",
            "time_bin_center_s",
            "firing_rate_hz",
            "spike_count",
        }
        time_start = "time_bin_start_s"
        time_end = "time_bin_end_s"
        time_center = "time_bin_center_s"

    if canonical_cols.issubset(set(df.columns)):
        return df.copy()
    if kind == "psth":
        return convert_wide_psth_to_long(df, file_id=file_id)
    return convert_wide_fullrate_to_long(df, file_id=file_id)


def read_delimited_text_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in raw_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Raw numerical results file is empty: {path}")

    parsers = [
        lambda text: pd.read_csv(StringIO(text), sep="\t"),
        lambda text: pd.read_csv(StringIO(text)),
        lambda text: pd.read_csv(StringIO(text), sep=r"\s+", engine="python"),
    ]
    for parser in parsers:
        try:
            df = parser("\n".join(lines))
            if not df.empty and len(df.columns) >= 2:
                return df
        except Exception:
            continue
    raise ValueError(f"Unable to parse raw numerical results table: {path}")
