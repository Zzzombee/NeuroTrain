from __future__ import annotations

import argparse
from pathlib import Path

from scripts.adapters.neuroexplorer_adapter import NeedsManualActionError, NeuroExplorerAdapter
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_include_column, normalize_stim_schedule, read_table


def _expected_export_path(config: dict, paths: dict, file_id: str, kind: str) -> Path:
    export_cfg = config.get("neuroexplorer", {}).get("export", {})
    if kind == "psth":
        pattern = export_cfg.get("expected_psth_pattern", "{file_id}_LightOn_PSTH_bin{bin_width_s}s.csv")
        output_dir = paths["nex_psth_dir"]
        bin_width = config.get("neuroexplorer", {}).get("psth", {}).get("bin_width_s", config.get("aligned_rate", {}).get("bin_width_s", 1))
    else:
        pattern = export_cfg.get("expected_fullrate_pattern", "{file_id}_FullRate_bin{bin_width_s}s.csv")
        output_dir = paths["nex_fullrate_dir"]
        bin_width = config["neuroexplorer"]["fullrate"]["bin_width_s"]
    filename = pattern.format(file_id=file_id, bin_width_s=bin_width)
    return output_dir / filename


def _selected_unit_names(file_units) -> list[str]:
    names = []
    for row in file_units.itertuples(index=False):
        candidate = getattr(row, "original_name", None) or getattr(row, "unit_id")
        text = str(candidate).strip()
        if text and text not in names:
            names.append(text)
    return names


def _analysis_mode(config: dict) -> str:
    return config.get("analysis", {}).get("mode", "fullrate_aligned")


def _uses_fullrate_aligned_primary(config: dict) -> bool:
    return _analysis_mode(config) in {"fullrate_aligned", "auto"}


def _fullrate_template_name(config: dict) -> str:
    return config["neuroexplorer"]["fullrate"].get(
        "template_name",
        config["neuroexplorer"]["templates"]["fullrate_template_name"],
    )


def _manual_fallback_for_file(
    adapter: NeuroExplorerAdapter,
    file_id: str,
    pl2_path: Path,
    light_on_times: list[float],
    light_off_times: list[float],
    unit_names: list[str],
    psth_output: Path,
    fullrate_output: Path,
) -> None:
    manual_backend = adapter.activate_manual_backend(f"Manual fallback requested for file_id={file_id}")
    manual_backend.open_file(pl2_path)
    analysis_mode = _analysis_mode(adapter.config)
    if not _uses_fullrate_aligned_primary(adapter.config):
        manual_backend.ensure_events(file_id, light_on_times, light_off_times)
    if adapter.config["neuroexplorer"].get("export_psth", True) and not _uses_fullrate_aligned_primary(adapter.config):
        manual_backend.configure_psth_template(
            adapter.config["neuroexplorer"]["templates"]["psth_template_name"],
            adapter.config["neuroexplorer"]["psth"]["reference_event"],
            adapter.config["neuroexplorer"]["psth"]["x_min_s"],
            adapter.config["neuroexplorer"]["psth"]["x_max_s"],
            adapter.config["neuroexplorer"]["psth"]["bin_width_s"],
            adapter.config["neuroexplorer"]["psth"]["histogram_unit"],
        )
        manual_backend.export_psth(file_id, unit_names, psth_output)
    if adapter.config["neuroexplorer"]["fullrate"].get("enabled", False):
        manual_backend.configure_fullrate_template(
            _fullrate_template_name(adapter.config),
            adapter.config["neuroexplorer"]["fullrate"]["bin_width_s"],
            adapter.config["neuroexplorer"]["fullrate"]["histogram_unit"],
        )
        manual_backend.export_fullrate(file_id, unit_names, fullrate_output)


def _run_fullrate_only_export(adapter: NeuroExplorerAdapter, config: dict, file_id: str, unit_names: list[str], fullrate_output: Path) -> None:
    template_name = _fullrate_template_name(config)
    adapter.configure_fullrate_template(
        template_name,
        config["neuroexplorer"]["fullrate"]["bin_width_s"],
        config["neuroexplorer"]["fullrate"]["histogram_unit"],
    )
    adapter.run_template(template_name)
    adapter.export_fullrate(str(file_id), unit_names, fullrate_output)


def _run_psth_export(
    adapter: NeuroExplorerAdapter,
    config: dict,
    file_id: str,
    light_on_times: list[float],
    light_off_times: list[float],
    unit_names: list[str],
    psth_output: Path,
    fullrate_output: Path,
    export_psth: bool,
    export_fullrate: bool,
) -> None:
    adapter.ensure_events(str(file_id), light_on_times, light_off_times)
    adapter.validate_required_events()
    if export_psth:
        reference_event_name = adapter.get_reference_event_name()
        adapter.configure_psth_template(
            config["neuroexplorer"]["templates"]["psth_template_name"],
            reference_event_name,
            config["neuroexplorer"]["psth"]["x_min_s"],
            config["neuroexplorer"]["psth"]["x_max_s"],
            config["neuroexplorer"]["psth"]["bin_width_s"],
            config["neuroexplorer"]["psth"]["histogram_unit"],
        )
        adapter.run_template(config["neuroexplorer"]["templates"]["psth_template_name"])
        adapter.export_psth(str(file_id), unit_names, psth_output)

    if export_fullrate:
        try:
            _run_fullrate_only_export(adapter, config, str(file_id), unit_names, fullrate_output)
        except NeedsManualActionError as exc:
            if config["neuroexplorer"]["fullrate"].get("skip_if_template_missing", True):
                adapter.logger.log(
                    "export_from_neuroexplorer",
                    str(file_id),
                    str(adapter.current_file) if getattr(adapter, "current_file", None) else "",
                    str(fullrate_output),
                    "warning",
                    f"FullRate export skipped. NeuroExplorer does not have the requested template {_fullrate_template_name(config)}. Create it in the GUI or set neuroexplorer.fullrate.enabled=false.",
                    exception=exc,
                )
            else:
                raise


def export_from_neuroexplorer(config: dict, logger: PipelineLogger) -> None:
    paths = resolve_project_paths(config)
    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    unit_df = normalize_include_column(read_table(paths["unit_quality_path"]))
    included_df = unit_df[unit_df["include_bool"]].copy()
    adapter = NeuroExplorerAdapter(config=config, logger=logger)
    analysis_mode = _analysis_mode(config)
    if analysis_mode in {"fullrate_aligned", "auto"}:
        logger.log(
            "export_from_neuroexplorer",
            "*",
            "",
            "",
            "success",
            f"{analysis_mode} mode: preferring fullrate_aligned export path and skipping NeuroExplorer Light_On / Light_Interval event creation unless fallback is required.",
        )

    try:
        adapter.connect()
        if config["neuroexplorer"]["nex_package"].get("smoke_test", True):
            adapter.smoke_test()
        if config["neuroexplorer"]["nex_package"].get("introspect_api", True):
            adapter.introspect()
    except Exception as exc:
        logger.log(
            "export_from_neuroexplorer",
            "*",
            "",
            "",
            "warning",
            "Initial NeuroExplorer connection/introspection failed. Falling back to manual_csv if configured.",
            exception=exc,
        )
        if config["neuroexplorer"]["nex_package"].get("fail_to_manual_csv", True):
            adapter.activate_manual_backend(str(exc)).connect()
        else:
            raise

    for file_id, stim_sub_df in stim_df.groupby(config["project"]["file_id_column"], sort=False):
        file_units = included_df[included_df[config["project"]["file_id_column"]] == file_id]
        unit_names = _selected_unit_names(file_units)
        file_has_light = any(stim_sub_df["has_light"].astype(str).str.strip().str.lower() == "yes") if "has_light" in stim_sub_df.columns else True
        light_on_times = stim_sub_df["light_on_s"].dropna().astype(float).tolist() if file_has_light else []
        light_off_times = stim_sub_df["light_off_s"].dropna().astype(float).tolist() if file_has_light else []
        pl2_path = paths["pl2_dir"] / str(stim_sub_df["pl2_file"].iloc[0])
        psth_output = _expected_export_path(config, paths, str(file_id), "psth")
        fullrate_output = _expected_export_path(config, paths, str(file_id), "fullrate")
        export_psth = config["neuroexplorer"].get("export_psth", True) and not _uses_fullrate_aligned_primary(config)
        export_fullrate = config["neuroexplorer"]["fullrate"].get("enabled", False)

        if config["neuroexplorer"].get("use_existing_csv_if_available", True):
            psth_ready = (not export_psth) or psth_output.exists()
            fullrate_ready = (not export_fullrate) or fullrate_output.exists()
            if psth_ready and fullrate_ready and not config["run"]["overwrite"]:
                logger.log(
                    "export_from_neuroexplorer",
                    str(file_id),
                    str(psth_output),
                    str(fullrate_output),
                    "skipped",
                    "Expected NeuroExplorer CSV outputs already exist.",
                )
                continue

        try:
            adapter.open_file(pl2_path)
            if analysis_mode in {"fullrate_aligned", "auto"}:
                _run_fullrate_only_export(adapter, config, str(file_id), unit_names, fullrate_output)
            else:
                _run_psth_export(
                    adapter,
                    config,
                    str(file_id),
                    light_on_times,
                    light_off_times,
                    unit_names,
                    psth_output,
                    fullrate_output,
                    export_psth,
                    export_fullrate,
                )

            logger.log(
                "export_from_neuroexplorer",
                str(file_id),
                str(pl2_path),
                str(psth_output),
                "success",
                f"Finished NeuroExplorer export flow for this file. analysis_mode={analysis_mode}",
            )
        except NeedsManualActionError as exc:
            if analysis_mode == "auto":
                logger.log(
                    "export_from_neuroexplorer",
                    str(file_id),
                    str(pl2_path),
                    "",
                    "warning",
                    "Primary fullrate_aligned path failed; retrying file with experimental neuroexplorer_psth fallback.",
                    exception=exc,
                )
                fallback_config = dict(config)
                fallback_config["analysis"] = dict(config.get("analysis", {}))
                fallback_config["analysis"]["mode"] = "neuroexplorer_psth"
                fallback_adapter = NeuroExplorerAdapter(config=fallback_config, logger=logger)
                try:
                    fallback_adapter.connect()
                    fallback_adapter.open_file(pl2_path)
                    _run_psth_export(
                        fallback_adapter,
                        fallback_config,
                        str(file_id),
                        light_on_times,
                        light_off_times,
                        unit_names,
                        psth_output,
                        fullrate_output,
                        export_psth,
                        export_fullrate,
                    )
                    logger.log(
                        "export_from_neuroexplorer",
                        str(file_id),
                        str(pl2_path),
                        str(psth_output),
                        "success",
                        "Fallback to neuroexplorer_psth succeeded after fullrate_aligned failed.",
                    )
                    continue
                except Exception as fallback_exc:
                    logger.log(
                        "export_from_neuroexplorer",
                        str(file_id),
                        str(pl2_path),
                        "",
                        "warning",
                        "Experimental neuroexplorer_psth fallback also failed; proceeding to manual CSV fallback for this file.",
                        exception=fallback_exc,
                    )
                finally:
                    fallback_adapter.close_file()
            logger.log(
                "export_from_neuroexplorer",
                str(file_id),
                str(pl2_path),
                "",
                "warning",
                "NeuroExplorer requires manual action or manual CSV fallback for this file.",
                exception=exc,
            )
            _manual_fallback_for_file(
                adapter,
                str(file_id),
                pl2_path,
                light_on_times,
                light_off_times,
                unit_names,
                psth_output,
                fullrate_output,
            )
        except Exception as exc:
            logger.log(
                "export_from_neuroexplorer",
                str(file_id),
                str(pl2_path),
                "",
                "failed",
                "Unexpected NeuroExplorer export failure for this file.",
                exception=exc,
            )
            if config["run"].get("stop_on_error", False):
                raise
        finally:
            adapter.close_file()

    adapter.quit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export PSTH/full-rate tables from NeuroExplorer using nex/manual backends.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        export_from_neuroexplorer(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
