from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table, write_table


VALID_DUPLICATE_POLICIES = {"keep_all", "exclude_duplicates", "keep_representative_only"}
COHORT_COLUMNS = [
    "file_id",
    "source_unit_id",
    "unit_id",
    "original_name",
    "include_raw",
    "include_status",
    "included",
    "exclusion_reason",
    "quality_exclusion_reason",
    "duplicate_of",
    "representative_unit",
    "duplicate_policy",
    "metadata_matched",
]


@dataclass(frozen=True)
class UnitCohort:
    decisions: pd.DataFrame
    quality_table: pd.DataFrame
    metadata: dict

    @property
    def included(self) -> pd.DataFrame:
        return self.decisions[self.decisions["included"]].copy()

    @property
    def excluded(self) -> pd.DataFrame:
        return self.decisions[~self.decisions["included"]].copy()


def unit_selection_config(config: dict) -> dict:
    return {
        "required": True,
        "include_value": "yes",
        "fail_on_unmatched_data_units": True,
        "duplicate_policy": "keep_all",
        **dict(config.get("unit_selection", {})),
    }


def _text(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _strict_include_status(value, include_value: str) -> tuple[str, bool]:
    text = _text(value).lower()
    if not text:
        return "blank", False
    if text == include_value:
        return include_value, True
    return text, False


def load_unit_quality_table(config: dict) -> pd.DataFrame:
    path = resolve_project_paths(config)["unit_quality_path"]
    if not path.exists():
        raise FileNotFoundError(
            f"Required unit_quality_table does not exist: {path}. "
            "Create/update it with: python run_pipeline.py --config <config.yaml> --module build_unit_table"
        )
    table = read_table(path).copy()
    required = {"file_id", "unit_id", "include"}
    missing = required - set(table.columns)
    if missing:
        raise ValueError(
            f"unit_quality_table {path} is missing required columns {sorted(missing)}. "
            "Rebuild the table, then restore any reviewed manual fields."
        )
    for column in [
        "original_name",
        "channel",
        "exclusion_reason",
        "duplicate_of",
        "representative_unit",
        "note",
        "detected_in_latest_scan",
    ]:
        if column not in table.columns:
            table[column] = ""
    table["file_id"] = table["file_id"].map(_text)
    table["unit_id"] = table["unit_id"].map(_text)
    table["original_name"] = table["original_name"].map(_text)
    invalid_keys = table["file_id"].eq("") | table["unit_id"].eq("")
    if invalid_keys.any():
        raise ValueError(
            f"unit_quality_table {path} contains {int(invalid_keys.sum())} row(s) with blank file_id or unit_id."
        )
    duplicate_keys = table.duplicated(["file_id", "unit_id"], keep=False)
    if duplicate_keys.any():
        keys = table.loc[duplicate_keys, ["file_id", "unit_id"]].drop_duplicates().to_dict("records")
        raise ValueError(f"unit_quality_table contains duplicate (file_id, unit_id) rows: {keys}")
    return table


def _normalize_discovered_units(discovered_units: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(discovered_units, pd.DataFrame):
        raise TypeError("discovered_units must be a pandas DataFrame.")
    required = {"file_id", "unit_id"}
    missing = required - set(discovered_units.columns)
    if missing:
        raise ValueError(f"Analysis input is missing Unit identity columns: {sorted(missing)}")
    units = discovered_units.copy()
    units["file_id"] = units["file_id"].map(_text)
    units["unit_id"] = units["unit_id"].map(_text)
    units = units[(units["file_id"] != "") & (units["unit_id"] != "")]
    return units[["file_id", "unit_id"]].drop_duplicates().reset_index(drop=True)


def select_unit_cohort(
    config: dict,
    discovered_units: pd.DataFrame,
    *,
    module: str,
    logger: PipelineLogger | None = None,
    duplicate_policy: str | None = None,
    minimum_included_units: int = 1,
) -> UnitCohort:
    selection_cfg = unit_selection_config(config)
    include_value = _text(selection_cfg.get("include_value", "yes")).lower()
    if include_value != "yes":
        raise ValueError("unit_selection.include_value must be 'yes'; only literal include: yes is eligible.")
    policy = _text(duplicate_policy or selection_cfg.get("duplicate_policy", "keep_all")).lower()
    if policy not in VALID_DUPLICATE_POLICIES:
        raise ValueError(f"duplicate_policy must be one of {sorted(VALID_DUPLICATE_POLICIES)}; got {policy!r}.")

    units = _normalize_discovered_units(discovered_units)
    if units.empty:
        raise ValueError(f"{module}: no Unit identities were discovered in the analysis inputs.")
    quality = load_unit_quality_table(config)
    decisions: list[dict] = []
    unmatched: list[dict] = []
    for row in units.itertuples(index=False):
        file_rows = quality[quality["file_id"] == row.file_id]
        matches = file_rows[
            (file_rows["unit_id"] == row.unit_id)
            | (file_rows["original_name"].ne("") & (file_rows["original_name"] == row.unit_id))
        ]
        if len(matches) > 1:
            raise ValueError(
                f"{module}: ambiguous unit_quality_table match for file_id={row.file_id!r}, "
                f"data unit_id={row.unit_id!r}."
            )
        if matches.empty:
            unmatched.append({"file_id": row.file_id, "unit_id": row.unit_id})
            decisions.append(
                {
                    "file_id": row.file_id,
                    "source_unit_id": row.unit_id,
                    "unit_id": "",
                    "original_name": "",
                    "include_raw": "",
                    "include_status": "missing_row",
                    "included": False,
                    "exclusion_reason": "missing_from_unit_quality_table",
                    "quality_exclusion_reason": "",
                    "duplicate_of": "",
                    "representative_unit": "",
                    "duplicate_policy": policy,
                    "metadata_matched": False,
                }
            )
            continue

        quality_row = matches.iloc[0]
        include_status, included = _strict_include_status(quality_row.get("include", ""), include_value)
        duplicate_of = _text(quality_row.get("duplicate_of", ""))
        representative = _text(quality_row.get("representative_unit", ""))
        analysis_unit_id = _text(quality_row.get("unit_id", ""))
        exclusion_reason = ""
        if not included:
            exclusion_reason = f"include_{include_status}"
        elif policy == "exclude_duplicates" and duplicate_of:
            included = False
            exclusion_reason = "duplicate_excluded"
        elif policy == "keep_representative_only" and duplicate_of and representative not in {
            analysis_unit_id,
            _text(quality_row.get("original_name", "")),
        }:
            included = False
            exclusion_reason = "duplicate_excluded"
        decisions.append(
            {
                "file_id": row.file_id,
                "source_unit_id": row.unit_id,
                "unit_id": analysis_unit_id,
                "original_name": _text(quality_row.get("original_name", "")),
                "include_raw": _text(quality_row.get("include", "")),
                "include_status": include_status,
                "included": bool(included),
                "exclusion_reason": exclusion_reason,
                "quality_exclusion_reason": _text(quality_row.get("exclusion_reason", "")),
                "duplicate_of": duplicate_of,
                "representative_unit": representative,
                "duplicate_policy": policy,
                "metadata_matched": True,
            }
        )

    decision_df = pd.DataFrame(decisions, columns=COHORT_COLUMNS)
    if unmatched and bool(selection_cfg.get("fail_on_unmatched_data_units", True)):
        preview = unmatched[:10]
        raise ValueError(
            f"{module}: unit_quality_table does not match {len(unmatched)} discovered Unit(s): {preview}. "
            "Run build_unit_table to append new Units, review include values, then rerun the analysis."
        )
    n_included = int(decision_df["included"].sum())
    if n_included < int(minimum_included_units):
        reason_counts = decision_df.loc[~decision_df["included"], "exclusion_reason"].value_counts().to_dict()
        raise ValueError(
            f"{module}: no eligible Unit cohort (included={n_included}, required={minimum_included_units}); "
            f"exclusions={reason_counts}. Set include: yes for reviewed Units in unit_quality_table."
        )
    excluded = decision_df[~decision_df["included"]]
    metadata = {
        "module": module,
        "unit_quality_table": str(resolve_project_paths(config)["unit_quality_path"]),
        "include_rule": "only literal include: yes is included",
        "duplicate_policy": policy,
        "n_units_discovered": int(len(decision_df)),
        "n_units_included": n_included,
        "n_units_excluded": int(len(excluded)),
        "include_status_counts": {str(k): int(v) for k, v in decision_df["include_status"].value_counts(dropna=False).items()},
        "exclusion_reason_counts": {str(k): int(v) for k, v in excluded["exclusion_reason"].value_counts(dropna=False).items()},
        "quality_exclusion_reason_counts": {
            str(k): int(v)
            for k, v in excluded["quality_exclusion_reason"].replace("", "unspecified").value_counts(dropna=False).items()
        },
    }
    if logger is not None:
        logger.log(
            module,
            "*",
            metadata["unit_quality_table"],
            "",
            "success",
            (
                f"Resolved Unit cohort from unit_quality_table: discovered={metadata['n_units_discovered']}; "
                f"included={n_included}; excluded={metadata['n_units_excluded']}; "
                f"include_status_counts={metadata['include_status_counts']}; "
                f"exclusion_reason_counts={metadata['exclusion_reason_counts']}; duplicate_policy={policy}"
            ),
        )
    return UnitCohort(decisions=decision_df, quality_table=quality, metadata=metadata)


def filter_to_included_units(frame: pd.DataFrame, cohort: UnitCohort) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    required = {"file_id", "unit_id"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Cannot apply Unit cohort; data frame is missing columns {sorted(missing)}.")
    keys = cohort.included[["file_id", "source_unit_id"]].rename(columns={"source_unit_id": "unit_id"})
    result = frame.copy()
    result["file_id"] = result["file_id"].map(_text)
    result["unit_id"] = result["unit_id"].map(_text)
    return result.merge(keys.assign(_cohort_include=True), on=["file_id", "unit_id"], how="inner").drop(
        columns=["_cohort_include"]
    )


def select_quality_table_cohort(
    config: dict,
    *,
    module: str,
    logger: PipelineLogger | None = None,
    duplicate_policy: str | None = None,
) -> tuple[pd.DataFrame, UnitCohort]:
    quality = load_unit_quality_table(config)
    cohort = select_unit_cohort(
        config,
        quality[["file_id", "unit_id"]],
        module=module,
        logger=logger,
        duplicate_policy=duplicate_policy,
    )
    keys = cohort.included[["file_id", "unit_id"]]
    selected = quality.merge(keys.assign(_cohort_include=True), on=["file_id", "unit_id"], how="inner").drop(
        columns=["_cohort_include"]
    )
    selected["include_bool"] = True
    return selected, cohort


def write_cohort_metadata(cohort: UnitCohort, output_dir: Path, *, stem: str = "unit_cohort") -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    table_path = output_dir / f"{stem}.csv"
    metadata_path = output_dir / f"{stem}_metadata.json"
    write_table(cohort.decisions, table_path)
    metadata_path.write_text(json.dumps(cohort.metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"table": table_path, "metadata": metadata_path}
