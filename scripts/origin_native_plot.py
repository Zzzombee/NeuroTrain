from __future__ import annotations

import argparse
from pathlib import Path

from scripts.origin_native.build_origin_manifest import build_origin_manifest
from scripts.origin_native.originpro_runner import run_origin_native_manifest
from utils.logging_utils import PipelineLogger
from utils.path_utils import load_yaml, resolve_project_paths


def _origin_cfg(config: dict) -> dict:
    cfg = dict(config.get("origin", {}))
    cfg.setdefault("enabled", True)
    cfg.setdefault("backend", "matplotlib_png")
    return cfg


def origin_native_plot(config: dict, logger: PipelineLogger) -> dict:
    origin_cfg = _origin_cfg(config)
    if not origin_cfg.get("enabled", True):
        logger.log("origin_native_plot", "*", "", "", "skipped", "origin.enabled=false")
        return {"status": "skipped", "manifest_rows": 0}

    manifest_df = build_origin_manifest(config=config, logger=logger)
    result = {
        "status": "manifest_only",
        "manifest_rows": len(manifest_df),
        "runner": None,
    }
    if origin_cfg.get("backend") in {"origin_native", "both"}:
        runner_result = run_origin_native_manifest(config=config, logger=logger)
        result["runner"] = runner_result
        result["status"] = runner_result.get("status", "skipped")
    else:
        logger.log(
            "origin_native_plot",
            "*",
            str(resolve_project_paths(config)["origin_native_manifest_path"]),
            "",
            "skipped",
            "Native Origin runner skipped because origin.backend is not origin_native/both. Manifest was still generated for manual Origin import.",
            event="run_manifest",
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally run native OriginPro plotting from pipeline CSV outputs.")
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(Path(args.config))
    logger = PipelineLogger(resolve_project_paths(config)["logs_dir"])
    try:
        origin_native_plot(config=config, logger=logger)
        return 0
    finally:
        logger.save()


if __name__ == "__main__":
    raise SystemExit(main())

