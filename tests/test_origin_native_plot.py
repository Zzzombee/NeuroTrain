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
from scripts.origin_native.build_origin_manifest import MANIFEST_COLUMNS, build_origin_manifest
from scripts.origin_native.originpro_runner import run_origin_native_manifest
from tests.test_neuroexplorer_nex_backend import sample_config
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths
from utils.table_utils import read_table, write_table


class FakeOriginSheet:
    def __init__(self):
        self.frames = []

    def from_df(self, df):
        self.frames.append(df.copy())


class FakeOriginBook:
    def __init__(self):
        self.sheet = FakeOriginSheet()

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.sheet


class FakeOriginLayer:
    def __init__(self):
        self.plots = []
        self.commands = []

    def add_plot(self, sheet, y_col, x_col=None):
        self.plots.append((sheet, y_col, x_col))

    def lt_exec(self, command):
        self.commands.append(command)


class FakeOriginGraph:
    def __init__(self):
        self.layer = FakeOriginLayer()
        self.saved_figs = []

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.layer

    def save_fig(self, path, **kwargs):
        Path(path).write_bytes(b"fake origin image")
        self.saved_figs.append((path, kwargs))


class FakeOriginModule:
    def __init__(self):
        self.books = []
        self.graphs = []
        self.saved_paths = []
        self.new_count = 0

    def new(self):
        self.new_count += 1

    def new_book(self, **kwargs):
        book = FakeOriginBook()
        self.books.append((kwargs, book))
        return book

    def new_graph(self, **kwargs):
        graph = FakeOriginGraph()
        self.graphs.append((kwargs, graph))
        return graph

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake opju")
        self.saved_paths.append(path)

    def attach(self):
        return None

    def set_show(self, visible):
        self.visible = visible


class OriginNativePlotTests(unittest.TestCase):
    def _config(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        config = sample_config(tmp_root)
        config["analysis"]["mode"] = "fullrate_aligned"
        config["neuroexplorer"]["export_fullrate"] = True
        config["neuroexplorer"]["fullrate"]["enabled"] = True
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
                    "graph_pages": {
                        "fullrate": True,
                        "aligned_rate": True,
                        "prepost_summary": True,
                        "summary": True,
                    },
                },
            }
        )
        config["run"]["modules"]["origin_native_plot"] = False
        paths = resolve_project_paths(config)
        logger = PipelineLogger(paths["logs_dir"])
        return config, logger

    def _write_project_tables(self, config: dict, file_ids=("demo",)) -> None:
        paths = resolve_project_paths(config)
        rows = []
        units = []
        for file_id in file_ids:
            rows.append(
                {
                    "file_id": file_id,
                    "pl2_file": f"{file_id}.pl2",
                    "has_light": "yes",
                    "light_on_s": 120,
                    "duration_s": 15,
                    "light_off_s": 135,
                    "event_group": "120light15",
                }
            )
            units.append({"file_id": file_id, "unit_id": "unit01", "original_name": "SPK_SPKC01a", "include": "yes"})
            write_table(
                pd.DataFrame(
                    {
                        "file_id": [file_id, file_id],
                        "unit_id": ["unit01", "unit01"],
                        "time_bin_center_s": [0.0, 1.0],
                        "firing_rate_hz": [1.0, 2.0],
                    }
                ),
                paths["nex_fullrate_dir"] / f"{file_id}_FullRate_bin1s.csv",
            )
            write_table(
                pd.DataFrame(
                    {
                        "file_id": [file_id, file_id],
                        "unit_id": ["unit01", "unit01"],
                        "aligned_time_s": [0.0, 1.0],
                        "firing_rate_hz": [1.5, 2.5],
                        "duration_s": [15.0, 15.0],
                    }
                ),
                paths["nex_aligned_rate_dir"] / f"{file_id}_LightAlignedRate_pre60_post85_bin1s.csv",
            )
            write_table(
                pd.DataFrame(
                    {
                        "file_id": [file_id],
                        "unit_id": ["unit01"],
                        "trial_id": ["aggregated"],
                        "baseline_hz": [1.0],
                        "light_hz": [2.0],
                        "post_hz": [1.5],
                    }
                ),
                paths["nex_aligned_rate_dir"] / f"{file_id}_PreLightPostSummary.csv",
            )
        write_table(pd.DataFrame(rows), paths["stim_schedule_path"])
        write_table(pd.DataFrame(units), paths["unit_quality_path"])

    def test_manifest_contains_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_project_tables(config)
            manifest = build_origin_manifest(config, logger)
            self.assertFalse(manifest.empty)
            for column in MANIFEST_COLUMNS:
                self.assertIn(column, manifest.columns)
            paths = resolve_project_paths(config)
            saved = read_table(paths["origin_native_manifest_path"])
            self.assertIn("fullrate", set(saved["graph_type"].astype(str)))
            self.assertIn("aligned_rate", set(saved["graph_type"].astype(str)))
            self.assertEqual(saved.iloc[0]["include"], "yes")

    def test_default_config_does_not_run_origin_native_plot(self):
        config = load_yaml(ROOT / "config_template.yaml")
        self.assertIn("origin_native_plot", MODULE_RUNNERS)
        self.assertFalse(config["run"]["modules"]["origin_native_plot"])
        self.assertEqual(config["origin"]["backend"], "matplotlib_png")

    def test_originpro_unavailable_is_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_project_tables(config)
            build_origin_manifest(config, logger)
            with mock.patch("scripts.origin_native.originpro_runner._originpro_module", side_effect=ImportError("missing originpro")):
                result = run_origin_native_manifest(config, logger)
            self.assertEqual(result["status"], "skipped")
            self.assertTrue(any("OriginPro unavailable" in record.message for record in logger.records))

    def test_per_file_opju_grouping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_project_tables(config, file_ids=("demo1", "demo2"))
            build_origin_manifest(config, logger)
            fake_origin = FakeOriginModule()
            with mock.patch("scripts.origin_native.originpro_runner._originpro_module", return_value=fake_origin):
                result = run_origin_native_manifest(config, logger)
            self.assertEqual(result["status"], "success")
            self.assertEqual(len(fake_origin.saved_paths), 2)
            self.assertTrue(any(Path(path).name == "demo1_origin_native.opju" for path in fake_origin.saved_paths))
            self.assertTrue(any(Path(path).name == "demo2_origin_native.opju" for path in fake_origin.saved_paths))
            self.assertGreater(result["n_graph_pages"], 0)


if __name__ == "__main__":
    unittest.main()
