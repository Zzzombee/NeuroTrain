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

