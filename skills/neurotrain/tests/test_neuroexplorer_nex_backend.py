from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.adapters.neuroexplorer_adapter import NeedsManualActionError
from scripts.adapters.neuroexplorer_adapter import NeuroExplorerAdapter, NexPackageUnavailableError
from scripts.adapters.neuroexplorer_nex_backend import NexPackageBackend
from scripts import export_from_neuroexplorer as export_module
from scripts.export_from_neuroexplorer import export_from_neuroexplorer
from scripts.utils.neuroexplorer_introspection import dump_nex_api
from utils.event_utils import read_neuroexplorer_interval_csv
from utils.logging_utils import PipelineLogger
from utils.path_utils import resolve_project_paths
from utils.table_utils import convert_wide_psth_to_long


def sample_config(tmp_root: Path) -> dict:
    return {
        "analysis": {"mode": "neuroexplorer_psth"},
        "project": {"root_dir": str(tmp_root), "file_id_column": "file_id"},
        "input": {
            "pl2_dir": "00_raw_pl2",
            "stim_schedule": "02_stim_events/stim_schedule_master.csv",
            "unit_quality_table": "01_sorting_info/unit_quality_table.csv",
        },
        "stim_schedule": {
            "auto_build_from_filenames": True,
            "update_existing": True,
            "preserve_manual_edits": True,
            "output_path": "02_stim_events/stim_schedule_master.csv",
            "source": {"pl2_dir": "00_raw_pl2", "file_glob": "*.pl2"},
            "filename_parser": {
                "enabled": True,
                "pattern_name": "sorted_index_light_channels",
                "regex": r"^sorted_(?P<file_index>\d+)_(?P<light_on>\d+(?:\.\d+)?)light(?P<duration>\d+(?:\.\d+)?)_(?P<channels>[0-9,]+)\.pl2$",
                "patterns": {
                    "light": {
                        "regex": r"^sorted_(?P<file_index>\d+)_(?P<light_on>\d+(?:\.\d+)?)light(?P<duration>\d+(?:\.\d+)?)_(?P<channels>[0-9,]+)\.pl2$",
                    },
                    "no_light": {
                        "regex": r"^sorted_(?P<file_index>\d+)_nolight_(?P<channels>[0-9,]+)\.pl2$",
                    },
                },
                "case_sensitive": False,
            },
            "file_id": {"format": "{file_index}", "zero_pad": 2},
            "defaults": {"condition": "", "note_prefix": "sorted channels: "},
            "conflict_policy": {
                "existing_row_key": "pl2_file",
                "on_parse_fail": "warn_skip",
                "on_existing_manual_edit": "preserve",
            },
        },
        "unit_table": {
            "enabled": True,
            "auto_build_if_missing": True,
            "update_existing": True,
            "preserve_manual_edits": True,
            "output_path": "01_sorting_info/unit_quality_table.csv",
            "source": {"backend": "nex", "use_active_doc": False, "open_pl2": True},
            "unit_detection": {
                "variable_kind": "NeuronNames",
                "include_patterns": ["SPK", "SPKC"],
                "exclude_patterns": ["Unsorted", "Noise", "INVALID"],
                "case_sensitive": False,
            },
            "numbering": {
                "unit_id_format": "unit{index:02d}",
                "per_file_reset": True,
                "sort_by_channel_then_suffix": True,
            },
            "default_values": {
                "include": "yes",
                "exclusion_reason": "",
                "duplicate_of": "",
                "representative_unit": "",
                "note": "",
            },
        },
        "aligned_rate": {
            "enabled": True,
            "window_mode": "light_duration_plus_margin",
            "pre_margin_s": 60,
            "post_margin_s": 60,
            "x_min_s": -60,
            "x_max_s": 75,
            "pre_window_s": [-60, 0],
            "light_window_s": [5, 20],
            "post_window_s": [25, 85],
            "align_to": "light_on_s",
            "bin_width_s": 1,
            "multi_trial_aggregation": "mean",
            "variable_duration_policy": "keep_trials",
            "require_light_on_on_bin_boundary": False,
            "off_boundary_policy": "nearest",
        },
        "plotting": {
            "light_band_source": "interval",
            "psth_light_band_mode": "relative_to_interval_start",
            "fullrate_light_band_mode": "absolute_interval",
            "psth_duration_policy": "median",
            "psth_like_from_fullrate": True,
            "full_session_light_band_source": "stim_schedule",
            "aligned_light_band_start_s": 0,
            "aligned_light_band_end_mode": "duration_s",
        },
        "neuroexplorer": {
            "enabled": True,
            "backend": "nex_package",
            "use_existing_csv_if_available": True,
            "stop_if_export_failed": False,
            "export_psth": True,
            "export_fullrate": False,
            "nex_package": {
                "require_active_neuroexplorer": True,
                "enable_external_python_required": True,
                "smoke_test": True,
                "introspect_api": True,
                "fail_to_manual_csv": True,
            },
            "files": {"open_pl2_mode": "try_nex_then_manual", "save_intermediate_nex5": False},
            "events": {
                "stimulus_input_mode": "interval",
                "interval_name": "Light_Interval",
                "event_on_name": "Light_On",
                "event_off_name": "Light_Off",
                "reference_event": "Light_On",
                "require_light_on": True,
                "require_light_off": False,
                "derive_events_from_interval": True,
                "allow_single_interval_variable_auto_match": False,
            },
            "event_creation": {"method": "disabled", "if_exists": "replace"},
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
            "templates": {
                "psth_template_name": "PSTH_LightOn",
                "fullrate_template_name": "FullRate",
                "raster_template_name": "PerieventRaster",
                "auto_modify_template": True,
                "parameter_names": {},
            },
            "psth": {
                "x_min_s": -60,
                "x_max_s": 75,
                "bin_width_s": 1,
                "histogram_unit": "Spikes per second",
                "reference_event": "Light_On",
                "light_band_start_s": 0,
                "light_band_end_mode": "duration_s",
            },
            "fullrate": {
                "enabled": False,
                "template_name": "RateHist_FullSession",
                "bin_width_s": 1,
                "histogram_unit": "Spikes per second",
                "x_min_s": 0,
                "x_max_s": None,
                "save_num_results": True,
                "skip_if_template_missing": True,
            },
            "export": {
                "output_psth_dir": "03_nex_exports/psth",
                "output_fullrate_dir": "03_nex_exports/fullrate",
                "output_raster_dir": "03_nex_exports/raster",
                "output_aligned_rate_dir": "03_nex_exports/aligned_rate",
                "format": "csv",
                "expected_psth_pattern": "{file_id}_LightOn_PSTH_bin{bin_width_s}s.csv",
                "expected_fullrate_pattern": "{file_id}_FullRate_bin{bin_width_s}s.csv",
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
        "run": {
            "overwrite": False,
            "dry_run": False,
            "stop_on_error": False,
            "modules": {"build_stim_schedule": True, "build_unit_table": True},
        },
    }


class FakeNexModule:
    def GetActiveDocument(self):
        return {"name": "fake"}

    def ApplyTemplate(self, doc, template_name):
        return None

    def ModifyTemplate(self, doc, template_name, parameter_name, value):
        return None

    def SaveDocument(self, path):
        return None


class ExportingNexModule:
    def __init__(self, fail_save_num: bool = False, fail_save_results: bool = False):
        self.fail_save_num = fail_save_num
        self.fail_save_results = fail_save_results
        self.applied_templates: list[str] = []
        self.saved_num_paths: list[str] = []
        self.saved_results_paths: list[str] = []

    def ApplyTemplate(self, doc, template_name):
        self.applied_templates.append(template_name)

    def SaveNumResults(self, doc, path):
        self.saved_num_paths.append(path)
        if self.fail_save_num:
            raise RuntimeError("SaveNumResults failed")
        Path(path).write_text("bin_center_s\tSPK_SPKC04a\n0\t5\n1\t6\n", encoding="utf-8")

    def SaveResults(self, doc, path):
        self.saved_results_paths.append(path)
        if self.fail_save_results:
            raise RuntimeError("SaveResults failed")
        Path(path).write_text("bin_center_s,SPK_SPKC04a\n0,5\n1,6\n", encoding="utf-8")


class FakeNexVar:
    _next_id = 1

    def __init__(self, name: str, var_type: str):
        self.name = name
        self.varType = var_type
        self.varId = FakeNexVar._next_id
        self.docId = 1
        self.intervals: list[tuple[float, float]] = []
        self.timestamps: list[float] = []
        FakeNexVar._next_id += 1

    def __repr__(self):
        return f"FakeNexVar(name={self.name!r}, varType={self.varType!r}, varId={self.varId})"


class FakeVarCollection:
    def __init__(self, doc, kind: str, support_add: bool = False):
        self.doc = doc
        self.kind = kind
        self.support_add = support_add

    def __getitem__(self, key):
        return self.doc.vars[key]

    def get(self, key):
        return self.doc.vars.get(key)

    def __iter__(self):
        names = self.doc.interval_names if self.kind == "interval" else self.doc.event_names
        return iter(self.doc.vars[name] for name in names)

    def __len__(self):
        names = self.doc.interval_names if self.kind == "interval" else self.doc.event_names
        return len(names)

    def Add(self, name):
        if not self.support_add:
            raise RuntimeError("Add not supported")
        return self.doc._create_var(name, self.kind)


class FakeDocForCreation:
    def __init__(self, *, support_setitem: bool = False, support_collection_add: bool = False):
        self.support_setitem = support_setitem
        self.vars: dict[str, FakeNexVar] = {}
        self.interval_names: list[str] = []
        self.event_names: list[str] = []
        self.neuron_names: list[str] = []
        self._interval_vars = FakeVarCollection(self, "interval", support_add=support_collection_add)
        self._event_vars = FakeVarCollection(self, "event", support_add=support_collection_add)
        self._create_var("AllFile", "interval")
        self._create_var("Neuron01", "neuron")

    def _create_var(self, name: str, kind: str):
        if name in self.vars:
            return self.vars[name]
        var_type = "interval" if kind == "interval" else "event" if kind == "event" else "neuron"
        var = FakeNexVar(name, var_type)
        self.vars[name] = var
        if kind == "interval":
            self.interval_names.append(name)
        elif kind == "event":
            self.event_names.append(name)
        elif kind == "neuron":
            self.neuron_names.append(name)
        return var

    def __getitem__(self, key):
        return self.vars[key]

    def __setitem__(self, key, value):
        if not self.support_setitem:
            raise RuntimeError("__setitem__ not supported")
        kind = "interval" if "Interval" in key else "event"
        self._create_var(key, kind)

    @property
    def EventNames(self):
        return list(self.event_names)

    @property
    def IntervalNames(self):
        return list(self.interval_names)

    @property
    def NeuronNames(self):
        return list(self.neuron_names)

    @property
    def EventVars(self):
        return self._event_vars

    @property
    def IntervalVars(self):
        return self._interval_vars


class CreationNexModule:
    def __init__(self, doc: FakeDocForCreation, *, delete_raises: bool = False):
        self.doc = doc
        self.add_interval_calls: list[tuple] = []
        self.add_timestamp_calls: list[tuple] = []
        self.delete_calls: list[tuple] = []
        self.delete_raises = delete_raises

    def AddInterval(self, *args):
        self.add_interval_calls.append(args)
        if len(args) == 3 and isinstance(args[0], FakeNexVar) and args[0].varType == "interval":
            args[0].intervals.append((float(args[1]), float(args[2])))
            return None
        raise RuntimeError("unsupported AddInterval signature")

    def AddTimestamp(self, *args):
        self.add_timestamp_calls.append(args)
        if len(args) == 2 and isinstance(args[0], FakeNexVar) and args[0].varType == "event":
            args[0].timestamps.append(float(args[1]))
            return None
        raise RuntimeError("unsupported AddTimestamp signature")

    def GetName(self, var):
        if isinstance(var, FakeNexVar):
            return var.name
        raise RuntimeError("not a NexVar")

    def DeleteVar(self, *args):
        self.delete_calls.append(args)
        if self.delete_raises:
            raise RuntimeError("delete failed")
        if len(args) == 3 and args[0] is self.doc:
            var_id = args[1]
            target_name = None
            for name, var in list(self.doc.vars.items()):
                if var.varId == var_id:
                    target_name = name
                    break
            if target_name is None:
                raise RuntimeError("varId not found")
            if target_name in self.doc.interval_names:
                self.doc.interval_names.remove(target_name)
            if target_name in self.doc.event_names:
                self.doc.event_names.remove(target_name)
            self.doc.vars.pop(target_name, None)
            return None
        raise RuntimeError("unsupported DeleteVar signature")


class FakeAdapterForExportFlow:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.paths = resolve_project_paths(config)
        self.fullrate_attempted = False

    def connect(self): pass
    def smoke_test(self): pass
    def introspect(self): pass
    def open_file(self, pl2_path): pass
    def ensure_events(self, file_id, light_on_times, light_off_times): pass
    def validate_required_events(self): pass
    def get_reference_event_name(self): return "Light_On"
    def configure_psth_template(self, *args): pass
    def run_template(self, template_name):
        if template_name == "RateHist_FullSession":
            self.fullrate_attempted = True
            raise NeedsManualActionError("Template RateHist_FullSession was not found in NeuroExplorer.")
    def export_psth(self, file_id, unit_names, output_csv):
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        output_csv.write_text("file_id,unit_id,bin_start_s,bin_end_s,bin_center_s,firing_rate_hz,spike_count,n_events\n"
                              f"{file_id},SPK_SPKC04a,-0.5,0.5,0,5,,\n", encoding="utf-8")
    def configure_fullrate_template(self, *args): pass
    def export_fullrate(self, file_id, unit_names, output_csv): raise AssertionError("Should not export fullrate when template missing")
    def activate_manual_backend(self, reason): raise AssertionError("Should not activate manual backend for optional fullrate skip")
    def close_file(self): pass
    def quit(self): pass


class NeuroExplorerNexBackendTests(unittest.TestCase):
    def _init_project_files(self, tmp_root: Path) -> tuple[dict, PipelineLogger]:
        for relative_dir in [
            "00_raw_pl2",
            "01_sorting_info",
            "02_stim_events",
            "03_nex_exports/psth",
            "03_nex_exports/fullrate",
            "03_nex_exports/raster",
            "99_logs",
        ]:
            (tmp_root / relative_dir).mkdir(parents=True, exist_ok=True)
        (tmp_root / "00_raw_pl2" / "demo.pl2").write_text("", encoding="utf-8")
        (tmp_root / "02_stim_events" / "stim_schedule_master.csv").write_text(
            "file_id,pl2_file,light_on_s,duration_s,light_off_s\n"
            "demo,demo.pl2,120,15,135\n",
            encoding="utf-8",
        )
        (tmp_root / "01_sorting_info" / "unit_quality_table.csv").write_text(
            "file_id,unit_id,original_name,include\n"
            "demo,unit01,SPK_SPKC04a,yes\n",
            encoding="utf-8",
        )
        config = sample_config(tmp_root)
        logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
        return config, logger

    def test_convert_wide_psth_to_long(self):
        import pandas as pd

        df = pd.DataFrame({"bin_center_s": [-0.5, 0.5, 1.5], "SPK_SPKC04a": [1, 2, 3]})
        long_df = convert_wide_psth_to_long(df, file_id="demo")
        self.assertEqual(
            list(long_df.columns),
            ["file_id", "unit_id", "bin_start_s", "bin_end_s", "bin_center_s", "firing_rate_hz", "spike_count", "n_events"],
        )
        self.assertEqual(long_df["file_id"].nunique(), 1)
        self.assertEqual(long_df["unit_id"].iloc[0], "SPK_SPKC04a")

    def test_nex_import_failure_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = sample_config(Path(tmpdir))
            logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            with mock.patch("importlib.import_module", side_effect=ImportError("missing nex")):
                with self.assertRaises(NexPackageUnavailableError) as ctx:
                    backend.connect()
            self.assertIn("official `nex` package is not installed", str(ctx.exception))

    def test_clean_config_without_legacy_events_constructs_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = sample_config(Path(tmpdir))
            config["neuroexplorer"].pop("events", None)
            config["neuroexplorer"].pop("interval", None)
            logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            self.assertEqual(backend.current_event_on_name, "Light_On")
            self.assertEqual(backend.current_event_off_name, "Light_Off")
            self.assertEqual(backend.current_interval_name, "Light_Interval")

    def test_auto_mode_uses_fullrate_primary_without_legacy_psth_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = sample_config(Path(tmpdir))
            config["analysis"]["mode"] = "auto"
            config["neuroexplorer"].pop("psth", None)
            config["neuroexplorer"].pop("events", None)
            config["neuroexplorer"]["export"].pop("expected_psth_pattern", None)
            paths = resolve_project_paths(config)
            self.assertTrue(export_module._uses_fullrate_aligned_primary(config))
            self.assertFalse(config["neuroexplorer"].get("export_psth", True) and not export_module._uses_fullrate_aligned_primary(config))
            psth_path = export_module._expected_export_path(config, paths, "demo", "psth")
            self.assertEqual(psth_path.name, "demo_LightOn_PSTH_bin1s.csv")

    def test_introspection_dump_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_path = Path(tmpdir) / "neuroexplorer_nex_api_dump.txt"
            available = dump_nex_api(dump_path, FakeNexModule(), {"doc": "ok"})
            self.assertTrue(dump_path.exists())
            self.assertTrue(available["GetActiveDocument"])

    def test_manual_csv_fallback_activation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = sample_config(Path(tmpdir))
            logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
            adapter = NeuroExplorerAdapter(config=config, logger=logger)
            with mock.patch("importlib.import_module", side_effect=ImportError("missing nex")):
                adapter.connect()
            self.assertEqual(adapter.backend_name, "manual_csv")

    def test_live_nex_smoke_skips_without_nex(self):
        if importlib.util.find_spec("nex") is None:
            self.skipTest("nex is not installed in this environment")
        self.assertTrue(True)

    def test_read_neuroexplorer_interval_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "interval.csv"
            path.write_text("Light_Interval\n120,135\n", encoding="utf-8")
            name, intervals = read_neuroexplorer_interval_csv(path, expected_interval_name="Light_Interval")
            self.assertEqual(name, "Light_Interval")
            self.assertEqual(intervals, [(120.0, 135.0)])

    def test_read_neuroexplorer_interval_csv_rejects_header_and_missing_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "interval.csv"
            path.write_text("start,end\n120,135\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_neuroexplorer_interval_csv(path)
            path.write_text("120,135\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_neuroexplorer_interval_csv(path)

    def test_create_interval_var_via_doc_setitem_then_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_setitem=True)
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            interval_var, create_strategy = backend._create_interval_var("Light_Interval_probe")
            write_strategy = backend._write_interval_to_var(interval_var, 120.0, 135.0, "neuroexplorer_var_object_probe_path")
            self.assertIn("doc['Light_Interval_probe']", create_strategy)
            self.assertTrue(write_strategy.startswith("A:"))
            self.assertEqual(len(backend.nex.add_interval_calls), 1)
            self.assertIn("Light_Interval_probe", doc.interval_names)

    def test_create_event_var_via_doc_setitem_then_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_setitem=True)
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            event_var, create_strategy = backend._create_event_var("Light_On_probe")
            write_strategy = backend._write_timestamp_to_var(event_var, 120.0, "neuroexplorer_var_object_probe_path")
            self.assertIn("doc['Light_On_probe']", create_strategy)
            self.assertTrue(write_strategy.startswith("A:"))
            self.assertEqual(len(backend.nex.add_timestamp_calls), 1)
            self.assertIn("Light_On_probe", doc.event_names)

    def test_create_interval_var_via_collection_add(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_collection_add=True)
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            interval_var, create_strategy = backend._create_interval_var("Light_Interval_probe")
            self.assertIn("IntervalVars.Add", create_strategy)
            self.assertTrue(backend._is_valid_nex_var(interval_var, "Light_Interval_probe"))

    def test_create_var_raises_when_no_creation_method_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation()
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            with self.assertRaises(NeedsManualActionError) as ctx:
                backend._create_interval_var("Light_Interval_probe")
            self.assertIn("AddInterval requires NexVar but no valid creation method found", str(ctx.exception))

    def test_get_var_by_name_returns_nexvar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation()
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            var = backend._get_var_by_name("AllFile")
            self.assertIsNotNone(var)
            self.assertTrue(backend._is_valid_nex_var(var, "AllFile"))

    def test_refuse_write_to_allfile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_setitem=True)
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            with self.assertRaises(NeedsManualActionError):
                backend._create_interval_var("AllFile")

    def test_replace_existing_interval_uses_deletevar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_setitem=True)
            doc._create_var("Light_Interval", "interval")
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            resolved = backend._ensure_target_name("Light_Interval", "interval")
            self.assertEqual(resolved, "Light_Interval")
            self.assertTrue(backend.nex.delete_calls)

    def test_replace_existing_interval_rename_when_delete_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_setitem=True)
            doc._create_var("Light_Interval", "interval")
            backend.doc = doc
            backend.nex = CreationNexModule(doc, delete_raises=True)
            resolved = backend._ensure_target_name("Light_Interval", "interval")
            self.assertEqual(resolved, "Light_Interval_auto")

    def test_cleanup_probe_vars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            doc = FakeDocForCreation(support_setitem=True)
            doc._create_var("Light_Interval_probe", "interval")
            doc._create_var("Light_On_probe", "event")
            backend.doc = doc
            backend.nex = CreationNexModule(doc)
            backend.cleanup_probe_vars(["Light_Interval_probe", "Light_On_probe"])
            self.assertNotIn("Light_Interval_probe", doc.interval_names)
            self.assertNotIn("Light_On_probe", doc.event_names)

    def test_export_psth_prefers_save_num_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            backend.nex = ExportingNexModule()
            backend.doc = object()
            output_csv = resolve_project_paths(config)["nex_psth_dir"] / "demo_LightOn_PSTH_bin1s.csv"
            backend.export_psth("demo", ["SPK_SPKC04a"], output_csv)
            raw_path = output_csv.with_name(output_csv.stem + "_raw.txt")
            self.assertTrue(raw_path.exists())
            self.assertTrue(output_csv.exists())
            self.assertEqual(backend.nex.saved_num_paths, [str(raw_path)])
            self.assertEqual(backend.nex.saved_results_paths, [])

    def test_export_psth_falls_back_to_save_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            backend.nex = ExportingNexModule(fail_save_num=True)
            backend.doc = object()
            output_csv = resolve_project_paths(config)["nex_psth_dir"] / "demo_LightOn_PSTH_bin1s.csv"
            backend.export_psth("demo", ["SPK_SPKC04a"], output_csv)
            raw_path = output_csv.with_name(output_csv.stem + "_raw.txt")
            self.assertTrue(raw_path.exists())
            self.assertTrue(output_csv.exists())
            self.assertEqual(backend.nex.saved_num_paths, [str(raw_path)])
            self.assertEqual(backend.nex.saved_results_paths, [str(raw_path)])

    def test_export_psth_raises_when_all_export_methods_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            backend = NexPackageBackend(config, resolve_project_paths(config), logger)
            backend.nex = ExportingNexModule(fail_save_num=True, fail_save_results=True)
            backend.doc = object()
            output_csv = resolve_project_paths(config)["nex_psth_dir"] / "demo_LightOn_PSTH_bin1s.csv"
            with self.assertRaises(NeedsManualActionError):
                backend.export_psth("demo", ["SPK_SPKC04a"], output_csv)

    def test_export_from_neuroexplorer_skips_optional_fullrate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            config["neuroexplorer"]["export_fullrate"] = True
            config["neuroexplorer"]["fullrate"]["enabled"] = True
            with mock.patch("scripts.export_from_neuroexplorer.NeuroExplorerAdapter", side_effect=lambda config, logger: FakeAdapterForExportFlow(config, logger)):
                export_from_neuroexplorer(config, logger)
            messages = [record.message for record in logger.records]
            self.assertTrue(any("FullRate export skipped" in message for message in messages))
            self.assertTrue(any("Finished NeuroExplorer export flow" in message for message in messages))

    def test_psth_uses_included_cohort_while_fullrate_retains_all_units(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config, logger = self._init_project_files(Path(tmpdir))
            config["analysis"]["mode"] = "neuroexplorer_psth"
            config["neuroexplorer"]["export_psth"] = True
            config["neuroexplorer"]["export_fullrate"] = True
            config["neuroexplorer"]["fullrate"]["enabled"] = True
            paths = resolve_project_paths(config)
            paths["unit_quality_path"].write_text(
                "file_id,unit_id,original_name,include,exclusion_reason\n"
                "demo,unit01,SPK_SPKC04a,yes,\n"
                "demo,unit02,SPK_SPKC05a,no,noise\n",
                encoding="utf-8",
            )

            class CapturingAdapter(FakeAdapterForExportFlow):
                psth_units = []
                fullrate_units = []

                def run_template(self, template_name):
                    return None

                def export_psth(self, file_id, unit_names, output_csv):
                    type(self).psth_units = list(unit_names)

                def export_fullrate(self, file_id, unit_names, output_csv):
                    type(self).fullrate_units = list(unit_names)

            with mock.patch(
                "scripts.export_from_neuroexplorer.NeuroExplorerAdapter",
                side_effect=lambda config, logger: CapturingAdapter(config, logger),
            ):
                export_from_neuroexplorer(config, logger)
            self.assertEqual(CapturingAdapter.psth_units, ["SPK_SPKC04a"])
            self.assertEqual(CapturingAdapter.fullrate_units, ["SPK_SPKC04a", "SPK_SPKC05a"])


if __name__ == "__main__":
    unittest.main()
