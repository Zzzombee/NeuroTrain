from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
import pandas as pd
import yaml

from scripts.raster_pipeline import (
    RasterConfig,
    RasterConfigError,
    RasterInputError,
    RasterPaths,
    align_unit_spikes,
    build_trials,
    load_inputs,
    load_raster_config,
    load_spike_table,
    main,
    render_combined_raster,
    render_raster,
    run_raster_pipeline,
)
from scripts.raster_project import initialize_raster_project, run_project_raster


def make_config(
    root: Path,
    *,
    time_unit: str = "seconds",
    window: tuple[float, float] = (-1.0, 1.0),
    overlap: str = "allow",
    overwrite: bool = False,
) -> RasterConfig:
    input_root = root / "input"
    output_root = root / "output"
    input_root.mkdir(parents=True, exist_ok=True)
    return RasterConfig(
        schema_version=1,
        config_path=root / "raster_config.yaml",
        paths=RasterPaths(input_root, output_root, "*unit_train.csv", "*events.csv"),
        input={
            "format": "neuroexplorer_long_csv",
            "delimiter": None,
            "encoding": "utf-8-sig",
            "time_unit": time_unit,
            "columns": {
                "session_id": "session_id",
                "unit_id": "unit_id",
                "channel_id": "channel_id",
                "spike_time": "timestamp",
                "event_name": "event_name",
                "event_time": "timestamp",
                "trial_id": None,
                "stimulus_duration": "stimulus_duration_s",
            },
        },
        alignment={
            "event_name": "Light_On",
            "window_s": list(window),
            "boundary": "left_closed_right_open",
            "trial_order": "event_time",
            "minimum_inter_event_interval_s": None,
            "overlapping_windows": overlap,
        },
        trial_filter={"include_trial_ids": None, "exclude_trial_ids": []},
        plot={
            "formats": ["png"],
            "dpi": 72,
            "figsize_inches": [4.0, 3.0],
            "spike_linewidth": 0.6,
            "spike_height_fraction": 0.8,
            "alignment_linewidth": 1.0,
            "show_alignment_line": True,
            "transparent_background": False,
            "combined_width_inches": 5.0,
            "combined_row_height_inches": 0.5,
            "combined_min_height_inches": 3.0,
        },
        output={
            "write_trial_summary_csv": True,
            "write_unit_summary_csv": True,
            "write_exclusion_csv": True,
            "write_aligned_spikes_long_csv": False,
            "write_manifest_json": True,
            "write_individual_figures": True,
            "write_combined_figure": True,
            "combined_filename": "project_combined_raster",
            "write_combined_row_map_csv": True,
            "overwrite": overwrite,
        },
        runtime={"fail_on_empty_unit": False, "continue_on_unit_error": True},
        raw={},
    )


def write_tables(config: RasterConfig) -> tuple[Path, Path]:
    spike_path = config.paths.input_root / "sample_unit_train.csv"
    event_path = config.paths.input_root / "sample_events.csv"
    pd.DataFrame(
        {
            "session_id": ["session-A", "session-A", "session-A", "session-A"],
            "unit_id": ["unit-1", "unit-1", "unit-2", "unit-2"],
            "channel_id": ["SPK01", "SPK01", "SPK02", "SPK02"],
            "timestamp": [9.5, 10.0, 10.5, 20.0],
        }
    ).to_csv(spike_path, index=False)
    pd.DataFrame(
        {
            "session_id": ["session-A", "session-A"],
            "event_name": ["Light_On", "Light_On"],
            "timestamp": [10.0, 20.0],
            "stimulus_duration_s": [2.0, 3.0],
        }
    ).to_csv(event_path, index=False)
    return spike_path, event_path


def yaml_config(config: RasterConfig) -> dict:
    return {
        "schema_version": 1,
        "paths": {
            "input_root": str(config.paths.input_root),
            "output_root": str(config.paths.output_root),
            "spike_table_glob": config.paths.spike_table_glob,
            "event_table_glob": config.paths.event_table_glob,
            "output_subdir": "raster",
        },
        "input": config.input,
        "alignment": config.alignment,
        "trial_filter": config.trial_filter,
        "plot": config.plot,
        "output": config.output,
        "runtime": config.runtime,
    }


class RasterPipelineTests(unittest.TestCase):
    def test_long_csv_parser_preserves_units_precision_and_time_scale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), time_unit="milliseconds")
            path = config.paths.input_root / "parser_unit_train.csv"
            pd.DataFrame(
                {
                    "session_id": ["s1", "s1", "s1", "s1", None],
                    "unit_id": ["u1", "u1", "u2", "u2", "u3"],
                    "channel_id": ["c1", "c1", "c2", "c2", "c3"],
                    "timestamp": [1000.125, 2000.5, 500.25, None, 99.0],
                }
            ).to_csv(path, index=False)

            parsed = load_spike_table(path, config)

            self.assertEqual(set(parsed["unit_id"]), {"u1", "u2"})
            self.assertEqual(parsed.groupby("unit_id").size().to_dict(), {"u1": 2, "u2": 1})
            self.assertAlmostEqual(parsed.loc[parsed["unit_id"] == "u1", "spike_time_absolute_s"].iloc[0], 1.000125)
            self.assertAlmostEqual(parsed.loc[parsed["unit_id"] == "u2", "spike_time_absolute_s"].iloc[0], 0.50025)
            self.assertEqual(parsed["_source_file"].unique().tolist(), [str(path)])

    def test_alignment_boundary_order_and_empty_trials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            spikes = pd.DataFrame(
                {
                    "session_id": ["s1"] * 7,
                    "unit_id": ["u1"] * 6 + ["u2"],
                    "channel_id": ["c1"] * 6 + ["c2"],
                    "spike_time_absolute_s": [9.0, 10.0, 11.0, 19.0, 20.0, 21.0, 100.0],
                    "_source_file": ["spikes.csv"] * 7,
                }
            )
            events = pd.DataFrame(
                {
                    "session_id": ["s1", "s1", "s1"],
                    "event_name": ["Light_On"] * 3,
                    "event_time_absolute_s": [20.0, 10.0, 30.0],
                    "trial_id": ["", "", ""],
                    "_source_file": ["events.csv"] * 3,
                }
            )

            trials = build_trials(events, config)
            aligned, summary = align_unit_spikes(spikes, trials, config)

            self.assertEqual(trials["event_time_absolute_s"].tolist(), [10.0, 20.0, 30.0])
            self.assertEqual(aligned.loc[aligned["unit_id"] == "u1", "spike_time_relative_s"].tolist(), [-1.0, 0.0, -1.0, 0.0])
            self.assertNotIn(1.0, aligned["spike_time_relative_s"].tolist())
            self.assertEqual(summary.shape[0], 6)
            self.assertEqual(summary.loc[(summary["unit_id"] == "u1") & (summary["trial_index"] == 3), "n_spikes_in_window"].iloc[0], 0)
            self.assertEqual(summary["source_event_file"].unique().tolist(), ["events.csv"])

    def test_overlapping_windows_allow_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), overlap="allow")
            events = pd.DataFrame(
                {
                    "session_id": ["s1", "s1"],
                    "event_name": ["Light_On", "Light_On"],
                    "event_time_absolute_s": [10.0, 10.5],
                    "trial_id": ["", ""],
                    "_source_file": ["events.csv", "events.csv"],
                }
            )
            spikes = pd.DataFrame(
                {
                    "session_id": ["s1"],
                    "unit_id": ["u1"],
                    "channel_id": ["c1"],
                    "spike_time_absolute_s": [10.2],
                    "_source_file": ["spikes.csv"],
                }
            )

            trials = build_trials(events, config)
            aligned, _ = align_unit_spikes(spikes, trials, config)
            self.assertEqual(len(aligned), 2)
            self.assertEqual(trials["overlaps_another_trial_window"].tolist(), [True, True])

            error_config = make_config(Path(tmp), overlap="error")
            with self.assertRaisesRegex(RasterInputError, r"minimum_inter_event_interval_s=0.5.*required_window_length_s=2.0"):
                build_trials(events, error_config)

    def test_independent_config_validation_and_missing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_config(root)
            write_tables(config)
            raw = yaml_config(config)
            config_path = root / "standalone_raster.yaml"
            config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

            loaded = load_raster_config(config_path)
            self.assertEqual(loaded.schema_version, 1)
            self.assertEqual(main(["--config", str(config_path), "--validate-only"]), 0)
            self.assertFalse((root / "config.yaml").exists())

            for key, value, message in [
                ("schema_version", 99, "schema_version"),
                ("input.time_unit", "fortnights", "input.time_unit"),
                ("alignment.window_s", [1.0, -1.0], "alignment.window_s"),
            ]:
                invalid = yaml.safe_load(yaml.safe_dump(raw))
                if "." in key:
                    parent, child = key.split(".")
                    invalid[parent][child] = value
                else:
                    invalid[key] = value
                invalid_path = root / f"invalid_{message.replace('.', '_')}.yaml"
                invalid_path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
                with self.assertRaisesRegex(RasterConfigError, message.replace(".", r"\.")):
                    load_raster_config(invalid_path)

            pd.DataFrame({"session_id": ["s1"], "unit_id": ["u1"]}).to_csv(
                config.paths.input_root / "bad_unit_train.csv", index=False
            )
            config.paths.spike_table_glob = "bad_unit_train.csv"
            with self.assertRaisesRegex(RasterInputError, "Missing required columns.*timestamp"):
                load_inputs(config)

    def test_render_limits_and_end_to_end_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_config(root)
            write_tables(config)
            spikes, events, _, _ = load_inputs(config)
            trials = build_trials(events, config)
            aligned, trial_summary = align_unit_spikes(spikes, trials, config)
            unit_trials = trial_summary[trial_summary["unit_id"] == "unit-1"]
            unit_aligned = aligned[aligned["unit_id"] == "unit-1"]
            preview = root / "preview"
            with patch("scripts.raster_pipeline.plt.close"):
                render_raster(unit_aligned, unit_trials, config, preview)
                figure = plt.figure(plt.get_fignums()[-1])
                self.assertEqual(figure.axes[0].get_xlim(), (-1.0, 1.0))
                self.assertEqual(figure.axes[0].get_yticks().tolist(), unit_trials["trial_index"].tolist())
                self.assertEqual(len(figure.axes[0].collections), len(unit_trials))
            plt.close("all")

            summary = run_raster_pipeline(config)

            raster_root = config.paths.raster_root
            pngs = sorted((raster_root / "figures").glob("**/*.png"))
            self.assertEqual(len(pngs), 3)
            self.assertTrue(all(path.stat().st_size > 0 for path in pngs))
            self.assertEqual(summary["figures_written"], 3)
            self.assertEqual(summary["combined_figures_written"], 1)
            self.assertTrue((raster_root / "tables" / "unit_summary.csv").exists())
            self.assertTrue((raster_root / "tables" / "trial_summary.csv").exists())
            self.assertTrue((raster_root / "tables" / "exclusions.csv").exists())
            manifest = json.loads((raster_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["alignment"]["boundary"], "left_closed_right_open")
            self.assertEqual(manifest["trial_axis_order"], "trial 1 at top")
            self.assertEqual(len(manifest["figure_mappings"]), 2)
            self.assertTrue(Path(manifest["outputs"]["combined_figure"]).exists())
            row_map = pd.read_csv(raster_root / "tables" / "combined_row_map.csv")
            self.assertEqual(row_map["session_id"].tolist(), ["session-A"] * 4)
            self.assertEqual(row_map["stimulus_duration_s"].tolist(), [2.0, 3.0, 2.0, 3.0])
            self.assertEqual(plt.get_fignums(), [])
            with self.assertRaisesRegex(RasterInputError, "output.overwrite=false"):
                run_raster_pipeline(config)

    def test_combined_raster_orders_units_and_draws_per_row_durations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            trials = pd.DataFrame(
                {
                    "session_id": ["s2", "s1"],
                    "unit_id": ["u2", "u1"],
                    "trial_id": ["t2", "t1"],
                    "trial_index": [1, 1],
                    "event_time_absolute_s": [10.0, 10.0],
                    "stimulus_duration_s": [0.8, 0.4],
                    "source_event_file": ["events.csv", "events.csv"],
                    "n_spikes_in_window": [1, 1],
                    "overlaps_another_trial_window": [False, False],
                }
            )
            aligned = pd.DataFrame(
                {
                    "session_id": ["s2", "s1"],
                    "unit_id": ["u2", "u1"],
                    "trial_id": ["t2", "t1"],
                    "spike_time_relative_s": [0.2, -0.2],
                }
            )
            output = Path(tmp) / "combined"
            with patch("scripts.raster_pipeline.plt.close"):
                written, row_map = render_combined_raster(aligned, trials, config, output)
                figure = plt.figure(plt.get_fignums()[-1])
                axis = figure.axes[0]
                self.assertEqual([label.get_text() for label in axis.get_yticklabels()], ["s1 | u1", "s2 | u2"])
                self.assertEqual([rectangle.get_width() for rectangle in axis.patches], [0.4, 0.8])
                self.assertEqual(row_map["session_id"].tolist(), ["s1", "s2"])
                self.assertTrue(written[0].exists())
            plt.close("all")

    def test_low_agent_project_run_initializes_exports_and_plots(self) -> None:
        class FakeVar:
            def __init__(self, name: str, timestamps: list[float]):
                self._name = name
                self._timestamps = timestamps

            def Name(self):
                return self._name

            def Timestamps(self):
                return self._timestamps

        class FakeDoc:
            def __init__(self, timestamps: list[float]):
                self._timestamps = timestamps

            def NeuronVars(self):
                return [FakeVar("SPK01a", self._timestamps)]

        class FakeNex:
            @staticmethod
            def OpenDocument(path: str):
                return FakeDoc([9.5, 10.0, 10.5] if "01" in Path(path).name else [19.5, 20.0])

            @staticmethod
            def CloseDocument(_doc):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "00_raw_pl2").mkdir()
            for name in ["sorted_01.pl2", "sorted_02.pl2"]:
                (project / "00_raw_pl2" / name).touch()
            (project / "02_stim_events").mkdir()
            pd.DataFrame(
                {
                    "file_id": [1, 2],
                    "pl2_file": ["sorted_01.pl2", "sorted_02.pl2"],
                    "has_light": ["yes", "yes"],
                    "light_on_s": [10.0, 20.0],
                    "duration_s": [0.4, 0.8],
                }
            ).to_excel(project / "02_stim_events" / "stim_schedule_master.xlsx", index=False)
            (project / "01_sorting_info").mkdir()
            pd.DataFrame(
                {
                    "file_id": ["01", "02"],
                    "unit_id": ["unit01", "unit01"],
                    "channel": [1, 1],
                    "original_name": ["SPK01a", "SPK01a"],
                    "include": ["yes", "yes"],
                }
            ).to_excel(project / "01_sorting_info" / "unit_quality_table.xlsx", index=False)

            config_path, created = initialize_raster_project(project)
            self.assertTrue(created)
            self.assertTrue(config_path.exists())
            self.assertFalse((project / "config.yaml").exists())
            result = run_project_raster(project, nex_module=FakeNex)

            self.assertEqual(result["export"]["counts"]["units"], 2)
            self.assertEqual(result["export"]["counts"]["spikes"], 5)
            self.assertEqual(result["raster"]["combined_figures_written"], 1)
            raster_root = project / "03_nex_exports" / "raster"
            self.assertTrue((raster_root / "figures" / "project_combined_raster.png").exists())
            events = pd.read_csv(project / "03_nex_exports" / "raster_input" / "alignment_events.csv")
            self.assertEqual(events["stimulus_duration_s"].tolist(), [0.4, 0.8])


if __name__ == "__main__":
    unittest.main()
