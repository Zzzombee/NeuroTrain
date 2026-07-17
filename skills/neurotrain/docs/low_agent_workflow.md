# Low / No Agent Workflow

This workflow does not require Codex at runtime.

## Full Run

```powershell
.\run_auto.ps1 -Config config.yaml
```

Equivalent Python command:

```powershell
python run_pipeline.py --config config.yaml
```

## Validate Only

```powershell
.\run_validate_only.ps1 -Config config.yaml
```

## Rebuild Statistics From Existing Fullrate CSV

```powershell
.\run_stats.ps1 -Config config.yaml
```

## Independent Aligned-Rate Branches

Ordinary figures, PPTX, and PreLightPost summaries (original center-subtraction
bin definition):

```powershell
python build_aligned_rate_from_fullrate.py --config config.yaml
```

This command requires a reviewed `unit_quality_table`. It keeps all Units in the aligned-rate intermediate CSV, filters `PreLightPostSummary` to literal `include: yes`, and writes cohort CSV/JSON metadata. Missing tables, unmatched Units, or an empty eligible cohort fail explicitly.

Time-cluster boundary-aligned reconstruction and analysis:

```powershell
python build_time_cluster_aligned_rate.py --config config.yaml
python time_cluster_permutation.py --config config.yaml
```

The dedicated builder also retains all Units. The permutation command applies the same strict quality-table cohort and records discovered/included/excluded counts plus the effective duplicate policy.

The second branch reads only
`03_nex_exports/time_cluster_aligned_rate/*_TimeClusterAlignedRate_*.csv`; it
does not depend on normal `LightAlignedRate` files. Both commands are regular
terminal entry points and require no Codex/Agent runtime.

## Manual NeuroExplorer Fallback

1. Open the PL2 file in NeuroExplorer.
2. Run or apply the `RateHist_FullSession` template.
3. Export numerical results as CSV.
4. Place the CSV under `03_nex_exports/fullrate/`.
5. Run:

```powershell
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module build_pptx
```

