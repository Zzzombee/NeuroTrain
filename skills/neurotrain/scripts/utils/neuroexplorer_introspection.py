from __future__ import annotations

from pathlib import Path
from typing import Any


def safe_dir(obj: Any) -> list[str]:
    try:
        return sorted(dir(obj))
    except Exception:
        return []


def safe_signature_text(callable_obj: Any) -> str:
    try:
        import inspect

        return str(inspect.signature(callable_obj))
    except Exception:
        return "(signature unavailable)"


def describe_object(obj: Any, title: str) -> list[str]:
    lines = [f"[{title}]"]
    if obj is None:
        lines.append("None")
        return lines
    lines.append(f"type: {type(obj)}")
    names = safe_dir(obj)
    lines.append(f"attrs({len(names)}): {', '.join(names[:80])}")
    return lines


def dump_nex_api(output_path: Path, nex_module: Any, doc: Any | None = None) -> dict[str, bool]:
    required_functions = [
        "GetActiveDocument",
        "ApplyTemplate",
        "ModifyTemplate",
        "SaveDocument",
    ]
    candidate_functions = [
        "OpenDocument",
        "OpenFile",
        "ReadFile",
        "AddInterval",
        "AddTimestamp",
        "DeleteVar",
        "SaveNumResults",
        "SaveNumSummary",
        "SaveResults",
        "SaveGraphics",
        "ExportResults",
        "ExportData",
        "GetAllVariables",
        "CreateWaveformVariable",
        "GetIntervalVarFromMatlab",
        "GetContVarFromMatlab",
        "GetContVarWithTimestampsFromMatlab",
        "GetName",
        "GetField",
        "GetNumFields",
        "GetAllAnalysisParameters",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.extend(describe_object(nex_module, "nex module"))
    lines.append("")

    available = {}
    for name in required_functions + candidate_functions:
        present = hasattr(nex_module, name)
        available[name] = present
        if present:
            lines.append(f"{name}: present {safe_signature_text(getattr(nex_module, name))}")
        else:
            lines.append(f"{name}: missing")

    lines.append("")
    if available.get("SaveNumResults", False):
        lines.append("PSTH numerical export method: SaveNumResults")
    elif available.get("SaveResults", False):
        lines.append("PSTH numerical export method: SaveResults")
    else:
        lines.append("PSTH numerical export method: none detected")
    lines.append("")
    lines.extend(describe_object(doc, "active document"))
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return available
