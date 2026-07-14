from __future__ import annotations

from pathlib import Path

import pandas as pd


def format_event_time(value) -> str:
    if pd.isna(value):
        raise ValueError("Event time cannot be empty or NaN.")

    numeric_value = float(value)
    if not float("-inf") < numeric_value < float("inf"):
        raise ValueError(f"Event time must be finite: {value!r}")

    if numeric_value.is_integer():
        return str(int(numeric_value))

    formatted = f"{numeric_value:.6f}".rstrip("0").rstrip(".")
    if "e" in formatted.lower():
        formatted = format(numeric_value, ".6f").rstrip("0").rstrip(".")
    return formatted


def write_event_times(output_path: Path, times) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    formatted_times = [format_event_time(value) for value in times]
    output_path.write_text("\n".join(formatted_times) + "\n", encoding="utf-8")


def write_interval_times(
    output_path: Path,
    start_times,
    end_times,
    *,
    interval_name: str | None = None,
    include_variable_name_first_line: bool = True,
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    formatted_lines: list[str] = []
    if include_variable_name_first_line:
        if not interval_name or not str(interval_name).strip():
            raise ValueError("Interval variable name is required when include_variable_name_first_line=True.")
        formatted_lines.append(str(interval_name).strip())
    formatted_lines.extend(
        f"{format_event_time(start_value)},{format_event_time(end_value)}"
        for start_value, end_value in zip(start_times, end_times)
    )
    output_path.write_text("\n".join(formatted_lines) + "\n", encoding="utf-8")


def resolve_event_file_path(events_export_dir: Path, file_id: str, event_name: str, stimulus_input_mode: str) -> Path:
    suffix = ".txt" if stimulus_input_mode == "interval" else ".csv"
    return Path(events_export_dir) / f"{file_id}_{event_name}{suffix}"


def resolve_interval_file_path(events_export_dir: Path, file_id: str, interval_pattern: str) -> Path:
    return Path(events_export_dir) / interval_pattern.format(file_id=file_id)


def read_light_intervals(
    path: Path,
    interval_name: str | None = None,
    has_variable_name_first_line: bool = True,
    delimiter: str = ",",
) -> list[tuple[float, float]]:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise ValueError(f"Interval CSV is empty: {path}")
    data_lines = lines
    if has_variable_name_first_line:
        first = lines[0].strip()
        lowered_first = first.lower()
        if delimiter in first and lowered_first not in {"light_interval"}:
            if lowered_first in {"start,end", "light_on_s,light_off_s"}:
                raise ValueError(
                    f"Interval CSV first line should be the interval variable name, not a standard header: {path}"
                )
            raise ValueError(
                f"Interval CSV first line appears to be interval data, so the variable name is missing: {path}"
            )
        if interval_name and first != interval_name:
            raise ValueError(f"Expected interval variable name {interval_name!r}, got {first!r}: {path}")
        data_lines = lines[1:]
        if not data_lines:
            raise ValueError(f"Interval CSV contains no interval rows after the variable name line: {path}")
    intervals: list[tuple[float, float]] = []
    for line in data_lines:
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in {"start,end", "light_on_s,light_off_s", "time_s"}:
            raise ValueError(f"Interval CSV contains a standard header row, which is not allowed: {path}")
        if delimiter not in stripped:
            raise ValueError(
                "Interval row contains no delimiter. This file is not an interval CSV; "
                f"if it is a Light_On event file, do not import it as an interval variable: {path}"
            )
        parts = stripped.split(delimiter)
        if len(parts) != 2:
            raise ValueError(f"Interval CSV must have exactly 2 columns per row: {path} -> {line!r}")
        start_value = float(parts[0].strip())
        end_value = float(parts[1].strip())
        if end_value <= start_value:
            raise ValueError(f"Interval end must be greater than interval start: {path} -> {line!r}")
        intervals.append((start_value, end_value))
    if not intervals:
        raise ValueError(f"Interval CSV contains no valid interval rows: {path}")
    return intervals


def read_neuroexplorer_interval_csv(
    path: Path,
    *,
    expected_interval_name: str | None = None,
    allow_missing_variable_name: bool = False,
    delimiter: str = ",",
) -> tuple[str, list[tuple[float, float]]]:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise ValueError(f"Interval CSV is empty: {path}")

    first_line = lines[0].strip()
    if not first_line:
        raise ValueError(f"Interval CSV first line is empty: {path}")
    lowered_first = first_line.lower()
    disallowed = {"start,end", "light_on_s,light_off_s", "time_s"}
    if lowered_first in disallowed:
        raise ValueError("Interval CSV first line should be variable name, not a standard header.")

    if delimiter in first_line:
        if not allow_missing_variable_name:
            raise ValueError(
                "Interval CSV first line is missing the interval variable name. "
                "If this is a data row such as '120,135', add the variable name as the first line."
            )
        interval_name = expected_interval_name or "Light_Interval"
        data_lines = lines
    else:
        interval_name = first_line
        if expected_interval_name and interval_name != expected_interval_name:
            raise ValueError(f"Expected interval variable name {expected_interval_name!r}, got {interval_name!r}.")
        data_lines = lines[1:]

    if not data_lines:
        raise ValueError(f"Interval CSV contains no interval rows after the variable name line: {path}")

    intervals: list[tuple[float, float]] = []
    for line in data_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower() in disallowed:
            raise ValueError("Interval CSV contains a standard header in data rows.")
        if stripped.count(delimiter) != 1:
            raise ValueError(
                "This file is not a valid interval CSV; if it is a Light_On event file, do not use it as interval input."
            )
        start_text, end_text = [part.strip() for part in stripped.split(delimiter, 1)]
        start_value = float(start_text)
        end_value = float(end_text)
        if end_value <= start_value:
            raise ValueError(f"Interval end must be greater than interval start: {stripped!r}")
        intervals.append((start_value, end_value))
    if not intervals:
        raise ValueError(f"Interval CSV contains no valid intervals: {path}")
    return interval_name, intervals


def derive_light_on_off_from_intervals(intervals: list[tuple[float, float]]):
    light_on_times = [start for start, _ in intervals]
    light_off_times = [end for _, end in intervals]
    durations = [end - start for start, end in intervals]
    return light_on_times, light_off_times, durations


def validate_event_file(output_path: Path) -> list[str]:
    output_path = Path(output_path)
    lines = output_path.read_text(encoding="utf-8").splitlines()
    disallowed_headers = {"time_s", "light_on_s", "light_off_s", "file_id", "event_name"}
    if not lines:
        raise ValueError(f"Event file is empty: {output_path}")
    if lines[0].strip() in disallowed_headers:
        raise ValueError(f"Event file contains a header row, which is not allowed: {output_path}")
    for line in lines:
        if "," in line:
            raise ValueError(f"Event file must not contain commas: {output_path}")
        float(line.strip())
    return lines


def validate_interval_file(output_path: Path) -> list[str]:
    output_path = Path(output_path)
    lines = output_path.read_text(encoding="utf-8").splitlines()
    disallowed_headers = {"time_s", "light_on_s", "light_off_s", "file_id", "event_name", "start,end", "light_on_s,light_off_s"}
    if not lines:
        raise ValueError(f"Interval file is empty: {output_path}")
    first_line = lines[0].strip()
    if not first_line:
        raise ValueError(f"Interval file first line is empty: {output_path}")
    if first_line.lower() in disallowed_headers:
        raise ValueError(f"Interval file first line must be the variable name, not a standard header: {output_path}")
    data_lines = lines[1:]
    if not data_lines:
        raise ValueError(f"Interval file contains no interval rows after the variable name: {output_path}")
    for line in data_lines:
        if line.count(",") != 1:
            raise ValueError(f"Each interval row must contain exactly one comma: {output_path} -> {line!r}")
        start_text, end_text = line.split(",", 1)
        start_value = float(start_text.strip())
        end_value = float(end_text.strip())
        if end_value <= start_value:
            raise ValueError(f"Interval end must be greater than interval start: {output_path} -> {line!r}")
    return data_lines
