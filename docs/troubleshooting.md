# Troubleshooting

## NeuroExplorer Cannot Open PL2

Use the manual fallback: open the file in NeuroExplorer, export full-session rate CSV, and place it in `03_nex_exports/fullrate/`.

## Active Document Fallback

If the NEX package cannot open a file but NeuroExplorer already has the document open, use the active document/manual CSV route and continue from `aligned_rate`.

## Missing Fullrate CSV

Check `99_logs/error_log.xlsx` and confirm the expected filename pattern:

```text
{file_id}_FullRate_bin{bin_width_s}s.csv
```

## File ID Mismatch

Confirm `stim_schedule_master.xlsx`, `unit_quality_table.xlsx`, and fullrate CSV filenames use the same `file_id`.

## No-Light Files

No-light files intentionally skip light-aligned event outputs. Full-session figures can still be generated.

## PPTX Missing Figures

Run:

```powershell
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module build_pptx
```

## Statistics Missing

Run:

```powershell
python run_pipeline.py --config config.yaml --module aligned_rate
```

Statistics are written to `07_statistics/` and aligned summaries are also kept under `03_nex_exports/aligned_rate/`.

## Origin / OPJU

Origin/OPJU automation is paused and not a stable workflow requirement. Use Python/matplotlib QC figures and PPTX output.

