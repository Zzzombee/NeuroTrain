# Changelog

## Unreleased

### Added

- Optional OriginPro OPJU archive output in the stable `export_figures` stage when `origin.save_opju: true`.
- OPJU config template fields for single-project archive output, imported data workbook selection, and graph page selection.

### Fixed

- Documentation now states that OPJU generation is disabled by default and does not affect PNG/PPTX output unless explicitly enabled.

## v0.2.0-managed - 2026-05-11

### Added

- Stable `fullrate_aligned` workflow.
- Filename-based `stim_schedule_master` generation.
- `unit_quality_table` auto build.
- No-light file support.
- Dynamic aligned window: absolute `[light_on - 60 s, light_off + 60 s]`; aligned `[-60 s, duration + 60 s]`.
- Pre/light/post statistics.
- Python/matplotlib QC figures.
- PPTX build.
- Low-agent PowerShell workflow scripts.
- Release and troubleshooting documentation.

### Changed

- Default plotting now uses Python/matplotlib QC figures.
- `export_figures` is the preferred plotting module name.
- `origin_plot` is retained only as a compatibility alias.
- `07_statistics/` is part of the stable output layout.
- Real data and generated outputs are excluded through `.gitignore`.

### Deprecated / Paused

- Origin-ready package generation.
- OGS generation.
- OPJU auto generation.
- pywinauto/SendKeys Origin automation.
- External OriginPro execution verification.

### Fixed

- Default workflow no longer depends on `originpro`, COM, pywin32, pywinauto, or OriginPro.
- `init_project` does not automatically move raw `.pl2` files.

### Known Limitations

- NeuroExplorer `.pl2` auto-open can still require active-document or manual CSV fallback depending on local NeuroExplorer/NEX behavior.
- Origin final figure editing is manual and outside the stable pipeline.
- The current skill directory is not yet tracked by a valid Git commit in the parent repository, so release tags require Git setup first.
