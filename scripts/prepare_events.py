from __future__ import annotations

import argparse
from pathlib import Path

from utils.event_utils import (
    derive_light_on_off_from_intervals,
    resolve_event_file_path,
    resolve_interval_file_path,
    read_light_intervals,
    validate_event_file,
    validate_interval_file,
    write_event_times,
    write_interval_times,
)
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table


def _light_off_was_auto_computed(raw_df) -> dict[str, bool]:
    result: dict[str, bool] = {}
    file_id_column = "file_id"
    for file_id, sub_df in raw_df.groupby(file_id_column, sort=False):
        if "light_off_s" not in sub_df.columns:
            result[str(file_id)] = True
            continue
        raw_values = sub_df["light_off_s"]
        result[str(file_id)] = raw_values.isna().any() or raw_values.astype(str).str.strip().eq("").any()
    return result


def _validate_event_pairs(light_on_times: list[float], light_off_times: list[float], file_id: str) -> None:
    if len(light_on_times) != len(light_off_times):
        raise ValueError(
            f"Light_On and Light_Off count mismatch for {file_id}: {len(light_on_times)} vs {len(light_off_times)}"
        )
    for on_value, off_value in zip(light_on_times, light_off_times):
        if float(off_value) <= float(on_value):
            raise ValueError(f"Light_Off must be greater than Light_On for {file_id}: {on_value} -> {off_value}")


def _resolve_interval_path(config: dict, paths: dict, file_id: str) -> Path:
    pattern = config["neuroexplorer"]["interval"]["interval_csv_pattern"]
    return resolve_interval_file_path(paths["events_export_dir"], file_id, pattern)


def prepare_events(config: dict, logger: PipelineLogger) -> None:
    paths = resolve_project_paths(config)
    raw_stim_df = read_table(paths["stim_schedule_path"])
    stim_df = normalize_stim_schedule(raw_stim_df, file_id_column=config["project"]["file_id_column"])
    auto_computed_map = _light_off_was_auto_computed(raw_stim_df)
    file_id_column = config["project"]["file_id_column"]

    if config["run"]["dry_run"]:
        logger.log(
            "prepare_events",
            "*",
            str(paths["stim_schedule_path"]),
            str(paths["events_export_dir"]),
            "skipped",
            "Dry-run mode: event CSVs were not written.",
        )
        return

    for file_id, sub_df in stim_df.groupby(file_id_column, sort=False):
        if not bool(sub_df["has_light_bool"].any()):
            logger.log(
                "prepare_events",
                str(file_id),
                str(paths["stim_schedule_path"]),
                "",
                "skipped",
                "No light event; event helper CSV generation skipped.",
            )
            continue
        stimulus_input_mode = config["neuroexplorer"]["events"].get("stimulus_input_mode", "event")
        on_path = resolve_event_file_path(
            paths["events_export_dir"],
            str(file_id),
            config["neuroexplorer"]["events"]["event_on_name"],
            stimulus_input_mode,
        )
        off_path = resolve_event_file_path(
            paths["events_export_dir"],
            str(file_id),
            config["neuroexplorer"]["events"]["event_off_name"],
            stimulus_input_mode,
        )
        interval_path = _resolve_interval_path(config, paths, str(file_id))
        light_on_times = sub_df["light_on_s"].astype(float).tolist()
        light_off_times = sub_df["light_off_s"].astype(float).tolist()

        _validate_event_pairs(light_on_times, light_off_times, str(file_id))

        write_event_times(on_path, light_on_times)
        write_event_times(off_path, light_off_times)
        interval_cfg = config["neuroexplorer"]["interval"]
        interval_name = interval_cfg.get(
            "interval_variable_name",
            config["neuroexplorer"]["events"].get("interval_name", "Light_Interval"),
        )
        write_interval_times(
            interval_path,
            light_on_times,
            light_off_times,
            interval_name=interval_name,
            include_variable_name_first_line=interval_cfg.get("include_variable_name_first_line", True),
        )

        on_lines = validate_event_file(on_path)
        off_lines = validate_event_file(off_path)
        interval_lines = validate_interval_file(interval_path)
        parsed_intervals = read_light_intervals(
            interval_path,
            interval_name=interval_name,
            has_variable_name_first_line=interval_cfg.get("include_variable_name_first_line", True),
            delimiter=interval_cfg["delimiter"],
        )
        derived_on, derived_off, _ = derive_light_on_off_from_intervals(parsed_intervals)

        if len(on_lines) != len(off_lines):
            logger.log(
                "prepare_events",
                str(file_id),
                str(paths["stim_schedule_path"]),
                f"{on_path} | {off_path} | {interval_path}",
                "warning",
                f"Event count mismatch after export: Light_On={len(on_lines)}, Light_Off={len(off_lines)}",
            )
        if len(interval_lines) != len(on_lines):
            logger.log(
                "prepare_events",
                str(file_id),
                str(paths["stim_schedule_path"]),
                str(interval_path),
                "warning",
                f"Interval count mismatch after export: Light_Interval={len(interval_lines)}, Light_On={len(on_lines)}",
            )
        if on_path == off_path:
            raise ValueError(f"Light_On and Light_Off were incorrectly merged into one file for {file_id}")
        if interval_path in {on_path, off_path}:
            raise ValueError(f"Light_Interval was incorrectly merged with event files for {file_id}")
        if derived_on != light_on_times or derived_off != light_off_times:
            raise ValueError(f"Derived Light_On/Light_Off from interval CSV do not match source schedule for {file_id}")

        logger.log(
            "prepare_events",
            str(file_id),
            str(paths["stim_schedule_path"]),
            f"{on_path} | {off_path} | {interval_path}",
            "success",
            (
                f"Wrote event helper and interval files. Light_On path={on_path}; Light_Off path={off_path}; Light_Interval path={interval_path}; "
                f"Light_On count={len(on_lines)}; Light_Off count={len(off_lines)}; "
                f"Light_Interval count={len(interval_lines)}; "
                f"auto_computed_light_off={auto_computed_map.get(str(file_id), False)}"
            ),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Light_On / Light_Off event CSV files.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        prepare_events(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
