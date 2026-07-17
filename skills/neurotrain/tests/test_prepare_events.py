from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prepare_events import prepare_events
from utils.event_utils import derive_light_on_off_from_intervals, read_light_intervals
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths


def sample_config(tmp_root: Path) -> dict:
    return {
        "project": {"root_dir": str(tmp_root), "file_id_column": "file_id"},
        "input": {
            "pl2_dir": "00_raw_pl2",
            "stim_schedule": "02_stim_events/stim_schedule_master.csv",
            "unit_quality_table": "01_sorting_info/unit_quality_table.csv",
        },
        "neuroexplorer": {
            "enabled": True,
            "backend": "manual_csv",
            "events": {
                "stimulus_input_mode": "interval",
                "interval_name": "Light_Interval",
                "event_on_name": "Light_On",
                "event_off_name": "Light_Off",
                "reference_event": "Light_On",
                "require_light_on": True,
                "require_light_off": False,
                "derive_events_from_interval": True,
            },
            "interval": {
                "import_interval_csv": True,
                "interval_csv_pattern": "{file_id}_Light_Interval.csv",
                "include_variable_name_first_line": True,
                "interval_variable_name": "Light_Interval",
                "interval_start_column": 0,
                "interval_end_column": 1,
                "no_standard_header": True,
                "no_header": True,
                "delimiter": ",",
            },
            "export": {
                "output_psth_dir": "03_nex_exports/psth",
                "output_fullrate_dir": "03_nex_exports/fullrate",
                "output_raster_dir": "03_nex_exports/raster",
                "format": "csv",
                "expected_psth_pattern": "{file_id}_LightOn_PSTH_bin{bin_width_s}s.csv",
                "expected_fullrate_pattern": "{file_id}_FullRate_bin{bin_width_s}s.csv",
            },
            "psth": {
                "reference_source": "interval_start",
                "x_min_s": -60,
                "x_max_s": 75,
                "bin_width_s": 1,
                "histogram_unit": "Spikes per second",
                "reference_event": "Light_On",
                "light_band_start_s": 0,
                "light_band_end_mode": "duration_s",
            },
            "fullrate": {"bin_width_s": 1, "histogram_unit": "Spikes per second"},
            "plotting": {
                "light_band_source": "interval",
                "psth_light_band_mode": "relative_to_interval_start",
                "fullrate_light_band_mode": "absolute_interval",
                "psth_duration_policy": "median",
            },
        },
        "origin": {
            "enabled": True,
            "use_com": True,
            "template_psth": "04_origin_projects/templates/PSTH_template.otpu",
            "template_fullrate": "04_origin_projects/templates/FullRate_template.otpu",
            "export_format": "png",
            "dpi": 300,
            "light_band_color": "#B7C9E8",
            "light_band_alpha": 0.30,
            "plot_style": "step_line",
            "fallback_matplotlib": True,
        },
        "pptx": {
            "enabled": True,
            "output_file": "06_pptx/PSTH_summary_auto.pptx",
            "layout": "one_unit_per_slide",
            "slide_width_in": 13.333,
            "slide_height_in": 7.5,
            "include_metadata": True,
            "include_qc_notes": True,
        },
        "run": {"overwrite": False, "dry_run": False, "stop_on_error": False, "modules": {}},
    }


class PrepareEventsTests(unittest.TestCase):
    def _init_project(self, tmp_root: Path, stim_csv: str) -> tuple[dict, PipelineLogger]:
        for relative_dir in [
            "00_raw_pl2",
            "01_sorting_info",
            "02_stim_events",
            "03_nex_exports/psth",
            "03_nex_exports/fullrate",
            "03_nex_exports/raster",
            "04_origin_projects/templates",
            "05_exported_figures/psth",
            "05_exported_figures/fullrate",
            "05_exported_figures/summary",
            "06_pptx",
            "99_logs",
        ]:
            (tmp_root / relative_dir).mkdir(parents=True, exist_ok=True)
        (tmp_root / "02_stim_events" / "stim_schedule_master.csv").write_text(stim_csv, encoding="utf-8")
        (tmp_root / "01_sorting_info" / "unit_quality_table.csv").write_text(
            "file_id,unit_id,include\n"
            "test02,unit01,yes\n",
            encoding="utf-8",
        )
        config = sample_config(tmp_root)
        logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
        return config, logger

    def test_single_event_no_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            config, logger = self._init_project(
                tmp_root,
                "file_id,pl2_file,event_group,light_on_s,duration_s,light_off_s,condition,note\n"
                "test02,02_sorted.pl2,light,120,15,,420nm LED,1\n",
            )
            prepare_events(config=config, logger=logger)
            on_text = (tmp_root / "02_stim_events" / "exported_events" / "test02_Light_On.txt").read_text(encoding="utf-8")
            off_text = (tmp_root / "02_stim_events" / "exported_events" / "test02_Light_Off.txt").read_text(encoding="utf-8")
            interval_text = (tmp_root / "02_stim_events" / "exported_events" / "test02_Light_Interval.csv").read_text(encoding="utf-8")
            self.assertEqual(on_text, "120\n")
            self.assertEqual(off_text, "135\n")
            self.assertEqual(interval_text, "Light_Interval\n120,135\n")
            self.assertNotIn("time_s", on_text)
            self.assertNotIn("time_s", off_text)
            self.assertNotIn("start,end", interval_text.lower())

    def test_multi_event_no_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            config, logger = self._init_project(
                tmp_root,
                "file_id,pl2_file,event_group,light_on_s,duration_s,light_off_s,condition,note\n"
                "test02,02_sorted.pl2,light,120,15,,420nm LED,1\n"
                "test02,02_sorted.pl2,light,240,15,,420nm LED,2\n",
            )
            prepare_events(config=config, logger=logger)
            on_text = (tmp_root / "02_stim_events" / "exported_events" / "test02_Light_On.txt").read_text(encoding="utf-8")
            off_text = (tmp_root / "02_stim_events" / "exported_events" / "test02_Light_Off.txt").read_text(encoding="utf-8")
            interval_text = (tmp_root / "02_stim_events" / "exported_events" / "test02_Light_Interval.csv").read_text(encoding="utf-8")
            self.assertEqual(on_text, "120\n240\n")
            self.assertEqual(off_text, "135\n255\n")
            self.assertEqual(interval_text, "Light_Interval\n120,135\n240,255\n")
            self.assertNotIn("time_s", on_text)
            self.assertNotIn("time_s", off_text)
            self.assertNotIn("start,end", interval_text.lower())

    def test_read_light_intervals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "interval.csv"
            path.write_text("Light_Interval\n120,135\n", encoding="utf-8")
            intervals = read_light_intervals(path, interval_name="Light_Interval")
            self.assertEqual(intervals, [(120.0, 135.0)])
            on_times, off_times, durations = derive_light_on_off_from_intervals(intervals)
            self.assertEqual(on_times, [120.0])
            self.assertEqual(off_times, [135.0])
            self.assertEqual(durations, [15.0])

    def test_read_light_intervals_rejects_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "interval.csv"
            path.write_text("start,end\n120,135\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_light_intervals(path, interval_name="Light_Interval")

    def test_read_light_intervals_rejects_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "interval.csv"
            path.write_text("Light_Interval\n120\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_light_intervals(path, interval_name="Light_Interval")

            path.write_text("Light_Interval\n120,120\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_light_intervals(path, interval_name="Light_Interval")

    def test_read_light_intervals_rejects_missing_variable_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "interval.csv"
            path.write_text("120,135\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_light_intervals(path, interval_name="Light_Interval")
