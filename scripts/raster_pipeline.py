from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml
from matplotlib.patches import Rectangle


SUPPORTED_SCHEMA_VERSION = 1
TIME_UNIT_TO_SECONDS = {"seconds": 1.0, "milliseconds": 0.001, "minutes": 60.0}
BOUNDARIES = {"left_closed_right_open", "closed", "open", "left_open_right_closed"}


class RasterConfigError(ValueError):
    pass


class RasterInputError(ValueError):
    pass


@dataclass
class RasterPaths:
    input_root: Path
    output_root: Path
    spike_table_glob: str
    event_table_glob: str | None
    output_subdir: str = "raster"

    @property
    def raster_root(self) -> Path:
        return self.output_root / self.output_subdir


@dataclass
class RasterConfig:
    schema_version: int
    config_path: Path
    paths: RasterPaths
    input: dict[str, Any]
    alignment: dict[str, Any]
    trial_filter: dict[str, Any]
    plot: dict[str, Any]
    output: dict[str, Any]
    runtime: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)


def _require_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise RasterConfigError(f"Config key {key!r} must be a mapping; got {type(value).__name__}.")
    return value


def _as_path(base: Path, value: str, key: str) -> Path:
    if not value:
        raise RasterConfigError(f"Config key {key} must be a non-empty path.")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _require_positive_number(value: Any, key: str) -> float:
    try:
        number = float(value)
    except Exception as exc:
        raise RasterConfigError(f"Config key {key} must be a positive number; got {value!r}.") from exc
    if not math.isfinite(number) or number <= 0:
        raise RasterConfigError(f"Config key {key} must be a positive finite number; got {value!r}.")
    return number


def _load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_raster_config(config_path: Path) -> RasterConfig:
    config_path = Path(config_path).expanduser().resolve()
    raw = _load_yaml(config_path)
    schema_version = raw.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise RasterConfigError(f"Config key schema_version must be {SUPPORTED_SCHEMA_VERSION}; got {schema_version!r}.")

    paths_raw = _require_mapping(raw, "paths")
    input_raw = _require_mapping(raw, "input")
    alignment_raw = _require_mapping(raw, "alignment")
    plot_raw = _require_mapping(raw, "plot")
    output_raw = _require_mapping(raw, "output")
    runtime_raw = _require_mapping(raw, "runtime")
    trial_filter_raw = raw.get("trial_filter") or {}
    if not isinstance(trial_filter_raw, dict):
        raise RasterConfigError("Config key trial_filter must be a mapping when provided.")

    config_dir = config_path.parent
    input_root = _as_path(config_dir, str(paths_raw.get("input_root", "")), "paths.input_root")
    output_root = _as_path(config_dir, str(paths_raw.get("output_root", "")), "paths.output_root")
    if not input_root.exists():
        raise RasterConfigError(f"Config key paths.input_root points to a missing directory: {input_root}")
    try:
        output_root.resolve().relative_to(input_root.resolve())
    except ValueError:
        pass
    else:
        raise RasterConfigError("Config keys paths.output_root and paths.input_root would make output recursively discoverable.")

    spike_glob = paths_raw.get("spike_table_glob")
    if not spike_glob:
        raise RasterConfigError("Config key paths.spike_table_glob must be non-empty.")
    event_glob = paths_raw.get("event_table_glob")
    raster_paths = RasterPaths(
        input_root=input_root,
        output_root=output_root,
        spike_table_glob=str(spike_glob),
        event_table_glob=None if event_glob in {None, ""} else str(event_glob),
        output_subdir=str(paths_raw.get("output_subdir", "raster")),
    )

    if input_raw.get("format", "neuroexplorer_long_csv") not in {"neuroexplorer_long_csv", "long_csv"}:
        raise RasterConfigError(f"Config key input.format has unsupported value: {input_raw.get('format')!r}.")
    if input_raw.get("time_unit") not in TIME_UNIT_TO_SECONDS:
        raise RasterConfigError(f"Config key input.time_unit has unsupported value: {input_raw.get('time_unit')!r}.")
    columns = input_raw.get("columns")
    if not isinstance(columns, dict):
        raise RasterConfigError("Config key input.columns must be a mapping.")
    for key in ["session_id", "unit_id", "spike_time", "event_name", "event_time"]:
        if not columns.get(key):
            raise RasterConfigError(f"Config key input.columns.{key} must be non-empty.")

    window = alignment_raw.get("window_s")
    if not isinstance(window, list) or len(window) != 2:
        raise RasterConfigError(f"Config key alignment.window_s must be [start, end]; got {window!r}.")
    start, end = float(window[0]), float(window[1])
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise RasterConfigError(f"Config key alignment.window_s must satisfy start < end; got {window!r}.")
    if not str(alignment_raw.get("event_name", "")).strip():
        raise RasterConfigError("Config key alignment.event_name must be non-empty.")
    if alignment_raw.get("boundary", "left_closed_right_open") not in BOUNDARIES:
        raise RasterConfigError(f"Config key alignment.boundary has unsupported value: {alignment_raw.get('boundary')!r}.")
    if alignment_raw.get("overlapping_windows", "allow") not in {"allow", "error"}:
        raise RasterConfigError(f"Config key alignment.overlapping_windows must be 'allow' or 'error'; got {alignment_raw.get('overlapping_windows')!r}.")
    if alignment_raw.get("trial_order", "event_time") != "event_time":
        raise RasterConfigError(
            f"Config key alignment.trial_order currently supports only 'event_time'; got {alignment_raw.get('trial_order')!r}."
        )
    if alignment_raw.get("missing_event", "error") != "error":
        raise RasterConfigError(
            f"Config key alignment.missing_event currently supports only 'error'; got {alignment_raw.get('missing_event')!r}."
        )
    minimum_interval = alignment_raw.get("minimum_inter_event_interval_s")
    if minimum_interval is not None:
        _require_positive_number(minimum_interval, "alignment.minimum_inter_event_interval_s")
    if alignment_raw.get("light_off_event_name") and alignment_raw.get("fixed_stimulus_duration_s") is not None:
        raise RasterConfigError("Config keys alignment.light_off_event_name and alignment.fixed_stimulus_duration_s are mutually exclusive.")
    if alignment_raw.get("light_off_event_name"):
        raise RasterConfigError(
            "Config key alignment.light_off_event_name is not supported until real NeuroExplorer off-event pairing is validated; "
            "set it to null or configure alignment.fixed_stimulus_duration_s explicitly."
        )
    if alignment_raw.get("fixed_stimulus_duration_s") is not None:
        _require_positive_number(alignment_raw["fixed_stimulus_duration_s"], "alignment.fixed_stimulus_duration_s")

    formats = plot_raw.get("formats", ["png"])
    if not formats or any(fmt not in {"png", "svg", "pdf"} for fmt in formats):
        raise RasterConfigError(f"Config key plot.formats must contain png, svg, or pdf; got {formats!r}.")
    _require_positive_number(plot_raw.get("dpi", 300), "plot.dpi")
    figsize = plot_raw.get("figsize_inches", [10.0, 6.0])
    if not isinstance(figsize, list) or len(figsize) != 2:
        raise RasterConfigError(f"Config key plot.figsize_inches must be [width, height]; got {figsize!r}.")
    _require_positive_number(figsize[0], "plot.figsize_inches[0]")
    _require_positive_number(figsize[1], "plot.figsize_inches[1]")
    _require_positive_number(plot_raw.get("combined_width_inches", figsize[0]), "plot.combined_width_inches")
    _require_positive_number(plot_raw.get("combined_row_height_inches", 0.45), "plot.combined_row_height_inches")
    _require_positive_number(plot_raw.get("combined_min_height_inches", 4.0), "plot.combined_min_height_inches")
    _require_positive_number(plot_raw.get("spike_linewidth", 0.6), "plot.spike_linewidth")
    _require_positive_number(plot_raw.get("spike_height_fraction", 0.8), "plot.spike_height_fraction")
    _require_positive_number(plot_raw.get("alignment_linewidth", 1.0), "plot.alignment_linewidth")
    if output_raw.get("write_combined_figure", True) and not str(
        output_raw.get("combined_filename", "project_combined_raster")
    ).strip():
        raise RasterConfigError("Config key output.combined_filename must be non-empty when combined output is enabled.")

    include_ids = trial_filter_raw.get("include_trial_ids")
    exclude_ids = trial_filter_raw.get("exclude_trial_ids") or []
    if include_ids is not None and set(map(str, include_ids)).intersection(set(map(str, exclude_ids))):
        raise RasterConfigError("Config keys trial_filter.include_trial_ids and exclude_trial_ids overlap.")

    return RasterConfig(
        schema_version=schema_version,
        config_path=config_path,
        paths=raster_paths,
        input=input_raw,
        alignment=alignment_raw,
        trial_filter=trial_filter_raw,
        plot=plot_raw,
        output=output_raw,
        runtime=runtime_raw,
        raw=raw,
    )


def discover_inputs(config: RasterConfig) -> tuple[list[Path], list[Path]]:
    spike_files = sorted(config.paths.input_root.glob(config.paths.spike_table_glob))
    if not spike_files:
        raise RasterInputError(f"No spike tables matched {config.paths.spike_table_glob!r} under {config.paths.input_root}.")
    if config.paths.event_table_glob is None:
        return spike_files, spike_files
    event_files = sorted(config.paths.input_root.glob(config.paths.event_table_glob))
    if not event_files:
        raise RasterInputError(f"No event tables matched {config.paths.event_table_glob!r} under {config.paths.input_root}.")
    return spike_files, event_files


def _read_csv(path: Path, config: RasterConfig) -> pd.DataFrame:
    delimiter = config.input.get("delimiter")
    sep = "," if delimiter in {None, ""} else delimiter
    return pd.read_csv(path, sep=sep, encoding=config.input.get("encoding", "utf-8-sig"))


def _require_columns(df: pd.DataFrame, columns: list[str], path: Path) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise RasterInputError(f"Missing required columns {missing} in {path}. Available columns: {list(df.columns)}")


def _source_session_id(path: Path, columns: dict[str, str], df: pd.DataFrame) -> str:
    session_column = columns["session_id"]
    if session_column in df.columns and df[session_column].notna().any():
        values = sorted(df[session_column].dropna().astype(str).str.strip().unique().tolist())
        if len(values) == 1:
            return values[0]
    return path.stem


def load_spike_table(path: Path, config: RasterConfig) -> pd.DataFrame:
    columns = config.input["columns"]
    df = _read_csv(path, config)
    _require_columns(df, [columns["session_id"], columns["unit_id"], columns["spike_time"]], path)
    scale = TIME_UNIT_TO_SECONDS[config.input["time_unit"]]
    result = df.copy()
    result["_source_file"] = str(path)
    result["session_id"] = result[columns["session_id"]].fillna("").astype(str).str.strip()
    result["unit_id"] = result[columns["unit_id"]].fillna("").astype(str).str.strip()
    if columns.get("channel_id") and columns["channel_id"] in result.columns:
        result["channel_id"] = result[columns["channel_id"]].fillna("").astype(str).str.strip()
    else:
        result["channel_id"] = ""
    result["_spike_numeric"] = pd.to_numeric(result[columns["spike_time"]], errors="coerce")
    non_numeric = result["_spike_numeric"].isna() & result[columns["spike_time"]].notna() & (result[columns["spike_time"]].astype(str).str.strip() != "")
    if non_numeric.any():
        examples = result.loc[non_numeric, columns["spike_time"]].head(3).tolist()
        raise RasterInputError(f"Non-numeric spike timestamps in {path}: {examples!r}")
    result = result.dropna(subset=["_spike_numeric"]).copy()
    result = result[(result["session_id"] != "") & (result["unit_id"] != "")].copy()
    if not result["_spike_numeric"].map(math.isfinite).all():
        raise RasterInputError(f"Non-finite spike timestamps in {path}.")
    result["spike_time_absolute_s"] = result["_spike_numeric"].astype(float) * scale
    result = result[["session_id", "unit_id", "channel_id", "spike_time_absolute_s", "_source_file"]]
    if result.empty:
        return result
    result["was_out_of_order"] = (
        result.groupby(["session_id", "unit_id"], sort=False)["spike_time_absolute_s"].diff().fillna(0) < 0
    )
    return result.sort_values(["session_id", "unit_id", "spike_time_absolute_s"], kind="mergesort").reset_index(drop=True)


def load_event_table(path: Path, config: RasterConfig) -> pd.DataFrame:
    columns = config.input["columns"]
    df = _read_csv(path, config)
    _require_columns(df, [columns["session_id"], columns["event_name"], columns["event_time"]], path)
    scale = TIME_UNIT_TO_SECONDS[config.input["time_unit"]]
    result = df.copy()
    result["_source_file"] = str(path)
    result["session_id"] = result[columns["session_id"]].fillna("").astype(str).str.strip()
    result["event_name"] = result[columns["event_name"]].fillna("").astype(str).str.strip()
    result["_event_numeric"] = pd.to_numeric(result[columns["event_time"]], errors="coerce")
    non_numeric = result["_event_numeric"].isna() & result[columns["event_time"]].notna() & (result[columns["event_time"]].astype(str).str.strip() != "")
    if non_numeric.any():
        examples = result.loc[non_numeric, columns["event_time"]].head(3).tolist()
        raise RasterInputError(f"Non-numeric event timestamps in {path}: {examples!r}")
    result = result.dropna(subset=["_event_numeric"]).copy()
    result = result[(result["session_id"] != "") & (result["event_name"] != "")].copy()
    if not result["_event_numeric"].map(math.isfinite).all():
        raise RasterInputError(f"Non-finite event timestamps in {path}.")
    result["event_time_absolute_s"] = result["_event_numeric"].astype(float) * scale
    trial_col = columns.get("trial_id")
    if trial_col and trial_col in result.columns:
        result["trial_id"] = result[trial_col].fillna("").astype(str).str.strip()
    else:
        result["trial_id"] = ""
    duration_col = columns.get("stimulus_duration")
    if duration_col and duration_col in result.columns:
        duration_numeric = pd.to_numeric(result[duration_col], errors="coerce")
        invalid_duration = (
            duration_numeric.isna()
            & result[duration_col].notna()
            & (result[duration_col].astype(str).str.strip() != "")
        )
        if invalid_duration.any():
            examples = result.loc[invalid_duration, duration_col].head(3).tolist()
            raise RasterInputError(f"Non-numeric stimulus durations in {path}: {examples!r}")
        finite_duration = duration_numeric.dropna().map(math.isfinite)
        if not finite_duration.all() or (duration_numeric.dropna() <= 0).any():
            raise RasterInputError(f"Stimulus durations must be positive finite values in {path}.")
        result["stimulus_duration_s"] = duration_numeric.astype(float) * scale
    else:
        result["stimulus_duration_s"] = float("nan")
    return result[
        ["session_id", "event_name", "event_time_absolute_s", "trial_id", "stimulus_duration_s", "_source_file"]
    ].sort_values(
        ["session_id", "event_name", "event_time_absolute_s"], kind="mergesort"
    )


def load_inputs(config: RasterConfig) -> tuple[pd.DataFrame, pd.DataFrame, list[Path], list[Path]]:
    spike_files, event_files = discover_inputs(config)
    spikes = pd.concat([load_spike_table(path, config) for path in spike_files], ignore_index=True)
    events = pd.concat([load_event_table(path, config) for path in event_files], ignore_index=True)
    if spikes.empty:
        raise RasterInputError("All matched spike tables were empty after numeric timestamp parsing.")
    channel_counts = (
        spikes[spikes["channel_id"] != ""]
        .groupby(["session_id", "unit_id"])["channel_id"]
        .nunique()
    )
    conflicting_channels = channel_counts[channel_counts > 1]
    if not conflicting_channels.empty:
        raise RasterInputError(
            "A session_id + unit_id maps to multiple channel_id values: "
            f"{[list(key) for key in conflicting_channels.index.tolist()[:5]]}"
        )
    return spikes, events, spike_files, event_files


def _include_relative_time(value: float, start: float, end: float, boundary: str) -> bool:
    if boundary == "left_closed_right_open":
        return start <= value < end
    if boundary == "closed":
        return start <= value <= end
    if boundary == "open":
        return start < value < end
    if boundary == "left_open_right_closed":
        return start < value <= end
    raise AssertionError(boundary)


def _apply_trial_filter(trials: pd.DataFrame, config: RasterConfig) -> pd.DataFrame:
    result = trials.copy()
    first = config.trial_filter.get("first_trial")
    last = config.trial_filter.get("last_trial")
    if first is not None:
        result = result[result["trial_index"] >= int(first)]
    if last is not None:
        result = result[result["trial_index"] <= int(last)]
    include = config.trial_filter.get("include_trial_ids")
    if include is not None:
        result = result[result["trial_id"].astype(str).isin(set(map(str, include)))]
    exclude = config.trial_filter.get("exclude_trial_ids") or []
    if exclude:
        result = result[~result["trial_id"].astype(str).isin(set(map(str, exclude)))]
    return result.reset_index(drop=True)


def build_trials(events: pd.DataFrame, config: RasterConfig) -> pd.DataFrame:
    event_name = str(config.alignment["event_name"])
    selected = events[events["event_name"] == event_name].copy()
    if selected.empty:
        searched = sorted(events["_source_file"].dropna().unique().tolist())
        raise RasterInputError(f"Missing alignment event {event_name!r}. Searched event files: {searched}")
    selected = selected.sort_values(["session_id", "event_time_absolute_s"], kind="mergesort")
    generated = []
    for session_id, sub in selected.groupby("session_id", sort=False):
        for idx, (_, row) in enumerate(sub.iterrows(), start=1):
            trial_id = row["trial_id"] or f"{session_id}_trial{idx:04d}"
            event_duration = row.get("stimulus_duration_s")
            generated.append(
                {
                    "session_id": session_id,
                    "trial_id": str(trial_id),
                    "trial_index": idx,
                    "event_name": row["event_name"],
                    "event_time_absolute_s": float(row["event_time_absolute_s"]),
                    "stimulus_duration_s": (
                        float(event_duration)
                        if event_duration is not None and pd.notna(event_duration)
                        else config.alignment.get("fixed_stimulus_duration_s")
                    ),
                    "source_event_file": row["_source_file"],
                }
            )
    trials = _apply_trial_filter(pd.DataFrame(generated), config)
    if trials.empty:
        raise RasterInputError("No trials remain after applying trial_filter.")
    duplicated_trial_ids = trials.duplicated(["session_id", "trial_id"], keep=False)
    if duplicated_trial_ids.any():
        examples = trials.loc[duplicated_trial_ids, ["session_id", "trial_id"]].drop_duplicates().head(5).values.tolist()
        raise RasterInputError(f"Duplicate trial_id values within a session: {examples}")
    start, end = map(float, config.alignment["window_s"])
    window_len = end - start
    overlap_flags = []
    min_interval = None
    for _session_id, sub in trials.groupby("session_id", sort=False):
        times = sub["event_time_absolute_s"].astype(float).tolist()
        intervals = [b - a for a, b in zip(times, times[1:])]
        if intervals:
            session_min = min(intervals)
            min_interval = session_min if min_interval is None else min(min_interval, session_min)
        for index, event_time in enumerate(times):
            overlap_flags.append(any(index != other_index and abs(event_time - other) < window_len for other_index, other in enumerate(times)))
    trials["overlaps_another_trial_window"] = overlap_flags
    configured_minimum = config.alignment.get("minimum_inter_event_interval_s")
    if configured_minimum is not None and min_interval is not None and min_interval < float(configured_minimum):
        raise RasterInputError(
            "Alignment events violate alignment.minimum_inter_event_interval_s: "
            f"minimum_inter_event_interval_s={min_interval}; configured_minimum_s={float(configured_minimum)}."
        )
    if config.alignment.get("overlapping_windows", "allow") == "error" and any(overlap_flags):
        raise RasterInputError(
            f"Overlapping trial windows detected. minimum_inter_event_interval_s={min_interval}; required_window_length_s={window_len}."
        )
    return trials


def align_unit_spikes(spikes: pd.DataFrame, trials: pd.DataFrame, config: RasterConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    start, end = map(float, config.alignment["window_s"])
    boundary = config.alignment.get("boundary", "left_closed_right_open")
    aligned_rows: list[dict[str, Any]] = []
    trial_rows: list[dict[str, Any]] = []
    for unit_key, unit_spikes in spikes.groupby(["session_id", "unit_id"], sort=True):
        session_id, unit_id = unit_key
        session_trials = trials[trials["session_id"] == session_id]
        spike_records = unit_spikes[["spike_time_absolute_s", "_source_file"]].itertuples(index=False, name=None)
        spike_records = [(float(spike_time), source_file) for spike_time, source_file in spike_records]
        for trial in session_trials.itertuples(index=False):
            n_spikes = 0
            for spike_time, source_file in spike_records:
                relative = spike_time - float(trial.event_time_absolute_s)
                if _include_relative_time(relative, start, end, boundary):
                    n_spikes += 1
                    aligned_rows.append(
                        {
                            "session_id": session_id,
                            "source_file": source_file,
                            "unit_id": unit_id,
                            "channel_id": unit_spikes["channel_id"].iloc[0],
                            "trial_id": trial.trial_id,
                            "trial_index": int(trial.trial_index),
                            "event_name": trial.event_name,
                            "event_time_absolute_s": float(trial.event_time_absolute_s),
                            "stimulus_duration_s": trial.stimulus_duration_s,
                            "spike_time_absolute_s": spike_time,
                            "spike_time_relative_s": relative,
                        }
                    )
            trial_rows.append(
                {
                    "session_id": session_id,
                    "unit_id": unit_id,
                    "trial_id": trial.trial_id,
                    "trial_index": int(trial.trial_index),
                    "event_time_absolute_s": float(trial.event_time_absolute_s),
                    "stimulus_duration_s": trial.stimulus_duration_s,
                    "source_event_file": trial.source_event_file,
                    "n_spikes_in_window": n_spikes,
                    "overlaps_another_trial_window": bool(trial.overlaps_another_trial_window),
                }
            )
    return pd.DataFrame(aligned_rows), pd.DataFrame(trial_rows)


def _safe_filename(value: str) -> str:
    raw = str(value).strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._") or "unnamed"
    if text != raw:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        return f"{text}_{digest}"
    return text


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        handle.write(text)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def _atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=path.parent, suffix=".tmp") as handle:
        df.to_csv(handle, index=False)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def _add_stimulus_rectangles(ax, rows: pd.DataFrame, y_column: str, plot_cfg: dict[str, Any]) -> None:
    color = plot_cfg.get("stimulus_band_color", "#B7C9E8")
    alpha = float(plot_cfg.get("stimulus_band_alpha", 0.25))
    for row in rows.itertuples(index=False):
        duration = getattr(row, "stimulus_duration_s", None)
        if duration is None or pd.isna(duration):
            continue
        y_position = float(getattr(row, y_column))
        ax.add_patch(
            Rectangle(
                (0.0, y_position - 0.5),
                float(duration),
                1.0,
                facecolor=color,
                edgecolor="none",
                alpha=alpha,
                zorder=0,
            )
        )


def _save_figure(fig, output_base: Path, config: RasterConfig) -> list[Path]:
    output_paths = []
    for fmt in config.plot.get("formats", ["png"]):
        path = output_base.with_suffix(f".{fmt}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(delete=False, dir=path.parent, suffix=f".{fmt}.tmp") as handle:
            tmp_path = Path(handle.name)
        try:
            fig.savefig(
                tmp_path,
                format=fmt,
                dpi=int(config.plot.get("dpi", 300)),
                transparent=bool(config.plot.get("transparent_background", False)),
            )
            tmp_path.replace(path)
        finally:
            tmp_path.unlink(missing_ok=True)
        output_paths.append(path)
    return output_paths


def render_raster(
    unit_aligned: pd.DataFrame,
    unit_trials: pd.DataFrame,
    config: RasterConfig,
    output_base: Path,
) -> list[Path]:
    plot_cfg = config.plot
    start, end = map(float, config.alignment["window_s"])
    trial_indices = unit_trials["trial_index"].astype(int).tolist()
    spike_groups = [
        unit_aligned.loc[unit_aligned["trial_index"] == trial_index, "spike_time_relative_s"].astype(float).tolist()
        for trial_index in trial_indices
    ]
    fig, ax = plt.subplots(figsize=tuple(plot_cfg.get("figsize_inches", [10.0, 6.0])))
    ax.eventplot(
        spike_groups,
        lineoffsets=trial_indices,
        linelengths=float(plot_cfg.get("spike_height_fraction", 0.8)),
        linewidths=float(plot_cfg.get("spike_linewidth", 0.6)),
        colors=plot_cfg.get("spike_color", "black"),
        zorder=2,
    )
    _add_stimulus_rectangles(ax, unit_trials, "trial_index", plot_cfg)
    if plot_cfg.get("show_alignment_line", True):
        ax.axvline(
            0,
            color=plot_cfg.get("alignment_line_color", "red"),
            linewidth=float(plot_cfg.get("alignment_linewidth", 1.0)),
            zorder=3,
        )
    ax.set_xlim(start, end)
    ax.set_ylim(0.5, max(trial_indices, default=1) + 0.5)
    ax.set_yticks(trial_indices)
    ax.invert_yaxis()
    ax.set_xlabel(plot_cfg.get("x_label", "Time from event (s)"))
    ax.set_ylabel(plot_cfg.get("y_label", "Trial"))
    if not unit_trials.empty:
        row = unit_trials.iloc[0]
        title = plot_cfg.get("title_template", "{session_id} | {unit_id} | aligned to {event_name}").format(
            session_id=row["session_id"],
            unit_id=row["unit_id"],
            event_name=config.alignment["event_name"],
        )
        ax.set_title(title)
    fig.tight_layout()
    output_paths = _save_figure(fig, output_base, config)
    plt.close(fig)
    return output_paths


def render_combined_raster(
    aligned: pd.DataFrame,
    trial_summary: pd.DataFrame,
    config: RasterConfig,
    output_base: Path,
) -> tuple[list[Path], pd.DataFrame]:
    plot_cfg = config.plot
    start, end = map(float, config.alignment["window_s"])
    row_records = []
    y_position = 1
    unit_ticks = []
    unit_labels = []
    for (session_id, unit_id), unit_trials in trial_summary.groupby(["session_id", "unit_id"], sort=True):
        unit_positions = []
        for trial in unit_trials.sort_values("trial_index", kind="mergesort").itertuples(index=False):
            unit_positions.append(y_position)
            row_records.append(
                {
                    "plot_row": y_position,
                    "session_id": session_id,
                    "unit_id": unit_id,
                    "trial_id": trial.trial_id,
                    "trial_index": int(trial.trial_index),
                    "event_time_absolute_s": float(trial.event_time_absolute_s),
                    "stimulus_duration_s": trial.stimulus_duration_s,
                    "n_spikes_in_window": int(trial.n_spikes_in_window),
                }
            )
            y_position += 1
        unit_ticks.append(sum(unit_positions) / len(unit_positions))
        unit_labels.append(f"{session_id} | {unit_id}")

    row_map = pd.DataFrame(row_records)
    spike_groups = []
    for row in row_map.itertuples(index=False):
        if aligned.empty:
            spike_groups.append([])
            continue
        mask = (
            (aligned["session_id"] == row.session_id)
            & (aligned["unit_id"] == row.unit_id)
            & (aligned["trial_id"] == row.trial_id)
        )
        spike_groups.append(aligned.loc[mask, "spike_time_relative_s"].astype(float).tolist())

    figure_width = float(plot_cfg.get("combined_width_inches", plot_cfg.get("figsize_inches", [10.0, 6.0])[0]))
    row_height = float(plot_cfg.get("combined_row_height_inches", 0.45))
    minimum_height = float(plot_cfg.get("combined_min_height_inches", 4.0))
    figure_height = max(minimum_height, len(row_map) * row_height + 1.8)
    fig, ax = plt.subplots(figsize=(figure_width, figure_height))
    ax.eventplot(
        spike_groups,
        lineoffsets=row_map["plot_row"].tolist(),
        linelengths=float(plot_cfg.get("spike_height_fraction", 0.8)),
        linewidths=float(plot_cfg.get("spike_linewidth", 0.6)),
        colors=plot_cfg.get("spike_color", "black"),
        zorder=2,
    )
    _add_stimulus_rectangles(ax, row_map, "plot_row", plot_cfg)
    if plot_cfg.get("show_alignment_line", True):
        ax.axvline(
            0,
            color=plot_cfg.get("alignment_line_color", "red"),
            linewidth=float(plot_cfg.get("alignment_linewidth", 1.0)),
            zorder=3,
        )
    ax.set_xlim(start, end)
    ax.set_ylim(0.5, max(row_map["plot_row"].tolist(), default=1) + 0.5)
    ax.set_yticks(unit_ticks, labels=unit_labels)
    ax.invert_yaxis()
    ax.set_xlabel(plot_cfg.get("x_label", "Time from event (s)"))
    ax.set_ylabel(plot_cfg.get("combined_y_label", "Session | Unit"))
    ax.set_title(plot_cfg.get("combined_title", f"Project raster | aligned to {config.alignment['event_name']}"))
    fig.tight_layout()
    output_paths = _save_figure(fig, output_base, config)
    plt.close(fig)
    return output_paths, row_map


def _check_output_conflicts(config: RasterConfig, paths: list[Path]) -> None:
    if config.output.get("overwrite", False):
        return
    conflicts = [path for path in paths if path.exists()]
    if conflicts:
        raise RasterInputError(f"Output conflict with output.overwrite=false: {conflicts[:5]}")


def run_raster_pipeline(config: RasterConfig, *, validate_only: bool = False, session: str | None = None, unit: str | None = None) -> dict[str, Any]:
    spikes, events, spike_files, event_files = load_inputs(config)
    if session:
        spikes = spikes[spikes["session_id"] == session]
        events = events[events["session_id"] == session]
    if unit:
        spikes = spikes[spikes["unit_id"] == unit]
    if spikes.empty:
        raise RasterInputError("No spike rows remain after session/unit filtering.")
    trials = build_trials(events, config)
    missing_event_sessions = sorted(set(spikes["session_id"]) - set(trials["session_id"]))
    if missing_event_sessions:
        raise RasterInputError(
            f"Missing alignment event {config.alignment['event_name']!r} for spike sessions: {missing_event_sessions}."
        )
    aligned, trial_summary = align_unit_spikes(spikes, trials, config)

    raster_root = config.paths.raster_root
    figure_root = raster_root / "figures"
    table_root = raster_root / "tables"
    log_path = raster_root / "raster.log"
    manifest_path = raster_root / "manifest.json"
    unit_summary_path = table_root / "unit_summary.csv"
    trial_summary_path = table_root / "trial_summary.csv"
    exclusions_path = table_root / "exclusions.csv"
    aligned_long_path = table_root / "aligned_spikes_long.csv"
    combined_row_map_path = table_root / "combined_row_map.csv"
    combined_filename = str(config.output.get("combined_filename", "project_combined_raster"))
    combined_base = figure_root / _safe_filename(combined_filename)

    expected_outputs = [log_path]
    if config.output.get("write_manifest_json", True):
        expected_outputs.append(manifest_path)
    if config.output.get("write_unit_summary_csv", True):
        expected_outputs.append(unit_summary_path)
    if config.output.get("write_trial_summary_csv", True):
        expected_outputs.append(trial_summary_path)
    if config.output.get("write_exclusion_csv", True):
        expected_outputs.append(exclusions_path)
    if config.output.get("write_aligned_spikes_long_csv", False):
        expected_outputs.append(aligned_long_path)
    if config.output.get("write_combined_row_map_csv", True):
        expected_outputs.append(combined_row_map_path)
    if config.output.get("write_combined_figure", True):
        for fmt in config.plot.get("formats", ["png"]):
            expected_outputs.append(combined_base.with_suffix(f".{fmt}"))
    if config.output.get("write_individual_figures", True):
        for (session_id, unit_id), _unit_spikes in spikes.groupby(["session_id", "unit_id"], sort=True):
            safe_base = figure_root / _safe_filename(session_id) / f"{_safe_filename(unit_id)}_raster"
            for fmt in config.plot.get("formats", ["png"]):
                expected_outputs.append(safe_base.with_suffix(f".{fmt}"))
    if validate_only:
        return {
            "sessions": int(spikes["session_id"].nunique()),
            "units": int(spikes[["session_id", "unit_id"]].drop_duplicates().shape[0]),
            "trials": int(trials.shape[0]),
            "output_dir": str(raster_root),
            "validate_only": True,
        }
    _check_output_conflicts(config, expected_outputs)

    unit_rows = []
    exclusions = []
    figure_count = 0
    fail_on_empty = bool(config.runtime.get("fail_on_empty_unit", False))
    for (session_id, unit_id), unit_spikes in spikes.groupby(["session_id", "unit_id"], sort=True):
        unit_trials = trial_summary[(trial_summary["session_id"] == session_id) & (trial_summary["unit_id"] == unit_id)].copy()
        unit_aligned = aligned[(aligned["session_id"] == session_id) & (aligned["unit_id"] == unit_id)].copy()
        status = "success"
        exclusion_reason = ""
        figure_path = ""
        if unit_aligned.empty and fail_on_empty:
            status = "excluded"
            exclusion_reason = "empty_unit_in_alignment_window"
            exclusions.append({"session_id": session_id, "unit_id": unit_id, "reason": exclusion_reason})
        elif config.output.get("write_individual_figures", True):
            safe_base = figure_root / _safe_filename(session_id) / f"{_safe_filename(unit_id)}_raster"
            try:
                written = render_raster(unit_aligned, unit_trials, config, safe_base)
                figure_count += len(written)
                figure_path = str(written[0]) if written else ""
            except Exception as exc:
                if not config.runtime.get("continue_on_unit_error", True):
                    raise
                status = "failed"
                exclusion_reason = f"render_error: {exc}"
                exclusions.append({"session_id": session_id, "unit_id": unit_id, "reason": exclusion_reason})
        unit_rows.append(
            {
                "session_id": session_id,
                "unit_id": unit_id,
                "source_spike_file": ";".join(sorted(unit_spikes["_source_file"].unique().tolist())),
                "source_event_file": ";".join(sorted(unit_trials["source_event_file"].unique().tolist())) if not unit_trials.empty else "",
                "alignment_event": config.alignment["event_name"],
                "n_trials": int(unit_trials.shape[0]),
                "n_spikes_full_session": int(unit_spikes.shape[0]),
                "n_aligned_spikes": int(unit_aligned.shape[0]),
                "n_empty_trials": int((unit_trials["n_spikes_in_window"] == 0).sum()) if not unit_trials.empty else 0,
                "window_start_s": float(config.alignment["window_s"][0]),
                "window_end_s": float(config.alignment["window_s"][1]),
                "figure_path": figure_path,
                "status": status,
                "exclusion_reason": exclusion_reason,
            }
        )

    unit_summary = pd.DataFrame(unit_rows)
    exclusions_df = pd.DataFrame(exclusions, columns=["session_id", "unit_id", "reason"])
    successful_units = unit_summary.loc[unit_summary["status"] == "success", ["session_id", "unit_id"]]
    combined_trials = trial_summary.merge(successful_units, on=["session_id", "unit_id"], how="inner")
    combined_paths: list[Path] = []
    combined_row_map = pd.DataFrame()
    if config.output.get("write_combined_figure", True) and not combined_trials.empty:
        combined_paths, combined_row_map = render_combined_raster(aligned, combined_trials, config, combined_base)
        figure_count += len(combined_paths)
    if config.output.get("write_unit_summary_csv", True):
        _atomic_write_csv(unit_summary, unit_summary_path)
    if config.output.get("write_trial_summary_csv", True):
        _atomic_write_csv(trial_summary, trial_summary_path)
    if config.output.get("write_exclusion_csv", True):
        _atomic_write_csv(exclusions_df, exclusions_path)
    if config.output.get("write_aligned_spikes_long_csv", False):
        _atomic_write_csv(aligned, aligned_long_path)
    if config.output.get("write_combined_row_map_csv", True):
        _atomic_write_csv(combined_row_map, combined_row_map_path)

    manifest = {
        "schema_version": config.schema_version,
        "config_path": str(config.config_path),
        "resolved_config": _jsonable_config(config),
        "input_files": {
            "spike_tables": [str(path) for path in spike_files],
            "event_tables": [str(path) for path in event_files],
        },
        "input_session_mappings": {
            "spike_tables": {
                source: sorted(group["session_id"].unique().tolist())
                for source, group in spikes.groupby("_source_file", sort=True)
            },
            "event_tables": {
                source: sorted(group["session_id"].unique().tolist())
                for source, group in events.groupby("_source_file", sort=True)
            },
        },
        "time_unit": config.input["time_unit"],
        "time_unit_to_seconds": TIME_UNIT_TO_SECONDS[config.input["time_unit"]],
        "alignment": config.alignment,
        "trial_order": config.alignment.get("trial_order", "event_time"),
        "trial_axis_order": "trial 1 at top",
        "counts": {
            "sessions": int(spikes["session_id"].nunique()),
            "units_discovered": int(spikes[["session_id", "unit_id"]].drop_duplicates().shape[0]),
            "units_excluded": int((unit_summary["status"] == "excluded").sum()),
            "units_failed": int((unit_summary["status"] == "failed").sum()),
            "trials": int(trials.shape[0]),
            "overlapping_trial_windows": int(trials["overlaps_another_trial_window"].sum()),
            "figures_written": int(figure_count),
            "individual_figures_written": int(
                unit_summary["figure_path"].astype(str).str.strip().ne("").sum()
                * len(config.plot.get("formats", ["png"]))
            ),
            "combined_figures_written": int(len(combined_paths)),
            "aligned_spikes": int(aligned.shape[0]),
            "duplicate_spike_timestamps": int(
                spikes.duplicated(["session_id", "unit_id", "spike_time_absolute_s"], keep=False).sum()
            ),
            "out_of_order_spike_rows": int(spikes.get("was_out_of_order", pd.Series(dtype=bool)).sum()),
        },
        "figure_mappings": unit_summary[
            ["session_id", "unit_id", "source_spike_file", "source_event_file", "figure_path", "status"]
        ].to_dict(orient="records"),
        "outputs": {
            "figures_dir": str(figure_root),
            "combined_figure": str(combined_paths[0]) if combined_paths else None,
            "combined_row_map_csv": str(combined_row_map_path),
            "unit_summary_csv": str(unit_summary_path),
            "trial_summary_csv": str(trial_summary_path),
            "exclusions_csv": str(exclusions_path),
            "aligned_spikes_long_csv": str(aligned_long_path) if config.output.get("write_aligned_spikes_long_csv", False) else None,
            "manifest_json": str(manifest_path),
            "log": str(log_path),
        },
        "git_commit": _git_commit(),
        "software": {
            "neurotrain_version": _neurotrain_version(),
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "run_time": datetime.now().isoformat(timespec="seconds"),
        "warnings": [
            "Automatic NeuroExplorer export reads raw NexVar.Timestamps(); manually validate counts and event alignment for each new project."
        ],
    }
    if config.output.get("write_manifest_json", True):
        _atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))
    log_lines = [
        f"{datetime.now().isoformat(timespec='seconds')} raster pipeline completed",
        f"config={config.config_path}",
        f"sessions={manifest['counts']['sessions']} units={manifest['counts']['units_discovered']} trials={manifest['counts']['trials']}",
        f"figures={manifest['counts']['figures_written']} excluded={manifest['counts']['units_excluded']} failed={manifest['counts']['units_failed']}",
    ]
    for row in exclusions:
        log_lines.append(f"unit_status session={row['session_id']} unit={row['unit_id']} reason={row['reason']}")
    _atomic_write_text(log_path, "\n".join(log_lines) + "\n")

    if not (unit_summary["status"] == "success").any():
        raise RasterInputError(f"All units were excluded or failed. QC outputs were written to {raster_root}.")

    summary = dict(manifest["counts"])
    summary["output_dir"] = str(raster_root)
    summary["validate_only"] = False
    return summary


def _jsonable_config(config: RasterConfig) -> dict[str, Any]:
    data = asdict(config)
    data.pop("raw", None)
    data["config_path"] = str(config.config_path)
    data["paths"] = {
        "input_root": str(config.paths.input_root),
        "output_root": str(config.paths.output_root),
        "spike_table_glob": config.paths.spike_table_glob,
        "event_table_glob": config.paths.event_table_glob,
        "output_subdir": config.paths.output_subdir,
    }
    return data


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return ""


def _neurotrain_version() -> str:
    version_path = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build event-aligned raw-spike raster plots from Unit Train/Event tables.")
    parser.add_argument("--config", required=True, help="Path to independent raster YAML config.")
    parser.add_argument("--validate-only", action="store_true", help="Load config and inputs without writing outputs.")
    parser.add_argument("--session", help="Optional session_id filter.")
    parser.add_argument("--unit", help="Optional unit_id filter.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_raster_config(Path(args.config))
        summary = run_raster_pipeline(config, validate_only=args.validate_only, session=args.session, unit=args.unit)
    except (RasterConfigError, RasterInputError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        "Raster summary: "
        f"sessions={summary.get('sessions', 0)}; "
        f"units={summary.get('units_discovered', summary.get('units', 0))}; "
        f"trials={summary.get('trials', 0)}; "
        f"overlapping_windows={summary.get('overlapping_trial_windows', 0)}; "
        f"figures={summary.get('figures_written', 0)}; "
        f"excluded={summary.get('units_excluded', 0)}; "
        f"failed={summary.get('units_failed', 0)}; "
        f"output_dir={summary.get('output_dir')}; "
        f"validate_only={summary.get('validate_only')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
