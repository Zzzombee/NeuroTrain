from __future__ import annotations

import inspect
import importlib
from pathlib import Path
from typing import Any

from scripts.adapters.neuroexplorer_adapter import NeedsManualActionError, NexPackageUnavailableError
from scripts.utils.neuroexplorer_introspection import dump_nex_api, safe_signature_text
from utils.event_utils import (
    derive_light_on_off_from_intervals,
    read_light_intervals,
    read_neuroexplorer_interval_csv,
    resolve_event_file_path,
    resolve_interval_file_path,
    write_event_times,
)
from utils.table_utils import convert_rate_export_to_long, read_delimited_text_table, read_table, write_table


class NexPackageBackend:
    def __init__(self, config: dict, paths: dict, logger):
        self.config = config
        self.paths = paths
        self.logger = logger
        self.nex = None
        self.doc = None
        self.available_api: dict[str, bool] = {}
        self.current_file: Path | None = None
        self.current_interval_name = self.config["neuroexplorer"]["events"].get("interval_name", "Light_Interval")
        self.current_event_on_name = self.config["neuroexplorer"]["events"]["event_on_name"]
        self.current_event_off_name = self.config["neuroexplorer"]["events"]["event_off_name"]

    def connect(self) -> None:
        try:
            self.nex = importlib.import_module("nex")
        except ImportError as exc:
            raise NexPackageUnavailableError(
                "The official `nex` package is not installed. Run `python.exe -m pip install -U nex`, "
                "then enable `Script | Enable Running Python Scripts in External Editor` in NeuroExplorer."
            ) from exc
        self.logger.log("neuroexplorer_nex_backend", "*", "", "", "success", "Imported official nex Python package.")

    def _get_active_document_safe(self):
        if self.nex is None:
            return None
        if not hasattr(self.nex, "GetActiveDocument"):
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                "",
                "warning",
                "nex.GetActiveDocument is not available. Smoke test and active-document workflows are limited.",
            )
            return None
        try:
            self.doc = self.nex.GetActiveDocument()
            return self.doc
        except Exception as exc:
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                "",
                "warning",
                "Failed to query the active NeuroExplorer document.",
                exception=exc,
            )
            return None

    def smoke_test(self) -> None:
        doc = self._get_active_document_safe()
        if doc is None:
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                "",
                "warning",
                "Level 1 smoke test: no active NeuroExplorer document detected.",
            )
            return
        self.logger.log(
            "neuroexplorer_nex_backend",
            "*",
            "",
            "",
            "success",
            f"Level 1 smoke test: active document type={type(doc)} attrs={', '.join(dir(doc)[:25])}",
        )

    def introspect(self) -> None:
        if self.nex is None:
            return
        doc = self._get_active_document_safe()
        self.available_api = dump_nex_api(self.paths["neuroexplorer_api_dump_path"], self.nex, doc)
        for name in ["GetActiveDocument", "ApplyTemplate", "ModifyTemplate", "SaveDocument"]:
            if not self.available_api.get(name, False):
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    str(self.paths["neuroexplorer_api_dump_path"]),
                    "warning",
                    f"nex API probe: required function candidate missing: {name}",
                )
        if self.available_api.get("SaveNumResults", False):
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                str(self.paths["neuroexplorer_api_dump_path"]),
                "success",
                "PSTH numerical export method: SaveNumResults",
            )

    def _append_probe(self, line: str, probe_key: str = "neuroexplorer_interval_event_probe_path") -> None:
        probe_path = self.paths[probe_key]
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        with probe_path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")

    def _reset_probe(self, probe_key: str) -> None:
        probe_path = self.paths[probe_key]
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text("", encoding="utf-8")

    def _describe_callable(self, name: str, callable_obj: Any) -> str:
        exists = callable(callable_obj)
        try:
            signature = str(inspect.signature(callable_obj))
        except Exception:
            signature = "(signature unavailable)"
        doc = inspect.getdoc(callable_obj) or ""
        doc = doc.splitlines()[0] if doc else ""
        return f"{name}: exists={exists} signature={signature} doc={doc}"

    def _log_probe_header(
        self,
        file_id: str,
        interval_csv_path: Path | None = None,
        *,
        probe_key: str = "neuroexplorer_interval_event_probe_path",
    ) -> None:
        self._append_probe("=" * 80, probe_key=probe_key)
        self._append_probe(f"file_id: {file_id}", probe_key=probe_key)
        self._append_probe(f"pl2_path: {self.current_file}", probe_key=probe_key)
        self._append_probe(f"interval_csv_path: {interval_csv_path}", probe_key=probe_key)

    def _describe_object(self, name: str, obj: Any) -> list[str]:
        lines = [f"{name}: type={type(obj)} repr={obj!r}"]
        try:
            attrs = sorted(dir(obj))
        except Exception:
            attrs = []
        lines.append(f"{name}.attrs({len(attrs)}): {', '.join(attrs[:120])}")
        return lines

    def _get_doc_collection_names(self, attr_name: str) -> list[str]:
        doc = self.doc or self._get_active_document_safe()
        if doc is None or not hasattr(doc, attr_name):
            return []
        try:
            values = getattr(doc, attr_name)
            if callable(values):
                values = values()
            return [str(value) for value in values if str(value).strip()]
        except Exception:
            return []

    def _get_interval_names(self) -> list[str]:
        return self._get_doc_collection_names("IntervalNames")

    def _get_event_names(self) -> list[str]:
        return self._get_doc_collection_names("EventNames")

    def _get_var_name(self, var: Any) -> str | None:
        if var is None:
            return None
        if self.nex is not None and hasattr(self.nex, "GetName"):
            try:
                return str(self.nex.GetName(var))
            except Exception:
                pass
        for attr_name in ["name", "Name"]:
            if hasattr(var, attr_name):
                try:
                    value = getattr(var, attr_name)
                    if callable(value):
                        value = value()
                    if value:
                        return str(value)
                except Exception:
                    continue
        return None

    def _get_var_type(self, var: Any) -> str | None:
        if var is None:
            return None
        for attr_name in ["varType", "VarType", "type", "Type"]:
            if hasattr(var, attr_name):
                try:
                    value = getattr(var, attr_name)
                    if callable(value):
                        value = value()
                    if value:
                        return str(value)
                except Exception:
                    continue
        return None

    def _is_valid_nex_var(self, var: Any, expected_name: str | None = None) -> bool:
        if var is None:
            return False
        if hasattr(var, "varId"):
            actual_name = self._get_var_name(var)
            if expected_name is None or actual_name == expected_name:
                return True
        actual_name = self._get_var_name(var)
        return bool(actual_name and (expected_name is None or actual_name == expected_name))

    def _probe_collection(self, owner_name: str, collection_name: str, probe_key: str) -> None:
        doc = self.doc or self._get_active_document_safe()
        if doc is None or not hasattr(doc, collection_name):
            self._append_probe(f"{collection_name}: missing", probe_key=probe_key)
            return
        collection = getattr(doc, collection_name)
        self._append_probe(f"{collection_name}: exists=True type={type(collection)} repr={collection!r}", probe_key=probe_key)
        try:
            self._append_probe(f"{collection_name}.dir={', '.join(sorted(dir(collection))[:120])}", probe_key=probe_key)
        except Exception:
            pass
        trials = [
            ("callable", lambda c: c()),
            ("list()", lambda c: list(c)),
            ("len()", lambda c: len(c)),
            ("keys()", lambda c: list(c.keys())),
            ("items()", lambda c: list(c.items())),
            ("[0]", lambda c: c[0]),
            ("['AllFile']", lambda c: c["AllFile"]),
            ("get('AllFile')", lambda c: c.get("AllFile")),
        ]
        for label, fn in trials:
            try:
                result = fn(collection)
                self._append_probe(f"{collection_name}.{label}: success type={type(result)} repr={result!r}", probe_key=probe_key)
                if label == "callable" and isinstance(result, list) and result:
                    first = result[0]
                    self._append_probe(
                        f"{collection_name}.callable[0]: type={type(first)} repr={first!r} "
                        f"has_varId={hasattr(first, 'varId')} name={self._get_var_name(first)}",
                        probe_key=probe_key,
                    )
            except Exception as exc:
                self._append_probe(f"{collection_name}.{label}: failed {type(exc).__name__}: {exc}", probe_key=probe_key)

    def _probe_attr_value(self, attr_name: str, probe_key: str) -> None:
        doc = self.doc or self._get_active_document_safe()
        if doc is None or not hasattr(doc, attr_name):
            self._append_probe(f"doc.{attr_name}: missing", probe_key=probe_key)
            return
        value = getattr(doc, attr_name)
        self._append_probe(f"doc.{attr_name}: type={type(value)} repr={value!r}", probe_key=probe_key)
        try:
            attrs = sorted(dir(value))
            self._append_probe(f"doc.{attr_name}.dir={', '.join(attrs[:120])}", probe_key=probe_key)
        except Exception:
            pass
        if callable(value):
            try:
                result = value()
                self._append_probe(f"doc.{attr_name}(): type={type(result)} repr={result!r}", probe_key=probe_key)
                if isinstance(result, list) and result:
                    first = result[0]
                    self._append_probe(
                        f"doc.{attr_name}()[0]: type={type(first)} repr={first!r} "
                        f"has_varId={hasattr(first, 'varId')} name={self._get_var_name(first)}",
                        probe_key=probe_key,
                    )
            except Exception as exc:
                self._append_probe(
                    f"doc.{attr_name}(): failed {type(exc).__name__}: {exc}",
                    probe_key=probe_key,
                )

    def probe_var_collections(self, probe_key: str = "neuroexplorer_var_object_probe_path") -> None:
        doc = self.doc or self._get_active_document_safe()
        self._reset_probe(probe_key)
        self._log_probe_header("probe", probe_key=probe_key)
        for line in self._describe_object("doc", doc):
            self._append_probe(line, probe_key=probe_key)
        if self.nex is not None:
            for name in [
                "AddTimestamp",
                "AddInterval",
                "DeleteVar",
                "CreateWaveformVariable",
                "GetIntervalVarFromMatlab",
                "GetContVarFromMatlab",
                "GetContVarWithTimestampsFromMatlab",
                "GetName",
                "GetField",
                "GetNumFields",
                "GetAllAnalysisParameters",
            ]:
                if hasattr(self.nex, name):
                    self._append_probe(self._describe_callable(name, getattr(self.nex, name)), probe_key=probe_key)
                else:
                    self._append_probe(f"{name}: missing", probe_key=probe_key)
        for attr_name in [
            "EventNames",
            "EventVars",
            "IntervalNames",
            "IntervalVars",
            "NeuronNames",
            "NeuronVars",
            "vars",
            "_VarsList",
            "_VarNames",
            "__getitem__",
            "__setitem__",
        ]:
            exists = hasattr(doc, attr_name) if doc is not None else False
            self._append_probe(f"doc.{attr_name}: exists={exists}", probe_key=probe_key)
        for attr_name in ["vars", "_VarsList", "_VarNames"]:
            self._probe_attr_value(attr_name, probe_key)
        self._probe_collection("doc", "IntervalVars", probe_key)
        self._probe_collection("doc", "EventVars", probe_key)
        for getter_name in ["__getitem__", "__setitem__"]:
            if doc is not None and hasattr(doc, getter_name):
                getter = getattr(doc, getter_name)
                self._append_probe(
                    self._describe_callable(f"doc.{getter_name}", getter),
                    probe_key=probe_key,
                )

    def _get_var_by_name(self, name: str) -> Any | None:
        doc = self.doc or self._get_active_document_safe()
        if doc is None:
            return None
        candidates = [doc]
        for collection_attr in ["IntervalVars", "EventVars", "NeuronVars", "MarkerVars", "ContinuousVars"]:
            if hasattr(doc, collection_attr):
                candidates.append(getattr(doc, collection_attr))
        for container in candidates:
            if container is None:
                continue
            accessors = [
                lambda c, n=name: c[n],
                lambda c, n=name: c.__getitem__(n),
                lambda c, n=name: c.Item(n),
                lambda c, n=name: c.GetVar(n),
                lambda c, n=name: c.get(n),
            ]
            for accessor in accessors:
                try:
                    value = accessor(container)
                except Exception:
                    continue
                if self._is_valid_nex_var(value):
                    return value
        return None

    def _delete_var_by_name(self, name: str) -> bool:
        if name == "AllFile":
            self._append_probe("Refusing to delete built-in AllFile interval.", probe_key="neuroexplorer_var_object_probe_path")
            return False
        var = self._get_var_by_name(name)
        attempts = []
        if var is not None and self.nex is not None and hasattr(self.nex, "DeleteVar"):
            var_id = getattr(var, "varId", None)
            var_type = self._get_var_type(var)
            if var_id is not None and var_type is not None:
                attempts.append(
                    (
                        "nex.DeleteVar(doc, varId, varType)",
                        lambda v_id=var_id, v_type=var_type: self.nex.DeleteVar(self.doc, v_id, v_type),
                    )
                )
            attempts.extend(
                [
                    ("nex.DeleteVar(doc, name)", lambda: self.nex.DeleteVar(self.doc, name)),
                    ("nex.DeleteVar(name)", lambda: self.nex.DeleteVar(name)),
                ]
            )
        doc = self.doc or self._get_active_document_safe()
        if doc is not None and hasattr(doc, "DeleteVar"):
            attempts.append(("doc.DeleteVar(name)", lambda: doc.DeleteVar(name)))
        for label, func in attempts:
            try:
                func()
                self._append_probe(f"Delete strategy success: {label}", probe_key="neuroexplorer_var_object_probe_path")
                return True
            except Exception as exc:
                self._append_probe(
                    f"Delete strategy failed: {label} -> {type(exc).__name__}: {exc}",
                    probe_key="neuroexplorer_var_object_probe_path",
                )
        return False

    def _ensure_target_name(self, base_name: str, kind: str) -> str:
        cfg = self.config["neuroexplorer"].get("event_creation", {})
        if_exists = cfg.get("if_exists", "replace")
        existing = set(self._get_interval_names() if kind == "interval" else self._get_event_names())
        if base_name not in existing:
            return base_name
        if if_exists == "skip":
            self._append_probe(f"{kind} variable already exists and if_exists=skip: {base_name}", probe_key="neuroexplorer_var_object_probe_path")
            return base_name
        if if_exists == "error":
            raise NeedsManualActionError(f"{kind} variable already exists: {base_name}")
        if if_exists == "replace":
            if self._delete_var_by_name(base_name):
                return base_name
        suffix = "_auto"
        counter = 0
        while True:
            candidate = f"{base_name}{suffix if counter == 0 else suffix + str(counter)}"
            if candidate not in existing:
                self._append_probe(
                    f"{kind} variable rename fallback: {base_name} -> {candidate}",
                    probe_key="neuroexplorer_var_object_probe_path",
                )
                return candidate
            counter += 1

    def get_reference_event_name(self) -> str:
        return self.current_event_on_name

    def cleanup_probe_vars(self, names: list[str], probe_key: str = "neuroexplorer_var_object_probe_path") -> None:
        for name in names:
            if not name:
                continue
            if self._delete_var_by_name(name):
                self._append_probe(f"cleanup success: {name}", probe_key=probe_key)
            else:
                self._append_probe(f"cleanup warning: failed to delete {name}", probe_key=probe_key)

    def _doc_path_matches(self, doc: Any, pl2_path: Path) -> bool:
        for attr_name in ["FileName", "Filename", "Path", "Name", "DocumentName"]:
            if hasattr(doc, attr_name):
                try:
                    attr_value = getattr(doc, attr_name)
                    if isinstance(attr_value, str) and pl2_path.name.lower() in attr_value.lower():
                        return True
                except Exception:
                    continue
        return False

    def open_file(self, pl2_path: Path) -> None:
        self.current_file = pl2_path
        mode = self.config["neuroexplorer"]["files"]["open_pl2_mode"]
        if mode == "manual_only":
            raise NeedsManualActionError(
                f"open_pl2_mode=manual_only. Manually open {pl2_path.name} in NeuroExplorer, then rerun this module."
            )

        if self.nex is None:
            raise RuntimeError("nex backend is not connected.")

        for function_name in ["OpenDocument", "OpenFile", "ReadFile"]:
            if hasattr(self.nex, function_name):
                opener = getattr(self.nex, function_name)
                try:
                    result = opener(str(pl2_path))
                    if result is not None:
                        self.doc = result
                    else:
                        self.doc = self._get_active_document_safe()
                    self.logger.log(
                        "neuroexplorer_nex_backend",
                        pl2_path.stem,
                        str(pl2_path),
                        "",
                        "success",
                        f"Opened file using nex.{function_name}{safe_signature_text(opener)}",
                    )
                    return
                except Exception as exc:
                    self.logger.log(
                        "neuroexplorer_nex_backend",
                        pl2_path.stem,
                        str(pl2_path),
                        "",
                        "warning",
                        f"nex.{function_name} exists but did not open the file successfully.",
                        exception=exc,
                    )

        active_doc = self._get_active_document_safe()
        if active_doc is not None and self._doc_path_matches(active_doc, pl2_path):
            self.doc = active_doc
            self.logger.log(
                "neuroexplorer_nex_backend",
                pl2_path.stem,
                str(pl2_path),
                "",
                "success",
                "Using manually opened active NeuroExplorer document because no verified open-file API was available.",
            )
            return

        if mode == "active_document_only":
            raise NeedsManualActionError(
                f"Manually open the target .pl2 file in NeuroExplorer first: {pl2_path.name}, then rerun this module."
            )
        raise NeedsManualActionError(
            f"Manually open the target .pl2 file in NeuroExplorer first: {pl2_path.name}, then rerun this module, "
            "or set open_pl2_mode=manual_only."
        )

    def get_active_document(self):
        return self._get_active_document_safe()

    def list_variables(self):
        doc = self.doc or self._get_active_document_safe()
        if doc is None:
            return []

        variables: list[str] = []
        seen: set[str] = set()
        def _normalize_values(raw_values):
            if raw_values is None:
                return []
            if callable(raw_values):
                raw_values = raw_values()
            return [str(value) for value in raw_values if str(value).strip()]

        strategies = [
            ("keys", lambda d: list(d.keys())),
            ("items", lambda d: [name for name, _ in d.items()]),
            ("__iter__", lambda d: [str(item) for item in d]),
            ("Variables", lambda d: getattr(d, "Variables")),
            ("variable_names", lambda d: getattr(d, "variable_names")),
            ("EventNames", lambda d: getattr(d, "EventNames")),
            ("IntervalNames", lambda d: getattr(d, "IntervalNames")),
            ("NeuronNames", lambda d: getattr(d, "NeuronNames")),
            ("MarkerNames", lambda d: getattr(d, "MarkerNames")),
            ("ContinuousNames", lambda d: getattr(d, "ContinuousNames")),
        ]
        for strategy_name, strategy in strategies:
            if strategy_name in {"keys", "items", "__iter__"} and not hasattr(doc, strategy_name):
                continue
            if strategy_name not in {"keys", "items", "__iter__"} and not hasattr(doc, strategy_name):
                continue
            try:
                raw_values = strategy(doc)
                strategy_values = _normalize_values(raw_values)
                new_count = 0
                for value in strategy_values:
                    if value not in seen:
                        seen.add(value)
                        variables.append(value)
                        new_count += 1
                if new_count:
                    self.logger.log(
                        "neuroexplorer_nex_backend",
                        "*",
                        "",
                        "",
                        "success",
                        f"Listed variables using document strategy: {strategy_name}. added={new_count}, total={len(variables)}",
                    )
            except Exception as exc:
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    "",
                    "warning",
                    f"Variable listing strategy failed: {strategy_name}",
                    exception=exc,
                )

        if variables:
            return variables
        self.logger.log(
            "neuroexplorer_nex_backend",
            "*",
            "",
            "",
            "warning",
            "Could not auto-list variables from the nex document. Provide `original_name` in unit_quality_table and make sure the NeuroExplorer template targets the correct variables.",
        )
        return []

    def list_spike_variables(self):
        variables = self.list_variables()
        patterns = ["spk", "spkc", "neuron", "unit"]
        results = []
        for variable in variables:
            lowered = variable.lower()
            if any(pattern in lowered for pattern in patterns):
                results.append(variable)
                continue
            if lowered.endswith(("a", "b", "c")) and any(ch.isdigit() for ch in lowered):
                results.append(variable)
        return results

    def list_neuron_variables(self):
        doc = self.doc or self._get_active_document_safe()
        collected: list[str] = []
        if doc is not None and hasattr(doc, "NeuronNames"):
            try:
                raw_names = getattr(doc, "NeuronNames")
                if callable(raw_names):
                    raw_names = raw_names()
                collected.extend(str(name) for name in raw_names if str(name).strip())
            except Exception as exc:
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    "",
                    "warning",
                    "Failed to query NeuronNames directly from nex document.",
                    exception=exc,
                )
        if collected:
            return collected
        return self.list_spike_variables()

    def list_event_variables(self):
        doc = self.doc or self._get_active_document_safe()
        collected: list[str] = []
        if doc is not None and hasattr(doc, "EventNames"):
            try:
                raw_names = getattr(doc, "EventNames")
                if callable(raw_names):
                    raw_names = raw_names()
                collected.extend(str(name) for name in raw_names if str(name).strip())
            except Exception as exc:
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    "",
                    "warning",
                    "Failed to query EventNames directly from nex document.",
                    exception=exc,
                )
        if collected:
            return collected
        variables = self.list_variables()
        event_names = [
            self.config["neuroexplorer"]["events"]["event_on_name"],
            self.config["neuroexplorer"]["events"]["event_off_name"],
        ]
        return [name for name in variables if name in event_names]

    def get_selected_var_names(self) -> list[str]:
        doc = self.doc or self._get_active_document_safe()
        if doc is None or not hasattr(doc, "GetSelVarNames"):
            return []
        try:
            raw_names = doc.GetSelVarNames()
            return [str(name) for name in raw_names if str(name).strip()]
        except Exception as exc:
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                "",
                "warning",
                "Failed to query selected NeuroExplorer variable names from the current document.",
                exception=exc,
            )
            return []

    def _create_var_with_strategies(self, *, kind: str, target_name: str, probe_key: str) -> tuple[Any, str]:
        doc = self.doc or self._get_active_document_safe()
        if doc is None:
            raise NeedsManualActionError("No active nex document available for variable creation.")
        if target_name == "AllFile":
            raise NeedsManualActionError("Refusing to create or overwrite built-in AllFile.")

        collection_name = "IntervalVars" if kind == "interval" else "EventVars"
        doc_methods = (
            ["CreateIntervalVariable", "AddIntervalVariable", "NewInterval", "NewIntervalVariable"]
            if kind == "interval"
            else ["CreateEventVariable", "AddEventVariable", "NewEvent", "NewEventVariable"]
        )
        nex_methods = (
            ["CreateIntervalVariable", "AddIntervalVariable", "NewIntervalVariable"]
            if kind == "interval"
            else ["CreateEventVariable", "AddEventVariable", "NewEventVariable"]
        )

        strategies: list[tuple[str, Any]] = []
        if hasattr(doc, "__setitem__"):
            strategies.append((f"I1/E1: doc['{target_name}'] = []", lambda: doc.__setitem__(target_name, [])))

        collection = getattr(doc, collection_name, None)
        if callable(collection):
            try:
                collection = collection()
            except Exception:
                collection = None
        if collection is not None:
            if hasattr(collection, "__setitem__"):
                strategies.append((f"I2/E2: {collection_name}['{target_name}'] = []", lambda c=collection: c.__setitem__(target_name, [])))
            for method_name in ["Add", "append", "create", "Create"]:
                if hasattr(collection, method_name):
                    method = getattr(collection, method_name)
                    strategies.append((f"I3/E3: {collection_name}.{method_name}('{target_name}')", lambda m=method: m(target_name)))

        for attr_name in ["vars", "_VarsList", "_VarNames"]:
            if hasattr(doc, attr_name):
                container = getattr(doc, attr_name)
                if callable(container):
                    try:
                        container = container()
                    except Exception:
                        continue
                if hasattr(container, "__setitem__"):
                    strategies.append((f"I6/E6: doc.{attr_name}['{target_name}'] = []", lambda c=container: c.__setitem__(target_name, [])))
                for method_name in ["Add", "append", "create", "Create"]:
                    if hasattr(container, method_name):
                        method = getattr(container, method_name)
                        strategies.append((f"I6/E6: doc.{attr_name}.{method_name}('{target_name}')", lambda m=method: m(target_name)))

        for method_name in doc_methods:
            if hasattr(doc, method_name):
                method = getattr(doc, method_name)
                strategies.append((f"I4/E4: doc.{method_name}('{target_name}')", lambda m=method: m(target_name)))

        if self.nex is not None:
            for method_name in nex_methods:
                if hasattr(self.nex, method_name):
                    method = getattr(self.nex, method_name)
                    strategies.append((f"I5/E5: nex.{method_name}(doc, '{target_name}')", lambda m=method: m(doc, target_name)))

        failures: list[str] = []
        for label, call in strategies:
            try:
                result = call()
                candidate = result if self._is_valid_nex_var(result, expected_name=target_name) else self._get_var_by_name(target_name)
                if self._is_valid_nex_var(candidate, expected_name=target_name):
                    self._append_probe(f"create {kind} success: {label}", probe_key=probe_key)
                    for line in self._describe_object(f"{target_name}_var", candidate):
                        self._append_probe(line, probe_key=probe_key)
                    self._append_probe(
                        f"{target_name}_var.has_varId={hasattr(candidate, 'varId')} has_docId={hasattr(candidate, 'docId')} "
                        f"nex.GetName={self._get_var_name(candidate)}",
                        probe_key=probe_key,
                    )
                    return candidate, label
                failures.append(f"{label} -> no valid NexVar returned")
                self._append_probe(f"create {kind} failed: {label} -> no valid NexVar returned", probe_key=probe_key)
            except Exception as exc:
                failures.append(f"{label} -> {type(exc).__name__}: {exc}")
                self._append_probe(f"create {kind} failed: {label} -> {type(exc).__name__}: {exc}", probe_key=probe_key)
        raise NeedsManualActionError(
            f"AddInterval requires NexVar but no valid creation method found for {kind} variable {target_name}. "
            + " | ".join(failures)
        )

    def _create_interval_var(self, interval_name: str, probe_key: str = "neuroexplorer_var_object_probe_path") -> tuple[Any, str]:
        return self._create_var_with_strategies(kind="interval", target_name=interval_name, probe_key=probe_key)

    def _create_event_var(self, event_name: str, probe_key: str = "neuroexplorer_var_object_probe_path") -> tuple[Any, str]:
        return self._create_var_with_strategies(kind="event", target_name=event_name, probe_key=probe_key)

    def _write_interval_to_var(self, interval_var: Any, start: float, end: float, probe_key: str) -> str:
        if self.nex is None:
            raise NeedsManualActionError("nex backend is not connected.")
        strategies: list[tuple[str, Any]] = []
        if hasattr(self.nex, "AddInterval"):
            strategies.append(("A:nex.AddInterval(var, start, end)", lambda: self.nex.AddInterval(interval_var, start, end)))
        doc = self.doc or self._get_active_document_safe()
        if doc is not None and hasattr(doc, "AddInterval"):
            method = getattr(doc, "AddInterval")
            strategies.append(("B:doc.AddInterval(var, start, end)", lambda m=method: m(interval_var, start, end)))
        failures: list[str] = []
        for label, call in strategies:
            try:
                call()
                self._append_probe(f"write interval success: {label}", probe_key=probe_key)
                return label
            except Exception as exc:
                failures.append(f"{label} -> {type(exc).__name__}: {exc}")
                self._append_probe(f"write interval failed: {label} -> {type(exc).__name__}: {exc}", probe_key=probe_key)
        raise NeedsManualActionError("All interval write strategies failed. " + " | ".join(failures))

    def _write_timestamp_to_var(self, event_var: Any, time_s: float, probe_key: str) -> str:
        if self.nex is None:
            raise NeedsManualActionError("nex backend is not connected.")
        strategies: list[tuple[str, Any]] = []
        if hasattr(self.nex, "AddTimestamp"):
            strategies.append(("A:nex.AddTimestamp(var, timestamp)", lambda: self.nex.AddTimestamp(event_var, time_s)))
        doc = self.doc or self._get_active_document_safe()
        if doc is not None and hasattr(doc, "AddTimestamp"):
            method = getattr(doc, "AddTimestamp")
            strategies.append(("B:doc.AddTimestamp(var, timestamp)", lambda m=method: m(event_var, time_s)))
        failures: list[str] = []
        for label, call in strategies:
            try:
                call()
                self._append_probe(f"write timestamp success: {label}", probe_key=probe_key)
                return label
            except Exception as exc:
                failures.append(f"{label} -> {type(exc).__name__}: {exc}")
                self._append_probe(f"write timestamp failed: {label} -> {type(exc).__name__}: {exc}", probe_key=probe_key)
        raise NeedsManualActionError("All timestamp write strategies failed. " + " | ".join(failures))

    def create_interval_variable_from_csv(self, file_id, interval_csv_path: Path, interval_name: str = "Light_Interval") -> str:
        parsed_interval_name, intervals = read_neuroexplorer_interval_csv(
            interval_csv_path,
            expected_interval_name=interval_name,
            allow_missing_variable_name=False,
            delimiter=self.config["neuroexplorer"]["interval"].get("delimiter", ","),
        )
        probe_key = "neuroexplorer_interval_event_probe_path"
        self._reset_probe(probe_key)
        self._log_probe_header(str(file_id), interval_csv_path, probe_key=probe_key)
        self._append_probe(f"parsed_interval_variable_name: {parsed_interval_name}", probe_key=probe_key)
        self._append_probe(f"parsed_intervals: {intervals}", probe_key=probe_key)
        if self.nex is not None:
            for func_name in ["AddInterval", "AddTimestamp", "DeleteVar", "GetName"]:
                if hasattr(self.nex, func_name):
                    self._append_probe(self._describe_callable(func_name, getattr(self.nex, func_name)), probe_key=probe_key)
        self._append_probe(f"IntervalNames before: {self._get_interval_names()}", probe_key=probe_key)
        target_name = self._ensure_target_name(parsed_interval_name, "interval")
        interval_var, create_strategy = self._create_interval_var(target_name, probe_key=probe_key)
        used_strategy = ""
        for start, end in intervals:
            used_strategy = self._write_interval_to_var(interval_var, float(start), float(end), probe_key=probe_key)
        interval_names_after = self._get_interval_names()
        self._append_probe(f"IntervalNames after: {interval_names_after}", probe_key=probe_key)
        if target_name not in interval_names_after:
            raise NeedsManualActionError(
                f"Interval creation did not produce variable {target_name}. Create strategy={create_strategy}; write strategy={used_strategy or 'none'}"
            )
        self.current_interval_name = target_name
        self.logger.log(
            "neuroexplorer_nex_backend",
            str(file_id),
            str(interval_csv_path),
            "",
            "success",
            f"Created NeuroExplorer interval variable {target_name} from CSV using create={create_strategy}, write={used_strategy}.",
        )
        return target_name

    def create_event_variable_from_times(self, event_name: str, times) -> str:
        probe_key = "neuroexplorer_interval_event_probe_path"
        self._append_probe(f"EventNames before: {self._get_event_names()}", probe_key=probe_key)
        target_name = self._ensure_target_name(event_name, "event")
        event_var, create_strategy = self._create_event_var(target_name, probe_key=probe_key)
        used_strategy = ""
        for time_s in times:
            used_strategy = self._write_timestamp_to_var(event_var, float(time_s), probe_key=probe_key)
        event_names_after = self._get_event_names()
        self._append_probe(f"EventNames after: {event_names_after}", probe_key=probe_key)
        if target_name not in event_names_after:
            raise NeedsManualActionError(
                f"Event creation did not produce variable {target_name}. Create strategy={create_strategy}; write strategy={used_strategy or 'none'}"
            )
        self.logger.log(
            "neuroexplorer_nex_backend",
            "*",
            "",
            "",
            "success",
            f"Created NeuroExplorer event variable {target_name} using create={create_strategy}, write={used_strategy}.",
        )
        return target_name

    def ensure_events(self, file_id, light_on_times, light_off_times) -> None:
        events_cfg = self.config["neuroexplorer"]["events"]
        if events_cfg.get("stimulus_input_mode", "event") == "interval":
            interval_cfg = self.config["neuroexplorer"]["interval"]
            interval_path = resolve_interval_file_path(
                self.paths["events_export_dir"],
                str(file_id),
                interval_cfg["interval_csv_pattern"],
            )
            if not interval_path.exists():
                raise NeedsManualActionError(f"Interval CSV not found for interval mode: {interval_path}")
            interval_name, intervals = read_neuroexplorer_interval_csv(
                interval_path,
                expected_interval_name=events_cfg.get("interval_name", "Light_Interval"),
                allow_missing_variable_name=False,
                delimiter=interval_cfg.get("delimiter", ","),
            )
            derived_on, derived_off, _ = derive_light_on_off_from_intervals(intervals)
            try:
                actual_interval_name = self.create_interval_variable_from_csv(str(file_id), interval_path, interval_name=interval_name)
                self.current_interval_name = actual_interval_name
                if events_cfg.get("derive_events_from_interval", True):
                    self.current_event_on_name = self.create_event_variable_from_times(events_cfg["event_on_name"], derived_on)
                    if events_cfg.get("require_light_off", False):
                        self.current_event_off_name = self.create_event_variable_from_times(events_cfg["event_off_name"], derived_off)
            except NeedsManualActionError as exc:
                raise NeedsManualActionError(
                    "Automatic interval/event creation failed after trying AddInterval/AddTimestamp strategies. "
                    f"Probe log: {self.paths['neuroexplorer_interval_event_probe_path']}. "
                    "If you need manual fallback, provide this probe log."
                ) from exc
            return

        event_names = set(self.list_event_variables())
        on_name = self.config["neuroexplorer"]["events"]["event_on_name"]
        off_name = self.config["neuroexplorer"]["events"]["event_off_name"]
        if on_name in event_names and (off_name in event_names or not self.config["neuroexplorer"]["events"]["require_light_off"]):
            self.logger.log(
                "neuroexplorer_nex_backend",
                str(file_id),
                "",
                "",
                "success",
                f"Required NeuroExplorer event(s) already present: {sorted(event_names)}",
            )
            return

        on_path = resolve_event_file_path(self.paths["events_export_dir"], str(file_id), on_name, "event")
        off_path = resolve_event_file_path(self.paths["events_export_dir"], str(file_id), off_name, "event")
        write_event_times(on_path, light_on_times)
        write_event_times(off_path, light_off_times)
        raise NeedsManualActionError(
            f"The current nex API path is not verified for direct event creation. Event CSV files were created: "
            f"{on_path.name}, {off_path.name}. Import Light_On and Light_Off separately in NeuroExplorer. "
            "Do not merge them into one reference event. PSTH reference event must be Light_On."
        )

    def validate_required_events(self) -> None:
        event_names = set(self.list_event_variables())
        required = []
        if self.config["neuroexplorer"]["events"]["require_light_on"]:
            required.append(self.current_event_on_name)
        if self.config["neuroexplorer"]["events"]["require_light_off"]:
            required.append(self.current_event_off_name)
        missing = [name for name in required if name not in event_names]
        if missing:
            raise NeedsManualActionError(f"Required NeuroExplorer event(s) missing from current document: {missing}")

    def _modify_template_candidates(self, template_name: str, candidate_names: list[str], value: Any, required: bool = False) -> None:
        if self.nex is None or not hasattr(self.nex, "ModifyTemplate"):
            if required:
                raise NeedsManualActionError(
                    f"nex.ModifyTemplate is unavailable, so template {template_name} cannot be modified automatically. "
                    "Save a correctly configured GUI template first."
                )
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                "",
                "warning",
                f"nex.ModifyTemplate unavailable. Manual GUI template setup required for {template_name}.",
            )
            return

        for parameter_name in candidate_names:
            try:
                self.nex.ModifyTemplate(self.doc, template_name, parameter_name, str(value))
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    "",
                    "success",
                    f"Modified template {template_name}: {parameter_name}={value}",
                )
                return
            except Exception as exc:
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    "",
                    "warning",
                    f"ModifyTemplate failed for {template_name}: {parameter_name}={value}",
                    exception=exc,
                )
        if required:
            raise NeedsManualActionError(
                f"Critical template parameter update failed: {template_name} -> {candidate_names}. "
                "Save a correctly configured NeuroExplorer GUI template first."
            )

    def configure_psth_template(self, template_name, reference_event, x_min_s, x_max_s, bin_width_s, histogram_unit) -> None:
        templates_cfg = self.config["neuroexplorer"]["templates"]
        if not templates_cfg.get("auto_modify_template", True):
            self.logger.log("neuroexplorer_nex_backend", "*", "", "", "warning", "auto_modify_template disabled; using GUI-saved PSTH template as-is.")
            return

        names_cfg = templates_cfg.get("parameter_names", {})
        self._modify_template_candidates(
            template_name,
            names_cfg.get("psth_reference", ["Reference", "Ref. event", "Reference Event"]),
            reference_event,
            required=True,
        )
        self._modify_template_candidates(
            template_name,
            names_cfg.get("psth_x_min", ["X Minimum", "X Min (sec)", "XMin (sec)"]),
            x_min_s,
        )
        self._modify_template_candidates(
            template_name,
            names_cfg.get("psth_x_max", ["X Maximum", "X Max (sec)", "XMax (sec)"]),
            x_max_s,
        )
        self._modify_template_candidates(
            template_name,
            names_cfg.get("psth_bin", ["Bin (sec)", "Bin"]),
            bin_width_s,
            required=True,
        )
        self._modify_template_candidates(
            template_name,
            names_cfg.get("psth_units", ["Histogram Units", "Units"]),
            histogram_unit,
        )

    def configure_fullrate_template(self, template_name, bin_width_s, histogram_unit) -> None:
        templates_cfg = self.config["neuroexplorer"]["templates"]
        if not templates_cfg.get("auto_modify_template", True):
            return
        names_cfg = templates_cfg.get("parameter_names", {})
        self._modify_template_candidates(
            template_name,
            names_cfg.get("fullrate_bin", ["Bin (sec)", "Bin"]),
            bin_width_s,
            required=False,
        )
        self._modify_template_candidates(
            template_name,
            names_cfg.get("fullrate_units", ["Histogram Units", "Units"]),
            histogram_unit,
            required=False,
        )

    def run_template(self, template_name) -> None:
        if self.nex is None or not hasattr(self.nex, "ApplyTemplate"):
            raise NeedsManualActionError(
                f"nex.ApplyTemplate is unavailable, so template {template_name} cannot be executed automatically. "
                "Run the template manually in NeuroExplorer GUI."
            )
        try:
            self.nex.ApplyTemplate(self.doc, template_name)
            self.logger.log(
                "neuroexplorer_nex_backend",
                "*",
                "",
                "",
                "success",
                f"Applied NeuroExplorer template: {template_name}",
            )
        except Exception as exc:
            raise NeedsManualActionError(
                f"Template {template_name} was not found in NeuroExplorer. Create and save it in the NeuroExplorer GUI first."
            ) from exc

    def save_numerical_results(self, doc, output_path: Path) -> Path:
        if self.nex is None:
            raise NeedsManualActionError("nex backend is not connected.")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        export_attempts: list[tuple[str, str]] = []
        for function_name in ["SaveNumResults", "SaveResults"]:
            if not hasattr(self.nex, function_name):
                continue
            try:
                getattr(self.nex, function_name)(doc, str(output_path))
                if output_path.exists() and output_path.stat().st_size > 0:
                    self.logger.log(
                        "neuroexplorer_nex_backend",
                        "*",
                        "",
                        str(output_path),
                        "success",
                        f"Saved numerical results with nex.{function_name}.",
                    )
                    return output_path
                export_attempts.append((function_name, "Output file missing or empty after export call."))
            except Exception as exc:
                export_attempts.append((function_name, str(exc)))
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    "*",
                    "",
                    str(output_path),
                    "warning",
                    f"nex.{function_name} failed during numerical export.",
                    exception=exc,
                )
        detail = "; ".join(f"{name}: {message}" for name, message in export_attempts) or "No supported export function detected."
        raise NeedsManualActionError(
            f"Automatic numerical export failed for {output_path.name}. {detail}"
        )

    def normalize_neuroexplorer_psth_results(
        self,
        raw_path: Path,
        output_csv: Path,
        file_id: str,
        unit_names: list[str],
        bin_width_s: float,
    ) -> Path | None:
        raw_path = Path(raw_path)
        output_csv = Path(output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw_df = read_delimited_text_table(raw_path)
            long_df = convert_rate_export_to_long(raw_df, file_id=file_id, kind="psth")
            if unit_names:
                allowed = {str(name) for name in unit_names}
                long_df = long_df[long_df["unit_id"].astype(str).isin(allowed)].copy()
            if "bin_center_s" in long_df.columns:
                long_df["bin_start_s"] = long_df["bin_center_s"] - float(bin_width_s) / 2
                long_df["bin_end_s"] = long_df["bin_center_s"] + float(bin_width_s) / 2
            long_df["source_file"] = str(raw_path)
            write_table(long_df, output_csv)
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(raw_path),
                str(output_csv),
                "success",
                "Normalized SaveNumResults raw PSTH output into standard CSV.",
            )
            return output_csv
        except Exception as exc:
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(raw_path),
                str(output_csv),
                "warning",
                "PSTH raw numerical export was saved, but automatic parsing into standard CSV failed. Keep the raw file for manual inspection.",
                exception=exc,
            )
            return None

    def normalize_neuroexplorer_fullrate_results(
        self,
        raw_path: Path,
        output_csv: Path,
        file_id: str,
        unit_names: list[str],
    ) -> Path | None:
        raw_path = Path(raw_path)
        output_csv = Path(output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw_df = read_delimited_text_table(raw_path)
            long_df = convert_rate_export_to_long(raw_df, file_id=file_id, kind="fullrate")
            if unit_names:
                allowed = {str(name) for name in unit_names}
                long_df = long_df[long_df["unit_id"].astype(str).isin(allowed)].copy()
            long_df["source_file"] = str(raw_path)
            write_table(long_df, output_csv)
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(raw_path),
                str(output_csv),
                "success",
                "Normalized SaveNumResults raw full-session output into standard CSV.",
            )
            return output_csv
        except Exception as exc:
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(raw_path),
                str(output_csv),
                "warning",
                "Full-session raw numerical export was saved, but automatic parsing into standard CSV failed. Keep the raw file for manual inspection.",
                exception=exc,
            )
            return None

    def export_psth(self, file_id, unit_names, output_csv) -> None:
        raw_path = output_csv.with_name(output_csv.stem + "_raw.txt")
        try:
            self.save_numerical_results(self.doc, raw_path)
        except NeedsManualActionError as exc:
            message = (
                f"Automatic PSTH numerical export failed. Manually export the PSTH results to {output_csv}. "
                f"Raw target attempted: {raw_path}"
            )
            self.logger.log("neuroexplorer_nex_backend", file_id, "", str(output_csv), "warning", message, exception=exc)
            raise

        normalized_path = self.normalize_neuroexplorer_psth_results(
            raw_path,
            output_csv,
            file_id=file_id,
            unit_names=unit_names,
            bin_width_s=self.config["neuroexplorer"]["psth"]["bin_width_s"],
        )
        if normalized_path is None:
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(raw_path),
                str(output_csv),
                "warning",
                "PSTH raw file saved successfully, but standardized CSV could not be produced automatically.",
            )

    def export_fullrate(self, file_id, unit_names, output_csv) -> None:
        selected_vars = self.get_selected_var_names()
        if selected_vars:
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                "",
                "",
                "success",
                f"Current NeuroExplorer selected variables before fullrate export: {selected_vars}",
            )
        raw_path = output_csv.with_name(output_csv.stem + "_raw.txt")
        try:
            self.save_numerical_results(self.doc, raw_path)
        except NeedsManualActionError as exc:
            message = (
                f"Automatic full-session numerical export failed. Manually export the fullrate results to {output_csv}. "
                f"Raw target attempted: {raw_path}"
            )
            self.logger.log("neuroexplorer_nex_backend", file_id, "", str(output_csv), "warning", message, exception=exc)
            raise

        normalized_path = self.normalize_neuroexplorer_fullrate_results(
            raw_path,
            output_csv,
            file_id=file_id,
            unit_names=unit_names,
        )
        if normalized_path is None:
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(raw_path),
                str(output_csv),
                "warning",
                "Full-session raw file saved successfully, but standardized CSV could not be produced automatically.",
            )
            return

        try:
            exported_df = read_table(output_csv)
            exported_units = sorted(exported_df["unit_id"].astype(str).unique().tolist()) if "unit_id" in exported_df.columns else []
            requested_units = sorted({str(name) for name in unit_names})
            missing_units = [name for name in requested_units if name not in exported_units]
            if missing_units:
                self.logger.log(
                    "neuroexplorer_nex_backend",
                    file_id,
                    str(raw_path),
                    str(output_csv),
                    "warning",
                    "Fullrate export is missing some requested units. This usually means the NeuroExplorer template or current selection exported only a subset of neuron variables. "
                    f"requested={requested_units}; selected={selected_vars or 'unknown'}; exported={exported_units}; missing={missing_units}",
                )
        except Exception as exc:
            self.logger.log(
                "neuroexplorer_nex_backend",
                file_id,
                str(output_csv),
                "",
                "warning",
                "Could not compare requested vs exported units after fullrate export.",
                exception=exc,
            )

    def close_file(self) -> None:
        self.doc = None
        self.current_file = None

    def quit(self) -> None:
        self.close_file()
