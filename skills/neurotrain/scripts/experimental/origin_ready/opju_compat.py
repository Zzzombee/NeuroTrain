from __future__ import annotations

import importlib
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import normalize_include_column, normalize_stim_schedule, read_table


def _origin_cfg(config: dict) -> dict:
    cfg = dict(config.get("origin", {}))
    if "fallback_to_matplotlib" not in cfg and "fallback_matplotlib" in cfg:
        cfg["fallback_to_matplotlib"] = cfg["fallback_matplotlib"]
    return cfg


def _sanitize_windows_filename(value: str, *, default: str = "neuroexplorer_fullrate_aligned_project") -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized).strip(" ._")
    return sanitized or default


def _project_name(config: dict, paths: dict) -> str:
    raw_name = config.get("project", {}).get("name") or paths["root_dir"].name or "neuroexplorer_fullrate_aligned_project"
    return _sanitize_windows_filename(str(raw_name))


def resolve_opju_output_path(config: dict, paths: dict, *, now: datetime | None = None) -> Path:
    origin_cfg = _origin_cfg(config)
    project_name = _project_name(config, paths)
    filename_template = origin_cfg.get("opju_filename", "{project_name}_fullrate_aligned.opju")
    filename = _sanitize_windows_filename(filename_template.format(project_name=project_name))
    if not filename.lower().endswith(".opju"):
        filename = f"{filename}.opju"
    output_dir = paths["origin_output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    opju_path = output_dir / filename
    if origin_cfg.get("overwrite_opju", True) or not opju_path.exists():
        return opju_path
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return opju_path.with_name(f"{opju_path.stem}_{stamp}{opju_path.suffix}")


def _originpro_module():
    return importlib.import_module("originpro")


def _count_exported_pngs(paths: dict) -> int:
    figure_dirs = [
        paths["figure_psth_dir"],
        paths["figure_fullrate_dir"],
        paths["figure_aligned_dir"],
        paths["figure_prepost_dir"],
        paths["figure_summary_dir"],
    ]
    return sum(1 for figure_dir in figure_dirs for _ in figure_dir.glob("*.png"))


def _safe_origin_page_name(value: str, *, max_len: int = 48) -> str:
    safe = re.sub(r"[^0-9A-Za-z_]+", "_", str(value).strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return (safe or "sheet")[:max_len]


def _read_csvs(csv_paths: list[Path], logger: PipelineLogger, label: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for csv_path in csv_paths:
        try:
            frame = read_table(csv_path)
            frame.insert(0, "source_csv", csv_path.name)
            frames.append(frame)
        except Exception as exc:
            logger.log(
                "origin_plot",
                "*",
                str(csv_path),
                "",
                "warning",
                f"Failed to import CSV for OPJU archive table: {label}",
                exception=exc,
                event="save_opju",
            )
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _set_origin_visible(app, visible: bool) -> None:
    if hasattr(app, "set_show"):
        app.set_show(visible)
        return
    for attr in ("Visible", "visible"):
        try:
            setattr(app, attr, visible)
            return
        except Exception:
            continue


def _connect_originpro(op, visible: bool):
    app = None
    application_factory = getattr(op, "ApplicationSI", None)
    if callable(application_factory):
        try:
            app = application_factory()
            _set_origin_visible(app, visible)
            return app
        except AttributeError:
            app = None
    if hasattr(op, "attach"):
        try:
            op.attach()
        except Exception:
            pass
    _set_origin_visible(op, visible)
    return app


def _new_origin_workbook(op, name: str, df: pd.DataFrame):
    book = op.new_book(type="w", lname=_safe_origin_page_name(name))
    sheet = book[0] if hasattr(book, "__getitem__") else book
    if hasattr(sheet, "from_df"):
        sheet.from_df(df)
    elif hasattr(book, "from_df"):
        book.from_df(df)
    else:
        raise RuntimeError("originpro workbook object does not expose from_df().")
    return book


def _add_origin_png_page(op, png_path: Path, page_name: str) -> None:
    graph = op.new_graph(lname=_safe_origin_page_name(page_name))
    layer = graph[0] if hasattr(graph, "__getitem__") else graph
    if hasattr(layer, "add_image"):
        layer.add_image(str(png_path))
        return
    if hasattr(graph, "lt_exec"):
        escaped = str(png_path).replace("\\", "\\\\")
        graph.lt_exec(f'insertImg2g fname:="{escaped}";')
        return
    raise RuntimeError("originpro graph object does not expose add_image() or lt_exec().")


def _collect_opju_tables(config: dict, paths: dict, logger: PipelineLogger) -> list[tuple[str, pd.DataFrame]]:
    origin_cfg = _origin_cfg(config)
    content_cfg = origin_cfg.get("project_content", {})
    tables: list[tuple[str, pd.DataFrame]] = []
    if content_cfg.get("include_stim_schedule", True):
        try:
            tables.append(
                (
                    "stim_schedule_master",
                    normalize_stim_schedule(
                        read_table(paths["stim_schedule_path"]),
                        file_id_column=config["project"]["file_id_column"],
                    ),
                )
            )
        except Exception as exc:
            logger.log("origin_plot", "*", str(paths["stim_schedule_path"]), "", "warning", "Failed to import stim_schedule_master into OPJU.", exception=exc, event="save_opju")
    if content_cfg.get("include_unit_quality_table", True):
        try:
            tables.append(("unit_quality_table", normalize_include_column(read_table(paths["unit_quality_path"]))))
        except Exception as exc:
            logger.log("origin_plot", "*", str(paths["unit_quality_path"]), "", "warning", "Failed to import unit_quality_table into OPJU.", exception=exc, event="save_opju")
    if content_cfg.get("include_fullrate_data", True):
        fullrate_df = _read_csvs(sorted(paths["nex_fullrate_dir"].glob("*.csv")), logger, "fullrate_all")
        if not fullrate_df.empty:
            tables.append(("fullrate_all", fullrate_df))
    if content_cfg.get("include_aligned_rate_data", True):
        aligned_paths = [path for path in sorted(paths["nex_aligned_rate_dir"].glob("*.csv")) if "PreLightPostSummary" not in path.name]
        aligned_df = _read_csvs(aligned_paths, logger, "aligned_rate_all")
        if not aligned_df.empty:
            tables.append(("aligned_rate_all", aligned_df))
    if content_cfg.get("include_prepost_summary_data", True):
        summary_df = _read_csvs(sorted(paths["nex_aligned_rate_dir"].glob("*PreLightPostSummary*.csv")), logger, "prepost_summary_all")
        if not summary_df.empty:
            tables.append(("prepost_summary_all", summary_df))
    return tables


def _collect_opju_pngs(config: dict, paths: dict) -> list[tuple[Path, str]]:
    origin_cfg = _origin_cfg(config)
    content_cfg = origin_cfg.get("project_content", {})
    if not content_cfg.get("include_graph_pages", True):
        return []
    graph_cfg = origin_cfg.get("graph_pages", {})
    groups = [
        ("fullrate", paths["figure_fullrate_dir"], "_FullRate"),
        ("aligned_rate", paths["figure_aligned_dir"], "_AlignedRate"),
        ("prepost_summary", paths["figure_prepost_dir"], "_PreLightPost"),
        ("summary", paths["figure_summary_dir"], "_Summary"),
    ]
    pngs: list[tuple[Path, str]] = []
    for key, figure_dir, suffix in groups:
        if not graph_cfg.get(key, True):
            continue
        for png_path in sorted(figure_dir.glob("*.png")):
            pngs.append((png_path, png_path.stem if png_path.stem.endswith(suffix) else png_path.stem))
    return pngs


def save_origin_project_from_outputs(config: dict, logger: PipelineLogger, *, paths: dict | None = None) -> dict:
    paths = paths or resolve_project_paths(config)
    origin_cfg = _origin_cfg(config)
    opju_mode = origin_cfg.get("opju_mode", "single_project")
    opju_path = resolve_opju_output_path(config, paths)
    n_png_exported = _count_exported_pngs(paths)
    result = {
        "origin_available": "no",
        "opju_mode": opju_mode,
        "opju_output_path": str(opju_path),
        "n_workbooks": 0,
        "n_graph_pages": 0,
        "n_png_exported": n_png_exported,
        "status": "skipped",
    }
    if not origin_cfg.get("enabled", True) or not origin_cfg.get("save_opju", False) or not origin_cfg.get("use_originpro", True):
        logger.log(
            "origin_plot",
            "*",
            "",
            str(opju_path),
            "skipped",
            "Origin OPJU saving is disabled by config.",
            event="save_opju",
            origin_available="no",
            opju_mode=opju_mode,
            opju_output_path=str(opju_path),
            n_png_exported=n_png_exported,
        )
        return result
    if opju_mode != "single_project":
        message = f"Unsupported origin.opju_mode={opju_mode!r}; only single_project is implemented."
        logger.log(
            "origin_plot",
            "*",
            "",
            str(opju_path),
            "failed" if origin_cfg.get("require_opju_success", False) else "warning",
            message,
            event="save_opju",
            origin_available="no",
            opju_mode=opju_mode,
            opju_output_path=str(opju_path),
            n_png_exported=n_png_exported,
        )
        if origin_cfg.get("require_opju_success", False):
            raise RuntimeError(message)
        return result
    try:
        op = _originpro_module()
    except Exception as exc:
        message = "OriginPro unavailable; OPJU not generated; matplotlib fallback used."
        logger.log(
            "origin_plot",
            "*",
            "",
            str(opju_path),
            "warning",
            message,
            exception=exc,
            event="save_opju",
            origin_available="no",
            opju_mode=opju_mode,
            opju_output_path=str(opju_path),
            n_png_exported=n_png_exported,
        )
        if origin_cfg.get("require_opju_success", False):
            raise RuntimeError(message) from exc
        return result

    result["origin_available"] = "yes"
    n_workbooks = 0
    n_graph_pages = 0
    try:
        _connect_originpro(op, bool(origin_cfg.get("visible", True)))
        op.new()
        for name, df in _collect_opju_tables(config, paths, logger):
            if df.empty:
                continue
            _new_origin_workbook(op, name, df)
            n_workbooks += 1
        for png_path, page_name in _collect_opju_pngs(config, paths):
            _add_origin_png_page(op, png_path, page_name)
            n_graph_pages += 1
        op.save(str(opju_path))
        if not opju_path.exists() or opju_path.stat().st_size <= 0:
            raise RuntimeError("OPJU file was not created or has zero bytes after op.save().")
        result.update({"n_workbooks": n_workbooks, "n_graph_pages": n_graph_pages, "status": "success"})
        logger.log(
            "origin_plot",
            "*",
            "",
            str(opju_path),
            "success",
            "Saved OriginPro OPJU project.",
            event="save_opju",
            origin_available="yes",
            opju_mode=opju_mode,
            opju_output_path=str(opju_path),
            n_workbooks=n_workbooks,
            n_graph_pages=n_graph_pages,
            n_png_exported=n_png_exported,
        )
        return result
    except Exception as exc:
        result.update({"n_workbooks": n_workbooks, "n_graph_pages": n_graph_pages, "status": "failed"})
        logger.log(
            "origin_plot",
            "*",
            "",
            str(opju_path),
            "failed" if origin_cfg.get("require_opju_success", False) else "warning",
            "Failed to save OriginPro OPJU project.",
            exception=exc,
            event="save_opju",
            origin_available="yes",
            opju_mode=opju_mode,
            opju_output_path=str(opju_path),
            n_workbooks=n_workbooks,
            n_graph_pages=n_graph_pages,
            n_png_exported=n_png_exported,
        )
        if origin_cfg.get("require_opju_success", False):
            raise
        return result
    finally:
        if origin_cfg.get("close_origin_after_save", False):
            for close_method in ("exit", "Exit", "quit"):
                if hasattr(op, close_method):
                    try:
                        getattr(op, close_method)()
                    except Exception:
                        pass
                    break
