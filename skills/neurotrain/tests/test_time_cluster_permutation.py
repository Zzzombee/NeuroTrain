from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_aligned_rate_from_fullrate import build_aligned_rate_for_file
from scripts.time_cluster_permutation import (
    prepare_analysis_matrix,
    student_t_ppf,
    temporal_cluster_permutation_test,
)
from utils.path_utils import resolve_project_paths, save_yaml
from utils.table_utils import convert_rate_export_to_long, write_table


class TimeClusterPermutationTests(unittest.TestCase):
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
            aligned, _ = build_aligned_rate_for_file(config, "demo", fullrate, stim)
            aligned_path = paths["nex_aligned_rate_dir"] / "demo_LightAlignedRate_pre2_post6_bin1s.csv"
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
            "time_cluster_permutation": {
                "enabled": True,
                "input_pattern": "*_LightAlignedRate_*.csv",
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
