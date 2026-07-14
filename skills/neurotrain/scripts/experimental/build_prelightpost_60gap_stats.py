from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from utils.aligned_utils import aligned_window_tag
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_stim_schedule, read_table, write_table


MODULE = "prelightpost_60gap_stats"
STATS_COLUMNS = ["unit", "pre_hz", "light_hz", "post_hz"]
QC_COLUMNS = [
    "unit",
    "pre_hz",
    "light_hz",
    "post_hz",
    "max_hz",
    "total_expected_spikes",
    "qc_pass",
    "qc_reason",
]


def _cfg(config: dict) -> dict:
    return config.get("statistics", {}).get("prelightpost_60gap", {})


def _aligned_cfg(config: dict) -> dict:
    return config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))


def _figure_style_cfg(config: dict) -> dict:
    return {**config.get("origin", {}), **config.get("figure_style", {})}


def _window_mean(data: pd.DataFrame, window: list[float] | tuple[float, float]) -> float:
    start_s, end_s = float(window[0]), float(window[1])
    subset = data[(data["aligned_time_s"] >= start_s) & (data["aligned_time_s"] <= end_s)]
    if subset.empty:
        return float("nan")
    return float(subset["firing_rate_hz"].mean())


def _qc_reason(pre_hz: float, light_hz: float, post_hz: float, min_max_hz: float, min_total_spikes: float) -> tuple[str, float, float]:
    values = [pre_hz, light_hz, post_hz]
    if any(pd.isna(value) for value in values):
        return "missing_window_data", float("nan"), float("nan")
    max_hz = float(max(values))
    total_expected_spikes = float(5 * sum(values))
    if max_hz < min_max_hz:
        return "max_hz_below_0.5", max_hz, total_expected_spikes
    if total_expected_spikes < min_total_spikes:
        return "total_expected_spikes_below_10", max_hz, total_expected_spikes
    return "pass", max_hz, total_expected_spikes


def _write_unit_figure(unit_label: str, values: list[float], output_path: Path, config: dict) -> None:
    style_cfg = _figure_style_cfg(config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5.5, 3.8))
    labels = ["pre\n[-60,-55] s", "light\n[5,10] s", "post\n[70,75] s"]
    ax.bar(labels, values, color=["#9BA7B0", "#F3C969", "#8BC6A2"])
    ax.set_title(f"{unit_label} | Pre / light / post 60gap")
    ax.set_ylabel("Firing rate (Hz)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=int(style_cfg.get("dpi", 300)), bbox_inches="tight")
    plt.close(fig)


def _aligned_rate_path(config: dict, paths: dict, file_id: str) -> Path:
    aligned_cfg = _aligned_cfg(config)
    bin_width_s = float(aligned_cfg["bin_width_s"])
    return paths["nex_aligned_rate_dir"] / f"{file_id}_LightAlignedRate_{aligned_window_tag(aligned_cfg)}_bin{bin_width_s:g}s.csv"


def _aligned_rate_path_candidates(config: dict, paths: dict, file_id: str) -> list[Path]:
    candidates = [_aligned_rate_path(config, paths, file_id)]
    if str(file_id).isdigit():
        padded = f"{int(file_id):02d}"
        padded_path = _aligned_rate_path(config, paths, padded)
        if padded_path not in candidates:
            candidates.append(padded_path)
    return candidates


def build_prelightpost_60gap_stats(config: dict, logger: PipelineLogger) -> None:
    stats_cfg = _cfg(config)
    if not stats_cfg.get("enabled", True):
        logger.log(MODULE, "*", "", "", "skipped", "statistics.prelightpost_60gap.enabled=false")
        return

    paths = resolve_project_paths(config)
    output_csv = paths["statistics_dir"] / "all_units_pre_light_post_60gap.csv"
    qc_csv = paths["statistics_dir"] / "all_units_pre_light_post_60gap_qc.csv"
    figure_dir = paths["figure_prepost_60gap_dir"]
    pre_window = stats_cfg.get("pre_window_s", [-60, -55])
    light_window = stats_cfg.get("light_window_s", [5, 10])
    post_window = stats_cfg.get("post_window_s", [70, 75])
    qc_cfg = stats_cfg.get("qc", {})
    min_max_hz = float(qc_cfg.get("min_max_hz", 0.5))
    min_total_spikes = float(qc_cfg.get("min_total_expected_spikes", 10))

    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    rows: list[dict] = []
    qc_rows: list[dict] = []
    n_units_processed = 0
    n_units_skipped = 0

    for file_id, stim_sub in stim_df.groupby(config["project"]["file_id_column"], sort=False):
        has_light = any(stim_sub["has_light"].astype(str).str.strip().str.lower() == "yes") if "has_light" in stim_sub.columns else True
        event_group = " ".join(stim_sub.get("event_group", pd.Series(dtype=str)).astype(str).str.lower().tolist())
        if not has_light or "nolight" in event_group:
            n_units_skipped += 1
            logger.log(MODULE, str(file_id), "", "", "skipped", "No-light file skipped for 60gap pre/light/post stats.")
            continue

        aligned_candidates = _aligned_rate_path_candidates(config, paths, str(file_id))
        aligned_path = next((path for path in aligned_candidates if path.exists()), aligned_candidates[0])
        if not aligned_path.exists():
            logger.log(
                MODULE,
                str(file_id),
                "",
                "; ".join(str(path) for path in aligned_candidates),
                "warning",
                "Aligned-rate CSV missing; skipping 60gap stats for this file.",
            )
            continue
        try:
            aligned_df = read_table(aligned_path)
        except Exception as exc:
            logger.log(MODULE, str(file_id), str(aligned_path), "", "warning", "Failed to read aligned-rate CSV.", exception=exc)
            continue

        required = {"unit_id", "aligned_time_s", "firing_rate_hz"}
        missing = required - set(aligned_df.columns)
        if missing:
            logger.log(MODULE, str(file_id), str(aligned_path), "", "warning", f"Aligned-rate CSV missing required columns: {sorted(missing)}")
            continue

        work_df = aligned_df.copy()
        work_df["aligned_time_s"] = pd.to_numeric(work_df["aligned_time_s"], errors="coerce")
        work_df["firing_rate_hz"] = pd.to_numeric(work_df["firing_rate_hz"], errors="coerce")
        work_df = work_df.dropna(subset=["aligned_time_s", "firing_rate_hz"])
        if "trial_id" in work_df.columns and (work_df["trial_id"].astype(str) == "aggregated").any():
            work_df = work_df[work_df["trial_id"].astype(str) == "aggregated"].copy()

        for unit_id, unit_df in work_df.groupby("unit_id", sort=False):
            unit_label = f"{file_id}_{unit_id}"
            pre_hz = _window_mean(unit_df, pre_window)
            light_hz = _window_mean(unit_df, light_window)
            post_hz = _window_mean(unit_df, post_window)
            rows.append({"unit": unit_label, "pre_hz": pre_hz, "light_hz": light_hz, "post_hz": post_hz})
            reason, max_hz, total_expected_spikes = _qc_reason(pre_hz, light_hz, post_hz, min_max_hz, min_total_spikes)
            qc_rows.append(
                {
                    "unit": unit_label,
                    "pre_hz": pre_hz,
                    "light_hz": light_hz,
                    "post_hz": post_hz,
                    "max_hz": max_hz,
                    "total_expected_spikes": total_expected_spikes,
                    "qc_pass": "yes" if reason == "pass" else "no",
                    "qc_reason": reason,
                }
            )
            if reason == "missing_window_data":
                logger.log(MODULE, str(file_id), str(aligned_path), "", "warning", f"{unit_label} has missing 60gap window data.")

            figure_path = figure_dir / f"{file_id}_{unit_id}_PreLightPost60Gap.png"
            try:
                _write_unit_figure(unit_label, [pre_hz, light_hz, post_hz], figure_path, config)
            except Exception as exc:
                logger.log(MODULE, str(file_id), "", str(figure_path), "warning", "Failed to write 60gap figure.", exception=exc)
            n_units_processed += 1

    stats_df = pd.DataFrame(rows, columns=STATS_COLUMNS)
    qc_df = pd.DataFrame(qc_rows, columns=QC_COLUMNS)
    if not config["run"].get("dry_run", False):
        write_table(stats_df, output_csv)
        write_table(qc_df, qc_csv)
    logger.log(
        MODULE,
        "*",
        "",
        str(output_csv),
        "success",
        f"Built 60gap pre/light/post stats. n_units_processed={n_units_processed}; n_units_skipped={n_units_skipped}; output_csv={output_csv}; qc_csv={qc_csv}; figure_dir={figure_dir}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build fixed-window 60gap pre/light/post statistics from aligned-rate CSV files.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        build_prelightpost_60gap_stats(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
