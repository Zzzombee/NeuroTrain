from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from utils.file_id_utils import canonicalize_file_id
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table, write_table


MANUAL_UNIT_COLUMNS = ["include", "exclusion_reason", "duplicate_of", "representative_unit", "note"]


def _backup_table(path: Path) -> None:
    if not path.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}.backup_{timestamp}{path.suffix}")
    backup_path.write_bytes(path.read_bytes())


def _canonicalize_stim_schedule(df: pd.DataFrame, config: dict, logger: PipelineLogger) -> pd.DataFrame:
    if df.empty:
        return df
    rows = []
    for row in df.to_dict("records"):
        old_file_id = str(row.get("file_id", ""))
        new_file_id = canonicalize_file_id(old_file_id, row.get("pl2_file"), config)
        if new_file_id != old_file_id:
            logger.log("maintenance", old_file_id, str(row.get("pl2_file", "")), new_file_id, "success", f"Canonicalized file_id: {old_file_id} -> {new_file_id}")
        row["file_id"] = new_file_id
        rows.append(row)
    canonical_df = pd.DataFrame(rows)
    if "updated_at" in canonical_df.columns:
        canonical_df["updated_at"] = datetime.now().isoformat(timespec="seconds")
    canonical_df = canonical_df.drop_duplicates(subset=["file_id", "pl2_file"], keep="last")
    return canonical_df.reset_index(drop=True)


def _merge_unit_rows(rows: list[dict]) -> dict:
    merged = dict(rows[0])
    for row in rows[1:]:
        for key, value in row.items():
            if key in MANUAL_UNIT_COLUMNS:
                current = str(merged.get(key, "") if pd.notna(merged.get(key, "")) else "").strip()
                candidate = str(value if pd.notna(value) else "").strip()
                if key == "include":
                    if current.lower() == "no" or candidate.lower() == "no":
                        merged[key] = "no"
                    elif not current and candidate:
                        merged[key] = value
                    continue
                if not current and candidate:
                    merged[key] = value
                continue
            current = str(merged.get(key, "") if pd.notna(merged.get(key, "")) else "").strip()
            candidate = str(value if pd.notna(value) else "").strip()
            if not current and candidate:
                merged[key] = value
    return merged


def _canonicalize_unit_table(df: pd.DataFrame, config: dict, logger: PipelineLogger) -> pd.DataFrame:
    if df.empty:
        return df
    rows = []
    for row in df.to_dict("records"):
        old_file_id = str(row.get("file_id", ""))
        new_file_id = canonicalize_file_id(old_file_id, row.get("pl2_file"), config)
        if new_file_id != old_file_id:
            logger.log("maintenance", old_file_id, str(row.get("pl2_file", "")), new_file_id, "success", f"Canonicalized unit file_id: {old_file_id} -> {new_file_id}")
        row["file_id"] = new_file_id
        rows.append(row)
    canonical_df = pd.DataFrame(rows)
    key_column = "original_name" if "original_name" in canonical_df.columns else "unit_id"
    merged_rows = []
    for _, group in canonical_df.groupby(["file_id", key_column], sort=False, dropna=False):
        merged_rows.append(_merge_unit_rows(group.to_dict("records")))
    result = pd.DataFrame(merged_rows)
    if "updated_at" in result.columns:
        result["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return result.reset_index(drop=True)


def canonicalize_project_tables(config: dict, logger: PipelineLogger) -> None:
    maintenance_cfg = config.get("maintenance", {})
    if not maintenance_cfg.get("canonicalize_file_ids", True):
        return
    paths = resolve_project_paths(config)
    should_backup = maintenance_cfg.get("backup_tables_before_canonicalize", True)
    for table_name, path, canonicalizer in [
        ("stim_schedule_master", paths["stim_schedule_path"], _canonicalize_stim_schedule),
        ("unit_quality_table", paths["unit_quality_path"], _canonicalize_unit_table),
    ]:
        if not path.exists():
            continue
        if should_backup:
            _backup_table(path)
        df = read_table(path)
        canonical_df = canonicalizer(df, config, logger)
        write_table(canonical_df, path)
        logger.log("maintenance", "*", str(path), str(path), "success", f"Canonicalized {table_name}. rows_before={len(df)}; rows_after={len(canonical_df)}")
