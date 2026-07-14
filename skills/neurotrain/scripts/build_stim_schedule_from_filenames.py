from __future__ import annotations

import argparse
import copy
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_path, resolve_project_paths
from utils.table_utils import read_table, write_table


STIM_SCHEDULE_COLUMNS = [
    "file_id",
    "pl2_file",
    "event_group",
    "has_light",
    "light_on_s",
    "duration_s",
    "light_off_s",
    "condition",
    "note",
    "file_index",
    "sorted_channels",
    "detected_in_latest_scan",
    "created_at",
    "updated_at",
]

PRESERVE_COLUMNS = ["condition", "note"]
AUTO_COLUMNS = ["file_id", "event_group", "has_light", "light_on_s", "duration_s", "light_off_s", "file_index", "sorted_channels"]


def _stim_cfg(config: dict) -> dict:
    return config.get("stim_schedule", {})


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_number_text(value: float) -> str:
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.6f}".rstrip("0").rstrip(".")


def _parser_cfg(config: dict) -> dict:
    return _stim_cfg(config).get("filename_parser", {})


def _pattern_cfgs(config: dict) -> dict:
    parser_cfg = _parser_cfg(config)
    patterns = parser_cfg.get("patterns")
    if isinstance(patterns, dict) and patterns:
        return patterns
    regex = str(parser_cfg.get("regex", "")).strip()
    if not regex:
        raise ValueError("stim_schedule.filename_parser.regex or patterns is required.")
    return {"light": {"regex": regex}}


def _natural_file_id_key(value: str) -> tuple:
    parts = re.split(r"(\d+)", str(value))
    key = []
    for part in parts:
        if not part:
            continue
        key.append(int(part) if part.isdigit() else part.lower())
    return tuple(key)


def _read_existing_schedule(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=STIM_SCHEDULE_COLUMNS)
    df = read_table(path).copy()
    for column in STIM_SCHEDULE_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[STIM_SCHEDULE_COLUMNS]


def parse_pl2_filename(config: dict, pl2_path: Path) -> dict | None:
    parser_cfg = _parser_cfg(config)
    flags = 0 if parser_cfg.get("case_sensitive", False) else re.IGNORECASE
    pattern_name = None
    match = None
    for candidate_name, pattern_cfg in _pattern_cfgs(config).items():
        regex = str(pattern_cfg.get("regex", "")).strip()
        if not regex:
            continue
        candidate_match = re.match(regex, pl2_path.name, flags=flags)
        if candidate_match:
            pattern_name = candidate_name
            match = candidate_match
            break
    if match is None or pattern_name is None:
        return None

    file_index_raw = str(match.group("file_index"))
    sorted_channels = str(match.group("channels"))

    file_id_cfg = _stim_cfg(config).get("file_id", {})
    zero_pad = int(file_id_cfg.get("zero_pad", 0))
    file_index_formatted = file_index_raw.zfill(zero_pad) if zero_pad > 0 else file_index_raw
    file_id = str(file_id_cfg.get("format", "{file_index}")).format(file_index=file_index_formatted)

    defaults = _stim_cfg(config).get("defaults", {})
    note_prefix = str(defaults.get("note_prefix", "sorted channels: "))

    if pattern_name == "no_light":
        has_light = "no"
        light_on_s = ""
        duration_s = ""
        light_off_s = ""
        event_group = "nolight"
        condition = "no_light"
    else:
        light_on_s = float(match.group("light_on"))
        duration_s = float(match.group("duration"))
        light_off_s = light_on_s + duration_s
        event_group = f"{_as_number_text(light_on_s)}light{_as_number_text(duration_s)}"
        has_light = "yes"
        condition = str(defaults.get("condition", ""))

    return {
        "file_id": file_id,
        "pl2_file": pl2_path.name,
        "event_group": event_group,
        "has_light": has_light,
        "light_on_s": light_on_s,
        "duration_s": duration_s,
        "light_off_s": light_off_s,
        "condition": condition,
        "note": f"{note_prefix}{sorted_channels}",
        "file_index": file_index_formatted,
        "sorted_channels": sorted_channels,
        "detected_in_latest_scan": "yes",
        "created_at": _now_str(),
        "updated_at": _now_str(),
    }


def scan_pl2_filenames(config: dict, logger: PipelineLogger) -> list[dict]:
    paths = resolve_project_paths(config)
    if not _parser_cfg(config).get("enabled", True):
        logger.log("build_stim_schedule", "*", "", "", "warning", "stim_schedule.filename_parser.enabled=false; skipping automatic filename parsing.")
        return []
    source_cfg = _stim_cfg(config).get("source", {})
    file_glob = str(source_cfg.get("file_glob", "*.pl2"))
    source_pl2_dir = resolve_path(paths["root_dir"], source_cfg.get("pl2_dir", config["input"]["pl2_dir"]))
    conflict_policy = _stim_cfg(config).get("conflict_policy", {})
    on_parse_fail = str(conflict_policy.get("on_parse_fail", "warn_skip"))
    rows: list[dict] = []
    seen_file_ids: dict[str, str] = {}
    seen_pl2_files: set[str] = set()

    for pl2_path in sorted(source_pl2_dir.glob(file_glob), key=lambda item: item.name.lower()):
        parsed = parse_pl2_filename(config, pl2_path)
        if parsed is None:
            message = (
                "Filename does not match expected pattern "
                "sorted_<index>_<onset>light<duration>_<channels>.pl2 or sorted_<index>_nolight_<channels>.pl2"
            )
            logger.log("build_stim_schedule", pl2_path.stem, str(pl2_path), "", "skipped", message)
            if on_parse_fail == "error":
                raise ValueError(f"{message}: {pl2_path.name}")
            continue

        existing_pl2 = parsed["pl2_file"]
        existing_file_id = parsed["file_id"]
        if existing_pl2 in seen_pl2_files:
            raise ValueError(f"Duplicate pl2_file detected during filename scan: {existing_pl2}")
        if existing_file_id in seen_file_ids and seen_file_ids[existing_file_id] != existing_pl2:
            raise ValueError(
                f"Duplicate file_id detected during filename scan: {existing_file_id} -> "
                f"{seen_file_ids[existing_file_id]} and {existing_pl2}"
            )
        seen_pl2_files.add(existing_pl2)
        seen_file_ids[existing_file_id] = existing_pl2
        rows.append(parsed)
        logger.log(
            "build_stim_schedule",
            existing_file_id,
            str(pl2_path),
            "",
            "success",
            f"Parsed filename into stim schedule row. has_light={parsed['has_light']}; light_on_s={parsed['light_on_s']}; duration_s={parsed['duration_s']}; light_off_s={parsed['light_off_s']}",
        )
    return rows


def merge_stim_schedule(existing_df: pd.DataFrame, scanned_rows: list[dict], config: dict, logger: PipelineLogger) -> pd.DataFrame:
    preserve_manual = bool(_stim_cfg(config).get("preserve_manual_edits", True))
    conflict_policy = _stim_cfg(config).get("conflict_policy", {})
    existing_key = str(conflict_policy.get("existing_row_key", "pl2_file"))
    if existing_key not in {"pl2_file", "file_id"}:
        raise ValueError("stim_schedule.conflict_policy.existing_row_key must be 'pl2_file' or 'file_id'.")
    if not existing_df.empty:
        duplicate_pl2 = existing_df[existing_df["pl2_file"].astype(str).duplicated(keep=False)]
        duplicate_file_id = existing_df[existing_df["file_id"].astype(str).duplicated(keep=False)]
        if not duplicate_pl2.empty:
            raise ValueError(f"Duplicate pl2_file found in existing stim_schedule_master: {sorted(duplicate_pl2['pl2_file'].astype(str).unique().tolist())}")
        if not duplicate_file_id.empty:
            raise ValueError(f"Duplicate file_id found in existing stim_schedule_master: {sorted(duplicate_file_id['file_id'].astype(str).unique().tolist())}")

    existing_df = _read_existing_schedule(resolve_project_paths(config)["stim_schedule_path"]) if existing_df is None else existing_df.copy()
    existing_map = {
        str(getattr(row, existing_key)): row._asdict()
        for row in existing_df.itertuples(index=False)
    }
    scanned_keys = {str(row[existing_key]) for row in scanned_rows}
    merged_rows: list[dict] = []

    for new_row in scanned_rows:
        key = str(new_row[existing_key])
        old_row = existing_map.get(key)
        if old_row is None:
            merged_rows.append(new_row)
            continue

        merged = dict(old_row)
        merged["updated_at"] = new_row["updated_at"]
        merged["detected_in_latest_scan"] = "yes"

        for column in AUTO_COLUMNS:
            new_value = new_row[column]
            old_value = merged.get(column, "")
            if preserve_manual and pd.notna(old_value) and str(old_value).strip() != "":
                old_text = _as_number_text(old_value) if column in {"light_on_s", "duration_s", "light_off_s"} else str(old_value).strip()
                new_text = _as_number_text(new_value) if column in {"light_on_s", "duration_s", "light_off_s"} else str(new_value).strip()
                if old_text != new_text:
                    logger.log(
                        "build_stim_schedule",
                        str(merged.get("file_id", new_row["file_id"])),
                        str(merged.get("pl2_file", new_row["pl2_file"])),
                        "",
                        "warning",
                        f"Existing stim field differs from filename parse for {column}: existing={old_text}, parsed={new_text}. Preserving existing value.",
                    )
                    continue
            merged[column] = new_value

        for column in PRESERVE_COLUMNS:
            if not preserve_manual or not str(merged.get(column, "")).strip():
                merged[column] = new_row[column]
        if not str(merged.get("created_at", "")).strip():
            merged["created_at"] = new_row["created_at"]
        merged_rows.append(merged)

    for old_row in existing_df.itertuples(index=False):
        key = str(getattr(old_row, existing_key))
        if key in scanned_keys:
            continue
        stale = old_row._asdict()
        stale["detected_in_latest_scan"] = "no"
        stale["updated_at"] = _now_str()
        merged_rows.append(stale)

    merged_df = pd.DataFrame(merged_rows)
    for column in STIM_SCHEDULE_COLUMNS:
        if column not in merged_df.columns:
            merged_df[column] = ""
    merged_df["file_index_sort"] = pd.to_numeric(merged_df["file_index"], errors="coerce").fillna(10**9)
    merged_df["file_id_sort"] = merged_df["file_id"].map(_natural_file_id_key)
    merged_df = merged_df.sort_values(by=["file_index_sort", "file_id_sort", "pl2_file"], kind="stable").drop(
        columns=["file_index_sort", "file_id_sort"]
    )
    return merged_df[STIM_SCHEDULE_COLUMNS].reset_index(drop=True)


def build_stim_schedule_from_filenames(config: dict, logger: PipelineLogger) -> Path:
    paths = resolve_project_paths(config)
    output_path = paths["stim_schedule_path"]
    scanned_rows = scan_pl2_filenames(config, logger)
    existing_df = _read_existing_schedule(output_path)
    merged_df = merge_stim_schedule(existing_df, scanned_rows, config, logger)
    try:
        write_table(merged_df, output_path)
    except Exception as exc:
        logger.log("build_stim_schedule", "*", "", str(output_path), "failed", "Failed to write stim_schedule_master.", exception=exc)
        raise

    scanned_by_file = {str(row["pl2_file"]): row for row in scanned_rows}
    for pl2_file, parsed in scanned_by_file.items():
        current_row = merged_df[merged_df["pl2_file"].astype(str) == pl2_file].iloc[0]
        logger.log(
            "build_stim_schedule",
            str(current_row["file_id"]),
            pl2_file,
            str(output_path),
            "success",
            f"has_light={current_row['has_light']}; light_on_s={current_row['light_on_s']}; duration_s={current_row['duration_s']}; light_off_s={current_row['light_off_s']}",
        )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or update stim_schedule_master from .pl2 filenames.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        build_stim_schedule_from_filenames(config=config, logger=logger)
        return 0
    except Exception as exc:
        logger.log(
            "build_stim_schedule",
            "*",
            str(Path(args.config).resolve()),
            "",
            "failed",
            "build_stim_schedule_from_filenames terminated with an exception.",
            exception=exc,
        )
        return 1
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
