from __future__ import annotations

import re
from pathlib import Path


def safe_origin_name(value: str, *, max_len: int = 48, default: str = "Graph") -> str:
    """Return an Origin-safe page/name token."""

    safe = re.sub(r"[^0-9A-Za-z_]+", "_", str(value or "").strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return (safe or default)[:max_len]


def origin_path(path: Path | str) -> str:
    """Escape a filesystem path for LabTalk command strings."""

    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def light_band_labtalk(start_s: float, end_s: float) -> str:
    """
    Best-effort LabTalk annotation for a light band.

    Origin template behavior varies by version, so the runner treats this as an
    optional enhancement and logs failures rather than making native plotting fatal.
    """

    return (
        "draw -n LightBand -l -v rect "
        f"{float(start_s):.6g} 0 {float(end_s):.6g} 1; "
        "LightBand.fillcolor=color(183,201,232); LightBand.transparency=70;"
    )


def export_graph_labtalk(output_path: Path | str, image_format: str = "png", dpi: int = 300) -> str:
    suffix = str(image_format or "png").lower().lstrip(".")
    return f'expGraph type:={suffix} path:="{origin_path(output_path)}" tr1:=1 res:={int(dpi)};'

