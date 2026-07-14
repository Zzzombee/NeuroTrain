from __future__ import annotations

import argparse
import importlib
from datetime import datetime
from pathlib import Path

import pandas as pd

from scripts.origin_native.labtalk_templates import origin_path, safe_origin_name
from scripts.origin_native.originpro_runner import _connect_originpro, _new_origin_workbook
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_path, resolve_project_paths
from utils.table_utils import read_table, write_table


TEMPLATE_GRAPH_TYPES = ["fullrate", "aligned_rate", "prepost_summary"]


def _originpro_module():
    return importlib.import_module("originpro")


def _template_creation_cfg(config: dict) -> dict:
    native_cfg = config.get("origin", {}).get("native", {})
    cfg = dict(native_cfg.get("template_creation", {}))
    cfg.setdefault("enabled", False)
    cfg.setdefault("seed_opju_path", "04_origin_projects/template_seed/origin_template_seed.opju")
    cfg.setdefault("auto_save_otpu", True)
    cfg.setdefault("fail_if_otpu_save_failed", False)
    cfg.setdefault("overwrite_templates", True)
    cfg.setdefault("templates", {})
    cfg.setdefault("style", {})
    return cfg


def _style_cfg(config: dict) -> dict:
    origin_cfg = config.get("origin", {})
    cfg = {
        "line_color": "#1F77B4",
        "line_width_pt": 1.8,
        "light_band_color": origin_cfg.get("light_band_color", "#B7C9E8"),
        "light_band_transparency": 70,
        "baseline_bar_color": "#9BA7B0",
        "light_bar_color": "#F3C969",
        "post_bar_color": "#8BC6A2",
        "grid_color": "#B0B0B0",
        "grid_transparency": 75,
        "title_font_size": 24,
        "axis_title_font_size": 20,
        "tick_font_size": 16,
    }
    cfg.update(_template_creation_cfg(config).get("style", {}))
    return cfg


def _resolve_template_path(config: dict, graph_type: str) -> Path:
    root_dir = Path(config["project"]["root_dir"]).expanduser().resolve()
    creation_cfg = _template_creation_cfg(config)
    native_templates = config.get("origin", {}).get("native", {}).get("templates", {})
    legacy_defaults = {
        "fullrate": "04_origin_projects/templates/FullRate_template.otpu",
        "aligned_rate": "04_origin_projects/templates/AlignedRate_template.otpu",
        "prepost_summary": "04_origin_projects/templates/PreLightPost_template.otpu",
    }
    raw_path = creation_cfg.get("templates", {}).get(graph_type) or native_templates.get(graph_type) or legacy_defaults[graph_type]
    return resolve_path(root_dir, raw_path)


def _seed_opju_path(config: dict) -> Path:
    root_dir = Path(config["project"]["root_dir"]).expanduser().resolve()
    return resolve_path(root_dir, _template_creation_cfg(config).get("seed_opju_path", "04_origin_projects/template_seed/origin_template_seed.opju"))


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def select_seed_sources(config: dict) -> dict[str, Path | None]:
    paths = resolve_project_paths(config)
    return {
        "fullrate": _first_existing(sorted(paths["nex_fullrate_dir"].glob("*_FullRate_bin*.csv"))),
        "aligned_rate": _first_existing(
            [path for path in sorted(paths["nex_aligned_rate_dir"].glob("*_LightAlignedRate_*.csv")) if "no_light_skipped" not in path.name]
        ),
        "prepost_summary": _first_existing(
            [path for path in sorted(paths["nex_aligned_rate_dir"].glob("*_PreLightPostSummary*.csv")) if "no_light_skipped" not in path.name]
        ),
    }


def _first_unit_df(df: pd.DataFrame) -> pd.DataFrame:
    if "unit_id" not in df.columns or df.empty:
        return df.copy()
    first_unit = str(df["unit_id"].dropna().astype(str).iloc[0])
    return df[df["unit_id"].astype(str) == first_unit].copy()


def _prepost_seed_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame({"window_label": ["Baseline", "Light", "Post"], "firing_rate_hz": [0.2, 1.0, 0.5]})
    row = _first_unit_df(df).iloc[0]
    return pd.DataFrame(
        {
            "window_label": ["Baseline", "Light", "Post"],
            "firing_rate_hz": [row.get("baseline_hz", 0), row.get("light_hz", 0), row.get("post_hz", 0)],
        }
    )


def build_template_seed_manifest(config: dict, logger: PipelineLogger) -> pd.DataFrame:
    paths = resolve_project_paths(config)
    sources = select_seed_sources(config)
    rows = []
    for graph_type, source in sources.items():
        rows.append(
            {
                "graph_type": graph_type,
                "source_csv": "" if source is None else str(source),
                "template_path": str(_resolve_template_path(config, graph_type)),
                "seed_opju_path": str(_seed_opju_path(config)),
                "status": "missing_source" if source is None else "ready",
            }
        )
    manifest = pd.DataFrame(rows)
    manifest_path = paths["origin_native_manifest_path"].with_name("origin_template_seed_manifest.xlsx")
    if not config.get("run", {}).get("dry_run", False):
        write_table(manifest, manifest_path)
    logger.log(
        "origin_create_templates",
        "*",
        "",
        str(manifest_path),
        "success",
        "Built Origin template seed manifest.",
        event="build_seed_manifest",
    )
    return manifest


def _probe_log_path(config: dict) -> Path:
    return resolve_project_paths(config)["logs_dir"] / "origin_template_creation_probe.txt"


class TemplateProbeLog:
    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(str(text))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _lt_exec_if_possible(graph, command: str, probe: TemplateProbeLog) -> bool:
    targets = [graph]
    if hasattr(graph, "__getitem__"):
        try:
            targets.insert(0, graph[0])
        except Exception as exc:
            probe.write(f"graph[0] unavailable for lt_exec: {type(exc).__name__}: {exc}")
    for target in targets:
        if hasattr(target, "lt_exec"):
            try:
                target.lt_exec(command)
                probe.write(f"lt_exec success: {command}")
                return True
            except Exception as exc:
                probe.write(f"lt_exec failed: {command} :: {type(exc).__name__}: {exc}")
    probe.write(f"lt_exec unavailable: {command}")
    return False


def _probe_object(name: str, obj, probe: TemplateProbeLog) -> None:
    probe.write(f"{name} type: {type(obj)!r}")
    probe.write(f"{name} repr: {obj!r}")
    members = [member for member in dir(obj) if not member.startswith("__")]
    interesting = [member for member in members if any(token in member.lower() for token in ["plot", "axis", "scale", "label", "title", "save", "template", "col", "rescale"])]
    probe.write(f"{name} interesting dir: {interesting}")


def _plot_count(layer) -> int | None:
    plot_list = getattr(layer, "plot_list", None)
    if not callable(plot_list):
        return None
    try:
        return len(plot_list())
    except Exception:
        return None


def _safe_rescale(layer, probe: TemplateProbeLog) -> None:
    rescale = getattr(layer, "rescale", None)
    if callable(rescale):
        try:
            rescale()
            probe.write("range strategy: layer.rescale success")
        except Exception as exc:
            probe.write(f"range strategy: layer.rescale failed: {type(exc).__name__}: {exc}")


def _verify_plot(layer, before_count: int | None, probe: TemplateProbeLog) -> bool:
    after_count = _plot_count(layer)
    probe.write(f"plot count before={before_count}; after={after_count}")
    if after_count is None:
        probe.write("plot verification unavailable: layer.plot_list not callable")
        return True
    if before_count is None:
        return after_count > 0
    return after_count > before_count


def _column_index(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        raise ValueError(f"Column {column!r} not found in seed dataframe columns={list(df.columns)!r}")
    return list(df.columns).index(column)


def _try_add_plot(layer, sheet, seed_df: pd.DataFrame, x_col: str, y_col: str, graph_type: str, probe: TemplateProbeLog):
    add_plot = getattr(layer, "add_plot", None)
    if not callable(add_plot):
        probe.write("plot strategy unavailable: layer.add_plot is not callable")
        return None
    x_idx = _column_index(seed_df, x_col)
    y_idx = _column_index(seed_df, y_col)
    plot_types = ["c", "column", "?"] if graph_type == "prepost_summary" else ["l", "line", "?"]
    candidates = []
    for plot_type in plot_types:
        candidates.extend(
            [
                (f"add_plot positional indexes type={plot_type}", lambda pt=plot_type: add_plot(sheet, y_idx, x_idx, type=pt)),
                (f"add_plot keyword indexes type={plot_type}", lambda pt=plot_type: add_plot(sheet, coly=y_idx, colx=x_idx, type=pt)),
                (f"add_plot column names type={plot_type}", lambda pt=plot_type: add_plot(sheet, y_col, x_col, type=pt)),
            ]
        )
    for label, callback in candidates:
        before = _plot_count(layer)
        try:
            result = callback()
            _safe_rescale(layer, probe)
            success = _verify_plot(layer, before, probe)
            probe.write(f"plot strategy {label}: result={result!r}; success={success}")
            if success:
                return result
        except Exception as exc:
            probe.write(f"plot strategy {label}: failed {type(exc).__name__}: {exc}")
    return None


def _style_plot_object(plot, graph_type: str, style: dict, probe: TemplateProbeLog) -> None:
    if plot is None:
        return
    try:
        if graph_type in {"fullrate", "aligned_rate"}:
            plot.color = style["line_color"]
            if hasattr(plot, "set_float"):
                plot.set_float("line.width", float(style["line_width_pt"]))
            probe.write(f"plot style strategy: set line color={style['line_color']} width={style['line_width_pt']}")
        elif graph_type == "prepost_summary":
            plot.color = style["light_bar_color"]
            probe.write(f"plot style strategy: set prepost bar color={style['light_bar_color']}")
    except Exception as exc:
        probe.write(f"plot style strategy failed for {graph_type}: {type(exc).__name__}: {exc}")


def _axis_titles(graph_type: str) -> tuple[str, str]:
    if graph_type == "fullrate":
        return "Absolute recording time (s)", "Firing rate (Hz)"
    if graph_type == "aligned_rate":
        return "Time from light onset (s)", "Firing rate (Hz)"
    return "Window", "Firing rate (Hz)"


def _set_axis_titles(layer, graph_type: str, probe: TemplateProbeLog) -> None:
    x_title, y_title = _axis_titles(graph_type)
    for axis_name, title in [("x", x_title), ("y", y_title)]:
        try:
            axis = layer.axis(axis_name)
            axis.title = title
            probe.write(f"axis title strategy: layer.axis({axis_name!r}).title={title!r}; readback={axis.title!r}")
        except Exception as exc:
            probe.write(f"axis title strategy failed for {axis_name}: {type(exc).__name__}: {exc}")


def _set_axis_ranges(layer, seed_df: pd.DataFrame, x_col: str, y_col: str, graph_type: str, probe: TemplateProbeLog) -> None:
    try:
        x_values = pd.to_numeric(seed_df[x_col], errors="coerce").dropna()
    except Exception:
        x_values = pd.Series(dtype=float)
    y_values = pd.to_numeric(seed_df[y_col], errors="coerce").dropna()
    y_max = float(y_values.max()) * 1.1 if not y_values.empty and float(y_values.max()) > 0 else 1.0
    try:
        y_axis = layer.axis("y")
        y_axis.set_limits(0, y_max)
        probe.write(f"range strategy: y.set_limits(0, {y_max:g}) success; readback=({y_axis.sfrom}, {y_axis.sto})")
    except Exception as exc:
        probe.write(f"range strategy: y.set_limits failed: {type(exc).__name__}: {exc}")
    if graph_type != "prepost_summary" and not x_values.empty:
        try:
            x_min = float(x_values.min())
            x_max = float(x_values.max())
            x_axis = layer.axis("x")
            x_axis.set_limits(x_min, x_max)
            probe.write(f"range strategy: x.set_limits({x_min:g}, {x_max:g}) success; readback=({x_axis.sfrom}, {x_axis.sto})")
        except Exception as exc:
            probe.write(f"range strategy: x.set_limits failed: {type(exc).__name__}: {exc}")


def _export_seed_preview(graph, config: dict, graph_type: str, probe: TemplateProbeLog) -> Path | None:
    seed_opju = _seed_opju_path(config)
    preview_path = seed_opju.parent / f"{graph_type}_seed_preview.png"
    save_fig = getattr(graph, "save_fig", None)
    if not callable(save_fig):
        probe.write(f"seed preview export unavailable for {graph_type}: graph.save_fig not callable")
        return None
    try:
        save_fig(str(preview_path), type="png", replace=True)
        probe.write(f"seed preview exported: {preview_path}")
        return preview_path
    except Exception as exc:
        probe.write(f"seed preview export failed for {graph_type}: {type(exc).__name__}: {exc}")
        return None


def _add_light_band(graph, graph_type: str, seed_df: pd.DataFrame, x_col: str, config: dict, style: dict, probe: TemplateProbeLog) -> None:
    if graph_type == "prepost_summary":
        return
    start, end = 0.0, 15.0
    if graph_type == "fullrate":
        try:
            stim = read_table(resolve_project_paths(config)["stim_schedule_path"])
            light_rows = stim[stim.get("has_light", "yes").astype(str).str.lower().ne("no")] if "has_light" in stim.columns else stim
            first = light_rows.iloc[0]
            start = float(first["light_on_s"])
            end = float(first.get("light_off_s", start + float(first.get("duration_s", 15))))
        except Exception as exc:
            x_values = pd.to_numeric(seed_df[x_col], errors="coerce").dropna()
            if not x_values.empty:
                center = float(x_values.median())
                start, end = center, center + 15.0
            probe.write(f"LightBand fullrate fallback used: {type(exc).__name__}: {exc}")
    elif graph_type == "aligned_rate":
        if "duration_s" in seed_df.columns:
            duration_values = pd.to_numeric(seed_df["duration_s"], errors="coerce").dropna()
            if not duration_values.empty:
                end = float(duration_values.iloc[0])
    command = (
        f"draw -n LightBand -l -v rect {start:g} 0 {end:g} 1; "
        f"LightBand.fillcolor=color({style['light_band_color']}); "
        f"LightBand.transparency={int(style['light_band_transparency'])};"
    )
    if not _lt_exec_if_possible(graph, command, probe):
        probe.write(f"LightBand warning: could not create/update LightBand for {graph_type}")


def _apply_seed_style(graph, layer, graph_type: str, seed_df: pd.DataFrame, x_col: str, y_col: str, config: dict, style: dict, probe: TemplateProbeLog) -> None:
    # These LabTalk snippets are best-effort. Origin versions differ, so failures
    # are recorded in the probe log and the seed OPJU remains the reliable fallback.
    _lt_exec_if_possible(graph, f"page.name$={safe_origin_name(graph_type)};", probe)
    _lt_exec_if_possible(graph, f"label -s -n title {safe_origin_name(graph_type)} template seed;", probe)
    _lt_exec_if_possible(graph, "layer.grid.major=1;", probe)
    _set_axis_titles(layer, graph_type, probe)
    _set_axis_ranges(layer, seed_df, x_col, y_col, graph_type, probe)
    if graph_type in {"fullrate", "aligned_rate"}:
        _lt_exec_if_possible(graph, f"set %C -c color({style['line_color']}); set %C -w {float(style['line_width_pt']):.3g};", probe)
        _add_light_band(graph, graph_type, seed_df, x_col, config, style, probe)
    elif graph_type == "prepost_summary":
        _lt_exec_if_possible(graph, "set %C -c color(#F3C969);", probe)


def _save_template_candidate(graph, template_path: Path, probe: TemplateProbeLog) -> bool:
    template_path.parent.mkdir(parents=True, exist_ok=True)
    method_names = ["save_template", "save_as_template", "save_template_as", "save_templateas"]
    for method_name in method_names:
        method = getattr(graph, method_name, None)
        probe.write(f"template save method {method_name}: callable={callable(method)}")
        if not callable(method):
            continue
        try:
            method(str(template_path))
            if template_path.exists():
                probe.write(f"template save success via graph.{method_name}: {template_path}")
                return True
            probe.write(f"graph.{method_name} returned but file not found: {template_path}")
        except Exception as exc:
            probe.write(f"graph.{method_name} failed: {type(exc).__name__}: {exc}")
    labtalk_candidates = [
        f'save -t "{origin_path(template_path)}";',
        f'template_saveas fname:="{origin_path(template_path)}";',
        f'page -tsave "{origin_path(template_path)}";',
    ]
    for command in labtalk_candidates:
        if _lt_exec_if_possible(graph, command, probe) and template_path.exists():
            probe.write(f"template save success via LabTalk: {command}")
            return True
    probe.write(f"automatic OTPU save unsupported or failed for {template_path}")
    return False


def _source_to_seed_df(graph_type: str, source_path: Path) -> tuple[pd.DataFrame, str, str]:
    df = read_table(source_path)
    if graph_type == "fullrate":
        return _first_unit_df(df), "time_bin_center_s", "firing_rate_hz"
    if graph_type == "aligned_rate":
        return _first_unit_df(df), "aligned_time_s", "firing_rate_hz"
    return _prepost_seed_df(df), "window_label", "firing_rate_hz"


def create_origin_templates(config: dict, logger: PipelineLogger) -> dict:
    creation_cfg = _template_creation_cfg(config)
    if not creation_cfg.get("enabled", False):
        logger.log("origin_create_templates", "*", "", "", "skipped", "origin.native.template_creation.enabled=false")
        return {"status": "skipped"}

    probe = TemplateProbeLog(_probe_log_path(config))
    manifest = build_template_seed_manifest(config, logger)
    seed_opju = _seed_opju_path(config)
    result = {
        "status": "skipped",
        "seed_opju_path": str(seed_opju),
        "saved_templates": [],
        "failed_templates": [],
    }
    try:
        op = _originpro_module()
        _connect_originpro(op, visible=bool(config.get("origin", {}).get("visible", True)))
    except Exception as exc:
        probe.write(f"originpro unavailable: {type(exc).__name__}: {exc}")
        probe.save()
        logger.log("origin_create_templates", "*", "", "", "warning", "OriginPro unavailable; template seed creation skipped.", exception=exc)
        return result

    if hasattr(op, "new"):
        op.new()
    seed_opju.parent.mkdir(parents=True, exist_ok=True)
    n_graphs = 0
    n_plotted_graphs = 0
    result["failed_plots"] = []
    for row in manifest.itertuples(index=False):
        graph_type = str(row.graph_type)
        source_csv = Path(str(row.source_csv)) if str(row.source_csv) else None
        template_path = Path(str(row.template_path))
        if source_csv is None or not source_csv.exists():
            logger.log("origin_create_templates", graph_type, "", "", "warning", "Seed source CSV missing; graph skipped.")
            continue
        seed_df, x_col, y_col = _source_to_seed_df(graph_type, source_csv)
        book, sheet = _new_origin_workbook(op, f"{graph_type}_template_seed_data", seed_df)
        graph = op.new_graph(lname=safe_origin_name(f"{graph_type}_template_seed"))
        layer = graph[0] if hasattr(graph, "__getitem__") else graph
        probe.write(f"--- {graph_type} seed graph ---")
        probe.write(f"source_csv={source_csv}")
        probe.write(f"seed_df columns={list(seed_df.columns)!r}; n_rows={len(seed_df)}; x_col={x_col}; y_col={y_col}")
        _probe_object("workbook", book, probe)
        _probe_object("worksheet", sheet, probe)
        _probe_object("graph", graph, probe)
        _probe_object("layer", layer, probe)
        plot = _try_add_plot(layer, sheet, seed_df, x_col, y_col, graph_type, probe)
        if plot is not None:
            n_plotted_graphs += 1
        else:
            result["failed_plots"].append(graph_type)
            logger.log(
                "origin_create_templates",
                graph_type,
                str(source_csv),
                "",
                "warning",
                "Seed graph was created but no data plot could be verified.",
                event="create_seed_plot",
            )
        _style_plot_object(plot, graph_type, _style_cfg(config), probe)
        _apply_seed_style(graph, layer, graph_type, seed_df, x_col, y_col, config, _style_cfg(config), probe)
        _export_seed_preview(graph, config, graph_type, probe)
        n_graphs += 1
        if creation_cfg.get("auto_save_otpu", True):
            if template_path.exists() and not creation_cfg.get("overwrite_templates", True):
                probe.write(f"template exists and overwrite_templates=false; keeping existing template: {template_path}")
                result["saved_templates"].append(str(template_path))
                continue
            if _save_template_candidate(graph, template_path, probe):
                result["saved_templates"].append(str(template_path))
            else:
                result["failed_templates"].append(str(template_path))

    try:
        if hasattr(op, "save"):
            actual_seed_opju = seed_opju
            if seed_opju.exists() and creation_cfg.get("overwrite_templates", True):
                try:
                    seed_opju.unlink()
                    probe.write(f"removed existing seed OPJU before save: {seed_opju}")
                except Exception as exc:
                    probe.write(f"could not remove existing seed OPJU before save: {type(exc).__name__}: {exc}")
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    actual_seed_opju = seed_opju.with_name(f"{seed_opju.stem}_{stamp}{seed_opju.suffix}")
                    result["seed_opju_path"] = str(actual_seed_opju)
                    probe.write(f"using fallback seed OPJU path because target is locked: {actual_seed_opju}")
            op.save(str(actual_seed_opju))
            if actual_seed_opju.exists():
                probe.write(f"seed OPJU saved: {actual_seed_opju}; size={actual_seed_opju.stat().st_size}")
            else:
                probe.write(f"op.save returned but seed OPJU not found: {actual_seed_opju}")
        else:
            probe.write("originpro module does not expose save(); seed OPJU not saved.")
    except Exception as exc:
        probe.write(f"seed OPJU save failed: {type(exc).__name__}: {exc}")
        logger.log("origin_create_templates", "*", "", str(seed_opju), "warning", "Failed to save template seed OPJU.", exception=exc)
    finally:
        probe.save()

    if result["failed_templates"] and creation_cfg.get("fail_if_otpu_save_failed", False):
        raise RuntimeError("Automatic OTPU export failed for one or more templates.")

    message = "Template seed OPJU created."
    if result["failed_templates"]:
        message += " Automatic OTPU export not supported by current Origin API. Open OPJU and manually Save Template As..."
    if n_graphs and n_plotted_graphs == n_graphs:
        result["status"] = "success"
    else:
        result["status"] = "warning"
    logger.log(
        "origin_create_templates",
        "*",
        "",
        str(seed_opju),
        result["status"],
        message,
        event="create_templates",
        n_graph_pages=n_graphs,
    )
    result["n_graphs"] = n_graphs
    result["n_plotted_graphs"] = n_plotted_graphs
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create OriginPro template seed graphs and try to save OTPU templates.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        create_origin_templates(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
