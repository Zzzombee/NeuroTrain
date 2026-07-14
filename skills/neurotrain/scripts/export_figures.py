from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from utils.aligned_utils import aligned_window_tag
from utils.analysis_mode_utils import resolve_effective_analysis_mode
from utils.file_id_utils import canonicalize_file_id, legacy_file_id_candidates
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import normalize_include_column, normalize_stim_schedule, read_table


def _aligned_cfg(config: dict) -> dict:
    return config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))


def _make_placeholder_png(output_path: Path, title: str, message: str, dpi: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.axis("off")
    ax.set_facecolor("white")
    ax.text(0.5, 0.58, title, ha="center", va="center", fontsize=18, fontweight="bold")
    ax.text(0.5, 0.42, message, ha="center", va="center", fontsize=13)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _no_light_placeholder_paths(paths: dict, file_id: str, unit_id: str) -> tuple[Path, Path]:
    return (
        paths["figure_aligned_dir"] / f"{file_id}_{unit_id}_AlignedRate_no_light_skipped.png",
        paths["figure_prepost_dir"] / f"{file_id}_{unit_id}_PreLightPost_no_light_skipped.png",
    )


def _fullrate_figure_with_legacy(config: dict, paths: dict, file_id: str, pl2_file: str, unit_id: str, logger: PipelineLogger) -> Path:
    canonical = paths["figure_fullrate_dir"] / f"{file_id}_{unit_id}_FullRate.png"
    if canonical.exists():
        return canonical
    for candidate_id in legacy_file_id_candidates(file_id, pl2_file, config)[1:]:
        legacy = paths["figure_fullrate_dir"] / f"{candidate_id}_{unit_id}_FullRate.png"
        if legacy.exists():
            logger.log("export_figures", file_id, str(legacy), str(canonical), "warning", "Using legacy file_id figure path; please rerun export_figures after canonicalizing file_id.")
            return legacy
    return canonical


def _generate_no_light_unit_placeholders(config: dict, paths: dict, file_id: str, pl2_file: str, file_units, logger: PipelineLogger) -> list[tuple[str, Path, Path]]:
    dpi = int(config["origin"]["dpi"])
    rows = []
    for unit_row in file_units.itertuples(index=False):
        unit_id = str(unit_row.unit_id)
        full_png = _fullrate_figure_with_legacy(config, paths, file_id, pl2_file, unit_id, logger)
        aligned_placeholder, prepost_placeholder = _no_light_placeholder_paths(paths, file_id, unit_id)
        _make_placeholder_png(aligned_placeholder, "No light event", "Aligned analysis skipped.", dpi)
        _make_placeholder_png(prepost_placeholder, "No light event", "Pre / light / post summary not applicable.", dpi)
        rows.append((unit_id, full_png, aligned_placeholder))
    logger.log(
        "export_figures",
        str(file_id),
        "",
        str(paths["figure_aligned_dir"]),
        "success",
        "Generated no-light placeholder figures.",
    )
    return rows


def generate_summary_figures(config: dict, logger: PipelineLogger) -> None:
    paths = resolve_project_paths(config)
    unit_df = normalize_include_column(read_table(paths["unit_quality_path"]))
    file_id_column = config["project"]["file_id_column"]
    if "pl2_file" not in unit_df.columns:
        unit_df["pl2_file"] = ""
    unit_df[file_id_column] = [
        canonicalize_file_id(str(row[file_id_column]), row.get("pl2_file"), config)
        for row in unit_df.to_dict("records")
    ]
    included_df = unit_df[unit_df["include_bool"]]
    stim_df = normalize_stim_schedule(read_table(paths["stim_schedule_path"]), file_id_column=config["project"]["file_id_column"])
    stim_df[file_id_column] = [
        canonicalize_file_id(str(row[file_id_column]), row.get("pl2_file"), config)
        for row in stim_df.to_dict("records")
    ]
    aligned_cfg = _aligned_cfg(config)

    if config["run"]["dry_run"]:
        logger.log("export_figures", "*", "", str(paths["figure_summary_dir"]), "skipped", "Dry-run mode: summary figures not written.")
        return

    for file_id, file_units in included_df.groupby(config["project"]["file_id_column"], sort=False):
        rows = []
        stim_sub = stim_df[stim_df[file_id_column].astype(str) == str(file_id)]
        pl2_file = str(stim_sub["pl2_file"].iloc[0]) if not stim_sub.empty else str(file_id)
        has_light = bool(stim_sub["has_light_bool"].any()) if not stim_sub.empty else None
        has_aligned_images = any(
            (paths["figure_aligned_dir"] / f"{file_id}_{str(unit_id)}_AlignedRate_{aligned_window_tag(aligned_cfg)}.png").exists()
            for unit_id in file_units["unit_id"].astype(str).tolist()
        )
        has_fullrate_images = any(
            (paths["figure_fullrate_dir"] / f"{file_id}_{str(unit_id)}_FullRate.png").exists()
            for unit_id in file_units["unit_id"].astype(str).tolist()
        )
        effective_mode = resolve_effective_analysis_mode(
            config,
            has_light=has_light,
            has_fullrate=has_fullrate_images,
            has_aligned_assets=has_aligned_images,
        )
        if has_light is False:
            rows = _generate_no_light_unit_placeholders(config, paths, str(file_id), pl2_file, file_units, logger)
            summary_name = f"{file_id}_Summary_no_light.png"
        else:
            summary_name = f"{file_id}_Summary_{aligned_window_tag(aligned_cfg)}.png" if effective_mode == "fullrate_aligned" else f"{file_id}_Summary.png"

        for unit_row in file_units.itertuples(index=False):
            if has_light is False:
                continue
            unit_id = str(unit_row.unit_id)
            psth_png = paths["figure_psth_dir"] / f"{file_id}_{unit_id}_PSTH.png"
            full_png = paths["figure_fullrate_dir"] / f"{file_id}_{unit_id}_FullRate.png"
            aligned_png = paths["figure_aligned_dir"] / f"{file_id}_{unit_id}_AlignedRate_{aligned_window_tag(aligned_cfg)}.png"
            right_img = aligned_png if effective_mode == "fullrate_aligned" else full_png
            left_img = full_png if effective_mode == "fullrate_aligned" else psth_png
            if left_img.exists() or right_img.exists():
                rows.append((unit_id, left_img, right_img))

        if not rows:
            logger.log("export_figures", str(file_id), "", "", "warning", "No unit figures available for summary panel.")
            continue

        fig, axes = plt.subplots(
            nrows=len(rows),
            ncols=2,
            figsize=(12, max(3.5, 3.0 * len(rows))),
            squeeze=False,
        )
        for row_idx, (unit_id, psth_png, full_png) in enumerate(rows):
            for col_idx, img_path in enumerate([psth_png, full_png]):
                ax = axes[row_idx][col_idx]
                ax.axis("off")
                if img_path.exists():
                    ax.imshow(plt.imread(img_path))
                else:
                    ax.text(0.5, 0.5, f"Missing image\n{img_path.name}", ha="center", va="center")
                right_title = "AlignedRate" if effective_mode == "fullrate_aligned" else "FullRate"
                left_title = "FullRate" if effective_mode == "fullrate_aligned" else "PSTH"
                ax.set_title(f"{unit_id} | {left_title if col_idx == 0 else right_title}", fontsize=10)

        fig.suptitle(f"{file_id} | Included units summary", fontsize=14)
        fig.tight_layout()
        out_path = paths["figure_summary_dir"] / summary_name
        fig.savefig(out_path, dpi=int(config["origin"]["dpi"]), bbox_inches="tight")
        plt.close(fig)
        logger.log("export_figures", str(file_id), "", str(out_path), "success", "Exported summary panel figure.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build summary panel figures from exported PSTH/full-rate images.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        generate_summary_figures(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
