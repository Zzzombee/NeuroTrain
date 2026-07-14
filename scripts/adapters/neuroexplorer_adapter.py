from __future__ import annotations

from typing import Any

from utils.path_utils import resolve_project_paths


class NeuroExplorerError(RuntimeError):
    """Base error for NeuroExplorer automation."""


class NeedsManualActionError(NeuroExplorerError):
    """Raised when the pipeline can continue only after a manual NeuroExplorer step."""


class NexPackageUnavailableError(NeuroExplorerError):
    """Raised when the official `nex` Python package is unavailable."""


class NeuroExplorerAdapter:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        self.paths = resolve_project_paths(config)
        self.backend_name = config.get("neuroexplorer", {}).get("backend", "auto")
        self.backend = self._make_backend(self.backend_name)

    def _make_backend(self, backend_name: str):
        normalized = backend_name or "auto"
        neuro_cfg = self.config.get("neuroexplorer", {})

        if normalized in {"auto", "nex_package"}:
            from scripts.adapters.neuroexplorer_nex_backend import NexPackageBackend

            return NexPackageBackend(self.config, self.paths, self.logger)
        if normalized == "com_nexscript":
            from scripts.adapters.neuroexplorer_com_backend import ComNexScriptBackend

            return ComNexScriptBackend(self.config, self.paths, self.logger)
        if normalized == "manual_csv":
            from scripts.adapters.neuroexplorer_manual_backend import ManualCsvBackend

            return ManualCsvBackend(self.config, self.paths, self.logger)
        raise ValueError(f"Unsupported NeuroExplorer backend: {backend_name}")

    def activate_manual_backend(self, reason: str):
        from scripts.adapters.neuroexplorer_manual_backend import ManualCsvBackend

        self.logger.log(
            "neuroexplorer_adapter",
            "*",
            "",
            "",
            "warning",
            f"Falling back to manual_csv backend. Reason: {reason}",
        )
        self.backend_name = "manual_csv"
        self.backend = ManualCsvBackend(self.config, self.paths, self.logger)
        return self.backend

    def connect(self) -> None:
        try:
            self.backend.connect()
        except NexPackageUnavailableError as exc:
            nex_cfg = self.config.get("neuroexplorer", {}).get("nex_package", {})
            if nex_cfg.get("fail_to_manual_csv", True):
                self.activate_manual_backend(str(exc))
                self.backend.connect()
            else:
                raise

    def smoke_test(self) -> None:
        self.backend.smoke_test()

    def introspect(self) -> None:
        self.backend.introspect()

    def open_file(self, pl2_path) -> None:
        self.backend.open_file(pl2_path)

    def get_active_document(self) -> Any:
        return self.backend.get_active_document()

    def list_variables(self):
        return self.backend.list_variables()

    def list_spike_variables(self):
        return self.backend.list_spike_variables()

    def list_neuron_variables(self):
        return self.backend.list_neuron_variables()

    def list_event_variables(self):
        return self.backend.list_event_variables()

    def ensure_events(self, file_id, light_on_times, light_off_times) -> None:
        self.backend.ensure_events(file_id, light_on_times, light_off_times)

    def validate_required_events(self) -> None:
        self.backend.validate_required_events()

    def configure_psth_template(self, template_name, reference_event, x_min_s, x_max_s, bin_width_s, histogram_unit) -> None:
        self.backend.configure_psth_template(template_name, reference_event, x_min_s, x_max_s, bin_width_s, histogram_unit)

    def get_reference_event_name(self) -> str:
        if hasattr(self.backend, "get_reference_event_name"):
            return self.backend.get_reference_event_name()
        return self.config["neuroexplorer"]["events"]["reference_event"]

    def configure_fullrate_template(self, template_name, bin_width_s, histogram_unit) -> None:
        self.backend.configure_fullrate_template(template_name, bin_width_s, histogram_unit)

    def run_template(self, template_name) -> None:
        self.backend.run_template(template_name)

    def export_psth(self, file_id, unit_names, output_csv) -> None:
        self.backend.export_psth(file_id, unit_names, output_csv)

    def export_fullrate(self, file_id, unit_names, output_csv) -> None:
        self.backend.export_fullrate(file_id, unit_names, output_csv)

    def close_file(self) -> None:
        self.backend.close_file()

    def quit(self) -> None:
        self.backend.quit()
