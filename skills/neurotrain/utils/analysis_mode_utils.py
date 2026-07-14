from __future__ import annotations


def resolve_effective_analysis_mode(
    config: dict,
    *,
    has_light: bool | None = None,
    has_fullrate: bool = False,
    has_aligned_assets: bool = False,
) -> str:
    analysis_mode = config.get("analysis", {}).get("mode", "fullrate_aligned")
    if analysis_mode == "fullrate_aligned":
        return "fullrate_aligned"
    if analysis_mode == "neuroexplorer_psth":
        return "neuroexplorer_psth"
    if analysis_mode == "auto":
        if has_light is False:
            return "fullrate_aligned"
        if config.get("neuroexplorer", {}).get("export_fullrate", False):
            return "fullrate_aligned"
        if config.get("plotting", {}).get("psth_like_from_fullrate", False):
            return "fullrate_aligned"
        if has_aligned_assets or has_fullrate:
            return "fullrate_aligned"
        return "auto"
    return analysis_mode
