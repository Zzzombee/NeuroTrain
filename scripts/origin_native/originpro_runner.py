from __future__ import annotations

import importlib
import re
from pathlib import Path

import pandas as pd

from scripts.origin_native.labtalk_templates import export_graph_labtalk, light_band_labtalk, safe_origin_name
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table


def _originpro_module():
    return importlib.import_module("originpro")


def _origin_cfg(config: dict) -> dict:
    cfg = dict(config.get("origin", {}))
    cfg.setdefault("backend", "matplotlib_png")
    cfg.setdefault("opju_mode", "per_file")
    cfg.setdefault("save_opju", True)
    cfg.setdefault("export_images", True)
    cfg.setdefault("require_opju_success", False)
    return cfg


def _native_cfg(config: dict) -> dict:
    origin_cfg = _origin_cfg(config)
    cfg = dict(origin_cfg.get("native", {}))
    cfg.setdefault("use_originpro", origin_cfg.get("use_originpro", True))
    cfg.setdefault("image_format", origin_cfg.get("export_format", "png"))
    cfg.setdefault("dpi", origin_cfg.get("dpi", 300))
    return cfg


def _project_name(config: dict, paths: dict) -> str:
    raw_name = config.get("project", {}).get("name") or paths["root_dir"].name or "origin_native_project"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(raw_name).strip())
    safe = re.sub(r"\s+", "_", safe)
    safe = re.sub(r"_+", "_", safe).strip(" ._")
    return safe or "origin_native_project"


def _opju_path(config: dict, paths: dict, group_name: str) -> Path:
    origin_cfg = _origin_cfg(config)
    out_dir = paths["origin_native_opju_output_dir"]
    if origin_cfg.get("opju_mode", "per_file") == "per_file":
        filename = f"{safe_origin_name(group_name, max_len=96)}_origin_native.opju"
    else:
        filename = f"{_project_name(config, paths)}_origin_native.opju"
    return out_dir / filename


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


def _connect_originpro(op, visible: bool = True):
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


def _filter_source_data(df: pd.DataFrame, manifest_row: pd.Series) -> pd.DataFrame:
    graph_type = str(manifest_row.get("graph_type", ""))
    unit_id = str(manifest_row.get("unit_id", ""))
    if graph_type in {"fullrate", "aligned_rate"} and unit_id and "unit_id" in df.columns:
        filtered = df[df["unit_id"].astype(str) == unit_id].copy()
        return filtered if not filtered.empty else df
    if graph_type == "prepost_summary" and unit_id and "unit_id" in df.columns:
        filtered = df[df["unit_id"].astype(str) == unit_id].copy()
        if not filtered.empty:
            row = filtered.iloc[0]
            return pd.DataFrame(
                {
                    "window_label": ["baseline", "light", "post"],
                    "firing_rate_hz": [row.get("baseline_hz"), row.get("light_hz"), row.get("post_hz")],
                }
            )
    return df.copy()


def _new_origin_workbook(op, name: str, df: pd.DataFrame):
    book = op.new_book(type="w", lname=safe_origin_name(name))
    sheet = book[0] if hasattr(book, "__getitem__") else book
    if hasattr(sheet, "from_df"):
        sheet.from_df(df)
    elif hasattr(book, "from_df"):
        book.from_df(df)
    else:
        raise RuntimeError("originpro workbook object does not expose from_df().")
    return book, sheet


def _new_origin_graph(op, row: pd.Series, sheet, logger: PipelineLogger):
    graph = op.new_graph(lname=safe_origin_name(str(row.get("graph_page_name", "Graph"))))
    layer = graph[0] if hasattr(graph, "__getitem__") else graph
    x_col = str(row.get("x_col", ""))
    y_col = str(row.get("y_col", ""))
    try:
        if hasattr(layer, "add_plot"):
            try:
                layer.add_plot(sheet, y_col, x_col)
            except TypeError:
                layer.add_plot(sheet, y_col)
        elif hasattr(graph, "lt_exec"):
            graph.lt_exec("layer -i;")
    except Exception as exc:
        logger.log(
            "origin_native_plot",
            str(row.get("file_id", "")),
            str(row.get("source_csv", "")),
            "",
            "warning",
            "Origin graph page was created, but add_plot failed. Workbook data remain editable in OPJU.",
            exception=exc,
            event="create_graph",
        )
    return graph


def _apply_template_if_possible(graph, template_path: str, row: pd.Series, logger: PipelineLogger) -> None:
    if not template_path:
        return
    template = Path(template_path)
    if not template.exists():
        logger.log(
            "origin_native_plot",
            str(row.get("file_id", "")),
            template_path,
            "",
            "warning",
            "Origin template path does not exist; graph was created without template.",
            event="apply_template",
        )
        return
    try:
        if hasattr(graph, "load_template"):
            graph.load_template(str(template))
        elif hasattr(graph, "lt_exec"):
            graph.lt_exec(f'page -t "{str(template).replace("\\", "\\\\")}";')
    except Exception as exc:
        logger.log(
            "origin_native_plot",
            str(row.get("file_id", "")),
            template_path,
            "",
            "warning",
            "Failed to apply Origin template; graph remains editable.",
            exception=exc,
            event="apply_template",
        )


def _add_light_band_if_possible(graph, row: pd.Series, logger: PipelineLogger) -> None:
    start = row.get("light_band_start_s", "")
    end = row.get("light_band_end_s", "")
    if pd.isna(start) or pd.isna(end) or start == "" or end == "":
        return
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return
    try:
        target = graph
        if hasattr(graph, "__getitem__"):
            try:
                target = graph[0]
            except Exception:
                target = graph
        if hasattr(target, "lt_exec"):
            target.lt_exec(light_band_labtalk(start_f, end_f))
        elif hasattr(graph, "lt_exec"):
            graph.lt_exec(light_band_labtalk(start_f, end_f))
    except Exception as exc:
        logger.log(
            "origin_native_plot",
            str(row.get("file_id", "")),
            "",
            "",
            "warning",
            "Failed to add Origin light-band annotation; data and graph remain usable.",
            exception=exc,
            event="light_band",
        )


def _export_graph_if_possible(graph, row: pd.Series, native_cfg: dict, logger: PipelineLogger) -> bool:
    output_path = Path(str(row.get("output_image_path", "")))
    if not output_path:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_format = str(row.get("image_format") or native_cfg.get("image_format", "png")).lower().lstrip(".")
    dpi = int(float(row.get("dpi") or native_cfg.get("dpi", 300)))
    try:
        if hasattr(graph, "save_fig"):
            graph.save_fig(str(output_path), type=image_format, replace=True)
        elif hasattr(graph, "save"):
            graph.save(str(output_path))
        elif hasattr(graph, "lt_exec"):
            graph.lt_exec(export_graph_labtalk(output_path, image_format=image_format, dpi=dpi))
        else:
            return False
        return True
    except Exception as exc:
        logger.log(
            "origin_native_plot",
            str(row.get("file_id", "")),
            "",
            str(output_path),
            "warning",
            "Origin image export failed; OPJU may still contain editable data/graphs.",
            exception=exc,
            event="export_image",
        )
        return False


def _run_origin_group(op, config: dict, paths: dict, native_cfg: dict, logger: PipelineLogger, group_name: str, group_df: pd.DataFrame) -> dict:
    if hasattr(op, "new"):
        op.new()
    n_workbooks = 0
    n_graph_pages = 0
    n_images_exported = 0
    for row in group_df.itertuples(index=False):
        row_s = pd.Series(row._asdict())
        source_csv = Path(str(row_s.get("source_csv", "")))
        if not source_csv.exists():
            logger.log("origin_native_plot", str(row_s.get("file_id", "")), str(source_csv), "", "warning", "Manifest source CSV missing; graph skipped.", event="run_manifest")
            continue
        raw_df = read_table(source_csv)
        plot_df = _filter_source_data(raw_df, row_s)
        book, sheet = _new_origin_workbook(op, f"{row_s.get('graph_page_name', 'Data')}_data", plot_df)
        _ = book
        n_workbooks += 1
        graph = _new_origin_graph(op, row_s, sheet, logger)
        n_graph_pages += 1
        _apply_template_if_possible(graph, str(row_s.get("template_path", "")), row_s, logger)
        _add_light_band_if_possible(graph, row_s, logger)
        if _origin_cfg(config).get("export_images", True):
            if _export_graph_if_possible(graph, row_s, native_cfg, logger):
                n_images_exported += 1

    opju_path = _opju_path(config, paths, group_name)
    if _origin_cfg(config).get("save_opju", True):
        opju_path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(op, "save"):
            op.save(str(opju_path))
        else:
            raise RuntimeError("originpro module does not expose save().")
    return {
        "opju_output_path": str(opju_path),
        "n_workbooks": n_workbooks,
        "n_graph_pages": n_graph_pages,
        "n_images_exported": n_images_exported,
    }


def run_origin_native_manifest(config: dict, logger: PipelineLogger, manifest_path: Path | None = None) -> dict:
    paths = resolve_project_paths(config)
    origin_cfg = _origin_cfg(config)
    native_cfg = _native_cfg(config)
    manifest_path = manifest_path or paths["origin_native_manifest_path"]
    result = {
        "origin_available": "no",
        "status": "skipped",
        "n_workbooks": 0,
        "n_graph_pages": 0,
        "n_images_exported": 0,
        "opju_paths": [],
    }
    if origin_cfg.get("backend") not in {"origin_native", "both"}:
        logger.log("origin_native_plot", "*", str(manifest_path), "", "skipped", "origin.backend is not origin_native/both; runner skipped.", event="run_manifest")
        return result
    if not native_cfg.get("use_originpro", True):
        logger.log("origin_native_plot", "*", str(manifest_path), "", "skipped", "origin.native.use_originpro=false; runner skipped.", event="run_manifest")
        return result
    if not Path(manifest_path).exists():
        logger.log("origin_native_plot", "*", "", str(manifest_path), "warning", "Origin native manifest is missing; runner skipped.", event="run_manifest")
        return result
    try:
        op = _originpro_module()
        _connect_originpro(op, visible=bool(origin_cfg.get("visible", True)))
    except Exception as exc:
        message = "OriginPro unavailable; native Origin plotting skipped."
        logger.log("origin_native_plot", "*", str(manifest_path), "", "warning", message, exception=exc, event="run_manifest", origin_available="no")
        if origin_cfg.get("require_opju_success", False):
            raise RuntimeError(message) from exc
        return result

    manifest_df = read_table(Path(manifest_path))
    if manifest_df.empty:
        logger.log("origin_native_plot", "*", str(manifest_path), "", "warning", "Origin native manifest is empty; runner skipped.", event="run_manifest", origin_available="yes")
        return result
    if "include" in manifest_df.columns:
        manifest_df = manifest_df[manifest_df["include"].fillna("").astype(str).str.lower().isin({"yes", "true", "1", "include"})]
    group_col = "opju_group" if origin_cfg.get("opju_mode", "per_file") == "per_file" and "opju_group" in manifest_df.columns else None
    groups = manifest_df.groupby(group_col, sort=False) if group_col else [("project", manifest_df)]
    try:
        for group_name, group_df in groups:
            group_result = _run_origin_group(op, config, paths, native_cfg, logger, str(group_name), group_df)
            result["opju_paths"].append(group_result["opju_output_path"])
            result["n_workbooks"] += int(group_result["n_workbooks"])
            result["n_graph_pages"] += int(group_result["n_graph_pages"])
            result["n_images_exported"] += int(group_result["n_images_exported"])
        result["origin_available"] = "yes"
        result["status"] = "success"
        logger.log(
            "origin_native_plot",
            "*",
            str(manifest_path),
            ";".join(result["opju_paths"]),
            "success",
            "Origin native plotting completed.",
            event="run_manifest",
            origin_available="yes",
            opju_mode=origin_cfg.get("opju_mode", ""),
            n_workbooks=result["n_workbooks"],
            n_graph_pages=result["n_graph_pages"],
            n_png_exported=result["n_images_exported"],
        )
        return result
    except Exception as exc:
        logger.log("origin_native_plot", "*", str(manifest_path), "", "failed" if origin_cfg.get("require_opju_success", False) else "warning", "Origin native plotting failed.", exception=exc, event="run_manifest", origin_available="yes")
        if origin_cfg.get("require_opju_success", False):
            raise
        return result

