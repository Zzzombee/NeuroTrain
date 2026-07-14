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

from scripts.adapters.neuroexplorer_adapter import NeuroExplorerAdapter
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table, write_table


UNIT_TABLE_COLUMNS = [
    "file_id",
    "pl2_file",
    "unit_id",
    "channel",
    "original_name",
    "include",
    "exclusion_reason",
    "representative_unit",
    "duplicate_of",
    "note",
    "unit_index",
    "source_variable_type",
    "detected_by",
    "created_at",
    "updated_at",
    "detected_in_latest_scan",
]

MANUAL_PRESERVE_COLUMNS = [
    "include",
    "exclusion_reason",
    "representative_unit",
    "duplicate_of",
    "note",
]

MISSING_NOTE = "not detected in latest scan"


def _unit_table_cfg(config: dict) -> dict:
    return config.get("unit_table", {})


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _file_id_map(config: dict, paths: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    stim_path = paths["stim_schedule_path"]
    if stim_path.exists():
        stim_df = normalize_stim_schedule(read_table(stim_path), file_id_column=config["project"]["file_id_column"])
        for row in stim_df.itertuples(index=False):
            pl2_name = str(row.pl2_file).strip()
            file_id = str(getattr(row, config["project"]["file_id_column"])).strip()
            if not pl2_name or not file_id:
                continue
            if pl2_name in mapping and mapping[pl2_name] != file_id:
                raise ValueError(f"Conflicting file_id mapping for {pl2_name}: {mapping[pl2_name]} vs {file_id}")
            mapping[pl2_name] = file_id
    return mapping


def _resolve_file_id(pl2_path: Path, file_id_map: dict[str, str]) -> str:
    return file_id_map.get(pl2_path.name, pl2_path.stem)


def parse_channel(original_name: str) -> int | None:
    patterns = [
        r"SPK_SPKC(\d+)",
        r"SPKC(\d+)",
        r"SPK(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(original_name), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _suffix_key(original_name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)([A-Za-z]*)$", str(original_name))
    if not match:
        return (10**9, str(original_name).lower())
    return (int(match.group(1)), match.group(2).lower())


def sort_unit_names(unit_names: list[str], sort_by_channel_then_suffix: bool = True) -> list[str]:
    if not sort_by_channel_then_suffix:
        return sorted(unit_names, key=lambda value: str(value).lower())

    def _key(name: str):
        channel = parse_channel(name)
        return (
            channel if channel is not None else 10**9,
            _suffix_key(name),
            str(name).lower(),
        )

    return sorted(unit_names, key=_key)


def _matches_patterns(name: str, patterns: list[str], case_sensitive: bool) -> bool:
    if not patterns:
        return True
    haystack = name if case_sensitive else name.lower()
    for pattern in patterns:
        needle = pattern if case_sensitive else str(pattern).lower()
        if needle in haystack:
            return True
    return False


def filter_unit_names(unit_names: list[str], config: dict) -> list[str]:
    unit_detection = _unit_table_cfg(config).get("unit_detection", {})
    include_patterns = [str(value) for value in unit_detection.get("include_patterns", [])]
    exclude_patterns = [str(value) for value in unit_detection.get("exclude_patterns", [])]
    case_sensitive = bool(unit_detection.get("case_sensitive", False))
    filtered: list[str] = []
    for name in unit_names:
        if include_patterns and not _matches_patterns(name, include_patterns, case_sensitive):
            continue
        if exclude_patterns and _matches_patterns(name, exclude_patterns, case_sensitive):
            continue
        filtered.append(str(name))
    return sort_unit_names(filtered, _unit_table_cfg(config).get("numbering", {}).get("sort_by_channel_then_suffix", True))


def _default_representative(default_values: dict, unit_id: str) -> str:
    configured = str(default_values.get("representative_unit", "") or "").strip()
    return configured or unit_id


def _format_unit_id(index: int, config: dict) -> str:
    template = _unit_table_cfg(config).get("numbering", {}).get("unit_id_format", "unit{index:02d}")
    return template.format(index=index)


def _empty_unit_table() -> pd.DataFrame:
    return pd.DataFrame(columns=UNIT_TABLE_COLUMNS)


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in UNIT_TABLE_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return result[UNIT_TABLE_COLUMNS]


def _read_existing_unit_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_unit_table()
    return _ensure_columns(read_table(path))


def _read_units_from_existing_exports(paths: dict, file_id: str) -> list[str]:
    fullrate_csv = paths["nex_fullrate_dir"] / f"{file_id}_FullRate_bin1s.csv"
    if not fullrate_csv.exists():
        return []
    try:
        df = read_table(fullrate_csv)
    except Exception:
        return []
    if "unit_id" not in df.columns:
        return []
    return sorted({str(value).strip() for value in df["unit_id"].astype(str) if str(value).strip()})


def _scan_units_for_file(config: dict, paths: dict, logger: PipelineLogger, pl2_path: Path) -> list[str]:
    source_cfg = _unit_table_cfg(config).get("source", {})
    backend_name = str(source_cfg.get("backend", "nex")).lower()
    if backend_name == "existing_exports":
        file_id = _resolve_file_id(pl2_path, _file_id_map(config, paths))
        return _read_units_from_existing_exports(paths, file_id)
    if backend_name == "manual":
        logger.log(
            "build_unit_table",
            pl2_path.stem,
            str(pl2_path),
            "",
            "warning",
            "unit_table.source.backend=manual; no automatic unit scan performed for this file.",
        )
        return []

    adapter_config = copy.deepcopy(config)
    adapter_config.setdefault("neuroexplorer", {})["backend"] = "nex_package"
    adapter_config["neuroexplorer"].setdefault("files", {})
    adapter_config["neuroexplorer"]["files"]["open_pl2_mode"] = (
        "try_nex_then_manual" if source_cfg.get("open_pl2", True) else "active_document_only"
    )
    adapter = NeuroExplorerAdapter(config=adapter_config, logger=logger)
    try:
        adapter.connect()
        if source_cfg.get("use_active_doc", False) and not source_cfg.get("open_pl2", True):
            units = adapter.list_neuron_variables()
        else:
            adapter.open_file(pl2_path)
            units = adapter.list_neuron_variables()
        return units
    finally:
        adapter.close_file()
        adapter.quit()


def scan_pl2_units(config: dict, paths: dict, logger: PipelineLogger) -> dict[str, dict]:
    file_id_map = _file_id_map(config, paths)
    fallback_enabled = bool(_unit_table_cfg(config).get("source", {}).get("fallback_to_existing_fullrate_exports", False))
    scans: dict[str, dict] = {}
    for pl2_path in sorted(paths["pl2_dir"].glob("*.pl2")):
        file_id = _resolve_file_id(pl2_path, file_id_map)
        try:
            raw_units = _scan_units_for_file(config, paths, logger, pl2_path)
            filtered_units = filter_unit_names(raw_units, config)
            detected_by = _unit_table_cfg(config).get("source", {}).get("backend", "nex")
            if fallback_enabled and not filtered_units:
                fallback_units = _read_units_from_existing_exports(paths, file_id)
                if fallback_units:
                    raw_units = fallback_units
                    filtered_units = fallback_units
                    detected_by = "existing_fullrate_export_fallback"
                    logger.log(
                        "build_unit_table",
                        file_id,
                        str(pl2_path),
                        "",
                        "warning",
                        "PL2 unit scan returned no units; using existing fullrate CSV unit_id fallback.",
                    )
            scans[file_id] = {
                "pl2_file": pl2_path.name,
                "raw_units": raw_units,
                "units": filtered_units,
                "detected_by": detected_by,
            }
            logger.log(
                "build_unit_table",
                file_id,
                str(pl2_path),
                "",
                "success",
                f"Scanned unit names from PL2. n_raw={len(raw_units)}; n_filtered={len(filtered_units)}",
            )
        except Exception as exc:
            if fallback_enabled:
                fallback_units = _read_units_from_existing_exports(paths, file_id)
                if fallback_units:
                    scans[file_id] = {
                        "pl2_file": pl2_path.name,
                        "raw_units": fallback_units,
                        "units": fallback_units,
                        "detected_by": "existing_fullrate_export_fallback",
                    }
                    logger.log(
                        "build_unit_table",
                        file_id,
                        str(pl2_path),
                        "",
                        "warning",
                        "Failed to read neuron names from PL2; using existing fullrate CSV unit_id fallback.",
                        exception=exc,
                    )
                    continue
            logger.log(
                "build_unit_table",
                file_id,
                str(pl2_path),
                "",
                "failed",
                "Failed to read neuron names from PL2.",
                exception=exc,
            )
    return scans


def build_unit_rows_for_file(
    *,
    config: dict,
    file_id: str,
    pl2_file: str,
    unit_names: list[str],
) -> list[dict]:
    default_values = _unit_table_cfg(config).get("default_values", {})
    timestamp = _now_str()
    rows: list[dict] = []
    for index, original_name in enumerate(unit_names, start=1):
        unit_id = _format_unit_id(index, config)
        rows.append(
            {
                "file_id": file_id,
                "pl2_file": pl2_file,
                "unit_id": unit_id,
                "channel": parse_channel(original_name),
                "original_name": original_name,
                "include": default_values.get("include", "yes"),
                "exclusion_reason": default_values.get("exclusion_reason", ""),
                "representative_unit": _default_representative(default_values, unit_id),
                "duplicate_of": default_values.get("duplicate_of", ""),
                "note": default_values.get("note", ""),
                "unit_index": index,
                "source_variable_type": _unit_table_cfg(config).get("unit_detection", {}).get("variable_kind", "NeuronNames"),
                "detected_by": _unit_table_cfg(config).get("source", {}).get("backend", "nex"),
                "created_at": timestamp,
                "updated_at": timestamp,
                "detected_in_latest_scan": "yes",
            }
        )
    return rows


def merge_unit_quality_table(existing_df: pd.DataFrame, scanned_rows: list[dict], config: dict) -> pd.DataFrame:
    preserve_manual = _unit_table_cfg(config).get("preserve_manual_edits", True)
    result_df = _ensure_columns(existing_df)
    existing_map = {
        (str(row.file_id), str(row.original_name)): row._asdict()
        for row in result_df.itertuples(index=False)
    }
    scanned_keys = {(str(row["file_id"]), str(row["original_name"])) for row in scanned_rows}
    merged_rows: list[dict] = []

    for new_row in scanned_rows:
        key = (str(new_row["file_id"]), str(new_row["original_name"]))
        existing_row = existing_map.get(key)
        if existing_row is None:
            merged_rows.append(new_row)
            continue
        merged = dict(existing_row)
        merged.update(
            {
                "pl2_file": new_row["pl2_file"],
                "channel": new_row["channel"],
                "unit_index": new_row["unit_index"],
                "source_variable_type": new_row["source_variable_type"],
                "detected_by": new_row["detected_by"],
                "updated_at": new_row["updated_at"],
                "detected_in_latest_scan": "yes",
            }
        )
        if not preserve_manual:
            for column in MANUAL_PRESERVE_COLUMNS:
                merged[column] = new_row[column]
        else:
            if str(merged.get("note", "")).strip() == MISSING_NOTE:
                merged["note"] = ""
            if not str(merged.get("representative_unit", "")).strip():
                merged["representative_unit"] = _default_representative(
                    _unit_table_cfg(config).get("default_values", {}),
                    str(merged.get("unit_id", new_row["unit_id"])),
                )
        if not str(merged.get("created_at", "")).strip():
            merged["created_at"] = new_row["created_at"]
        merged_rows.append(merged)

    for existing_row in result_df.itertuples(index=False):
        key = (str(existing_row.file_id), str(existing_row.original_name))
        if key in scanned_keys:
            continue
        stale = existing_row._asdict()
        stale["detected_in_latest_scan"] = "no"
        stale["updated_at"] = _now_str()
        if not str(stale.get("note", "")).strip():
            stale["note"] = MISSING_NOTE
        merged_rows.append(stale)

    merged_df = _ensure_columns(pd.DataFrame(merged_rows))
    merged_df["channel_sort"] = pd.to_numeric(merged_df["channel"], errors="coerce").fillna(10**9)
    merged_df["original_name_sort"] = merged_df["original_name"].astype(str).str.lower()
    merged_df = merged_df.sort_values(
        by=["file_id", "channel_sort", "original_name_sort", "unit_index"],
        kind="stable",
    ).drop(columns=["channel_sort", "original_name_sort"])
    return merged_df.reset_index(drop=True)


def build_unit_quality_table(config: dict, logger: PipelineLogger) -> Path:
    paths = resolve_project_paths(config)
    unit_table_path = paths["unit_quality_path"]
    scans = scan_pl2_units(config, paths, logger)
    scanned_rows: list[dict] = []
    per_file_stats: dict[str, dict] = {}
    for file_id, payload in scans.items():
        rows = build_unit_rows_for_file(
            config=config,
            file_id=file_id,
            pl2_file=payload["pl2_file"],
            unit_names=payload["units"],
        )
        for row in rows:
            row["detected_by"] = payload.get("detected_by", row.get("detected_by", "nex"))
        scanned_rows.extend(rows)
        per_file_stats[file_id] = {
            "pl2_file": payload["pl2_file"],
            "n_units_detected": len(payload["units"]),
        }

    existing_df = _read_existing_unit_table(unit_table_path)
    merged_df = merge_unit_quality_table(existing_df, scanned_rows, config)
    try:
        write_table(merged_df, unit_table_path)
    except Exception as exc:
        logger.log(
            "build_unit_table",
            "*",
            "",
            str(unit_table_path),
            "failed",
            "Failed to write unit_quality_table.",
            exception=exc,
        )
        raise

    for file_id, stats in per_file_stats.items():
        existing_rows = existing_df[existing_df["file_id"].astype(str) == str(file_id)] if not existing_df.empty else pd.DataFrame()
        existing_names = set(existing_rows["original_name"].astype(str).tolist()) if not existing_rows.empty else set()
        detected_names = {
            str(row["original_name"])
            for row in scanned_rows
            if str(row["file_id"]) == str(file_id)
        }
        existing_count = len(existing_names & detected_names)
        current_rows = merged_df[merged_df["file_id"].astype(str) == str(file_id)]
        missing_count = int((current_rows["detected_in_latest_scan"].astype(str) == "no").sum()) if not current_rows.empty else 0
        added_count = len(detected_names - existing_names)
        logger.log(
            "build_unit_table",
            str(file_id),
            stats["pl2_file"],
            str(unit_table_path),
            "success",
            f"n_units_detected={stats['n_units_detected']}; n_units_added={added_count}; n_units_existing={existing_count}; n_units_missing_from_latest_scan={missing_count}",
        )

    return unit_table_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or update unit_quality_table from PL2 neuron variable names.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        build_unit_quality_table(config=config, logger=logger)
        return 0
    except Exception as exc:
        logger.log(
            "build_unit_table",
            "*",
            str(Path(args.config).resolve()),
            "",
            "failed",
            "build_unit_quality_table terminated with an exception.",
            exception=exc,
        )
        return 1
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
