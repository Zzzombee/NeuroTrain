from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.test_neuroexplorer_nex_backend import sample_config
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import write_table
from utils.unit_selection import select_unit_cohort


class UnitSelectionTests(unittest.TestCase):
    def _config(self, root: Path) -> tuple[dict, PipelineLogger]:
        for relative in ["00_raw_pl2", "01_sorting_info", "02_stim_events", "03_nex_exports", "99_logs"]:
            (root / relative).mkdir(parents=True, exist_ok=True)
        config = sample_config(root)
        config["input"]["unit_quality_table"] = "01_sorting_info/unit_quality_table.csv"
        config["unit_table"]["output_path"] = "01_sorting_info/unit_quality_table.csv"
        logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
        return config, logger

    def test_only_literal_yes_is_included_and_counts_are_logged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            quality = pd.DataFrame(
                [
                    {"file_id": "demo", "unit_id": "u1", "include": "yes"},
                    {"file_id": "demo", "unit_id": "u2", "include": "true"},
                    {"file_id": "demo", "unit_id": "u3", "include": ""},
                    {"file_id": "demo", "unit_id": "u4", "include": "no", "exclusion_reason": "noise"},
                ]
            )
            write_table(quality, resolve_project_paths(config)["unit_quality_path"])
            discovered = pd.DataFrame({"file_id": ["demo"] * 4, "unit_id": ["u1", "u2", "u3", "u4"]})
            cohort = select_unit_cohort(config, discovered, module="test", logger=logger)
            self.assertEqual(cohort.included["source_unit_id"].tolist(), ["u1"])
            self.assertEqual(cohort.metadata["n_units_discovered"], 4)
            self.assertEqual(cohort.metadata["n_units_included"], 1)
            self.assertEqual(cohort.metadata["n_units_excluded"], 3)
            self.assertTrue(any("duplicate_policy=keep_all" in record.message for record in logger.records))

    def test_missing_quality_table_fails_with_actionable_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._config(Path(tmpdir))
            with self.assertRaisesRegex(FileNotFoundError, "build_unit_table"):
                select_unit_cohort(
                    config,
                    pd.DataFrame([{"file_id": "demo", "unit_id": "u1"}]),
                    module="test",
                )

    def test_unmatched_data_unit_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._config(Path(tmpdir))
            write_table(
                pd.DataFrame([{"file_id": "demo", "unit_id": "u1", "include": "yes"}]),
                resolve_project_paths(config)["unit_quality_path"],
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                select_unit_cohort(
                    config,
                    pd.DataFrame([{"file_id": "demo", "unit_id": "new_unit"}]),
                    module="test",
                )

    def test_no_include_yes_unit_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, _ = self._config(Path(tmpdir))
            write_table(
                pd.DataFrame([{"file_id": "demo", "unit_id": "u1", "include": "no"}]),
                resolve_project_paths(config)["unit_quality_path"],
            )
            with self.assertRaisesRegex(ValueError, "no eligible Unit cohort"):
                select_unit_cohort(
                    config,
                    pd.DataFrame([{"file_id": "demo", "unit_id": "u1"}]),
                    module="test",
                )


if __name__ == "__main__":
    unittest.main()
