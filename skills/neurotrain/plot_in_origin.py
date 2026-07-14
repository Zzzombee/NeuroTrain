from __future__ import annotations

from scripts import plot_in_origin as _plot_impl
from scripts.experimental.origin_ready import opju_compat as _opju_compat

main = _plot_impl.main
parse_args = _plot_impl.parse_args
_matplotlib_export = _plot_impl._matplotlib_export
_matplotlib_overlay_export = _plot_impl._matplotlib_overlay_export
_summary_bar_export = _plot_impl._summary_bar_export
generate_summary_figures = _plot_impl.generate_summary_figures
_originpro_module = _opju_compat._originpro_module
resolve_opju_output_path = _opju_compat.resolve_opju_output_path


def plot_in_origin(config: dict, logger):
    _plot_impl._matplotlib_export = _matplotlib_export
    _plot_impl._matplotlib_overlay_export = _matplotlib_overlay_export
    _plot_impl._summary_bar_export = _summary_bar_export
    _plot_impl.generate_summary_figures = generate_summary_figures
    _plot_impl.save_origin_project_from_outputs = save_origin_project_from_outputs
    return _plot_impl.plot_in_origin(config=config, logger=logger)


def save_origin_project_from_outputs(config: dict, logger, *, paths: dict | None = None) -> dict:
    _opju_compat._originpro_module = _originpro_module
    return _opju_compat.save_origin_project_from_outputs(config=config, logger=logger, paths=paths)


__all__ = [
    "plot_in_origin",
    "main",
    "parse_args",
    "_matplotlib_export",
    "_matplotlib_overlay_export",
    "_summary_bar_export",
    "generate_summary_figures",
    "_originpro_module",
    "resolve_opju_output_path",
    "save_origin_project_from_outputs",
]


if __name__ == "__main__":
    raise SystemExit(main())
