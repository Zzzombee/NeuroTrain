from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils.event_utils import resolve_event_file_path, write_event_times
from utils.table_utils import convert_rate_export_to_long, read_table, write_table


class ManualCsvBackend:
    def __init__(self, config: dict, paths: dict, logger):
        self.config = config
        self.paths = paths
        self.logger = logger
        self.current_file: Path | None = None

    def connect(self) -> None:
        self.logger.log("neuroexplorer_manual_backend", "*", "", "", "success", "manual_csv backend active.")

    def smoke_test(self) -> None:
        self.logger.log("neuroexplorer_manual_backend", "*", "", "", "skipped", "Smoke test skipped for manual_csv backend.")

    def introspect(self) -> None:
        self.logger.log("neuroexplorer_manual_backend", "*", "", "", "skipped", "API introspection skipped for manual_csv backend.")

    def open_file(self, pl2_path: Path) -> None:
        self.current_file = pl2_path
        self.logger.log(
            "neuroexplorer_manual_backend",
            pl2_path.stem,
            str(pl2_path),
            "",
            "warning",
            "manual_csv backend does not open PL2 files. User must open the target file in NeuroExplorer manually if analysis is needed.",
        )

    def get_active_document(self):
        return None

    def list_variables(self):
        return []

    def list_spike_variables(self):
        return []

    def list_neuron_variables(self):
        return []

    def list_event_variables(self):
        return []

    def ensure_events(self, file_id, light_on_times, light_off_times) -> None:
        stimulus_input_mode = self.config["neuroexplorer"]["events"].get("stimulus_input_mode", "event")
        on_path = resolve_event_file_path(
            self.paths["events_export_dir"],
            str(file_id),
            self.config["neuroexplorer"]["events"]["event_on_name"],
            stimulus_input_mode,
        )
        off_path = resolve_event_file_path(
            self.paths["events_export_dir"],
            str(file_id),
            self.config["neuroexplorer"]["events"]["event_off_name"],
            stimulus_input_mode,
        )
        write_event_times(on_path, light_on_times)
        write_event_times(off_path, light_off_times)
        self.logger.log(
            "neuroexplorer_manual_backend",
            str(file_id),
            "",
            str(on_path),
            "warning",
            "Generated headerless Light_On/Light_Off timestamp helper files for manual import. Do not merge Light_On and Light_Off into one event.",
        )

    def validate_required_events(self) -> None:
        self.logger.log(
            "neuroexplorer_manual_backend",
            "*",
            "",
            "",
            "warning",
            "Required event validation is manual in this backend. PSTH reference event must remain Light_On.",
        )

    def get_reference_event_name(self) -> str:
        return self.config["neuroexplorer"]["events"]["reference_event"]

    def configure_psth_template(self, template_name, reference_event, x_min_s, x_max_s, bin_width_s, histogram_unit) -> None:
        self.logger.log(
            "neuroexplorer_manual_backend",
            "*",
            "",
            "",
            "warning",
            (
                "Manual NeuroExplorer steps required: Perievent Histogram with "
                f"Reference={reference_event}, X Minimum={x_min_s}, X Maximum={x_max_s}, "
                f"Bin={bin_width_s}, Histogram Units={histogram_unit}. Template={template_name}."
            ),
        )

    def configure_fullrate_template(self, template_name, bin_width_s, histogram_unit) -> None:
        self.logger.log(
            "neuroexplorer_manual_backend",
            "*",
            "",
            "",
            "warning",
            f"Manual full-session rate template required: Template={template_name}, Bin={bin_width_s}, Histogram Units={histogram_unit}.",
        )

    def run_template(self, template_name) -> None:
        self.logger.log(
            "neuroexplorer_manual_backend",
            "*",
            "",
            "",
            "warning",
            f"Run template manually in NeuroExplorer GUI: {template_name}",
        )

    def _normalize_if_exists(self, file_id: str, kind: str, output_csv: Path) -> None:
        if output_csv.exists():
            raw_df = read_table(output_csv)
            long_df = convert_rate_export_to_long(raw_df, file_id=file_id, kind=kind)
            write_table(long_df, output_csv)
            self.logger.log(
                "neuroexplorer_manual_backend",
                file_id,
                str(output_csv),
                str(output_csv),
                "success",
                f"Normalized existing manual {kind} CSV into standard long-table format.",
            )
            return

        self.logger.log(
            "neuroexplorer_manual_backend",
            file_id,
            "",
            str(output_csv),
            "warning",
            (
                f"Expected manual {kind} CSV not found. "
                f"Please export it to {output_csv}. The pipeline can continue later from CSV."
            ),
        )
        if self.config["neuroexplorer"].get("stop_if_export_failed", False):
            raise FileNotFoundError(f"Required manual {kind} CSV not found: {output_csv}")

    def export_psth(self, file_id, unit_names, output_csv) -> None:
        self._normalize_if_exists(file_id, "psth", output_csv)

    def export_fullrate(self, file_id, unit_names, output_csv) -> None:
        self._normalize_if_exists(file_id, "fullrate", output_csv)

    def close_file(self) -> None:
        self.current_file = None

    def quit(self) -> None:
        self.current_file = None
