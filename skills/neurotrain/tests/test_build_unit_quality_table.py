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
from scripts.build_unit_quality_table import build_unit_quality_table, parse_channel
from tests.test_neuroexplorer_nex_backend import sample_config
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table


class FakeAdapter:
    UNIT_MAP = {
        "02_sorted.pl2": ["SPK_SPKC01a", "SPK_SPKC02a", "SPK_SPKC10b"],
        "07_sorted.pl2": ["SPK_SPKC04a", "SPK_SPKC05a", "Noise", "Unsorted"],
    }

    def __init__(self, config, logger):
        self.current_file: Path | None = None

    def connect(self):
        return None

    def open_file(self, pl2_path):
        self.current_file = Path(pl2_path)

    def list_neuron_variables(self):
        return list(self.UNIT_MAP.get(self.current_file.name if self.current_file else "", []))

    def close_file(self):
        self.current_file = None

    def quit(self):
        self.current_file = None


class BuildUnitQualityTableTests(unittest.TestCase):
    def _init_project(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        for relative_dir in ["00_raw_pl2", "01_sorting_info", "02_stim_events", "99_logs"]:
            (tmp_root / relative_dir).mkdir(parents=True, exist_ok=True)
        (tmp_root / "00_raw_pl2" / "02_sorted.pl2").write_text("", encoding="utf-8")
        (tmp_root / "00_raw_pl2" / "07_sorted.pl2").write_text("", encoding="utf-8")
        (tmp_root / "02_stim_events" / "stim_schedule_master.csv").write_text(
            "file_id,pl2_file,light_on_s,duration_s,light_off_s\n"
            "test02,02_sorted.pl2,120,15,135\n"
            "test07,07_sorted.pl2,240,15,255\n",
            encoding="utf-8",
        )
        config = sample_config(tmp_root)
        config["analysis"]["mode"] = "fullrate_aligned"
        config["input"]["unit_quality_table"] = "01_sorting_info/unit_quality_table.csv"
        config["unit_table"]["output_path"] = "01_sorting_info/unit_quality_table.csv"
        logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
        return config, logger

    def test_parse_channel(self):
        self.assertEqual(parse_channel("SPK_SPKC04a"), 4)
        self.assertEqual(parse_channel("SPKC12b"), 12)
        self.assertIsNone(parse_channel("Noise"))

    def test_build_unit_table_generates_numbering_and_file_id_mapping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project(Path(tmpdir))
            with mock.patch("scripts.build_unit_quality_table.NeuroExplorerAdapter", side_effect=lambda config, logger: FakeAdapter(config, logger)):
                output_path = build_unit_quality_table(config, logger)
            df = read_table(output_path)
            test02 = df[df["file_id"].astype(str) == "test02"].reset_index(drop=True)
            self.assertEqual(test02["unit_id"].tolist(), ["unit01", "unit02", "unit03"])
            self.assertEqual(test02["channel"].tolist(), [1, 2, 10])
            self.assertEqual(test02["original_name"].tolist(), ["SPK_SPKC01a", "SPK_SPKC02a", "SPK_SPKC10b"])

    def test_existing_table_preserves_manual_edits_and_adds_new_units(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project(Path(tmpdir))
            unit_table_path = resolve_project_paths(config)["unit_quality_path"]
            existing_df = pd.DataFrame(
                [
                    {
                        "file_id": "test02",
                        "pl2_file": "02_sorted.pl2",
                        "unit_id": "unit01",
                        "channel": 1,
                        "original_name": "SPK_SPKC01a",
                        "include": "no",
                        "exclusion_reason": "duplicate",
                        "representative_unit": "unit01",
                        "duplicate_of": "unit02",
                        "note": "manual",
                        "unit_index": 1,
                        "source_variable_type": "NeuronNames",
                        "detected_by": "nex",
                        "created_at": "old",
                        "updated_at": "old",
                        "detected_in_latest_scan": "yes",
                    },
                    {
                        "file_id": "test02",
                        "pl2_file": "02_sorted.pl2",
                        "unit_id": "unit99",
                        "channel": 99,
                        "original_name": "SPK_SPKC99a",
                        "include": "yes",
                        "exclusion_reason": "",
                        "representative_unit": "unit99",
                        "duplicate_of": "",
                        "note": "",
                        "unit_index": 99,
                        "source_variable_type": "NeuronNames",
                        "detected_by": "nex",
                        "created_at": "old",
                        "updated_at": "old",
                        "detected_in_latest_scan": "yes",
                    },
                ]
            )
            existing_df.to_csv(unit_table_path, index=False)
            with mock.patch("scripts.build_unit_quality_table.NeuroExplorerAdapter", side_effect=lambda config, logger: FakeAdapter(config, logger)):
                build_unit_quality_table(config, logger)
            df = read_table(unit_table_path)
            row_01 = df[(df["file_id"] == "test02") & (df["original_name"] == "SPK_SPKC01a")].iloc[0]
            self.assertEqual(row_01["include"], "no")
            self.assertEqual(row_01["exclusion_reason"], "duplicate")
            self.assertEqual(row_01["duplicate_of"], "unit02")
            new_row = df[(df["file_id"] == "test02") & (df["original_name"] == "SPK_SPKC02a")].iloc[0]
            self.assertEqual(new_row["include"], "yes")
            stale_row = df[(df["file_id"] == "test02") & (df["original_name"] == "SPK_SPKC99a")].iloc[0]
            self.assertEqual(str(stale_row["detected_in_latest_scan"]), "no")

    def test_fullrate_aligned_missing_unit_table_triggers_auto_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project(Path(tmpdir))
            unit_table_path = resolve_project_paths(config)["unit_quality_path"]
            if unit_table_path.exists():
                unit_table_path.unlink()
            with mock.patch.object(pipeline_module, "build_unit_quality_table") as mocked_build:
                pipeline_module._prepare_unit_table_if_needed(config, logger)
            mocked_build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
