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

import run_pipeline as pipeline_module
from scripts.build_stim_schedule_from_filenames import (
    build_stim_schedule_from_filenames,
    parse_pl2_filename,
)
from tests.test_neuroexplorer_nex_backend import sample_config
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table


class BuildStimScheduleTests(unittest.TestCase):
    def _init_project(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        (tmp_root / "00_raw_pl2").mkdir(parents=True, exist_ok=True)
        (tmp_root / "02_stim_events").mkdir(parents=True, exist_ok=True)
        (tmp_root / "99_logs").mkdir(parents=True, exist_ok=True)
        config = sample_config(tmp_root)
        config["analysis"]["mode"] = "fullrate_aligned"
        config["input"]["stim_schedule"] = "02_stim_events/stim_schedule_master.csv"
        config["stim_schedule"]["output_path"] = "02_stim_events/stim_schedule_master.csv"
        logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
        return config, logger

    def test_basic_parse(self):
        config, _ = self._init_project(Path(tempfile.mkdtemp()))
        parsed = parse_pl2_filename(config, Path("sorted_01_200light25_1,5,9.pl2"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["file_id"], "01")
        self.assertEqual(parsed["pl2_file"], "sorted_01_200light25_1,5,9.pl2")
        self.assertEqual(parsed["event_group"], "200light25")
        self.assertEqual(parsed["light_on_s"], 200.0)
        self.assertEqual(parsed["duration_s"], 25.0)
        self.assertEqual(parsed["light_off_s"], 225.0)
        self.assertEqual(parsed["has_light"], "yes")
        self.assertEqual(parsed["note"], "sorted channels: 1,5,9")
        self.assertEqual(parsed["sorted_channels"], "1,5,9")

    def test_no_light_parse(self):
        config, _ = self._init_project(Path(tempfile.mkdtemp()))
        parsed = parse_pl2_filename(config, Path("sorted_02_nolight_1,5,9.pl2"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["file_id"], "02")
        self.assertEqual(parsed["event_group"], "nolight")
        self.assertEqual(parsed["has_light"], "no")
        self.assertEqual(parsed["light_on_s"], "")
        self.assertEqual(parsed["duration_s"], "")
        self.assertEqual(parsed["light_off_s"], "")
        self.assertEqual(parsed["condition"], "no_light")
        self.assertEqual(parsed["note"], "sorted channels: 1,5,9")

    def test_decimal_parse(self):
        config, _ = self._init_project(Path(tempfile.mkdtemp()))
        parsed = parse_pl2_filename(config, Path("sorted_03_120.5light15_2,4.pl2"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["light_on_s"], 120.5)
        self.assertEqual(parsed["duration_s"], 15.0)
        self.assertEqual(parsed["light_off_s"], 135.5)

    def test_build_schedule_natural_sort_and_skip_non_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            config, logger = self._init_project(tmp_root)
            (tmp_root / "00_raw_pl2" / "sorted_10_200light25_1,5,9.pl2").write_text("", encoding="utf-8")
            (tmp_root / "00_raw_pl2" / "sorted_02_120light15_2,4.pl2").write_text("", encoding="utf-8")
            (tmp_root / "00_raw_pl2" / "abc.pl2").write_text("", encoding="utf-8")

            output_path = build_stim_schedule_from_filenames(config, logger)
            df = read_table(output_path)
            self.assertEqual(df["file_id"].tolist(), ["02", "10"])
            self.assertTrue(any("Filename does not match expected pattern" in record.message for record in logger.records))

    def test_existing_table_preserves_manual_condition_and_appends_new_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            config, logger = self._init_project(tmp_root)
            (tmp_root / "00_raw_pl2" / "sorted_01_200light25_1,5,9.pl2").write_text("", encoding="utf-8")
            (tmp_root / "00_raw_pl2" / "sorted_03_120.5light15_2,4.pl2").write_text("", encoding="utf-8")
            schedule_path = resolve_project_paths(config)["stim_schedule_path"]
            existing_df = pd.DataFrame(
                [
                    {
                        "file_id": "01",
                        "pl2_file": "sorted_01_200light25_1,5,9.pl2",
                        "event_group": "manual",
                        "light_on_s": 199.0,
                        "duration_s": 30.0,
                        "light_off_s": 229.0,
                        "condition": "materialA",
                        "note": "manual note",
                        "file_index": "01",
                        "sorted_channels": "1,5,9",
                        "detected_in_latest_scan": "yes",
                        "created_at": "old",
                        "updated_at": "old",
                    },
                    {
                        "file_id": "99",
                        "pl2_file": "sorted_99_100light10_1.pl2",
                        "event_group": "100light10",
                        "light_on_s": 100.0,
                        "duration_s": 10.0,
                        "light_off_s": 110.0,
                        "condition": "",
                        "note": "",
                        "file_index": "99",
                        "sorted_channels": "1",
                        "detected_in_latest_scan": "yes",
                        "created_at": "old",
                        "updated_at": "old",
                    },
                ]
            )
            existing_df.to_csv(schedule_path, index=False)

            build_stim_schedule_from_filenames(config, logger)
            df = read_table(schedule_path)

            row = df[df["pl2_file"].astype(str) == "sorted_01_200light25_1,5,9.pl2"].iloc[0]
            self.assertEqual(row["condition"], "materialA")
            self.assertEqual(row["note"], "manual note")
            self.assertEqual(float(row["light_on_s"]), 199.0)

            new_row = df[df["pl2_file"].astype(str) == "sorted_03_120.5light15_2,4.pl2"].iloc[0]
            self.assertEqual(new_row["file_id"], "03")
            self.assertEqual(float(new_row["light_off_s"]), 135.5)

            stale_row = df[df["pl2_file"].astype(str) == "sorted_99_100light10_1.pl2"].iloc[0]
            self.assertEqual(str(stale_row["detected_in_latest_scan"]), "no")

    def test_duplicate_file_id_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            config, logger = self._init_project(tmp_root)
            (tmp_root / "00_raw_pl2" / "sorted_01_200light25_1,5,9.pl2").write_text("", encoding="utf-8")
            (tmp_root / "00_raw_pl2" / "sorted_01_120light15_2,4.pl2").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_stim_schedule_from_filenames(config, logger)

    def test_fullrate_aligned_missing_schedule_triggers_auto_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            config, logger = self._init_project(tmp_root)
            schedule_path = resolve_project_paths(config)["stim_schedule_path"]
            if schedule_path.exists():
                schedule_path.unlink()
            with mock.patch.object(pipeline_module, "build_stim_schedule_from_filenames") as mocked_build:
                pipeline_module._prepare_stim_schedule_if_needed(config, logger)
            mocked_build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
