from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_pipeline import MODULE_RUNNERS
from scripts.origin_native.build_origin_manifest import build_origin_manifest
from scripts.origin_native.create_origin_templates import create_origin_templates, select_seed_sources
from scripts.origin_native.originpro_runner import run_origin_native_manifest
from tests.test_neuroexplorer_nex_backend import sample_config
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import write_table


class FakeSheet:
    def from_df(self, df):
        self.df = df.copy()


class FakeBook:
    def __init__(self):
        self.sheet = FakeSheet()

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.sheet


class FakeLayer:
    def __init__(self, fail_plot: bool = False):
        self.commands = []
        self.plots = []
        self.axes = {"x": FakeAxis(), "y": FakeAxis()}
        self.fail_plot = fail_plot

    def add_plot(self, sheet, y_col=None, x_col=None, **kwargs):
        if self.fail_plot:
            raise RuntimeError("plot failed")
        self.plot = (sheet, y_col, x_col, kwargs)
        self.plots.append(self.plot)
        return f"plot_{len(self.plots)}"

    def plot_list(self):
        return list(self.plots)

    def rescale(self):
        self.rescaled = True

    def axis(self, axis_name):
        return self.axes[axis_name]

    def lt_exec(self, command):
        self.commands.append(command)


class FakeAxis:
    def __init__(self):
        self.title = "%(?X)"
        self.sfrom = 0
        self.sto = 1

    def set_limits(self, begin=None, end=None, step=None):
        if begin is not None:
            self.sfrom = begin
        if end is not None:
            self.sto = end
        self.step = step


class FakeGraph:
    def __init__(self, save_template_supported: bool = False, fail_plot: bool = False):
        self.layer = FakeLayer(fail_plot=fail_plot)
        self.save_template_supported = save_template_supported
        self.saved_figs = []

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.layer

    def save_template(self, path):
        if not self.save_template_supported:
            raise RuntimeError("template save unsupported")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake otpu")

    def save_fig(self, path, **kwargs):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake image")
        self.saved_figs.append((path, kwargs))


class FakeOrigin:
    def __init__(self, save_template_supported: bool = False, fail_plot: bool = False):
        self.save_template_supported = save_template_supported
        self.fail_plot = fail_plot
        self.saved_paths = []
        self.graphs = []

    def new(self):
        return None

    def new_book(self, **kwargs):
        return FakeBook()

    def new_graph(self, **kwargs):
        graph = FakeGraph(save_template_supported=self.save_template_supported, fail_plot=self.fail_plot)
        self.graphs.append(graph)
        return graph

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake seed opju")
        self.saved_paths.append(path)

    def attach(self):
        return None

    def set_show(self, visible):
        self.visible = visible


class OriginTemplateCreationTests(unittest.TestCase):
    def _config(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        config = sample_config(tmp_root)
        config["analysis"]["mode"] = "fullrate_aligned"
        config["origin"].update(
            {
                "backend": "origin_native",
                "save_opju": True,
                "export_images": True,
                "opju_mode": "per_file",
                "require_opju_success": False,
                "native": {
                    "use_originpro": True,
                    "manifest_path": "04_origin_projects/origin_input/origin_plot_manifest.xlsx",
                    "opju_output_dir": "04_origin_projects/opju_outputs",
                    "image_output_dir": "05_exported_figures_origin",
                    "image_format": "png",
                    "dpi": 300,
                    "templates": {
                        "fullrate": "04_origin_projects/templates/FullRate_template.otpu",
                        "aligned_rate": "04_origin_projects/templates/AlignedRate_template.otpu",
                        "prepost_summary": "04_origin_projects/templates/PreLightPost_template.otpu",
                        "summary": "04_origin_projects/templates/Summary_template.otpu",
                    },
                    "template_creation": {
                        "enabled": True,
                        "seed_opju_path": "04_origin_projects/template_seed/origin_template_seed.opju",
                        "auto_save_otpu": True,
                        "fail_if_otpu_save_failed": False,
                        "overwrite_templates": True,
                        "templates": {
                            "fullrate": "04_origin_projects/templates/FullRate_template.otpu",
                            "aligned_rate": "04_origin_projects/templates/AlignedRate_template.otpu",
                            "prepost_summary": "04_origin_projects/templates/PreLightPost_template.otpu",
                        },
                        "style": {"line_color": "#1F77B4", "line_width_pt": 1.8},
                    },
                    "graph_pages": {
                        "fullrate": True,
                        "aligned_rate": True,
                        "prepost_summary": True,
                        "summary": True,
                    },
                },
            }
        )
        paths = resolve_project_paths(config)
        logger = PipelineLogger(paths["logs_dir"])
        return config, logger

    def _write_outputs(self, config: dict) -> None:
        paths = resolve_project_paths(config)
        write_table(
            pd.DataFrame(
                {
                    "file_id": ["demo"],
                    "pl2_file": ["demo.pl2"],
                    "has_light": ["yes"],
                    "light_on_s": [120],
                    "duration_s": [15],
                    "light_off_s": [135],
                }
            ),
            paths["stim_schedule_path"],
        )
        write_table(
            pd.DataFrame({"file_id": ["demo"], "unit_id": ["unit01"], "original_name": ["SPK_SPKC01a"], "include": ["yes"]}),
            paths["unit_quality_path"],
        )
        write_table(
            pd.DataFrame({"file_id": ["demo", "demo"], "unit_id": ["unit01", "unit01"], "time_bin_center_s": [0, 1], "firing_rate_hz": [0.0, 1.0]}),
            paths["nex_fullrate_dir"] / "demo_FullRate_bin1s.csv",
        )
        write_table(
            pd.DataFrame({"file_id": ["demo", "demo"], "unit_id": ["unit01", "unit01"], "aligned_time_s": [0, 1], "firing_rate_hz": [0.0, 1.0]}),
            paths["nex_aligned_rate_dir"] / "demo_LightAlignedRate_pre60_post85_bin1s.csv",
        )
        write_table(
            pd.DataFrame({"file_id": ["demo"], "unit_id": ["unit01"], "baseline_hz": [0.2], "light_hz": [1.0], "post_hz": [0.4]}),
            paths["nex_aligned_rate_dir"] / "demo_PreLightPostSummary.csv",
        )

    def test_default_config_registers_module_but_keeps_it_disabled(self):
        config = load_yaml(ROOT / "config_template.yaml")
        self.assertIn("origin_create_templates", MODULE_RUNNERS)
        self.assertFalse(config["run"]["modules"]["origin_create_templates"])
        self.assertFalse(config["origin"]["native"]["template_creation"]["enabled"])

    def test_select_seed_sources_from_existing_csvs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _logger = self._config(Path(tmpdir))
            self._write_outputs(config)
            sources = select_seed_sources(config)
            self.assertTrue(sources["fullrate"].name.endswith("_FullRate_bin1s.csv"))
            self.assertTrue(sources["aligned_rate"].name.endswith("_LightAlignedRate_pre60_post85_bin1s.csv"))
            self.assertTrue(sources["prepost_summary"].name.endswith("_PreLightPostSummary.csv"))

    def test_originpro_unavailable_skips_with_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_outputs(config)
            with mock.patch("scripts.origin_native.create_origin_templates._originpro_module", side_effect=ImportError("missing originpro")):
                result = create_origin_templates(config, logger)
            self.assertEqual(result["status"], "skipped")
            self.assertTrue(any("OriginPro unavailable" in record.message for record in logger.records))

    def test_seed_opju_created_when_otpu_save_fails_nonfatal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_outputs(config)
            fake_origin = FakeOrigin(save_template_supported=False)
            with mock.patch("scripts.origin_native.create_origin_templates._originpro_module", return_value=fake_origin):
                result = create_origin_templates(config, logger)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["n_plotted_graphs"], 3)
            self.assertTrue(all(graph.layer.plots for graph in fake_origin.graphs))
            self.assertTrue(Path(result["seed_opju_path"]).exists())
            self.assertEqual(len(result["failed_templates"]), 3)
            probe_path = resolve_project_paths(config)["logs_dir"] / "origin_template_creation_probe.txt"
            self.assertTrue(probe_path.exists())
            probe_text = probe_path.read_text(encoding="utf-8")
            self.assertIn("plot strategy", probe_text)
            self.assertIn("axis title strategy", probe_text)
            self.assertIn("range strategy", probe_text)

    def test_plot_creation_failure_records_warning_not_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_outputs(config)
            fake_origin = FakeOrigin(save_template_supported=False, fail_plot=True)
            with mock.patch("scripts.origin_native.create_origin_templates._originpro_module", return_value=fake_origin):
                result = create_origin_templates(config, logger)
            self.assertEqual(result["status"], "warning")
            self.assertEqual(sorted(result["failed_plots"]), ["aligned_rate", "fullrate", "prepost_summary"])
            self.assertTrue(any("no data plot could be verified" in record.message for record in logger.records))

    def test_otpu_save_success_creates_template_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_outputs(config)
            fake_origin = FakeOrigin(save_template_supported=True)
            with mock.patch("scripts.origin_native.create_origin_templates._originpro_module", return_value=fake_origin):
                result = create_origin_templates(config, logger)
            self.assertEqual(len(result["saved_templates"]), 3)
            for template_path in result["saved_templates"]:
                self.assertTrue(Path(template_path).exists())

    def test_origin_native_plot_no_template_missing_warning_when_templates_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_outputs(config)
            config["origin"]["native"]["graph_pages"]["summary"] = False
            paths = resolve_project_paths(config)
            for template_name in ["FullRate_template.otpu", "AlignedRate_template.otpu", "PreLightPost_template.otpu"]:
                (paths["origin_native_opju_output_dir"].parent / "templates" / template_name).parent.mkdir(parents=True, exist_ok=True)
                (paths["origin_native_opju_output_dir"].parent / "templates" / template_name).write_bytes(b"fake")
            build_origin_manifest(config, logger)
            fake_origin = FakeOrigin(save_template_supported=False)
            with mock.patch("scripts.origin_native.originpro_runner._originpro_module", return_value=fake_origin):
                run_origin_native_manifest(config, logger)
            self.assertFalse(any("template path does not exist" in record.message for record in logger.records))


if __name__ == "__main__":
    unittest.main()
