from __future__ import annotations

import argparse
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from scripts.export_figures import _make_placeholder_png, generate_summary_figures
from scripts.experimental.origin_ready.opju_compat import save_origin_project_from_outputs
from utils.aligned_utils import aligned_window_tag
from utils.analysis_mode_utils import resolve_effective_analysis_mode
from utils.file_id_utils import canonicalize_file_id, legacy_file_id_candidates
from utils.event_utils import derive_light_on_off_from_intervals, read_light_intervals
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table
from utils.unit_selection import select_quality_table_cohort, write_cohort_metadata

try:
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover
    win32com = None


def _plotting_cfg(config: dict) -> dict:
    return config.get("plotting", config.get("neuroexplorer", {}).get("plotting", {}))


def _aligned_cfg(config: dict) -> dict:
    return config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))


class OriginAdapter:
    """
    Replaceable adapter for OriginPro automation.

    The exact COM API may differ by Origin version. The TODO blocks identify the
    minimum expected behavior. The pipeline remains functional via matplotlib fallback.
    """

    def __init__(self, use_com: bool = True):
        self.use_com = use_com
        self.app = None

    def open_origin(self) -> None:
        if not self.use_com:
            raise RuntimeError("Origin COM disabled by config.")
        if win32com is None:
            raise RuntimeError("pywin32 is not installed, so Origin COM automation is unavailable.")
        try:
            # TODO: verify the correct ProgID for the installed OriginPro version.
            self.app = win32com.client.Dispatch("Origin.ApplicationSI")
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Failed to create Origin COM object. Confirm ProgID and Origin version.") from exc

    def import_csv(self, csv_path: Path) -> None:
        # TODO: implement workbook import into Origin.
        raise NotImplementedError(f"TODO: implement Origin CSV import for {csv_path}")

    def apply_template(self, template_path: Path) -> None:
        # TODO: implement graph template application.
        raise NotImplementedError(f"TODO: implement Origin template application for {template_path}")

    def add_light_band(self, x_start: float, x_end: float) -> None:
        # TODO: implement axis-bound reference band / color band in data coordinates.
        raise NotImplementedError(f"TODO: implement Origin light band from {x_start} to {x_end}")

    def export_graph(self, output_path: Path) -> None:
        # TODO: implement graph export.
        raise NotImplementedError(f"TODO: implement Origin graph export for {output_path}")

    def close(self) -> None:
        self.app = None


def _plot_style(ax: plt.Axes, x: pd.Series, y: pd.Series, style: str, label: str | None = None) -> None:
    if style == "bar":
        width = float(x.diff().median()) if len(x) > 1 else 1.0
        ax.bar(x, y, width=width, align="center", label=label)
    elif style == "line":
        ax.plot(x, y, linewidth=1.6, label=label)
    else:
        ax.step(x, y, where="mid", linewidth=1.6, label=label)


def _matplotlib_export(
    *,
    x: pd.Series,
    y: pd.Series,
    title: str,
    xlabel: str,
    ylabel: str,
    band_ranges: list[tuple[float, float]],
    output_path: Path,
    config: dict,
    logger: PipelineLogger,
    file_id: str,
    x_limits: tuple[float, float] | None = None,
) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for band_start, band_end in band_ranges:
        ax.axvspan(
            band_start,
            band_end,
            color=config["origin"]["light_band_color"],
            alpha=float(config["origin"]["light_band_alpha"]),
            zorder=0,
        )
    _plot_style(ax, x, y, config["origin"]["plot_style"])
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if x_limits is not None:
        ax.set_xlim(*x_limits)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=int(config["origin"]["dpi"]), bbox_inches="tight")
    plt.close(fig)
    logger.log("plot_in_origin", file_id, "", str(output_path), "success", "Exported figure with matplotlib fallback.")


def _matplotlib_overlay_export(
    *,
    data: pd.DataFrame,
    x_col: str,
    y_col: str,
    unit_col: str,
    title: str,
    xlabel: str,
    ylabel: str,
    band_ranges: list[tuple[float, float]],
    output_path: Path,
    config: dict,
    logger: PipelineLogger,
    file_id: str,
) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    for band_start, band_end in band_ranges:
        ax.axvspan(
            band_start,
            band_end,
            color=config["origin"]["light_band_color"],
            alpha=float(config["origin"]["light_band_alpha"]),
            zorder=0,
        )
    for unit_id, sub_df in data.groupby(unit_col, sort=False):
        _plot_style(ax, sub_df[x_col], sub_df[y_col], config["origin"]["plot_style"], label=str(unit_id))
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=int(config["origin"]["dpi"]), bbox_inches="tight")
    plt.close(fig)
    logger.log("plot_in_origin", file_id, "", str(output_path), "success", "Exported all-units overlay with matplotlib fallback.")


def _summary_bar_export(
    *,
    summary_df: pd.DataFrame,
    title: str,
    output_path: Path,
    config: dict,
    logger: PipelineLogger,
    file_id: str,
) -> None:
    fig, ax = plt.subplots(figsize=(5.5, 3.8))
    values = [
        float(summary_df["baseline_hz"].mean()),
        float(summary_df["light_hz"].mean()),
        float(summary_df["post_hz"].mean()),
    ]
    ax.bar(["Baseline", "Light", "Post"], values, color=["#9BA7B0", "#F3C969", "#8BC6A2"])
    ax.set_title(title)
    ax.set_ylabel("Firing rate (Hz)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=int(config["origin"]["dpi"]), bbox_inches="tight")
    plt.close(fig)
    logger.log("plot_in_origin", file_id, "", str(output_path), "success", "Exported pre/light/post summary figure.")


def _load_intervals_for_file(config: dict, paths: dict, file_id: str, stim_sub: pd.DataFrame) -> list[tuple[float, float]]:
    if not bool(stim_sub["has_light_bool"].any()):
        return []
    neuro_cfg = config.get("neuroexplorer", {})
    events_cfg = neuro_cfg.get("events", {})
    interval_cfg = neuro_cfg.get("interval", {})
    mode = events_cfg.get("stimulus_input_mode", "event")
    if mode == "interval":
        interval_path = paths["events_export_dir"] / interval_cfg.get("interval_csv_pattern", "{file_id}_Light_Interval.csv").format(file_id=file_id)
        if interval_path.exists():
            return read_light_intervals(interval_path, delimiter=interval_cfg.get("delimiter", ","))
    return [(float(row.light_on_s), float(row.light_off_s)) for row in stim_sub.itertuples(index=False)]


def _unit_key_candidates(file_units: pd.DataFrame) -> set[str]:
    keys: set[str] = set()
    for column in ("unit_id", "original_name"):
        if column in file_units.columns:
            keys.update(file_units[column].dropna().astype(str))
    return keys


def _expected_export_with_legacy(config: dict, paths: dict, file_id: str, pl2_file: str, kind: str, logger: PipelineLogger) -> Path:
    export_cfg = config.get("neuroexplorer", {}).get("export", {})
    if kind == "fullrate":
        pattern = export_cfg.get("expected_fullrate_pattern", "{file_id}_FullRate_bin{bin_width_s}s.csv")
        output_dir = paths["nex_fullrate_dir"]
        bin_width = config["neuroexplorer"]["fullrate"]["bin_width_s"]
    else:
        pattern = export_cfg.get("expected_psth_pattern", "{file_id}_LightOn_PSTH_bin{bin_width_s}s.csv")
        output_dir = paths["nex_psth_dir"]
        bin_width = config.get("neuroexplorer", {}).get("psth", {}).get("bin_width_s", config.get("aligned_rate", {}).get("bin_width_s", 1))
    candidates = [
        output_dir / pattern.format(file_id=candidate, bin_width_s=bin_width)
        for candidate in legacy_file_id_candidates(file_id, pl2_file, config)
    ]
    if kind == "fullrate":
        for candidate_file_id in legacy_file_id_candidates(file_id, pl2_file, config):
            candidates.extend(sorted(output_dir.glob(f"{candidate_file_id}_FullRate_bin*s.csv")))
    else:
        for candidate_file_id in legacy_file_id_candidates(file_id, pl2_file, config):
            candidates.extend(sorted(output_dir.glob(f"{candidate_file_id}_LightOn_PSTH_bin*s.csv")))
    deduped: list[Path] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    candidates = deduped
    canonical_path = candidates[0]
    for candidate in candidates:
        if candidate.exists():
            if candidate != canonical_path:
                logger.log(
                    "plot_in_origin",
                    file_id,
                    str(candidate),
                    str(canonical_path),
                    "warning",
                    f"Using legacy file_id {kind} CSV path; please rerun neuroexplorer_export after canonicalizing file_id.",
                )
            return candidate
    return canonical_path


def _resolve_psth_duration(intervals: list[tuple[float, float]], policy: str) -> float:
    durations = [end - start for start, end in intervals]
    unique = sorted({round(value, 6) for value in durations})
    if len(unique) == 1:
        return durations[0]
    if policy == "first":
        return durations[0]
    if policy == "median":
        return float(statistics.median(durations))
    if policy == "max":
        return max(durations)
    raise ValueError("PSTH duration policy set to error and interval durations are inconsistent.")


def plot_in_origin(config: dict, logger: PipelineLogger) -> None:
    paths = resolve_project_paths(config)
    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    unit_df, cohort = select_quality_table_cohort(
        config,
        module="plot_in_origin",
        logger=logger,
        duplicate_policy=config.get("unit_selection", {}).get("duplicate_policy", "keep_all"),
    )
    file_id_column = config["project"]["file_id_column"]
    if "pl2_file" not in unit_df.columns:
        unit_df["pl2_file"] = ""
    stim_df[file_id_column] = [
        canonicalize_file_id(str(row[file_id_column]), row.get("pl2_file"), config)
        for row in stim_df.to_dict("records")
    ]
    unit_df[file_id_column] = [
        canonicalize_file_id(str(row[file_id_column]), row.get("pl2_file"), config)
        for row in unit_df.to_dict("records")
    ]
    included_df = unit_df.copy()
    plotting_cfg = _plotting_cfg(config)
    aligned_cfg = _aligned_cfg(config)

    psth_path = paths["nex_psth_dir"] / "*"
    fullrate_path = paths["nex_fullrate_dir"] / "*"
    logger.log("plot_in_origin", "*", str(psth_path), str(fullrate_path), "success", "Scanning exported rate tables.")

    origin_adapter = OriginAdapter(use_com=config["origin"].get("use_com", True))
    if not config.get("run", {}).get("dry_run", False):
        write_cohort_metadata(cohort, paths["figure_summary_dir"], stem="plot_unit_cohort")

    for file_id, file_units in included_df.groupby(file_id_column, sort=False):
        stim_sub = stim_df[stim_df[file_id_column] == file_id]
        pl2_file = str(stim_sub["pl2_file"].iloc[0]) if not stim_sub.empty else str(file_id)
        psth_csv = _expected_export_with_legacy(config, paths, str(file_id), pl2_file, "psth", logger)
        fullrate_csv = _expected_export_with_legacy(config, paths, str(file_id), pl2_file, "fullrate", logger)
        aligned_csv = paths["nex_aligned_rate_dir"] / f"{file_id}_LightAlignedRate_{aligned_window_tag(aligned_cfg)}_bin{float(aligned_cfg['bin_width_s']):g}s.csv"
        summary_csv = paths["nex_aligned_rate_dir"] / f"{file_id}_PreLightPostSummary.csv"
        has_psth = psth_csv.exists()
        has_fullrate = fullrate_csv.exists()
        has_aligned = aligned_csv.exists()
        has_summary = summary_csv.exists()
        has_light = bool(stim_sub["has_light_bool"].any()) if not stim_sub.empty else None
        analysis_mode = resolve_effective_analysis_mode(
            config,
            has_light=has_light,
            has_fullrate=has_fullrate,
            has_aligned_assets=has_aligned or has_summary,
        )
        if analysis_mode == "fullrate_aligned" and not has_fullrate:
            logger.log(
                "plot_in_origin",
                str(file_id),
                "",
                str(fullrate_csv),
                "warning",
                "Missing standardized FullRate CSV. fullrate_aligned plotting cannot continue for this file.",
            )
            continue
        if analysis_mode != "fullrate_aligned" and not has_psth and not has_fullrate:
            logger.log(
                "plot_in_origin",
                str(file_id),
                str(psth_csv),
                str(fullrate_csv),
                "warning",
                "Missing standardized PSTH and FullRate CSV. Skipping this file.",
            )
            continue

        psth_df = read_table(psth_csv) if has_psth else pd.DataFrame()
        full_df = read_table(fullrate_csv) if has_fullrate else pd.DataFrame()
        aligned_df = read_table(aligned_csv) if has_aligned else pd.DataFrame()
        summary_df = read_table(summary_csv) if has_summary else pd.DataFrame()
        if stim_sub.empty:
            logger.log("plot_in_origin", str(file_id), "", "", "warning", "No stimulation schedule rows found for this file.")
            continue

        if analysis_mode != "fullrate_aligned" and not has_psth:
            logger.log(
                "plot_in_origin",
                str(file_id),
                str(psth_csv),
                "",
                "warning",
                "Missing standardized PSTH CSV. PSTH figures will be skipped for this file.",
            )
        if not has_fullrate:
            logger.log(
                "plot_in_origin",
                str(file_id),
                "",
                str(fullrate_csv),
                "warning",
                "Missing standardized FullRate CSV. Full-session figures will be skipped for this file.",
            )
        if analysis_mode == "fullrate_aligned" and has_light is not False and not has_aligned:
            logger.log(
                "plot_in_origin",
                str(file_id),
                str(aligned_csv),
                "",
                "warning",
                "Missing aligned-rate CSV. Run build_aligned_rate_from_fullrate first.",
            )
        if analysis_mode == "fullrate_aligned" and has_light is not False and not has_summary:
            logger.log(
                "plot_in_origin",
                str(file_id),
                str(summary_csv),
                "",
                "warning",
                "Missing pre/light/post summary CSV. Run build_aligned_rate_from_fullrate first.",
            )

        intervals = _load_intervals_for_file(config, paths, str(file_id), stim_sub)
        psth_duration_policy = plotting_cfg.get("psth_duration_policy", "median")
        if intervals:
            try:
                psth_duration = _resolve_psth_duration(intervals, psth_duration_policy)
            except ValueError:
                logger.log(
                    "plot_in_origin",
                    str(file_id),
                    "",
                    "",
                    "warning",
                    "Interval durations are inconsistent and psth_duration_policy=error. Falling back to median duration.",
                )
                psth_duration = float(statistics.median([end - start for start, end in intervals]))
            if len({round(end - start, 6) for start, end in intervals}) > 1:
                logger.log(
                    "plot_in_origin",
                    str(file_id),
                    "",
                    "",
                    "warning",
                    f"Interval durations vary for this file. Using policy={psth_duration_policy} and resolved duration={psth_duration}.",
                )
            psth_bands = [(0.0, psth_duration)]
        else:
            psth_duration = 0.0
            psth_bands = []
        full_bands = intervals

        for unit_row in file_units.itertuples(index=False):
            unit_id = str(unit_row.unit_id)
            unit_key_candidates = {unit_id, str(unit_row.original_name)}
            psth_unit = (
                psth_df[psth_df["unit_id"].astype(str).isin(unit_key_candidates)]
                if has_psth and "unit_id" in psth_df.columns
                else pd.DataFrame()
            )
            full_unit = (
                full_df[full_df["unit_id"].astype(str).isin(unit_key_candidates)]
                if has_fullrate and "unit_id" in full_df.columns
                else pd.DataFrame()
            )

            psth_png = paths["figure_psth_dir"] / f"{file_id}_{unit_id}_PSTH.png"
            full_png = paths["figure_fullrate_dir"] / f"{file_id}_{unit_id}_FullRate.png"
            aligned_png = paths["figure_aligned_dir"] / f"{file_id}_{unit_id}_AlignedRate_{aligned_window_tag(aligned_cfg)}.png"
            prepost_png = paths["figure_prepost_dir"] / f"{file_id}_{unit_id}_PreLightPost.png"

            if analysis_mode != "fullrate_aligned" and (not has_psth or psth_unit.empty):
                logger.log("plot_in_origin", str(file_id), str(psth_csv), str(psth_png), "warning", f"No PSTH data found for {unit_id}.")
            elif analysis_mode != "fullrate_aligned":
                _matplotlib_export(
                    x=psth_unit["bin_center_s"],
                    y=psth_unit["firing_rate_hz"],
                    title=f"{file_id} | {unit_id} | PSTH",
                    xlabel="Time from Light_On (s)",
                    ylabel="Firing rate (Hz)",
                    band_ranges=psth_bands,
                    output_path=psth_png,
                    config=config,
                    logger=logger,
                    file_id=str(file_id),
                )

            if not has_fullrate or full_unit.empty:
                logger.log("plot_in_origin", str(file_id), str(fullrate_csv), str(full_png), "warning", f"No full-session data found for {unit_id}.")
            else:
                _matplotlib_export(
                    x=full_unit["time_bin_center_s"],
                    y=full_unit["firing_rate_hz"],
                    title=f"{file_id} | {unit_id} | Full-session firing rate",
                    xlabel="Absolute recording time (s)",
                    ylabel="Firing rate (Hz)",
                    band_ranges=full_bands,
                    output_path=full_png,
                    config=config,
                    logger=logger,
                    file_id=str(file_id),
                )

            if analysis_mode == "fullrate_aligned":
                aligned_unit = aligned_df[aligned_df["unit_id"].astype(str).isin(unit_key_candidates)] if has_aligned and "unit_id" in aligned_df.columns else pd.DataFrame()
                summary_unit = summary_df[summary_df["unit_id"].astype(str).isin(unit_key_candidates)] if has_summary and "unit_id" in summary_df.columns else pd.DataFrame()
                if has_light is False:
                    aligned_placeholder = paths["figure_aligned_dir"] / f"{file_id}_{unit_id}_AlignedRate_no_light_skipped.png"
                    prepost_placeholder = paths["figure_prepost_dir"] / f"{file_id}_{unit_id}_PreLightPost_no_light_skipped.png"
                    if not config["run"]["dry_run"]:
                        dpi = int(config.get("origin", {}).get("dpi", 300))
                        _make_placeholder_png(aligned_placeholder, "No light event", "Aligned analysis skipped.", dpi)
                        _make_placeholder_png(prepost_placeholder, "No light event", "Pre / light / post summary not applicable.", dpi)
                    logger.log(
                        "plot_in_origin",
                        str(file_id),
                        "",
                        f"{aligned_placeholder}; {prepost_placeholder}",
                        "skipped",
                        f"No light event; aligned and pre/light/post plotting skipped for {unit_id}.",
                    )
                elif not aligned_unit.empty:
                    if "trial_id" in aligned_unit.columns and (aligned_unit["trial_id"].astype(str) == "aggregated").any():
                        aligned_plot_df = aligned_unit[aligned_unit["trial_id"].astype(str) == "aggregated"].copy()
                    elif "aggregation" in aligned_unit.columns and (aligned_unit["aggregation"].astype(str) != "keep_trials").any():
                        aligned_plot_df = aligned_unit[aligned_unit["aggregation"].astype(str) != "keep_trials"].copy()
                    else:
                        aligned_plot_df = aligned_unit.groupby("aligned_time_s", as_index=False)["firing_rate_hz"].mean()
                        if "duration_s" in aligned_unit.columns and aligned_unit["duration_s"].nunique() > 1:
                            logger.log(
                                "plot_in_origin",
                                str(file_id),
                                str(aligned_csv),
                                str(aligned_png),
                                "warning",
                                "Aligned-rate trials have variable durations and keep_trials output. Plotting a mean trace across aligned_time_s and using median duration for the light band.",
                            )
                    duration_value = float(aligned_unit["duration_s"].median()) if "duration_s" in aligned_unit.columns else psth_duration
                    x_min_value = float(aligned_unit["aligned_x_min_s"].min()) if "aligned_x_min_s" in aligned_unit.columns else float(aligned_cfg.get("x_min_s", -60))
                    x_max_value = float(aligned_unit["aligned_x_max_s"].max()) if "aligned_x_max_s" in aligned_unit.columns else float(aligned_cfg.get("x_max_s", 85))
                    _matplotlib_export(
                        x=aligned_plot_df["aligned_time_s"],
                        y=aligned_plot_df["firing_rate_hz"],
                        title=f"{file_id} | {unit_id} | Light-aligned rate ({x_min_value:g} to {x_max_value:g} s)",
                        xlabel="Time from light onset (s)",
                        ylabel="Firing rate (Hz)",
                        band_ranges=[(float(plotting_cfg.get("aligned_light_band_start_s", 0.0)), duration_value)],
                        output_path=aligned_png,
                        config=config,
                        logger=logger,
                        file_id=str(file_id),
                        x_limits=(x_min_value, x_max_value),
                    )
                elif has_light is not False:
                    logger.log("plot_in_origin", str(file_id), str(aligned_csv), str(aligned_png), "warning", f"No aligned-rate data found for {unit_id}.")
                if has_light is False:
                    pass
                elif not summary_unit.empty:
                    _summary_bar_export(
                        summary_df=summary_unit[summary_unit["trial_id"].astype(str).isin(["aggregated"])].copy() if "trial_id" in summary_unit.columns and (summary_unit["trial_id"].astype(str) == "aggregated").any() else summary_unit,
                        title=f"{file_id} | {unit_id} | Pre / light / post",
                        output_path=prepost_png,
                        config=config,
                        logger=logger,
                        file_id=str(file_id),
                    )
                else:
                    logger.log("plot_in_origin", str(file_id), str(summary_csv), str(prepost_png), "warning", f"No pre/light/post summary data found for {unit_id}.")

        filtered_all_units = (
            full_df[full_df["unit_id"].astype(str).isin(_unit_key_candidates(file_units))]
            if has_fullrate and "unit_id" in full_df.columns
            else pd.DataFrame()
        )
        if not filtered_all_units.empty:
            overlay_png = paths["figure_summary_dir"] / f"{file_id}_AllUnits_FullRate.png"
            _matplotlib_overlay_export(
                data=filtered_all_units,
                x_col="time_bin_center_s",
                y_col="firing_rate_hz",
                unit_col="unit_id",
                title=f"{file_id} | All included units | Full-session firing rate",
                xlabel="Absolute recording time (s)",
                ylabel="Firing rate (Hz)",
                band_ranges=full_bands,
                output_path=overlay_png,
                config=config,
                logger=logger,
                file_id=str(file_id),
            )
        elif has_fullrate:
            logger.log(
                "plot_in_origin",
                str(file_id),
                str(fullrate_csv),
                "",
                "warning",
                "No full-session overlay data available for included units.",
            )

    generate_summary_figures(config=config, logger=logger)
    if bool(config.get("origin", {}).get("save_opju", False)):
        if bool(config.get("run", {}).get("dry_run", False)):
            logger.log(
                "origin_plot",
                "*",
                "",
                "",
                "skipped",
                "Dry run enabled; OriginPro OPJU archive was not generated.",
                event="save_opju",
            )
        else:
            save_origin_project_from_outputs(config=config, logger=logger, paths=paths)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot PSTH/full-rate figures using Origin or matplotlib fallback.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        plot_in_origin(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
