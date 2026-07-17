from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_pptx import build_pptx
from plot_in_origin import plot_in_origin, resolve_opju_output_path, save_origin_project_from_outputs
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import write_table
from validate_project import validate_project
from tests.test_neuroexplorer_nex_backend import sample_config


class FakeOriginSheet:
    def __init__(self):
        self.df = None

    def from_df(self, df):
        self.df = df.copy()


class FakeOriginBook:
    def __init__(self):
        self.sheet = FakeOriginSheet()

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.sheet


class FakeOriginLayer:
    def add_image(self, path):
        self.path = path


class FakeOriginGraph:
    def __init__(self):
        self.layer = FakeOriginLayer()

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.layer


class FakeOriginModule:
    def __init__(self):
        self.books = []
        self.graphs = []
        self.saved_paths = []

    def ApplicationSI(self):
        return type("FakeOriginApp", (), {})()

    def new(self):
        return None

    def new_book(self, **kwargs):
        book = FakeOriginBook()
        self.books.append((kwargs, book))
        return book

    def new_graph(self, **kwargs):
        graph = FakeOriginGraph()
        self.graphs.append((kwargs, graph))
        return graph

    def save(self, path):
        Path(path).write_bytes(b"fake opju")
        self.saved_paths.append(path)


class FakeOriginPackageModule(FakeOriginModule):
    def __init__(self):
        super().__init__()
        self.attached = False
        self.visible = None

    def ApplicationSI(self):
        raise AttributeError("ApplicationSI should not be used for originpro package API")

    def attach(self):
        self.attached = True

    def set_show(self, visible):
        self.visible = visible


class OriginOpjuTests(unittest.TestCase):
    def _init_project_files(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        for relative_dir in [
            "00_raw_pl2",
            "01_sorting_info",
            "02_stim_events",
            "03_nex_exports/fullrate",
            "03_nex_exports/aligned_rate",
            "05_exported_figures/fullrate",
            "05_exported_figures/aligned_rate",
            "05_exported_figures/prepost_summary",
            "05_exported_figures/summary",
            "06_pptx",
            "99_logs",
        ]:
            (tmp_root / relative_dir).mkdir(parents=True, exist_ok=True)
        (tmp_root / "00_raw_pl2" / "demo.pl2").write_text("", encoding="utf-8")
        (tmp_root / "02_stim_events" / "stim_schedule_master.csv").write_text(
            "file_id,pl2_file,has_light,light_on_s,duration_s,light_off_s,event_group,note\n"
            "demo,demo.pl2,yes,120,15,135,light,test note\n",
            encoding="utf-8",
        )
        (tmp_root / "01_sorting_info" / "unit_quality_table.csv").write_text(
            "file_id,unit_id,original_name,include,note\n"
            "demo,unit01,SPK_SPKC04a,yes,\n",
            encoding="utf-8",
        )
        config = sample_config(tmp_root)
        config["analysis"]["mode"] = "fullrate_aligned"
        config["origin"]["save_opju"] = True
        config["origin"]["use_originpro"] = True
        config["origin"]["fallback_to_matplotlib"] = True
        config["origin"]["opju_mode"] = "single_project"
        config["origin"]["opju_output_dir"] = "04_origin_projects/opju_outputs"
        config["origin"]["opju_filename"] = "{project_name}_fullrate_aligned.opju"
        config["origin"]["overwrite_opju"] = True
        config["origin"]["require_opju_success"] = False
        config["origin"]["project_content"] = {
            "include_fullrate_data": True,
            "include_aligned_rate_data": True,
            "include_prepost_summary_data": True,
            "include_stim_schedule": True,
            "include_unit_quality_table": True,
            "include_graph_pages": True,
        }
        config["origin"]["graph_pages"] = {
            "fullrate": True,
            "aligned_rate": True,
            "prepost_summary": True,
            "summary": True,
        }
        paths = resolve_project_paths(config)
        write_table(
            pd.DataFrame(
                {
                    "file_id": ["demo"],
                    "unit_id": ["unit01"],
                    "time_bin_center_s": [120.0],
                    "firing_rate_hz": [2.0],
                }
            ),
            paths["nex_fullrate_dir"] / "demo_FullRate_bin1s.csv",
        )
        write_table(
            pd.DataFrame(
                {
                    "file_id": ["demo"],
                    "unit_id": ["unit01"],
                    "trial_id": ["aggregated"],
                    "aligned_time_s": [0.0],
                    "firing_rate_hz": [2.0],
                    "duration_s": [15.0],
                }
            ),
            paths["nex_aligned_rate_dir"] / "demo_LightAlignedRate_pre60_post85_bin1s.csv",
        )
        write_table(
            pd.DataFrame(
                {
                    "file_id": ["demo"],
                    "unit_id": ["unit01"],
                    "trial_id": ["aggregated"],
                    "baseline_hz": [1.0],
                    "light_hz": [2.0],
                    "post_hz": [1.5],
                }
            ),
            paths["nex_aligned_rate_dir"] / "demo_PreLightPostSummary.csv",
        )
        for png_path in [
            paths["figure_fullrate_dir"] / "demo_unit01_FullRate.png",
            paths["figure_aligned_dir"] / "demo_unit01_AlignedRate_pre60_post85.png",
            paths["figure_prepost_dir"] / "demo_unit01_PreLightPost.png",
            paths["figure_summary_dir"] / "demo_Summary_pre60_post85.png",
        ]:
            png_path.write_bytes(b"fake png")
        logger = PipelineLogger(paths["logs_dir"])
        return config, logger

    def test_validate_creates_opju_output_dir_when_save_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            paths = resolve_project_paths(config)
            validate_project(config, logger)
            self.assertTrue(paths["origin_output_dir"].exists())
            self.assertTrue(any(record.module == "validate_project" for record in logger.records))

    def test_single_project_resolves_one_opju_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._init_project_files(Path(tmpdir))
            config["project"]["name"] = "Demo Project"
            paths = resolve_project_paths(config)
            opju_path = resolve_opju_output_path(config, paths)
            self.assertEqual(opju_path.name, "Demo_Project_fullrate_aligned.opju")
            self.assertEqual(opju_path.parent, paths["origin_output_dir"])

    def test_overwrite_true_uses_fixed_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._init_project_files(Path(tmpdir))
            config["project"]["name"] = "demo"
            paths = resolve_project_paths(config)
            existing = paths["origin_output_dir"] / "demo_fullrate_aligned.opju"
            existing.write_bytes(b"old")
            self.assertEqual(resolve_opju_output_path(config, paths), existing)

    def test_overwrite_false_adds_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._init_project_files(Path(tmpdir))
            config["project"]["name"] = "demo"
            config["origin"]["overwrite_opju"] = False
            paths = resolve_project_paths(config)
            existing = paths["origin_output_dir"] / "demo_fullrate_aligned.opju"
            existing.write_bytes(b"old")
            resolved = resolve_opju_output_path(config, paths, now=datetime(2026, 5, 9, 12, 34, 56))
            self.assertEqual(resolved.name, "demo_fullrate_aligned_20260509_123456.opju")

    def test_origin_unavailable_logs_warning_and_does_not_fake_opju(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            with mock.patch("plot_in_origin._originpro_module", side_effect=ImportError("missing originpro")):
                result = save_origin_project_from_outputs(config, logger)
            self.assertEqual(result["status"], "skipped")
            self.assertFalse(Path(result["opju_output_path"]).exists())
            self.assertTrue(any("OriginPro unavailable; OPJU not generated; matplotlib fallback used." in record.message for record in logger.records))

    def test_opju_save_success_logs_output_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            fake_origin = FakeOriginModule()
            with mock.patch("plot_in_origin._originpro_module", return_value=fake_origin):
                result = save_origin_project_from_outputs(config, logger)
            self.assertEqual(result["status"], "success")
            self.assertEqual(len(fake_origin.saved_paths), 1)
            self.assertTrue(Path(result["opju_output_path"]).exists())
            save_records = [record for record in logger.records if record.event == "save_opju" and record.status == "success"]
            self.assertTrue(any(record.opju_output_path == result["opju_output_path"] for record in save_records))
            self.assertGreaterEqual(int(result["n_workbooks"]), 3)
            self.assertEqual(int(result["n_graph_pages"]), 4)

    def test_opju_save_supports_originpro_package_api_without_applicationsi(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            fake_origin = FakeOriginPackageModule()
            with mock.patch("plot_in_origin._originpro_module", return_value=fake_origin):
                result = save_origin_project_from_outputs(config, logger)
            self.assertEqual(result["status"], "success")
            self.assertTrue(fake_origin.attached)
            self.assertTrue(fake_origin.visible)
            self.assertTrue(Path(result["opju_output_path"]).exists())

    def test_export_figures_does_not_attempt_opju_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            config["origin"]["save_opju"] = False
            with mock.patch("plot_in_origin.generate_summary_figures", return_value=None), mock.patch(
                "plot_in_origin.save_origin_project_from_outputs",
                side_effect=AssertionError("OPJU should not be attempted when origin.save_opju is false."),
            ):
                plot_in_origin(config, logger)
            self.assertFalse(any(record.event == "save_opju" for record in logger.records))

    def test_export_figures_attempts_opju_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            opju_result = {
                "origin_available": "yes",
                "opju_mode": "single_project",
                "opju_output_path": str(resolve_project_paths(config)["origin_output_dir"] / "demo_fullrate_aligned.opju"),
                "n_workbooks": 1,
                "n_graph_pages": 1,
                "n_png_exported": 1,
                "status": "success",
            }
            with mock.patch("plot_in_origin.generate_summary_figures", return_value=None), mock.patch(
                "plot_in_origin.save_origin_project_from_outputs",
                return_value=opju_result,
            ) as save_opju:
                plot_in_origin(config, logger)
            save_opju.assert_called_once()

    def test_pptx_build_does_not_depend_on_opju(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            paths = resolve_project_paths(config)
            import matplotlib.pyplot as plt

            for output_path in [
                paths["figure_fullrate_dir"] / "demo_unit01_FullRate.png",
                paths["figure_aligned_dir"] / "demo_unit01_AlignedRate_pre60_post85.png",
                paths["figure_prepost_dir"] / "demo_unit01_PreLightPost.png",
            ]:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, output_path.stem, ha="center", va="center")
                ax.axis("off")
                fig.savefig(output_path)
                plt.close(fig)
            with mock.patch("scripts.build_pptx.generate_summary_figures", return_value=None):
                build_pptx(config, logger)
            self.assertTrue(paths["pptx_output_path"].exists())
            self.assertFalse((paths["origin_output_dir"] / "demo_fullrate_aligned.opju").exists())

    def test_illegal_filename_characters_are_replaced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._init_project_files(Path(tmpdir))
            config["project"]["name"] = 'bad:name*with?chars/"pipe|'
            paths = resolve_project_paths(config)
            resolved = resolve_opju_output_path(config, paths)
            self.assertEqual(resolved.name, "bad_name_with_chars_pipe_fullrate_aligned.opju")


if __name__ == "__main__":
    unittest.main()
