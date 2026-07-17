from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.init_project import ROOT_PL2_LOG_NAME, initialize_project
from scripts.validate_project import validate_project
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table


class InitProjectTests(unittest.TestCase):
    def test_initialize_project_creates_structure_and_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "中文 路径" / "new PSTH project"
            initialize_project(project_dir=project_dir, with_example=True)

            expected_dirs = [
                "00_raw_pl2",
                "01_sorting_info",
                "02_stim_events/exported_events",
                "03_nex_exports/fullrate",
                "03_nex_exports/aligned_rate",
                "03_nex_exports/psth",
                "03_nex_exports/raster",
                "04_origin_projects/templates",
                "04_origin_projects/opju_outputs",
                "05_exported_figures/fullrate",
                "05_exported_figures/aligned_rate",
                "05_exported_figures/prepost_summary",
                "05_exported_figures/summary",
                "06_pptx",
                "99_logs",
            ]
            for relative_dir in expected_dirs:
                self.assertTrue((project_dir / relative_dir).exists(), relative_dir)

            config = load_yaml(project_dir / "config.yaml")
            self.assertEqual(config["project"]["root_dir"], project_dir.resolve().as_posix())

            stim_df = read_table(project_dir / "02_stim_events" / "stim_schedule_master.xlsx")
            unit_df = read_table(project_dir / "01_sorting_info" / "unit_quality_table.xlsx")
            self.assertEqual(stim_df.columns.tolist(), [
                "file_id", "pl2_file", "event_group", "has_light", "light_on_s", "duration_s", "light_off_s",
                "condition", "note", "file_index", "sorted_channels", "detected_in_latest_scan",
                "created_at", "updated_at",
            ])
            self.assertEqual(unit_df.columns.tolist(), [
                "file_id", "pl2_file", "unit_id", "unit_index", "channel", "original_name",
                "source_variable_type", "include", "exclusion_reason", "representative_unit",
                "duplicate_of", "note", "detected_in_latest_scan", "detected_by", "created_at", "updated_at",
            ])
            self.assertTrue((project_dir / "README_project.md").exists())
            self.assertTrue((project_dir / "99_logs" / "processing_log.xlsx").exists())
            self.assertTrue((project_dir / "99_logs" / "error_log.xlsx").exists())
            self.assertTrue((project_dir / "99_logs" / "parameter_record.yaml").exists())

    def test_initialize_project_does_not_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            config_path = project_dir / "config.yaml"
            config_path.write_text("sentinel: keep\n", encoding="utf-8")
            initialize_project(project_dir=project_dir, force=False)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "sentinel: keep\n")

    def test_initialize_project_overwrites_with_force(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            config_path = project_dir / "config.yaml"
            config_path.write_text("sentinel: keep\n", encoding="utf-8")
            initialize_project(project_dir=project_dir, force=True)
            config = load_yaml(config_path)
            self.assertIn("analysis", config)

    def test_initialized_clean_config_validates_without_event_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            initialize_project(project_dir=project_dir, with_example=True)
            (project_dir / "00_raw_pl2" / "sorted_01_200light25_1,5,9.pl2").write_text("", encoding="utf-8")
            config = load_yaml(project_dir / "config.yaml")
            self.assertNotIn("events", config["neuroexplorer"])
            self.assertNotIn("interval", config["neuroexplorer"])
            logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
            validate_project(config, logger)
            self.assertTrue(any(record.module == "validate_project" and record.status == "success" for record in logger.records))

    def test_initialize_project_respects_margin_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            initialize_project(project_dir=project_dir, pre_margin=30, post_margin=90, bin_width=2)
            config = load_yaml(project_dir / "config.yaml")
            self.assertEqual(config["aligned_rate"]["pre_window_s"], [-30, 0])
            self.assertEqual(config["aligned_rate"]["light_window_s"], [5, 20])
            self.assertEqual(config["aligned_rate"]["post_window_s"], [25, 115])
            self.assertNotIn("window_mode", config["aligned_rate"])
            self.assertNotIn("pre_margin_s", config["aligned_rate"])
            self.assertNotIn("post_margin_s", config["aligned_rate"])
            self.assertNotIn("post_window_after_light_s", config["aligned_rate"])
            self.assertEqual(config["neuroexplorer"]["fullrate"]["bin_width_s"], 2)

    def test_initialize_project_detects_root_pl2_without_moving(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            root_pl2 = project_dir / "sorted_01_200light25_1,5,9.pl2"
            root_pl2.write_text("", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                initialize_project(project_dir=project_dir)

            self.assertTrue(root_pl2.exists())
            self.assertFalse((project_dir / "00_raw_pl2" / root_pl2.name).exists())
            log_df = read_table(project_dir / "99_logs" / "processing_log.xlsx")
            self.assertTrue((log_df["file_id"].astype(str) == "root_pl2_detected").any())
            self.assertTrue((project_dir / "99_logs" / ROOT_PL2_LOG_NAME).exists())
            console_text = stdout.getvalue()
            self.assertIn("Auto-move is disabled", console_text)
            self.assertIn(root_pl2.name, console_text)

    def test_initialize_project_limits_root_pl2_console_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            for idx in range(1, 8):
                (project_dir / f"sorted_{idx:02d}_200light25_1,5,9.pl2").write_text("", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                initialize_project(project_dir=project_dir)
            console_text = stdout.getvalue()
            self.assertIn("(+2 more)", console_text)
            for idx in range(1, 6):
                self.assertIn(f"sorted_{idx:02d}_200light25_1,5,9.pl2", console_text)
            self.assertNotIn("sorted_06_200light25_1,5,9.pl2", console_text)
            self.assertNotIn("sorted_07_200light25_1,5,9.pl2", console_text)

    def test_initialize_project_root_pl2_error_mode_does_not_move_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            root_pl2 = project_dir / "sorted_01_200light25_1,5,9.pl2"
            root_pl2.write_text("", encoding="utf-8")
            config_path = project_dir / "config.yaml"
            config = load_yaml(ROOT / "config_template.yaml")
            config["init_project"]["raw_pl2_policy"]["on_root_pl2_found"] = "error"
            from utils.path_utils import save_yaml

            save_yaml(config, config_path)
            with self.assertRaises(RuntimeError):
                initialize_project(project_dir=project_dir, force=False)
            self.assertTrue(root_pl2.exists())
            self.assertFalse((project_dir / "00_raw_pl2" / root_pl2.name).exists())


if __name__ == "__main__":
    unittest.main()
