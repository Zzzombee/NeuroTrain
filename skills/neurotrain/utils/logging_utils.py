from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class LogRecord:
    timestamp: str
    module: str
    event: str
    file_id: str
    input_path: str
    output_path: str
    origin_available: str
    opju_mode: str
    opju_output_path: str
    n_workbooks: str
    n_graph_pages: str
    n_png_exported: str
    n_wide_rows_raw: str
    n_wide_rows_qc_pass: str
    n_wide_rows_qc_excluded: str
    min_max_window_hz: str
    min_total_expected_spikes: str
    output_wide: str
    output_wide_qc: str
    output_qc_excluded: str
    status: str
    message: str
    exception: str


class PipelineLogger:
    def __init__(self, logs_dir: Path):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.records: list[LogRecord] = []

    def log(
        self,
        module: str,
        file_id: str,
        input_path: str,
        output_path: str,
        status: str,
        message: str,
        exception: Exception | str | None = None,
        *,
        event: str = "",
        origin_available: str = "",
        opju_mode: str = "",
        opju_output_path: str = "",
        n_workbooks: int | str = "",
        n_graph_pages: int | str = "",
        n_png_exported: int | str = "",
        n_wide_rows_raw: int | str = "",
        n_wide_rows_qc_pass: int | str = "",
        n_wide_rows_qc_excluded: int | str = "",
        min_max_window_hz: float | int | str = "",
        min_total_expected_spikes: float | int | str = "",
        output_wide: str = "",
        output_wide_qc: str = "",
        output_qc_excluded: str = "",
    ) -> None:
        self.records.append(
            LogRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                module=module,
                event=event,
                file_id=file_id,
                input_path=input_path,
                output_path=output_path,
                origin_available=origin_available,
                opju_mode=opju_mode,
                opju_output_path=opju_output_path,
                n_workbooks=str(n_workbooks),
                n_graph_pages=str(n_graph_pages),
                n_png_exported=str(n_png_exported),
                n_wide_rows_raw=str(n_wide_rows_raw),
                n_wide_rows_qc_pass=str(n_wide_rows_qc_pass),
                n_wide_rows_qc_excluded=str(n_wide_rows_qc_excluded),
                min_max_window_hz=str(min_max_window_hz),
                min_total_expected_spikes=str(min_total_expected_spikes),
                output_wide=output_wide,
                output_wide_qc=output_wide_qc,
                output_qc_excluded=output_qc_excluded,
                status=status,
                message=message,
                exception="" if exception is None else str(exception),
            )
        )

    def as_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(record) for record in self.records])

    def _write_excel_with_fallback(self, df: pd.DataFrame, path: Path, fallback_stamp: str) -> Path:
        try:
            df.to_excel(path, index=False)
            return path
        except PermissionError:
            fallback_path = path.with_name(f"{path.stem}_{fallback_stamp}{path.suffix}")
            df.to_excel(fallback_path, index=False)
            print(f"Warning: log file is locked; wrote fallback log: {fallback_path}")
            return fallback_path

    def save(self) -> None:
        df = self.as_dataframe()
        processing_path = self.logs_dir / "processing_log.xlsx"
        error_path = self.logs_dir / "error_log.xlsx"
        if df.empty:
            df = pd.DataFrame(
                columns=[
                    "timestamp",
                    "module",
                    "event",
                    "file_id",
                    "input_path",
                    "output_path",
                    "origin_available",
                    "opju_mode",
                    "opju_output_path",
                    "n_workbooks",
                    "n_graph_pages",
                    "n_png_exported",
                    "n_wide_rows_raw",
                    "n_wide_rows_qc_pass",
                    "n_wide_rows_qc_excluded",
                    "min_max_window_hz",
                    "min_total_expected_spikes",
                    "output_wide",
                    "output_wide_qc",
                    "output_qc_excluded",
                    "status",
                    "message",
                    "exception",
                ]
            )
        fallback_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._write_excel_with_fallback(df, processing_path, fallback_stamp)
        error_df = df[df["status"].isin(["warning", "failed"])]
        self._write_excel_with_fallback(error_df, error_path, fallback_stamp)
