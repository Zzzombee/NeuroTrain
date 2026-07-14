from __future__ import annotations

from pathlib import Path

from scripts.adapters.neuroexplorer_adapter import NeedsManualActionError

try:
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover
    win32com = None


class ComNexScriptBackend:
    """
    Secondary backend retained for future COM/NexScript integration.
    This is not the primary automation path in the current skill revision.
    """

    def __init__(self, config: dict, paths: dict, logger):
        self.config = config
        self.paths = paths
        self.logger = logger
        self.app = None
        self.document = None

    def connect(self) -> None:
        if win32com is None:
            raise RuntimeError("pywin32 is not installed, so COM backend is unavailable.")
        try:
            self.app = win32com.client.Dispatch("NeuroExplorer.Application")
            self.logger.log("neuroexplorer_com_backend", "*", "", "", "success", "Connected to NeuroExplorer COM backend.")
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Failed to create NeuroExplorer COM object. Confirm ProgID and installed version.") from exc

    def smoke_test(self) -> None:
        self.logger.log("neuroexplorer_com_backend", "*", "", "", "warning", "COM smoke test is limited in this revision.")

    def introspect(self) -> None:
        self.logger.log("neuroexplorer_com_backend", "*", "", "", "warning", "COM backend introspection is not implemented yet.")

    def open_file(self, pl2_path: Path) -> None:
        raise NeedsManualActionError(
            f"COM backend open_file is not implemented for {pl2_path}. Use backend=nex_package or manual_csv."
        )

    def get_active_document(self):
        return self.document

    def list_variables(self):
        return []

    def list_spike_variables(self):
        return []

    def list_neuron_variables(self):
        return []

    def list_event_variables(self):
        return []

    def ensure_events(self, file_id, light_on_times, light_off_times) -> None:
        raise NeedsManualActionError("COM backend event creation is not implemented. Import Light_On/Light_Off manually.")

    def validate_required_events(self) -> None:
        self.logger.log("neuroexplorer_com_backend", "*", "", "", "warning", "Required-event validation unavailable in COM backend.")

    def get_reference_event_name(self) -> str:
        return self.config["neuroexplorer"]["events"]["reference_event"]

    def configure_psth_template(self, template_name, reference_event, x_min_s, x_max_s, bin_width_s, histogram_unit) -> None:
        raise NeedsManualActionError(f"COM backend template configuration is not implemented for {template_name}.")

    def configure_fullrate_template(self, template_name, bin_width_s, histogram_unit) -> None:
        raise NeedsManualActionError(f"COM backend template configuration is not implemented for {template_name}.")

    def run_template(self, template_name) -> None:
        raise NeedsManualActionError(f"COM backend template execution is not implemented for {template_name}.")

    def export_psth(self, file_id, unit_names, output_csv) -> None:
        raise NeedsManualActionError(f"COM backend PSTH export is not implemented. Export manually to {output_csv}.")

    def export_fullrate(self, file_id, unit_names, output_csv) -> None:
        raise NeedsManualActionError(f"COM backend full-rate export is not implemented. Export manually to {output_csv}.")

    def close_file(self) -> None:
        self.document = None

    def quit(self) -> None:
        self.app = None
