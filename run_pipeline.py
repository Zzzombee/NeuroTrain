from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Callable

from scripts.build_pptx import build_pptx
from scripts.build_aligned_rate_from_fullrate import build_aligned_rate_from_fullrate
from scripts.build_time_cluster_aligned_rate import build_time_cluster_aligned_rate
from scripts.init_project import initialize_project
from scripts.maintenance import canonicalize_project_tables
from scripts.build_prelightpost_statistics import build_prelightpost_statistics
from scripts.build_stim_schedule_from_filenames import build_stim_schedule_from_filenames
from scripts.build_unit_quality_table import build_unit_quality_table
from scripts.export_from_neuroexplorer import export_from_neuroexplorer
from scripts.origin_native.create_origin_templates import create_origin_templates
from scripts.origin_native_plot import origin_native_plot
from scripts.plot_in_origin import plot_in_origin
from scripts.prepare_events import prepare_events
from scripts.time_cluster_permutation import run_time_cluster_permutation
from utils.logging_utils import PipelineLogger
from utils.path_utils import apply_runtime_overrides, load_yaml, resolve_project_paths, save_yaml
from scripts.validate_project import validate_project


MODULE_RUNNERS: dict[str, Callable] = {
    "init_project": lambda **kwargs: None,
    "build_stim_schedule": build_stim_schedule_from_filenames,
    "validate": validate_project,
    "prepare_events": prepare_events,
    "build_unit_table": build_unit_quality_table,
    "neuroexplorer_export": export_from_neuroexplorer,
    "aligned_rate": build_aligned_rate_from_fullrate,
    "time_cluster_aligned_rate": build_time_cluster_aligned_rate,
    "time_cluster_permutation": run_time_cluster_permutation,
    "prelightpost_stats": build_prelightpost_statistics,
    "export_figures": plot_in_origin,
    "python_plot": plot_in_origin,
    "origin_plot": plot_in_origin,
    "origin_native_plot": origin_native_plot,
    "origin_create_templates": create_origin_templates,
    "build_pptx": build_pptx,
}

MODULE_CONFIG_ALIASES: dict[str, tuple[str, ...]] = {
    "export_figures": ("origin_plot", "python_plot"),
}


def _clean_outputs_if_requested(config: dict, paths: dict, logger: PipelineLogger) -> None:
    run_cfg = config.get("run", {})
    if not run_cfg.get("clean_outputs_before_run", False):
        return
    targets = [
        paths["nex_aligned_rate_dir"],
        paths["time_cluster_aligned_rate_dir"],
        paths["figure_aligned_dir"],
        paths["figure_summary_dir"],
        paths["pptx_dir"],
    ]
    if run_cfg.get("clean_fullrate", False):
        targets.append(paths["nex_fullrate_dir"])
    for target_dir in targets:
        removed = 0
        skipped_locked = 0
        if target_dir.exists():
            for child in target_dir.iterdir():
                if child.is_file():
                    try:
                        child.unlink()
                        removed += 1
                    except PermissionError as exc:
                        skipped_locked += 1
                        logger.log(
                            "run_pipeline",
                            "*",
                            str(child),
                            "",
                            "warning",
                            "Could not remove output file before run because it is in use by another process.",
                            exception=exc,
                        )
        logger.log(
            "run_pipeline",
            "*",
            str(target_dir),
            "",
            "success",
            f"Cleaned output directory before run. removed_files={removed}; skipped_locked_files={skipped_locked}",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NeuroTrain event-aligned spike train analysis pipeline.")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument(
        "--module",
        choices=list(MODULE_RUNNERS.keys()),
        help="Run only one module instead of the whole pipeline.",
    )
    parser.add_argument("--project-dir", help="Target project directory for init_project.")
    parser.add_argument("--force", action="store_true", help="Override config and overwrite existing outputs.")
    parser.add_argument("--with-example", action="store_true", help="Write example rows into initialized template tables.")
    parser.add_argument("--config-preset", default="fullrate_aligned", help="Initialization preset for init_project.")
    parser.add_argument("--bin-width", type=float, default=1.0, help="Rate histogram bin width for init_project.")
    parser.add_argument("--pre-margin", type=float, default=60.0, help="Aligned pre-light margin for init_project.")
    parser.add_argument("--post-margin", type=float, default=60.0, help="Aligned post-light margin for init_project.")
    parser.add_argument("--file-id-format", default="{file_index}", help="file_id format for auto-generated stim schedules in init_project.")
    parser.add_argument("--dry-run", action="store_true", help="Override config and do not write outputs.")
    parser.add_argument("--overwrite", action="store_true", help="Override config and overwrite existing outputs.")
    return parser.parse_args()


def run_selected_module(module_name: str, config: dict, logger: PipelineLogger) -> None:
    MODULE_RUNNERS[module_name](config=config, logger=logger)


def _module_enabled(config: dict, module_name: str) -> bool:
    modules_cfg = config.get("run", {}).get("modules", {})
    if module_name in modules_cfg:
        return bool(modules_cfg.get(module_name))
    for alias in MODULE_CONFIG_ALIASES.get(module_name, ()):
        if alias in modules_cfg:
            return bool(modules_cfg.get(alias))
    return False


def _prepare_unit_table_if_needed(config: dict, logger: PipelineLogger) -> None:
    if not config.get("run", {}).get("modules", {}).get("build_unit_table", True):
        return
    unit_cfg = config.get("unit_table", {})
    if not unit_cfg.get("enabled", True):
        return
    analysis_mode = config.get("analysis", {}).get("mode", "fullrate_aligned")
    if analysis_mode not in {"fullrate_aligned", "auto"}:
        return
    paths = resolve_project_paths(config)
    unit_table_exists = paths["unit_quality_path"].exists()
    if unit_cfg.get("auto_build_if_missing", True) and not unit_table_exists:
        logger.log("run_pipeline", "*", "", str(paths["unit_quality_path"]), "success", "unit_quality_table missing; running build_unit_table automatically.")
        build_unit_quality_table(config=config, logger=logger)
        return
    if unit_cfg.get("update_existing", False):
        logger.log("run_pipeline", "*", str(paths["unit_quality_path"]), "", "success", "Updating unit_quality_table before downstream modules.")
        build_unit_quality_table(config=config, logger=logger)


def _prepare_stim_schedule_if_needed(config: dict, logger: PipelineLogger) -> None:
    stim_cfg = config.get("stim_schedule", {})
    if not stim_cfg.get("auto_build_from_filenames", False):
        return
    analysis_mode = config.get("analysis", {}).get("mode", "fullrate_aligned")
    if analysis_mode not in {"fullrate_aligned", "auto"}:
        return
    paths = resolve_project_paths(config)
    stim_exists = paths["stim_schedule_path"].exists()
    if not stim_exists:
        logger.log("run_pipeline", "*", "", str(paths["stim_schedule_path"]), "success", "stim_schedule_master missing; running build_stim_schedule automatically.")
        build_stim_schedule_from_filenames(config=config, logger=logger)
        return
    if stim_cfg.get("update_existing", False):
        logger.log("run_pipeline", "*", str(paths["stim_schedule_path"]), "", "success", "Updating stim_schedule_master before validation.")
        build_stim_schedule_from_filenames(config=config, logger=logger)


def main() -> int:
    args = parse_args()
    if args.module == "init_project":
        if not args.project_dir:
            raise SystemExit("--project-dir is required when --module init_project is used.")
        initialize_project(
            project_dir=Path(args.project_dir),
            force=args.force,
            with_example=args.with_example,
            config_preset=args.config_preset,
            bin_width=args.bin_width,
            pre_margin=args.pre_margin,
            post_margin=args.post_margin,
            file_id_format=args.file_id_format,
        )
        return 0
    if not args.config:
        raise SystemExit("--config is required unless --module init_project is used.")
    config_path = Path(args.config).expanduser().resolve()
    raw_config = load_yaml(config_path)
    config = apply_runtime_overrides(raw_config, dry_run=args.dry_run, overwrite=args.overwrite)
    paths = resolve_project_paths(config)
    logger = PipelineLogger(paths["logs_dir"])
    _clean_outputs_if_requested(config, paths, logger)

    parameter_record = copy.deepcopy(config)
    parameter_record["_runtime"] = {
        "config_path": str(config_path),
        "selected_module": args.module or "all",
    }
    if not config["run"]["dry_run"]:
        save_yaml(parameter_record, paths["logs_dir"] / "parameter_record.yaml")

    try:
        if args.module:
            run_selected_module(args.module, config, logger)
        else:
            _prepare_stim_schedule_if_needed(config, logger)
            _prepare_unit_table_if_needed(config, logger)
            if not config["run"].get("dry_run", False):
                canonicalize_project_tables(config, logger)
            validate_project(config=config, logger=logger)
            module_order = [
                "prepare_events",
                "neuroexplorer_export",
                "aligned_rate",
                "time_cluster_aligned_rate",
                "time_cluster_permutation",
                "prelightpost_stats",
                "export_figures",
                "origin_create_templates",
                "origin_native_plot",
                "build_pptx",
            ]
            for module_name in module_order:
                if _module_enabled(config, module_name):
                    run_selected_module(module_name, config, logger)
                else:
                    logger.log(
                        module="run_pipeline",
                        file_id="*",
                        input_path="",
                        output_path="",
                        status="skipped",
                        message=f"Module disabled by config: {module_name}",
                    )
        return 0
    except Exception as exc:
        logger.log(
            module="run_pipeline",
            file_id="*",
            input_path=str(config_path),
            output_path=str(paths["logs_dir"]),
            status="failed",
            message="Pipeline terminated with an exception.",
            exception=exc,
        )
        if config["run"].get("stop_on_error", False):
            raise
        return 1
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
