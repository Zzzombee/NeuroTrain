from __future__ import annotations

import argparse
import itertools
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_path, resolve_project_paths
from utils.table_utils import normalize_include_column, read_table, write_table


CLUSTER_COLUMNS = [
    "cluster_id",
    "direction",
    "start_time_s",
    "end_time_s",
    "n_bins",
    "cluster_mass",
    "cluster_p",
    "significant",
    "peak_time_s",
    "peak_t",
    "mean_delta_rate_at_peak_hz",
    "n_units",
    "n_permutations",
    "seed",
]

DEFAULTS = {
    "enabled": False,
    "input_pattern": "*_LightAlignedRate_*.csv",
    "analysis_window_s": None,
    "baseline_window_s": None,
    "test_window_s": None,
    "cluster_forming_alpha": 0.05,
    "cluster_alpha": 0.05,
    "n_permutations": 10000,
    "max_exact_permutations": 65536,
    "tail": 0,
    "statistic": "one_sample_t",
    "seed": 20260714,
    "min_valid_bins_per_unit": None,
    "include_only_unit_quality_include_yes": True,
    "duplicate_policy": "exclude_duplicates",
    "output_subdir": "time_cluster_permutation",
    "figure_format": None,
    "include_in_pptx": False,
}


@dataclass
class PreparedAnalysis:
    time_s: np.ndarray
    raw_rate_hz: np.ndarray
    delta_rate_hz: np.ndarray
    included_units: pd.DataFrame
    unit_summary: pd.DataFrame
    matrix_long: pd.DataFrame
    analysis_window_s: tuple[float, float]
    baseline_window_s: tuple[float, float]
    test_window_s: tuple[float, float]
    baseline_mask: np.ndarray
    test_mask: np.ndarray


@dataclass
class ClusterPermutationResult:
    clusters: pd.DataFrame
    time_statistics: pd.DataFrame
    null_max_cluster_mass: np.ndarray
    threshold: float
    permutation_method: str
    n_permutations: int


def time_cluster_config(config: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update(config.get("time_cluster_permutation", {}))
    return merged


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    max_iterations = 300
    epsilon = 3.0e-14
    floor = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < floor:
        d = floor
    d = 1.0 / d
    h = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + aa / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        h *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + aa / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            return h
    raise RuntimeError("Incomplete-beta continued fraction did not converge.")


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if not 0.0 <= x <= 1.0:
        raise ValueError("x must be in [0, 1] for the regularized incomplete beta function.")
    if a <= 0.0 or b <= 0.0:
        raise ValueError("a and b must be positive for the regularized incomplete beta function.")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_cdf(value: float, df: int) -> float:
    if df < 1:
        raise ValueError("Student-t degrees of freedom must be at least 1.")
    if math.isnan(value):
        return float("nan")
    if value == float("inf"):
        return 1.0
    if value == float("-inf"):
        return 0.0
    x = df / (df + value * value)
    beta_value = regularized_incomplete_beta(x, df / 2.0, 0.5)
    return 1.0 - 0.5 * beta_value if value >= 0 else 0.5 * beta_value


def student_t_ppf(probability: float, df: int) -> float:
    if not 0.0 < probability < 1.0:
        raise ValueError("Student-t quantile probability must be strictly between 0 and 1.")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -student_t_ppf(1.0 - probability, df)
    lower = 0.0
    upper = 1.0
    while student_t_cdf(upper, df) < probability:
        upper *= 2.0
        if upper > 1.0e8:
            raise RuntimeError("Could not bracket Student-t quantile.")
    for _ in range(100):
        midpoint = (lower + upper) / 2.0
        if student_t_cdf(midpoint, df) < probability:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def one_sample_t(matrix: np.ndarray, *, compute_p: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2:
        raise ValueError("One-sample t input must have shape (n_units, n_time_bins).")
    n_time = values.shape[1]
    t_values = np.full(n_time, np.nan, dtype=float)
    point_p = np.full(n_time, np.nan, dtype=float)
    n_valid = np.zeros(n_time, dtype=int)
    for time_index in range(n_time):
        column = values[:, time_index]
        column = column[np.isfinite(column)]
        n_valid[time_index] = len(column)
        if len(column) < 2:
            continue
        mean = float(np.mean(column))
        standard_deviation = float(np.std(column, ddof=1))
        if math.isclose(standard_deviation, 0.0, abs_tol=1.0e-15):
            if math.isclose(mean, 0.0, abs_tol=1.0e-15):
                t_values[time_index] = 0.0
                point_p[time_index] = 1.0
            continue
        t_value = mean / (standard_deviation / math.sqrt(len(column)))
        t_values[time_index] = t_value
        if compute_p:
            point_p[time_index] = min(1.0, 2.0 * (1.0 - student_t_cdf(abs(t_value), len(column) - 1)))
    return t_values, point_p, n_valid


def _contiguous_true_runs(mask: np.ndarray) -> Iterable[np.ndarray]:
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return
    split_points = np.flatnonzero(np.diff(indices) != 1) + 1
    for run in np.split(indices, split_points):
        if run.size:
            yield run


def find_temporal_clusters(t_values: np.ndarray, threshold: float, tail: int = 0) -> list[dict]:
    if tail not in {-1, 0, 1}:
        raise ValueError("tail must be -1, 0, or 1.")
    values = np.asarray(t_values, dtype=float)
    clusters: list[dict] = []
    if tail in {0, 1}:
        for indices in _contiguous_true_runs(np.isfinite(values) & (values >= threshold)):
            clusters.append({"direction": "positive", "indices": indices, "mass": float(np.sum(values[indices]))})
    if tail in {0, -1}:
        for indices in _contiguous_true_runs(np.isfinite(values) & (values <= -threshold)):
            clusters.append({"direction": "negative", "indices": indices, "mass": float(np.sum(np.abs(values[indices])))})
    return sorted(clusters, key=lambda item: int(item["indices"][0]))


def _sign_vectors(n_units: int, tail: int, n_requested: int, max_exact: int, seed: int) -> tuple[Iterable[np.ndarray], str, int]:
    # For a two-sided max-|cluster-mass| test, a sign vector and its global
    # negation produce the same null statistic. Fixing the first sign to +1
    # enumerates exactly one representative from each equivalent pair.
    n_free = n_units - 1 if tail == 0 else n_units
    n_unique = 2**n_free
    if n_unique <= n_requested and n_unique <= max_exact:
        def exact_vectors() -> Iterable[np.ndarray]:
            for bits in itertools.product((-1.0, 1.0), repeat=n_free):
                if tail == 0:
                    signs = np.asarray((1.0, *bits), dtype=float)
                else:
                    signs = np.asarray(bits, dtype=float)
                # The unflipped observed assignment is supplied by the +1
                # correction in the p-value numerator and denominator.
                if np.all(signs == 1.0):
                    continue
                yield signs

        return exact_vectors(), "exact", n_unique - 1

    rng = np.random.default_rng(seed)

    def monte_carlo_vectors() -> Iterable[np.ndarray]:
        for _ in range(n_requested):
            while True:
                signs = rng.choice(np.asarray([-1.0, 1.0]), size=n_units)
                if tail == 0:
                    signs[0] = 1.0
                if not np.all(signs == 1.0):
                    break
            yield signs

    return monte_carlo_vectors(), "monte_carlo", n_requested


def temporal_cluster_permutation_test(
    matrix: np.ndarray,
    time_s: np.ndarray,
    *,
    cluster_forming_alpha: float = 0.05,
    cluster_alpha: float = 0.05,
    n_permutations: int = 10000,
    tail: int = 0,
    seed: int = 20260714,
    max_exact_permutations: int = 65536,
) -> ClusterPermutationResult:
    values = np.asarray(matrix, dtype=float)
    times = np.asarray(time_s, dtype=float)
    if values.ndim != 2 or values.shape[1] != len(times):
        raise ValueError("matrix must be (n_units, n_time_bins) and match time_s.")
    if values.shape[0] < 2:
        raise ValueError("Temporal cluster permutation requires at least 2 included units.")
    if len(times) == 0 or np.any(np.diff(times) <= 0):
        raise ValueError("time_s must be non-empty and strictly increasing.")
    if not 0.0 < cluster_forming_alpha < 1.0:
        raise ValueError("cluster_forming_alpha must be strictly between 0 and 1.")
    if not 0.0 < cluster_alpha < 1.0:
        raise ValueError("cluster_alpha must be strictly between 0 and 1.")
    if n_permutations < 1:
        raise ValueError("n_permutations must be at least 1.")
    if tail not in {-1, 0, 1}:
        raise ValueError("tail must be -1, 0, or 1.")

    probability = 1.0 - cluster_forming_alpha / 2.0 if tail == 0 else 1.0 - cluster_forming_alpha
    threshold = student_t_ppf(probability, values.shape[0] - 1)
    observed_t, point_p, n_valid = one_sample_t(values)
    observed_clusters = find_temporal_clusters(observed_t, threshold, tail=tail)

    sign_vectors, method, actual_permutations = _sign_vectors(
        values.shape[0], tail, n_permutations, max_exact_permutations, seed
    )
    null_max = np.zeros(actual_permutations, dtype=float)
    for permutation_index, signs in enumerate(sign_vectors):
        permuted_t, _, _ = one_sample_t(values * signs[:, np.newaxis], compute_p=False)
        permutation_clusters = find_temporal_clusters(permuted_t, threshold, tail=tail)
        if permutation_clusters:
            null_max[permutation_index] = max(cluster["mass"] for cluster in permutation_clusters)

    cluster_rows: list[dict] = []
    labels = np.zeros(len(times), dtype=int)
    cluster_p_values = np.full(len(times), np.nan, dtype=float)
    for cluster_id, cluster in enumerate(observed_clusters, start=1):
        indices = cluster["indices"]
        mass = float(cluster["mass"])
        cluster_p = (1.0 + float(np.count_nonzero(null_max >= mass))) / (1.0 + actual_permutations)
        labels[indices] = cluster_id
        cluster_p_values[indices] = cluster_p
        peak_local_index = int(np.nanargmax(np.abs(observed_t[indices])))
        peak_index = int(indices[peak_local_index])
        cluster_rows.append(
            {
                "cluster_id": cluster_id,
                "direction": cluster["direction"],
                "start_time_s": float(times[indices[0]]),
                "end_time_s": float(times[indices[-1]]),
                "n_bins": len(indices),
                "cluster_mass": mass,
                "cluster_p": cluster_p,
                "significant": bool(cluster_p < cluster_alpha),
                "peak_time_s": float(times[peak_index]),
                "peak_t": float(observed_t[peak_index]),
                "mean_delta_rate_at_peak_hz": float(np.nanmean(values[:, peak_index])),
                "n_units": values.shape[0],
                "n_permutations": actual_permutations,
                "seed": seed,
            }
        )
    clusters_df = pd.DataFrame(cluster_rows, columns=CLUSTER_COLUMNS)
    time_statistics = pd.DataFrame(
        {
            "time_s": times,
            "t_value": observed_t,
            "point_p_uncorrected": point_p,
            "n_valid_units": n_valid,
            "cluster_label": labels,
            "cluster_p": cluster_p_values,
        }
    )
    return ClusterPermutationResult(
        clusters=clusters_df,
        time_statistics=time_statistics,
        null_max_cluster_mass=null_max,
        threshold=threshold,
        permutation_method=method,
        n_permutations=actual_permutations,
    )


def _window_pair(value, name: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must contain exactly [start_s, end_s].")
    start, end = float(value[0]), float(value[1])
    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError(f"{name} must have finite bounds with start_s < end_s; got {value!r}.")
    return start, end


def _validate_and_resolve_windows(config: dict, cfg: dict, available_time: np.ndarray) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    if len(available_time) < 2:
        raise ValueError("Aligned input must contain at least two distinct time bins.")
    aligned_cfg = config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))
    baseline_value = cfg.get("baseline_window_s")
    test_value = cfg.get("test_window_s")
    if baseline_value is None:
        baseline_value = aligned_cfg.get("pre_window_s")
    if test_value is None:
        test_value = aligned_cfg.get("light_window_s")
    if baseline_value is None:
        raise ValueError("time_cluster_permutation.baseline_window_s is missing and aligned_rate.pre_window_s is unavailable.")
    if test_value is None:
        raise ValueError("time_cluster_permutation.test_window_s is missing and aligned_rate.light_window_s is unavailable.")
    baseline = _window_pair(baseline_value, "time_cluster_permutation.baseline_window_s")
    test = _window_pair(test_value, "time_cluster_permutation.test_window_s")
    if max(baseline[0], test[0]) < min(baseline[1], test[1]):
        raise ValueError("Baseline and test windows must not overlap.")
    analysis_value = cfg.get("analysis_window_s")
    analysis = (float(available_time[0]), float(available_time[-1])) if analysis_value is None else _window_pair(
        analysis_value, "time_cluster_permutation.analysis_window_s"
    )
    tolerance = max(1.0e-9, float(np.median(np.diff(available_time))) * 1.0e-6)
    available_start = float(available_time[0])
    available_end = float(available_time[-1])
    if analysis[0] < available_start - tolerance or analysis[1] > available_end + tolerance:
        raise ValueError(
            f"Analysis window {analysis} s exceeds aligned recording range [{available_start}, {available_end}] s."
        )
    for name, window in (("baseline", baseline), ("test", test)):
        if window[0] < analysis[0] - tolerance or window[1] > analysis[1] + tolerance:
            raise ValueError(f"{name.capitalize()} window {window} s must lie inside analysis window {analysis} s.")
        mask = (available_time >= window[0] - tolerance) & (available_time < window[1] - tolerance)
        if not mask.any():
            raise ValueError(f"{name.capitalize()} window {window} s contains no aligned time bins.")
    return analysis, baseline, test


def _load_unit_metadata(config: dict, paths: dict) -> pd.DataFrame:
    path = paths["unit_quality_path"]
    if not path.exists():
        return pd.DataFrame()
    metadata = normalize_include_column(read_table(path))
    for column in ["file_id", "unit_id", "original_name", "channel", "exclusion_reason", "duplicate_of", "representative_unit"]:
        if column not in metadata.columns:
            metadata[column] = ""
    metadata["file_id"] = metadata["file_id"].astype(str)
    metadata["unit_id"] = metadata["unit_id"].astype(str)
    metadata["original_name"] = metadata["original_name"].astype(str)
    return metadata


def discover_aligned_rate_inputs(config: dict, paths: dict, cfg: dict) -> list[Path]:
    input_dir_raw = cfg.get("input_dir")
    input_dir = resolve_path(paths["root_dir"], input_dir_raw) if input_dir_raw else paths["nex_aligned_rate_dir"]
    pattern = str(cfg.get("input_pattern", DEFAULTS["input_pattern"]))
    candidates = [
        path
        for path in sorted(input_dir.glob(pattern))
        if path.is_file() and "no_light_skipped" not in path.name and "PreLightPostSummary" not in path.name
    ]
    if not candidates:
        raise FileNotFoundError(f"No reconstructed light-aligned rate CSV files matched {input_dir / pattern}.")
    return candidates


def load_aligned_rate_inputs(paths: list[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    file_to_sources: dict[str, set[str]] = {}
    for path in paths:
        frame = read_table(path).copy()
        required = {"unit_id", "aligned_time_s", "firing_rate_hz"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Aligned rate file {path} is missing required columns: {sorted(missing)}")
        if "file_id" not in frame.columns:
            frame["file_id"] = path.name.split("_LightAlignedRate_", 1)[0]
        frame["file_id"] = frame["file_id"].astype(str)
        frame["unit_id"] = frame["unit_id"].astype(str)
        frame["aligned_time_s"] = pd.to_numeric(frame["aligned_time_s"], errors="coerce")
        frame["firing_rate_hz"] = pd.to_numeric(frame["firing_rate_hz"], errors="coerce")
        frame["source_aligned_file"] = str(path)
        for file_id in frame["file_id"].dropna().unique():
            file_to_sources.setdefault(str(file_id), set()).add(str(path))
        frames.append(frame)
    duplicate_sources = {file_id: sources for file_id, sources in file_to_sources.items() if len(sources) > 1}
    if duplicate_sources:
        details = "; ".join(f"{file_id}: {sorted(sources)}" for file_id, sources in duplicate_sources.items())
        raise ValueError(f"Multiple aligned exports were discovered for the same file_id; narrow input_pattern to one export per file. {details}")
    return pd.concat(frames, ignore_index=True, sort=False)


def _select_and_collapse_trials(aligned: pd.DataFrame) -> pd.DataFrame:
    data = aligned.dropna(subset=["file_id", "unit_id", "aligned_time_s"]).copy()
    if "trial_id" not in data.columns:
        data["trial_id"] = ""
    if "aggregation" not in data.columns:
        data["aggregation"] = ""
    if "source_aligned_file" not in data.columns:
        data["source_aligned_file"] = ""
    data["trial_id"] = data["trial_id"].astype(str)
    data["aggregation"] = data["aggregation"].astype(str).str.lower()
    kept_frames: list[pd.DataFrame] = []
    for (_, _), unit_rows in data.groupby(["file_id", "unit_id"], sort=False):
        aggregated_mask = unit_rows["trial_id"].str.lower().eq("aggregated") | unit_rows["aggregation"].isin(
            {"mean", "median", "aggregated"}
        )
        selected = unit_rows[aggregated_mask] if aggregated_mask.any() else unit_rows
        kept_frames.append(selected)
    selected = pd.concat(kept_frames, ignore_index=True, sort=False)
    return (
        selected.groupby(["file_id", "unit_id", "aligned_time_s"], sort=False, as_index=False)
        .agg(firing_rate_hz=("firing_rate_hz", "mean"), source_aligned_file=("source_aligned_file", "first"))
    )


def _metadata_text(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _unit_metadata_row(file_id: str, source_unit_id: str, metadata: pd.DataFrame) -> dict:
    if metadata.empty:
        return {
            "analysis_unit_id": source_unit_id,
            "channel": "",
            "original_name": source_unit_id,
            "include_bool": True,
            "duplicate_of": "",
            "representative_unit": "",
            "exclusion_reason": "",
            "metadata_matched": False,
        }
    file_rows = metadata[metadata["file_id"].astype(str) == str(file_id)]
    matches = file_rows[(file_rows["unit_id"] == source_unit_id) | (file_rows["original_name"] == source_unit_id)]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous unit metadata for file_id={file_id!r}, aligned unit_id={source_unit_id!r}.")
    if matches.empty:
        return {
            "analysis_unit_id": source_unit_id,
            "channel": "",
            "original_name": source_unit_id,
            "include_bool": False,
            "duplicate_of": "",
            "representative_unit": "",
            "exclusion_reason": "not_in_unit_quality_table",
            "metadata_matched": False,
        }
    row = matches.iloc[0]
    return {
        "analysis_unit_id": str(row["unit_id"]),
        "channel": row.get("channel", ""),
        "original_name": _metadata_text(row.get("original_name", source_unit_id)) or source_unit_id,
        "include_bool": bool(row["include_bool"]),
        "duplicate_of": _metadata_text(row.get("duplicate_of", "")),
        "representative_unit": _metadata_text(row.get("representative_unit", "")),
        "exclusion_reason": _metadata_text(row.get("exclusion_reason", "")),
        "metadata_matched": True,
    }


def prepare_analysis_matrix(config: dict, aligned: pd.DataFrame, unit_metadata: pd.DataFrame | None = None) -> PreparedAnalysis:
    cfg = time_cluster_config(config)
    collapsed = _select_and_collapse_trials(aligned)
    if collapsed.empty:
        raise ValueError("No usable aligned rate rows remained after trial aggregation.")
    available_time = np.sort(collapsed["aligned_time_s"].dropna().unique().astype(float))
    if len(available_time) < 2 or np.any(np.diff(available_time) <= 0):
        raise ValueError("Aligned time axis must contain at least two strictly increasing bins.")
    differences = np.diff(available_time)
    median_step = float(np.median(differences))
    if not np.allclose(differences, median_step, rtol=1.0e-6, atol=max(1.0e-9, abs(median_step) * 1.0e-6)):
        raise ValueError("Aligned unit time axes do not share a common regular grid; shifted or inconsistent bins were detected.")
    aligned_cfg = config.get("aligned_rate", config.get("neuroexplorer", {}).get("aligned_rate", {}))
    expected_step = aligned_cfg.get("bin_width_s")
    if expected_step is not None and not math.isclose(
        median_step,
        float(expected_step),
        rel_tol=1.0e-6,
        abs_tol=max(1.0e-9, abs(float(expected_step)) * 1.0e-6),
    ):
        raise ValueError(
            f"Aligned time step {median_step:g} s does not match configured aligned_rate.bin_width_s={float(expected_step):g} s."
        )
    analysis_window, baseline_window, test_window = _validate_and_resolve_windows(config, cfg, available_time)
    analysis_mask_available = (available_time >= analysis_window[0]) & (available_time <= analysis_window[1])
    time_s = available_time[analysis_mask_available]
    tolerance = max(1.0e-9, abs(median_step) * 1.0e-6)
    baseline_mask = (time_s >= baseline_window[0] - tolerance) & (time_s < baseline_window[1] - tolerance)
    test_mask = (time_s >= test_window[0] - tolerance) & (time_s < test_window[1] - tolerance)

    metadata = pd.DataFrame() if unit_metadata is None else unit_metadata.copy()
    unit_rows: list[dict] = []
    for file_id, source_unit_id in collapsed[["file_id", "unit_id"]].drop_duplicates().itertuples(index=False):
        fields = _unit_metadata_row(str(file_id), str(source_unit_id), metadata)
        unit_rows.append(
            {
                "sample_id": f"{file_id}::{fields['analysis_unit_id']}",
                "file_id": str(file_id),
                "unit_id": fields["analysis_unit_id"],
                "source_unit_id": str(source_unit_id),
                **{key: value for key, value in fields.items() if key != "analysis_unit_id"},
            }
        )
    units = pd.DataFrame(unit_rows)
    if units["sample_id"].duplicated().any():
        duplicates = units.loc[units["sample_id"].duplicated(keep=False), "sample_id"].tolist()
        raise ValueError(f"Duplicate unit sample IDs are not allowed: {duplicates}")

    source_to_sample = units.set_index(["file_id", "source_unit_id"])["sample_id"].to_dict()
    collapsed["sample_id"] = [source_to_sample[(str(row.file_id), str(row.unit_id))] for row in collapsed.itertuples(index=False)]
    raw_frame = collapsed.pivot(index="sample_id", columns="aligned_time_s", values="firing_rate_hz")
    raw_frame = raw_frame.reindex(index=units["sample_id"], columns=time_s)
    raw = raw_frame.to_numpy(dtype=float)

    include_only = bool(cfg.get("include_only_unit_quality_include_yes", True))
    duplicate_policy = str(cfg.get("duplicate_policy", "exclude_duplicates")).strip().lower()
    if duplicate_policy not in {"keep_all", "exclude_duplicates", "keep_representative_only"}:
        raise ValueError("duplicate_policy must be keep_all, exclude_duplicates, or keep_representative_only.")
    minimum_valid = cfg.get("min_valid_bins_per_unit")
    minimum_valid = 1 if minimum_valid is None else int(minimum_valid)
    if minimum_valid < 1:
        raise ValueError("min_valid_bins_per_unit must be null or an integer >= 1.")

    baselines = np.full(len(units), np.nan, dtype=float)
    delta = np.full_like(raw, np.nan, dtype=float)
    included = np.ones(len(units), dtype=bool)
    reasons = np.asarray([""] * len(units), dtype=object)
    for index, row in units.iterrows():
        if include_only and not bool(row["include_bool"]):
            included[index] = False
            reasons[index] = row["exclusion_reason"] or "excluded_by_unit_quality_table"
        duplicate_of = str(row["duplicate_of"]).strip()
        representative = str(row["representative_unit"]).strip()
        if included[index] and duplicate_policy == "exclude_duplicates" and duplicate_of:
            included[index] = False
            reasons[index] = "duplicate_excluded"
        if included[index] and duplicate_policy == "keep_representative_only" and duplicate_of and representative != str(row["unit_id"]):
            included[index] = False
            reasons[index] = "duplicate_excluded"
        baseline_values = raw[index, baseline_mask]
        if np.isfinite(baseline_values).any():
            baselines[index] = float(np.nanmean(baseline_values))
            delta[index, :] = raw[index, :] - baselines[index]
        elif included[index]:
            included[index] = False
            reasons[index] = "baseline_window_no_valid_bins"
        valid_test_bins = int(np.count_nonzero(np.isfinite(delta[index, test_mask])))
        if included[index] and valid_test_bins < minimum_valid:
            included[index] = False
            reasons[index] = f"test_window_valid_bins_below_{minimum_valid}"

    units["baseline_hz"] = baselines
    units["n_valid_baseline_bins"] = np.sum(np.isfinite(raw[:, baseline_mask]), axis=1)
    units["n_valid_test_bins"] = np.sum(np.isfinite(delta[:, test_mask]), axis=1)
    units["constant_raw_trace"] = [
        bool(np.nanmax(row) == np.nanmin(row)) if np.isfinite(row).any() else False for row in raw
    ]
    units["included"] = included
    units["exclusion_reason"] = reasons
    if int(np.count_nonzero(included)) < 2:
        reason_counts = units.loc[~units["included"], "exclusion_reason"].value_counts().to_dict()
        raise ValueError(f"Temporal cluster permutation requires at least 2 included units; exclusions={reason_counts}.")

    matrix_rows: list[dict] = []
    for unit_index, unit in units.iterrows():
        for time_index, time_value in enumerate(time_s):
            matrix_rows.append(
                {
                    "sample_id": unit["sample_id"],
                    "file_id": unit["file_id"],
                    "unit_id": unit["unit_id"],
                    "source_unit_id": unit["source_unit_id"],
                    "channel": unit["channel"],
                    "original_name": unit["original_name"],
                    "time_s": float(time_value),
                    "raw_rate_hz": raw[unit_index, time_index],
                    "baseline_hz": baselines[unit_index],
                    "delta_rate_hz": delta[unit_index, time_index],
                    "included": bool(included[unit_index]),
                    "exclusion_reason": reasons[unit_index],
                }
            )
    return PreparedAnalysis(
        time_s=time_s,
        raw_rate_hz=raw[included, :],
        delta_rate_hz=delta[included, :],
        included_units=units[included].reset_index(drop=True),
        unit_summary=units.reset_index(drop=True),
        matrix_long=pd.DataFrame(matrix_rows),
        analysis_window_s=analysis_window,
        baseline_window_s=baseline_window,
        test_window_s=test_window,
        baseline_mask=baseline_mask,
        test_mask=test_mask,
    )


def _figure_extension(config: dict, cfg: dict) -> str:
    value = cfg.get("figure_format") or config.get("origin", {}).get("export_format", "png")
    extension = str(value).strip().lower().lstrip(".")
    if extension not in {"png", "svg", "pdf"}:
        raise ValueError("time_cluster_permutation.figure_format must be png, svg, or pdf.")
    return extension


def _shade_windows(axis, prepared: PreparedAnalysis) -> None:
    axis.axvspan(*prepared.baseline_window_s, color="#4C78A8", alpha=0.08, label="baseline window")
    axis.axvspan(*prepared.test_window_s, color="#F58518", alpha=0.08, label="test window")
    axis.axvline(0.0, color="black", linestyle="--", linewidth=1.0, label="stimulus")


def save_figures(
    prepared: PreparedAnalysis,
    result: ClusterPermutationResult,
    output_dir: Path,
    extension: str,
    dpi: int,
) -> dict[str, Path]:
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "heatmap": figure_dir / f"unit_time_delta_rate_heatmap.{extension}",
        "mean_curve": figure_dir / f"population_mean_delta_rate.{extension}",
        "t_statistic": figure_dir / f"temporal_t_statistic.{extension}",
    }

    finite_delta = prepared.delta_rate_hz[np.isfinite(prepared.delta_rate_hz)]
    color_limit = float(np.max(np.abs(finite_delta))) if finite_delta.size else 1.0
    color_limit = color_limit if color_limit > 0 else 1.0
    figure, axis = plt.subplots(figsize=(10, max(4.0, 0.22 * len(prepared.included_units) + 2.0)))
    image = axis.imshow(
        prepared.delta_rate_hz,
        aspect="auto",
        interpolation="nearest",
        cmap="RdBu_r",
        vmin=-color_limit,
        vmax=color_limit,
        extent=[prepared.time_s[0], prepared.time_s[-1], len(prepared.included_units) - 0.5, -0.5],
    )
    _shade_windows(axis, prepared)
    axis.set_title("Unit-level baseline-corrected firing rate")
    axis.set_xlabel("Stimulus-aligned time (s)")
    axis.set_ylabel("Independent unit")
    if len(prepared.included_units) <= 40:
        axis.set_yticks(np.arange(len(prepared.included_units)))
        axis.set_yticklabels(prepared.included_units["sample_id"].tolist(), fontsize=7)
    figure.colorbar(image, ax=axis, label="Delta firing rate (Hz)")
    figure.tight_layout()
    figure.savefig(paths["heatmap"], dpi=dpi)
    plt.close(figure)

    means = np.nanmean(prepared.delta_rate_hz, axis=0)
    sem = np.full(len(prepared.time_s), np.nan, dtype=float)
    for index in range(len(prepared.time_s)):
        column = prepared.delta_rate_hz[:, index]
        column = column[np.isfinite(column)]
        if len(column) >= 2:
            sem[index] = float(np.std(column, ddof=1) / math.sqrt(len(column)))
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.plot(prepared.time_s, means, color="#1F77B4", linewidth=2.0, label="unit mean")
    axis.fill_between(prepared.time_s, means - sem, means + sem, color="#1F77B4", alpha=0.2, label="unit-level SEM")
    axis.axhline(0.0, color="0.35", linewidth=0.8)
    _shade_windows(axis, prepared)
    lower, upper = axis.get_ylim()
    bar_y = lower + 0.04 * (upper - lower)
    for row in result.clusters.itertuples(index=False):
        if row.significant:
            color = "#D62728" if row.direction == "positive" else "#2CA02C"
            axis.plot([row.start_time_s, row.end_time_s], [bar_y, bar_y], color=color, linewidth=5, solid_capstyle="butt")
    axis.set_title("Population baseline-corrected firing rate (unit-level mean ± SEM)")
    axis.set_xlabel("Stimulus-aligned time (s)")
    axis.set_ylabel("Delta firing rate (Hz)")
    axis.legend(loc="best", fontsize=8)
    figure.tight_layout()
    figure.savefig(paths["mean_curve"], dpi=dpi)
    plt.close(figure)

    stats = result.time_statistics
    figure, axis = plt.subplots(figsize=(10, 5))
    axis.plot(stats["time_s"], stats["t_value"], color="#333333", linewidth=1.6)
    axis.axhline(result.threshold, color="#D62728", linestyle="--", label="cluster-forming threshold")
    axis.axhline(-result.threshold, color="#D62728", linestyle="--")
    axis.axhline(0.0, color="0.55", linewidth=0.8)
    _shade_windows(axis, prepared)
    for row in result.clusters.itertuples(index=False):
        color = "#D62728" if row.direction == "positive" else "#2CA02C"
        axis.axvspan(row.start_time_s, row.end_time_s, color=color, alpha=0.22 if row.significant else 0.07)
    axis.set_title("One-sample t statistic and temporal clusters")
    axis.set_xlabel("Stimulus-aligned time (s)")
    axis.set_ylabel("t statistic")
    axis.legend(loc="best", fontsize=8)
    figure.tight_layout()
    figure.savefig(paths["t_statistic"], dpi=dpi)
    plt.close(figure)
    return paths


def _software_metadata() -> dict:
    repository_root = Path(__file__).resolve().parents[1]
    version_path = repository_root / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "unknown"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    return {"neurotrain_version": version, "git_commit": commit}


def run_time_cluster_permutation(config: dict, logger: PipelineLogger) -> dict:
    cfg = time_cluster_config(config)
    if not bool(cfg.get("enabled", False)):
        logger.log("time_cluster_permutation", "*", "", "", "skipped", "time_cluster_permutation.enabled=false")
        print("Time cluster permutation skipped: time_cluster_permutation.enabled=false")
        return {"status": "skipped"}
    if str(cfg.get("statistic", "one_sample_t")) != "one_sample_t":
        raise ValueError("Only statistic=one_sample_t is supported in this unit-level branch.")
    paths = resolve_project_paths(config)
    input_paths = discover_aligned_rate_inputs(config, paths, cfg)
    aligned = load_aligned_rate_inputs(input_paths)
    unit_metadata = _load_unit_metadata(config, paths)
    prepared = prepare_analysis_matrix(config, aligned, unit_metadata)
    test_values = prepared.delta_rate_hz[:, prepared.test_mask]
    test_time = prepared.time_s[prepared.test_mask]
    result = temporal_cluster_permutation_test(
        test_values,
        test_time,
        cluster_forming_alpha=float(cfg["cluster_forming_alpha"]),
        cluster_alpha=float(cfg["cluster_alpha"]),
        n_permutations=int(cfg["n_permutations"]),
        tail=int(cfg["tail"]),
        seed=int(cfg["seed"]),
        max_exact_permutations=int(cfg["max_exact_permutations"]),
    )
    output_dir = resolve_path(paths["statistics_dir"], str(cfg["output_subdir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    cluster_path = output_dir / "cluster_table.csv"
    time_stats_path = output_dir / "time_bin_statistics.csv"
    matrix_path = output_dir / "unit_time_analysis_matrix.csv"
    unit_summary_path = output_dir / "unit_summary.csv"
    null_path = output_dir / "null_max_cluster_mass.csv"
    metadata_path = output_dir / "analysis_metadata.json"
    write_table(result.clusters, cluster_path)
    all_time_stats = pd.DataFrame(
        {
            "time_s": prepared.time_s,
            "in_baseline_window": prepared.baseline_mask,
            "in_test_window": prepared.test_mask,
        }
    ).merge(result.time_statistics, on="time_s", how="left")
    write_table(all_time_stats, time_stats_path)
    write_table(prepared.matrix_long, matrix_path)
    write_table(prepared.unit_summary, unit_summary_path)
    write_table(pd.DataFrame({"max_cluster_mass": result.null_max_cluster_mass}), null_path)
    extension = _figure_extension(config, cfg)
    figure_paths = save_figures(
        prepared,
        result,
        output_dir,
        extension,
        int(config.get("origin", {}).get("dpi", 300)),
    )
    excluded = prepared.unit_summary[~prepared.unit_summary["included"]]
    metadata = {
        "sample_unit": "one independent unit/channel",
        "n_units_read": int(len(prepared.unit_summary)),
        "n_units_included": int(len(prepared.included_units)),
        "n_units_excluded": int(len(excluded)),
        "excluded_reason_counts": excluded["exclusion_reason"].value_counts().to_dict(),
        "n_analysis_time_bins": int(len(prepared.time_s)),
        "n_test_time_bins": int(np.count_nonzero(prepared.test_mask)),
        "analysis_window_s": list(prepared.analysis_window_s),
        "baseline_window_s": list(prepared.baseline_window_s),
        "test_window_s": list(prepared.test_window_s),
        "time_unit": "seconds",
        "rate_unit": "Hz",
        "statistic": "one_sample_t",
        "tail": int(cfg["tail"]),
        "cluster_forming_alpha": float(cfg["cluster_forming_alpha"]),
        "cluster_forming_threshold": result.threshold,
        "cluster_alpha": float(cfg["cluster_alpha"]),
        "permutation_method": result.permutation_method,
        "n_permutations": result.n_permutations,
        "seed": int(cfg["seed"]),
        "n_significant_clusters": int(result.clusters["significant"].sum()) if not result.clusters.empty else 0,
        "input_files": [str(path) for path in input_paths],
        "include_in_pptx": False,
        "inference_note": (
            "Units are the exchange units. Dependence among units from the same animal or session is not modeled, "
            "so results do not imply an animal-level population effect. Cluster-level significance does not make "
            "each time bin independently significant, and cluster boundaries are not precise physiological onset times."
        ),
        **_software_metadata(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    if bool(cfg.get("include_in_pptx", False)):
        logger.log(
            "time_cluster_permutation",
            "*",
            "",
            str(output_dir),
            "warning",
            "include_in_pptx=true was requested, but the current PPTX generator has no isolated plugin interface; independent figures were written only.",
        )
    summary = {
        "status": "success",
        "output_dir": str(output_dir),
        "cluster_table": str(cluster_path),
        "time_statistics": str(time_stats_path),
        "matrix": str(matrix_path),
        "unit_summary": str(unit_summary_path),
        "metadata": str(metadata_path),
        "figures": {name: str(path) for name, path in figure_paths.items()},
        **metadata,
    }
    logger.log(
        "time_cluster_permutation",
        "*",
        ";".join(str(path) for path in input_paths),
        str(output_dir),
        "success",
        (
            f"Completed unit-level temporal cluster permutation. n_read={metadata['n_units_read']}; "
            f"n_included={metadata['n_units_included']}; n_excluded={metadata['n_units_excluded']}; "
            f"n_time_bins={metadata['n_test_time_bins']}; permutations={result.n_permutations}; "
            f"significant_clusters={metadata['n_significant_clusters']}"
        ),
    )
    print(f"Units read: {metadata['n_units_read']}")
    print(f"Units included/excluded: {metadata['n_units_included']}/{metadata['n_units_excluded']}")
    print(f"Analysis/test time bins: {metadata['n_analysis_time_bins']}/{metadata['n_test_time_bins']}")
    print(f"Baseline window (s): {prepared.baseline_window_s}")
    print(f"Test window (s): {prepared.test_window_s}")
    print(f"Permutations: {result.n_permutations} ({result.permutation_method})")
    print(f"Significant clusters: {metadata['n_significant_clusters']}")
    print(f"Output directory: {output_dir}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run unit-level one-dimensional temporal cluster permutation analysis.")
    parser.add_argument("--config", required=True, help="Path to the existing NeuroTrain YAML config.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config).expanduser().resolve())
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        result = run_time_cluster_permutation(config=config, logger=logger)
        return 0 if result.get("status") in {"success", "skipped"} else 1
    except Exception as exc:
        logger.log("time_cluster_permutation", "*", str(args.config), "", "failed", "Temporal cluster permutation failed.", exc)
        raise
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())
