from __future__ import annotations

from math import isnan


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(numeric):
        return None
    return numeric


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def aligned_window_tag(aligned_cfg: dict) -> str:
    if all(key in aligned_cfg for key in ["pre_window_s", "light_window_s", "post_window_s"]):
        starts = [float(aligned_cfg[key][0]) for key in ["pre_window_s", "light_window_s", "post_window_s"]]
        ends = [float(aligned_cfg[key][1]) for key in ["pre_window_s", "light_window_s", "post_window_s"]]
        return f"pre{_format_number(abs(min(starts)))}_post{_format_number(max(ends))}"
    window_mode = aligned_cfg.get("window_mode", "configured_windows")
    if window_mode == "light_duration_plus_margin":
        pre_margin = _format_number(float(aligned_cfg.get("pre_margin_s", 60)))
        post_margin = _format_number(float(aligned_cfg.get("post_margin_s", 60)))
        return f"pre{pre_margin}_post{post_margin}"
    x_min = int(float(aligned_cfg["x_min_s"]))
    x_max = int(float(aligned_cfg["x_max_s"]))
    min_tag = f"m{abs(x_min)}" if x_min < 0 else str(x_min)
    max_tag = f"m{abs(x_max)}" if x_max < 0 else str(x_max)
    return f"{min_tag}to{max_tag}"


def compute_aligned_window(light_on_s: float, light_off_s: float | None, duration_s: float | None, aligned_cfg: dict) -> dict:
    light_on = float(light_on_s)
    off_value = _as_float(light_off_s)
    duration_value = _as_float(duration_s)
    if off_value is None and duration_value is None:
        raise ValueError("Either light_off_s or duration_s is required to compute aligned windows.")
    if off_value is None:
        off_value = light_on + float(duration_value)
    if duration_value is None:
        duration_value = float(off_value - light_on)
    if duration_value <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_value}")

    window_mode = aligned_cfg.get("window_mode", "configured_windows")
    if all(key in aligned_cfg for key in ["pre_window_s", "light_window_s", "post_window_s"]):
        starts = [float(aligned_cfg[key][0]) for key in ["pre_window_s", "light_window_s", "post_window_s"]]
        ends = [float(aligned_cfg[key][1]) for key in ["pre_window_s", "light_window_s", "post_window_s"]]
        aligned_x_min_s = min(starts)
        aligned_x_max_s = max(ends)
        pre_margin_s = abs(min(0.0, aligned_x_min_s))
        post_margin_s = max(0.0, aligned_x_max_s - duration_value)
        abs_start_s = float(light_on + aligned_x_min_s)
        abs_end_s = float(light_on + aligned_x_max_s)
    elif window_mode == "light_duration_plus_margin":
        pre_margin_s = float(aligned_cfg.get("pre_margin_s", 60))
        post_margin_s = float(aligned_cfg.get("post_margin_s", 60))
        aligned_x_min_s = -pre_margin_s
        aligned_x_max_s = float(duration_value + post_margin_s)
        abs_start_s = float(light_on - pre_margin_s)
        abs_end_s = float(off_value + post_margin_s)
    else:
        pre_margin_s = abs(float(aligned_cfg["x_min_s"]))
        post_margin_s = float(aligned_cfg["x_max_s"]) - float(duration_value)
        aligned_x_min_s = float(aligned_cfg["x_min_s"])
        aligned_x_max_s = float(aligned_cfg["x_max_s"])
        abs_start_s = float(light_on + aligned_x_min_s)
        abs_end_s = float(light_on + aligned_x_max_s)

    return {
        "window_mode": window_mode,
        "light_on_s": light_on,
        "light_off_s": float(off_value),
        "duration_s": float(duration_value),
        "abs_start_s": abs_start_s,
        "abs_end_s": abs_end_s,
        "aligned_x_min_s": aligned_x_min_s,
        "aligned_x_max_s": aligned_x_max_s,
        "pre_margin_s": float(pre_margin_s),
        "post_margin_s": float(post_margin_s),
    }


def compute_pre_light_post_windows(duration_s: float, aligned_cfg: dict) -> dict:
    duration = float(duration_s)
    if duration <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration}")

    if "summary_window_mode" not in aligned_cfg and any(key in aligned_cfg for key in ["pre_window_s", "light_window_s", "post_window_s"]):
        missing = [key for key in ["pre_window_s", "light_window_s", "post_window_s"] if key not in aligned_cfg]
        if missing:
            raise ValueError(f"Missing aligned_rate window config keys: {missing}")
        pre_window = aligned_cfg["pre_window_s"]
        light_window = aligned_cfg["light_window_s"]
        post_window = aligned_cfg["post_window_s"]
        baseline_start, baseline_end = float(pre_window[0]), float(pre_window[1])
        light_start, light_end = float(light_window[0]), float(light_window[1])
        post_start, post_end = float(post_window[0]), float(post_window[1])
        summary_window_mode = "configured_windows"
    else:
        summary_window_mode = aligned_cfg.get("summary_window_mode", "match_light_duration")
        light_start, light_end = 0.0, duration
    if summary_window_mode == "match_light_duration":
        baseline_start, baseline_end = -duration, 0.0
        light_start, light_end = 0.0, duration
        post_start, post_end = duration, 2.0 * duration
    elif summary_window_mode == "fixed":
        baseline = aligned_cfg.get("baseline_window_s", [-15, 0])
        baseline_start, baseline_end = float(baseline[0]), float(baseline[1])
        post = aligned_cfg.get("post_window_s", [15, 30])
        post_start, post_end = float(post[0]), float(post[1])
    elif summary_window_mode != "configured_windows":
        raise ValueError("aligned_rate.summary_window_mode must be 'fixed' or 'match_light_duration'.")

    return {
        "summary_window_mode": summary_window_mode,
        "baseline_window_start_s": float(baseline_start),
        "baseline_window_end_s": float(baseline_end),
        "light_window_start_s": float(light_start),
        "light_window_end_s": float(light_end),
        "post_window_start_s": float(post_start),
        "post_window_end_s": float(post_end),
    }


def resolve_post_window(duration_s: float, aligned_cfg: dict) -> tuple[float, float]:
    windows = compute_pre_light_post_windows(duration_s, aligned_cfg)
    return windows["post_window_start_s"], windows["post_window_end_s"]


def resolve_summary_windows(duration_s: float, aligned_cfg: dict) -> dict:
    windows = compute_pre_light_post_windows(duration_s, aligned_cfg)
    return {
        "summary_window_mode": windows["summary_window_mode"],
        "baseline": (windows["baseline_window_start_s"], windows["baseline_window_end_s"]),
        "light": (windows["light_window_start_s"], windows["light_window_end_s"]),
        "post": (windows["post_window_start_s"], windows["post_window_end_s"]),
    }
