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

import run_pipeline
from scripts.build_prelightpost_statistics import QC_COLUMNS, WIDE_COLUMNS, build_prelightpost_statistics
from tests.test_neuroexplorer_nex_backend import sample_config
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import read_table, write_table


def _fid(value) -> str:
    return str(value).zfill(2)


class PreLightPostStatisticsTests(unittest.TestCase):
    def _config(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        for relative_dir in [
            "01_sorting_info",
            "02_stim_events",
            "03_nex_exports/aligned_rate",
            "07_statistics",
            "99_logs",
        ]:
            (tmp_root / relative_dir).mkdir(parents=True, exist_ok=True)
        config = sample_config(tmp_root)
        config["analysis"]["mode"] = "auto"
        config["input"]["stim_schedule"] = "02_stim_events/stim_schedule_master.csv"
        config["stim_schedule"]["output_path"] = "02_stim_events/stim_schedule_master.csv"
        config["input"]["unit_quality_table"] = "01_sorting_info/unit_quality_table.csv"
        config["unit_table"]["output_path"] = "01_sorting_info/unit_quality_table.csv"
        config["statistics"] = {
            "enabled": True,
            "output_dir": "07_statistics",
            "prelightpost": {
                "enabled": True,
                "input_dir": "03_nex_exports/aligned_rate",
                "input_pattern": "*_PreLightPostSummary.csv",
                "include_only_unit_quality_include_yes": True,
                "exclude_duplicate_units": False,
                "duplicate_policy": "keep_all",
                "include_trial_rows": True,
                "include_aggregated_rows": True,
                "preferred_aggregation": "trial",
                "output_wide_csv": "all_units_pre_light_post_wide.csv",
                "output_wide_qc_csv": "all_units_pre_light_post_wide_qc.csv",
                "output_qc_excluded_csv": "all_units_pre_light_post_qc_excluded.csv",
                "output_excel": "all_units_pre_light_post_statistics.xlsx",
                "output_long_csv": None,
                "output_summary_by_file": False,
                "output_summary_by_condition": False,
                "activity_filter": {
                    "enabled": True,
                    "min_max_window_hz": 0.5,
                    "min_pre_or_post_hz": 0.5,
                    "min_total_expected_spikes": 10,
                    "clean_table_suffix": "_qc",
                },
                "compute_derived_metrics": True,
                "fail_on_missing_light_summary": False,
            },
        }
        config.setdefault("run", {}).setdefault("modules", {})["prelightpost_stats"] = False
        return config, PipelineLogger(resolve_project_paths(config)["logs_dir"])

    def _summary_row(self, file_id: str, unit_id: str, baseline_hz, light_hz, post_hz, duration_s: float, **overrides) -> dict:
        row = {
            "file_id": file_id,
            "unit_id": unit_id,
            "trial_id": "1",
            "baseline_hz": baseline_hz,
            "light_hz": light_hz,
            "post_hz": post_hz,
            "duration_s": duration_s,
            "light_on_s": 120,
            "light_off_s": 120 + duration_s,
            "aligned_x_min_s": -60,
            "aligned_x_max_s": duration_s + 60,
            "pre_margin_s": 60,
            "post_margin_s": 60,
            "window_mode": "light_duration_plus_margin",
            "summary_window_mode": "match_light_duration",
            "baseline_window_start_s": -duration_s,
            "baseline_window_end_s": 0,
            "light_window_start_s": 0,
            "light_window_end_s": duration_s,
            "post_window_start_s": duration_s,
            "post_window_end_s": 2 * duration_s,
            "aggregation": "trial",
        }
        row.update(overrides)
        return row

    def _write_qc_inputs(self, config: dict) -> dict:
        paths = resolve_project_paths(config)
        write_table(
            pd.DataFrame(
                [
                    {"file_id": "01", "pl2_file": "sorted_01_120light15_1.pl2", "event_group": "120light15", "has_light": "yes", "condition": "pass", "light_on_s": 120, "duration_s": 15, "light_off_s": 135},
                    {"file_id": "02", "pl2_file": "sorted_02_120light15_1.pl2", "event_group": "120light15", "has_light": "yes", "condition": "low_rate", "light_on_s": 120, "duration_s": 15, "light_off_s": 135},
                    {"file_id": "03", "pl2_file": "sorted_03_120light10_1.pl2", "event_group": "120light10", "has_light": "yes", "condition": "low_spikes", "light_on_s": 120, "duration_s": 10, "light_off_s": 130},
                    {"file_id": "04", "pl2_file": "sorted_04_120light10_1.pl2", "event_group": "120light10", "has_light": "yes", "condition": "boundary_pass", "light_on_s": 120, "duration_s": 10, "light_off_s": 130},
                    {"file_id": "05", "pl2_file": "sorted_05_nolight_1.pl2", "event_group": "nolight", "has_light": "no", "condition": "no_light", "light_on_s": "", "duration_s": "", "light_off_s": ""},
                    {"file_id": "06", "pl2_file": "sorted_06_120light15_1.pl2", "event_group": "120light15", "has_light": "yes", "condition": "missing_rate", "light_on_s": 120, "duration_s": 15, "light_off_s": 135},
                    {"file_id": "07", "pl2_file": "sorted_07_120light15_1.pl2", "event_group": "120light15", "has_light": "yes", "condition": "include_no", "light_on_s": 120, "duration_s": 15, "light_off_s": 135},
                ]
            ),
            paths["stim_schedule_path"],
        )
        write_table(
            pd.DataFrame(
                [
                    {"file_id": "01", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "yes"},
                    {"file_id": "02", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "yes"},
                    {"file_id": "03", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "yes"},
                    {"file_id": "04", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "yes"},
                    {"file_id": "06", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "yes"},
                    {"file_id": "07", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "no"},
                ]
            ),
            paths["unit_quality_path"],
        )
        rows = [
            self._summary_row("01", "unit01", 0.6, 0.6, 0.1, 15),
            self._summary_row("02", "unit01", 0.1, 0.4, 0.2, 15),
            self._summary_row("03", "unit01", 0.1, 0.5, 0.0, 10),
            self._summary_row("04", "unit01", 0.1, 0.5, 0.4, 10),
            self._summary_row("06", "unit01", pd.NA, 0.6, 0.2, 15),
            self._summary_row("07", "unit01", 0.2, 0.6, 0.1, 15),
        ]
        for row in rows:
            write_table(pd.DataFrame([row]), paths["nex_aligned_rate_dir"] / f"{row['file_id']}_PreLightPostSummary.csv")
        write_table(
            pd.DataFrame([{"file_id": "05", "analysis_status": "no_light_skipped", "has_light": "no", "event_group": "nolight"}]),
            paths["nex_aligned_rate_dir"] / "05_PreLightPostSummary_no_light_skipped.csv",
        )
        return paths

    def test_activity_qc_outputs_and_excel_sheets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            paths = self._write_qc_inputs(config)
            build_prelightpost_statistics(config, logger)
            stats_dir = paths["statistics_dir"]
            wide = read_table(stats_dir / "all_units_pre_light_post_wide.csv")
            wide_qc = read_table(stats_dir / "all_units_pre_light_post_wide_qc.csv")
            excluded = read_table(stats_dir / "all_units_pre_light_post_qc_excluded.csv")

            self.assertEqual(len(wide), 5)
            self.assertEqual({_fid(value) for value in wide_qc["file_id"]}, {"01"})
            pass_row = wide_qc[wide_qc["file_id"].map(_fid).eq("01")].iloc[0]
            self.assertEqual(float(pass_row["max_window_hz"]), 0.6)
            self.assertEqual(float(pass_row["total_expected_spikes"]), 19.5)

            reasons_by_file = {_fid(row.file_id): row.activity_filter_reason for row in excluded.itertuples(index=False) if pd.notna(row.file_id)}
            self.assertEqual(reasons_by_file["02"], "low_max_window_hz;low_pre_and_post_hz")
            self.assertEqual(reasons_by_file["03"], "low_pre_and_post_hz;low_total_expected_spikes")
            self.assertEqual(reasons_by_file["04"], "low_pre_and_post_hz")
            self.assertEqual(reasons_by_file["05"], "no_light_control")
            self.assertEqual(reasons_by_file["06"], "missing_required_values")
            self.assertEqual(reasons_by_file["07"], "excluded_by_unit_quality_table")

            self.assertEqual(list(wide_qc.columns), [*WIDE_COLUMNS, *QC_COLUMNS])
            self.assertFalse((stats_dir / "all_units_pre_light_post_summary_by_file.csv").exists())
            self.assertFalse((stats_dir / "all_units_pre_light_post_summary_by_condition.csv").exists())
            with pd.ExcelFile(stats_dir / "all_units_pre_light_post_statistics.xlsx") as xls:
                self.assertEqual(set(xls.sheet_names), {"wide", "wide_qc", "qc_excluded", "skipped_or_missing"})

    def test_missing_summary_window_columns_are_filled_from_aligned_rate_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            paths = resolve_project_paths(config)
            write_table(
                pd.DataFrame(
                    [
                        {
                            "file_id": "01",
                            "pl2_file": "sorted_01_120light15_1.pl2",
                            "event_group": "120light15",
                            "has_light": "yes",
                            "condition": "configured_windows",
                            "light_on_s": 120,
                            "duration_s": 15,
                            "light_off_s": 135,
                        }
                    ]
                ),
                paths["stim_schedule_path"],
            )
            write_table(
                pd.DataFrame([{"file_id": "01", "unit_id": "unit01", "original_name": "SPK_SPKC01a", "channel": 1, "include": "yes"}]),
                paths["unit_quality_path"],
            )
            write_table(
                pd.DataFrame(
                    [
                        {
                            "file_id": "01",
                            "unit_id": "unit01",
                            "trial_id": "1",
                            "baseline_hz": 1.0,
                            "light_hz": 2.0,
                            "post_hz": 3.0,
                            "duration_s": 15,
                            "aggregation": "trial",
                        }
                    ]
                ),
                paths["nex_aligned_rate_dir"] / "01_PreLightPostSummary.csv",
            )
            build_prelightpost_statistics(config, logger)
            wide = read_table(paths["statistics_dir"] / "all_units_pre_light_post_wide.csv")
            row = wide.iloc[0]
            self.assertEqual(float(row["baseline_window_start_s"]), -60.0)
            self.assertEqual(float(row["baseline_window_end_s"]), 0.0)
            self.assertEqual(float(row["light_window_start_s"]), 5.0)
            self.assertEqual(float(row["light_window_end_s"]), 20.0)
            self.assertEqual(float(row["post_window_start_s"]), 25.0)
            self.assertEqual(float(row["post_window_end_s"]), 85.0)
            self.assertEqual(row["summary_window_mode"], "configured_windows")
            self.assertTrue(any("Filled missing PreLightPostSummary window metadata" in record.message for record in logger.records))

    def test_duplicate_policy_exclusions_are_recorded_in_qc_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            paths = self._write_qc_inputs(config)
            config["statistics"]["prelightpost"]["duplicate_policy"] = "exclude_duplicates"
            unit_df = read_table(paths["unit_quality_path"])
            unit_df.loc[unit_df["file_id"].astype(str).str.zfill(2).eq("01"), "duplicate_of"] = "unit02"
            write_table(unit_df, paths["unit_quality_path"])
            build_prelightpost_statistics(config, logger)
            excluded = read_table(paths["statistics_dir"] / "all_units_pre_light_post_qc_excluded.csv")
            row = excluded[excluded["file_id"].map(_fid).eq("01")].iloc[0]
            self.assertEqual(row["activity_filter_reason"], "duplicate_excluded")

    def test_pipeline_selected_module_does_not_call_other_steps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._config(Path(tmpdir))
            self._write_qc_inputs(config)
            with mock.patch.dict(
                run_pipeline.MODULE_RUNNERS,
                {
                    "neuroexplorer_export": mock.Mock(side_effect=AssertionError("NeuroExplorer should not run")),
                    "origin_plot": mock.Mock(side_effect=AssertionError("Plotting should not run")),
                    "build_pptx": mock.Mock(side_effect=AssertionError("PPTX should not run")),
                },
                clear=False,
            ):
                run_pipeline.run_selected_module("prelightpost_stats", config, logger)
            self.assertTrue((resolve_project_paths(config)["statistics_dir"] / "all_units_pre_light_post_wide_qc.csv").exists())


if __name__ == "__main__":
    unittest.main()
