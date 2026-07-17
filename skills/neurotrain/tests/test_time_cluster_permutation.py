from __future__ import annotations

import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_time_cluster_aligned_rate import build_time_cluster_aligned_rate_for_file
from scripts.time_cluster_permutation import (
    _save_heatmap_figure,
    heatmap_x_extent,
    prepare_analysis_matrix,
    student_t_ppf,
    temporal_cluster_permutation_test,
    time_cluster_config,
)
from utils.path_utils import resolve_project_paths, save_yaml
from utils.table_utils import convert_rate_export_to_long, write_table


class TimeClusterPermutationTests(unittest.TestCase):
    def test_boundary_center_windows_and_unshaded_heatmap(self):
        starts = np.arange(0.0, 450.0, 10.0)
        fullrate = pd.DataFrame(
            {
                "file_id": "demo",
                "unit_id": np.repeat(["u1", "u2"], len(starts)),
                "time_bin_start_s": np.tile(starts, 2),
                "time_bin_end_s": np.tile(starts + 10.0, 2),
                "time_bin_center_s": np.tile(starts + 5.0, 2),
                "firing_rate_hz": np.concatenate([np.ones(len(starts)), np.ones(len(starts)) * 2.0]),
            }
        )
        config = {
            "time_cluster_aligned_rate": {
                "bin_width_s": 10,
                "window_s": [-120, 320],
                "require_light_on_on_bin_boundary": True,
                "off_boundary_policy": "error",
            },
            "time_cluster_permutation": {
                "analysis_window_s": [-120, 320],
                "baseline_window_s": [-120, 0],
                "test_window_s": [0, 300],
            },
        }
        stimulus = pd.DataFrame(
            {"file_id": ["demo"], "light_on_s": [120.0], "duration_s": [25.0], "light_off_s": [145.0]}
        )
        aligned = build_time_cluster_aligned_rate_for_file(config, "demo", fullrate, stimulus)
        centers = aligned.loc[aligned["unit_id"] == "u1", "aligned_time_s"].to_numpy(dtype=float)
        np.testing.assert_array_equal(centers[:3], np.asarray([-115.0, -105.0, -95.0]))
        self.assertIn(-5.0, centers)
        self.assertIn(5.0, centers)
        self.assertIn(15.0, centers)
        self.assertNotIn(0.0, centers)
        prepared = prepare_analysis_matrix(config, aligned)
        np.testing.assert_array_equal(prepared.time_s[prepared.baseline_mask], np.arange(-115.0, 0.0, 10.0))
        np.testing.assert_array_equal(prepared.time_s[prepared.test_mask], np.arange(5.0, 300.0, 10.0))
        self.assertEqual(int(np.count_nonzero(prepared.baseline_mask)), 12)
        self.assertEqual(int(np.count_nonzero(prepared.test_mask)), 30)
        self.assertNotIn(0.0, prepared.time_s)
        self.assertEqual(heatmap_x_extent(prepared.time_s, prepared.bin_width_s), (-120.0, 320.0))
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "heatmap.png"
            with mock.patch("matplotlib.axes.Axes.axvspan") as axvspan:
                _save_heatmap_figure(prepared, output, dpi=50)
            axvspan.assert_not_called()
            self.assertTrue(output.exists())

    def test_dedicated_builder_exactly_aggregates_one_second_bins_to_thirty_seconds(self):
        config = {
            "neuroexplorer": {"fullrate": {"bin_width_s": 1.0}},
            "time_cluster_aligned_rate": {
                "window_s": [-60, 300],
                "bin_width_s": 30,
                "require_light_on_on_bin_boundary": False,
                "off_boundary_policy": "nearest",
            },
        }
        starts = np.arange(-0.5, 359.5, 1.0)
        fullrate = pd.DataFrame(
            {
                "file_id": "demo",
                "unit_id": "unit01",
                "time_bin_start_s": starts,
                "time_bin_end_s": starts + 1.0,
                "time_bin_center_s": starts + 0.5,
                "firing_rate_hz": starts + 0.5,
            }
        )
        stimulus = pd.DataFrame(
            {"file_id": ["demo"], "light_on_s": [60.0], "duration_s": [25.0], "light_off_s": [85.0]}
        )
        aligned = build_time_cluster_aligned_rate_for_file(config, "demo", fullrate, stimulus)

        np.testing.assert_array_equal(aligned["aligned_time_s"], np.arange(-45.0, 300.0, 30.0))
        self.assertNotIn(0.0, aligned["aligned_time_s"].tolist())
        self.assertTrue((aligned["n_source_bins"] == 30).all())
        self.assertTrue((aligned["source_bin_width_s"] == 1.0).all())
        self.assertTrue((aligned["target_bin_width_s"] == 30.0).all())
        self.assertEqual(set(aligned["rebin_method"]), {"duration_weighted_exact_aggregation"})
        self.assertAlmostEqual(float(aligned["firing_rate_hz"].iloc[0]), 14.5)
        self.assertEqual(float(aligned["alignment_boundary_s"].iloc[0]), 59.5)
        self.assertEqual(float(aligned["stimulus_time_aligned_s"].iloc[0]), 0.5)

    def test_terminal_dedicated_source_width_is_independent_from_normal_fullrate_width(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for relative in [
                "01_sorting_info",
                "02_stim_events",
                "03_nex_exports/fullrate",
                "03_nex_exports/aligned_rate",
                "03_nex_exports/time_cluster_aligned_rate",
                "07_statistics",
                "99_logs",
            ]:
                (root / relative).mkdir(parents=True, exist_ok=True)
            config = self._smoke_config(root)
            config["neuroexplorer"]["fullrate"]["bin_width_s"] = 10.0
            config["time_cluster_aligned_rate"].update(
                {
                    "source_bin_width_s": 1.0,
                    "bin_width_s": 30,
                    "window_s": [-120, 300],
                }
            )
            starts = np.arange(-0.5, 419.5, 1.0)
            frames = []
            for index in range(3):
                frames.append(
                    pd.DataFrame(
                        {
                            "file_id": "demo",
                            "unit_id": f"SPK_SPKC{index + 1:02d}a",
                            "time_bin_start_s": starts,
                            "time_bin_end_s": starts + 1.0,
                            "time_bin_center_s": starts + 0.5,
                            "firing_rate_hz": 2.0 + index + 0.01 * (starts + 0.5),
                        }
                    )
                )
            write_table(
                pd.concat(frames, ignore_index=True),
                root / "03_nex_exports/fullrate/demo_FullRate_bin1.0s.csv",
            )
            write_table(
                pd.DataFrame(
                    [
                        {
                            "file_id": "demo",
                            "pl2_file": "demo.pl2",
                            "has_light": "yes",
                            "light_on_s": 120.0,
                            "duration_s": 25.0,
                            "light_off_s": 145.0,
                        }
                    ]
                ),
                root / "02_stim_events/stim_schedule_master.csv",
            )
            write_table(
                pd.DataFrame(
                    [
                        {
                            "file_id": "demo",
                            "unit_id": f"unit{index + 1:02d}",
                            "original_name": f"SPK_SPKC{index + 1:02d}a",
                            "include": "yes",
                        }
                        for index in range(3)
                    ]
                ),
                root / "01_sorting_info/unit_quality_table.csv",
            )
            config_path = root / "config.yaml"
            save_yaml(config, config_path)

            completed = subprocess.run(
                [sys.executable, str(ROOT / "build_time_cluster_aligned_rate.py"), "--config", str(config_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output_path = (
                root
                / "03_nex_exports/time_cluster_aligned_rate/demo_TimeClusterAlignedRate_m120to300_bin30s.csv"
            )
            self.assertTrue(output_path.exists())
            output = pd.read_csv(output_path)
            self.assertEqual(set(output["source_bin_width_s"]), {1.0})
            self.assertEqual(set(output["target_bin_width_s"]), {30.0})
            self.assertEqual(set(output["n_source_bins"]), {30})
            self.assertEqual(set(output["rebin_method"]), {"duration_weighted_exact_aggregation"})

    def test_incomplete_target_bin_nan_policy_preserves_missingness(self):
        config = {
            "neuroexplorer": {"fullrate": {"bin_width_s": 10.0}},
            "time_cluster_aligned_rate": {
                "window_s": [-60, 60],
                "source_bin_width_s": 1.0,
                "bin_width_s": 30,
                "incomplete_target_bin_policy": "nan",
                "require_light_on_on_bin_boundary": False,
                "off_boundary_policy": "nearest",
            },
        }
        starts = np.arange(-0.5, 99.5, 1.0)
        fullrate = pd.DataFrame(
            {
                "file_id": "demo",
                "unit_id": "unit01",
                "time_bin_start_s": starts,
                "time_bin_end_s": starts + 1.0,
                "time_bin_center_s": starts + 0.5,
                "firing_rate_hz": np.ones(len(starts)),
            }
        )
        stimulus = pd.DataFrame(
            {"file_id": ["demo"], "light_on_s": [60.0], "duration_s": [25.0], "light_off_s": [85.0]}
        )
        aligned = build_time_cluster_aligned_rate_for_file(config, "demo", fullrate, stimulus)

        np.testing.assert_array_equal(aligned["aligned_time_s"], [-45.0, -15.0, 15.0, 45.0])
        incomplete = aligned.iloc[-1]
        self.assertTrue(pd.isna(incomplete["firing_rate_hz"]))
        self.assertEqual(int(incomplete["n_source_bins"]), 10)
        self.assertEqual(float(incomplete["source_coverage_s"]), 10.0)
        self.assertEqual(incomplete["rebin_method"], "incomplete_source_coverage_nan")

    def test_detects_reproducible_positive_temporal_cluster(self):
        rng = np.random.default_rng(721)
        time_s = np.arange(30, dtype=float)
        matrix = rng.normal(0.0, 1.0, size=(20, len(time_s)))
        matrix[:, 11:17] += 1.35

        first = temporal_cluster_permutation_test(
            matrix,
            time_s,
            n_permutations=999,
            seed=20260714,
        )
        second = temporal_cluster_permutation_test(
            matrix,
            time_s,
            n_permutations=999,
            seed=20260714,
        )

        significant = first.clusters[first.clusters["significant"]]
        positive = significant[significant["direction"] == "positive"]
        self.assertFalse(positive.empty)
        detected = positive.iloc[0]
        self.assertLessEqual(float(detected["start_time_s"]), 12.0)
        self.assertGreaterEqual(float(detected["end_time_s"]), 15.0)
        self.assertGreaterEqual(int(detected["n_bins"]), 4)
        self.assertLess(float(detected["cluster_p"]), 0.05)
        pd.testing.assert_frame_equal(first.clusters, second.clusters)
        np.testing.assert_array_equal(first.null_max_cluster_mass, second.null_max_cluster_mass)

    def test_fixed_seed_noise_has_no_significant_cluster(self):
        rng = np.random.default_rng(9917)
        time_s = np.arange(26, dtype=float)
        matrix = rng.normal(0.0, 1.0, size=(18, len(time_s)))
        result = temporal_cluster_permutation_test(
            matrix,
            time_s,
            n_permutations=999,
            seed=20260714,
        )
        self.assertTrue(result.clusters.empty or not bool(result.clusters["significant"].any()))

    def test_student_t_threshold_and_window_validation(self):
        self.assertEqual(time_cluster_config({})["test_window_s"], [0.0, 300.0])
        self.assertAlmostEqual(student_t_ppf(0.975, 19), 2.093024, places=5)
        exact = temporal_cluster_permutation_test(
            np.asarray([[0.1, 0.2], [0.0, -0.1], [-0.2, 0.1]]),
            np.asarray([0.0, 1.0]),
            n_permutations=100,
            max_exact_permutations=100,
        )
        self.assertEqual(exact.permutation_method, "exact")
        # Four two-sided complement classes: three null flips plus the observed
        # class supplied by the p-value's add-one correction.
        self.assertEqual(exact.n_permutations, 3)
        config = {
            "aligned_rate": {"pre_window_s": [-2, 0], "light_window_s": [1, 5]},
            "time_cluster_permutation": {
                "analysis_window_s": [-2, 6],
                "baseline_window_s": [-2, 2],
                "test_window_s": [1, 5],
            },
        }
        isolated_cfg = time_cluster_config({"aligned_rate": config["aligned_rate"]})
        self.assertEqual(isolated_cfg["baseline_window_s"], [-60.0, 0.0])
        self.assertEqual(
            isolated_cfg["input_pattern"],
            "*_TimeClusterAlignedRate_*.csv",
        )
        aligned = pd.DataFrame(
            {
                "file_id": ["demo"] * 18,
                "unit_id": np.repeat(["u1", "u2"], 9),
                "aligned_time_s": np.tile(np.arange(-2, 7, dtype=float), 2),
                "firing_rate_hz": np.ones(18),
                "source_aligned_file": ["fixture.csv"] * 18,
            }
        )
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            prepare_analysis_matrix(config, aligned)

    def test_minimal_fullrate_parser_to_outputs_smoke(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for relative in [
                "00_raw_pl2",
                "01_sorting_info",
                "02_stim_events",
                "03_nex_exports/fullrate",
                "03_nex_exports/aligned_rate",
                "03_nex_exports/time_cluster_aligned_rate",
                "07_statistics",
                "99_logs",
            ]:
                (root / relative).mkdir(parents=True, exist_ok=True)
            config = self._smoke_config(root)
            paths = resolve_project_paths(config)
            rng = np.random.default_rng(440)
            absolute_time = np.arange(3, 12, dtype=float)
            wide = pd.DataFrame({"Time": absolute_time})
            original_names = []
            for unit_index in range(12):
                original_name = f"SPK_SPKC{unit_index + 1:02d}a"
                original_names.append(original_name)
                values = 5.0 + rng.normal(0.0, 0.35, len(absolute_time))
                values[(absolute_time >= 6) & (absolute_time < 10)] += 1.8
                wide[original_name] = values

            # Reuse the existing SaveNumResults/fullrate parser, then the existing
            # light-aligned reconstruction before invoking the new branch.
            fullrate = convert_rate_export_to_long(wide, file_id="demo", kind="fullrate")
            stim = pd.DataFrame(
                [
                    {
                        "file_id": "demo",
                        "pl2_file": "demo.pl2",
                        "has_light": "yes",
                        "light_on_s": 5.0,
                        "duration_s": 4.0,
                        "light_off_s": 9.0,
                    }
                ]
            )
            aligned = build_time_cluster_aligned_rate_for_file(config, "demo", fullrate, stim)
            aligned_path = paths["time_cluster_aligned_rate_dir"] / "demo_TimeClusterAlignedRate_m2to6_bin1s.csv"
            write_table(aligned, aligned_path)
            write_table(
                pd.DataFrame(
                    [
                        {
                            "file_id": "demo",
                            "unit_id": f"unit{index + 1:02d}",
                            "original_name": original_name,
                            "channel": index + 1,
                            "include": "no" if index == len(original_names) - 1 else "yes",
                            "exclusion_reason": "manual_exclusion" if index == len(original_names) - 1 else "",
                        }
                        for index, original_name in enumerate(original_names)
                    ]
                ),
                paths["unit_quality_path"],
            )
            config_path = root / "config.yaml"
            save_yaml(config, config_path)
            completed = subprocess.run(
                [sys.executable, str(ROOT / "time_cluster_permutation.py"), "--config", str(config_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Units included/excluded: 11/1", completed.stdout)
            output_dir = root / "07_statistics/time_cluster_permutation"
            expected = [
                output_dir / "cluster_table.csv",
                output_dir / "time_bin_statistics.csv",
                output_dir / "unit_time_analysis_matrix.csv",
                output_dir / "unit_summary.csv",
                output_dir / "analysis_metadata.json",
                output_dir / "figures/unit_time_delta_rate_heatmap.png",
                output_dir / "figures/population_mean_delta_rate.png",
                output_dir / "figures/temporal_t_statistic.png",
            ]
            for path in expected:
                self.assertTrue(path.exists(), path)
                self.assertGreater(path.stat().st_size, 0, path)
            unit_summary = pd.read_csv(output_dir / "unit_summary.csv")
            excluded = unit_summary[~unit_summary["included"]]
            self.assertEqual(len(excluded), 1)
            self.assertEqual(excluded["exclusion_reason"].iloc[0], "manual_exclusion")

    def test_terminal_fullrate_and_time_cluster_branches_are_independent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for relative in [
                "00_raw_pl2",
                "01_sorting_info",
                "02_stim_events",
                "03_nex_exports/fullrate",
                "03_nex_exports/aligned_rate",
                "03_nex_exports/time_cluster_aligned_rate",
                "07_statistics",
                "99_logs",
            ]:
                (root / relative).mkdir(parents=True, exist_ok=True)
            config = self._smoke_config(root)
            config["aligned_rate"].update(
                {"pre_window_s": [-2, 0], "light_window_s": [0, 2], "post_window_s": [2, 4]}
            )
            config["time_cluster_aligned_rate"]["window_s"] = [-2, 4]
            config["time_cluster_permutation"].update(
                {
                    "analysis_window_s": [-2, 4],
                    "baseline_window_s": [-2, 0],
                    "test_window_s": [0, 4],
                }
            )
            centers = np.arange(3.0, 10.0, 1.0)
            frames = []
            metadata_rows = []
            for index in range(3):
                original_name = f"SPK_SPKC{index + 1:02d}a"
                frames.append(
                    pd.DataFrame(
                        {
                            "file_id": "demo",
                            "unit_id": original_name,
                            "time_bin_start_s": centers - 0.5,
                            "time_bin_end_s": centers + 0.5,
                            "time_bin_center_s": centers,
                            "firing_rate_hz": 2.0 + index + 0.2 * centers,
                        }
                    )
                )
                metadata_rows.append(
                    {
                        "file_id": "demo",
                        "unit_id": f"unit{index + 1:02d}",
                        "original_name": original_name,
                        "channel": index + 1,
                        "include": "no" if index == 2 else "yes",
                        "exclusion_reason": "manual_qc" if index == 2 else "",
                    }
                )
            write_table(
                pd.concat(frames, ignore_index=True),
                root / "03_nex_exports/fullrate/demo_FullRate_bin1s.csv",
            )
            write_table(
                pd.DataFrame(
                    [
                        {
                            "file_id": "demo",
                            "pl2_file": "demo.pl2",
                            "has_light": "yes",
                            "light_on_s": 5.0,
                            "duration_s": 2.0,
                            "light_off_s": 7.0,
                        }
                    ]
                ),
                root / "02_stim_events/stim_schedule_master.csv",
            )
            write_table(pd.DataFrame(metadata_rows), root / "01_sorting_info/unit_quality_table.csv")
            config_path = root / "config.yaml"
            save_yaml(config, config_path)

            normal = subprocess.run(
                [sys.executable, str(ROOT / "build_aligned_rate_from_fullrate.py"), "--config", str(config_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(normal.returncode, 0, normal.stderr)
            normal_path = root / "03_nex_exports/aligned_rate/demo_LightAlignedRate_pre2_post4_bin1s.csv"
            self.assertTrue(normal_path.exists())
            normal_data = pd.read_csv(normal_path)
            self.assertEqual(set(normal_data["unit_id"]), {row["original_name"] for row in metadata_rows})
            self.assertIn(0.0, normal_data["aligned_time_s"].unique())
            self.assertNotIn("aligned_bin_start_s", normal_data.columns)
            normal_summary = pd.read_csv(root / "03_nex_exports/aligned_rate/demo_PreLightPostSummary.csv")
            self.assertEqual(set(normal_summary["unit_id"]), {metadata_rows[0]["original_name"], metadata_rows[1]["original_name"]})
            self.assertFalse(any((root / "03_nex_exports/time_cluster_aligned_rate").glob("*.csv")))

            normal_path.unlink()
            dedicated = subprocess.run(
                [sys.executable, str(ROOT / "build_time_cluster_aligned_rate.py"), "--config", str(config_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(dedicated.returncode, 0, dedicated.stderr)
            dedicated_path = (
                root
                / "03_nex_exports/time_cluster_aligned_rate/demo_TimeClusterAlignedRate_m2to4_bin1s.csv"
            )
            self.assertTrue(
                dedicated_path.exists(),
                f"dedicated files={list((root / '03_nex_exports/time_cluster_aligned_rate').glob('*'))}; "
                f"stdout={dedicated.stdout!r}; stderr={dedicated.stderr!r}",
            )
            self.assertFalse(normal_path.exists())
            dedicated_data = pd.read_csv(dedicated_path)
            self.assertEqual(set(dedicated_data["unit_id"]), {row["original_name"] for row in metadata_rows})
            self.assertNotIn(0.0, dedicated_data["aligned_time_s"].unique())
            self.assertIn("aligned_bin_start_s", dedicated_data.columns)

            analysis = subprocess.run(
                [sys.executable, str(ROOT / "time_cluster_permutation.py"), "--config", str(config_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(analysis.returncode, 0, analysis.stderr)
            self.assertIn("Units included/excluded: 2/1", analysis.stdout)
            metadata_path = root / "07_statistics/time_cluster_permutation/analysis_metadata.json"
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_bin_widths_s"], [1.0])
            self.assertEqual(metadata["target_bin_widths_s"], [1.0])
            self.assertEqual(metadata["source_bins_per_target"], [1])
            self.assertEqual(metadata["rebin_methods"], ["identity"])

    @staticmethod
    def _smoke_config(root: Path) -> dict:
        return {
            "analysis": {"mode": "fullrate_aligned"},
            "project": {"root_dir": str(root), "file_id_column": "file_id"},
            "input": {
                "pl2_dir": "00_raw_pl2",
                "stim_schedule": "02_stim_events/stim_schedule_master.csv",
                "unit_quality_table": "01_sorting_info/unit_quality_table.csv",
            },
            "stim_schedule": {"output_path": "02_stim_events/stim_schedule_master.csv"},
            "unit_table": {"output_path": "01_sorting_info/unit_quality_table.csv"},
            "neuroexplorer": {
                "fullrate": {"bin_width_s": 1},
                "export": {
                    "output_fullrate_dir": "03_nex_exports/fullrate",
                    "output_aligned_rate_dir": "03_nex_exports/aligned_rate",
                    "expected_fullrate_pattern": "{file_id}_FullRate_bin{bin_width_s}s.csv",
                },
            },
            "aligned_rate": {
                "enabled": True,
                "pre_window_s": [-2, 0],
                "light_window_s": [1, 5],
                "post_window_s": [5, 6],
                "bin_width_s": 1,
                "multi_trial_aggregation": "mean",
                "variable_duration_policy": "keep_trials",
                "off_boundary_policy": "nearest",
            },
            "time_cluster_aligned_rate": {
                "enabled": True,
                "output_dir": "03_nex_exports/time_cluster_aligned_rate",
                "window_s": [-2, 6],
                "source_bin_width_s": None,
                "bin_width_s": 1,
                "require_light_on_on_bin_boundary": False,
                "off_boundary_policy": "nearest",
            },
            "time_cluster_permutation": {
                "enabled": True,
                "input_dir": "03_nex_exports/time_cluster_aligned_rate",
                "input_pattern": "*_TimeClusterAlignedRate_*.csv",
                "analysis_window_s": [-2, 6],
                "baseline_window_s": [-2, 0],
                "test_window_s": [1, 5],
                "cluster_forming_alpha": 0.05,
                "cluster_alpha": 0.05,
                "n_permutations": 255,
                "max_exact_permutations": 128,
                "tail": 0,
                "seed": 20260714,
                "output_subdir": "time_cluster_permutation",
                "figure_format": "png",
            },
            "statistics": {"output_dir": "07_statistics"},
            "origin": {"export_format": "png", "dpi": 80},
            "pptx": {"output_file": "06_pptx/PSTH_summary_auto.pptx"},
            "run": {"dry_run": False, "overwrite": True, "modules": {}},
        }


if __name__ == "__main__":
    unittest.main()
