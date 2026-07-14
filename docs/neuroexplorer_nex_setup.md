# NeuroExplorer `nex` Backend Setup

## 1. Install `nex`

```powershell
python.exe -m pip install -U nex
```

## 2. Enable External Python in NeuroExplorer

In NeuroExplorer, enable:

`Script | Enable Running Python Scripts in External Editor`

Without this, the official `nex` package workflow is typically unavailable from external Python.

## 3. Level 1 Test

```python
import nex
doc = nex.GetActiveDocument()
print(doc)
```

Expected behavior:

- If NeuroExplorer is open and a document is active, `doc` should be a live document object.
- If there is no active document, the skill records a warning instead of crashing.

## 4. Level 2 Test

1. Manually open a `.pl2` file in NeuroExplorer.
2. Run the skill's `neuroexplorer_export` module or a direct smoke test.
3. Confirm the log contains:
   - active document detection
   - `nex` API dump generation
   - variable/event listing attempts

If you use interval mode:

1. Import `{file_id}_Light_Interval.csv` with:
   - `Data -> Add Interval Variable from .csv File...`
   - first line should be the variable name, for example `Light_Interval`
2. Do not use `{file_id}_Light_On.txt` as an interval file.
   - NeuroExplorer will report `file contains no commas`
3. Create `Light_On` separately for PSTH reference if it is not already present.

Current preferred validation step:

```powershell
python scripts/smoke_tests/test_nex_create_interval_event.py --config config.yaml --file-id test07 --active-doc
```

This probe:

- reads `{file_id}_Light_Interval.csv`
- tests `AddInterval`
- tests `AddTimestamp`
- writes a full trace to `99_logs/nex_interval_event_creation_probe.txt`

Second-round variable-object probe:

```powershell
python scripts/smoke_tests/test_nex_create_var_objects.py --config config.yaml --file-id test07 --active-doc
```

This probe:

- inspects `doc.IntervalVars` / `doc.EventVars`
- tests `doc["name"]` access for existing variables such as `AllFile`
- tests how to create empty interval/event variables
- tests whether `nex.AddInterval(var, start, end)` and `nex.AddTimestamp(var, time)` work after object creation
- writes a full trace to `99_logs/nex_var_object_creation_probe.txt`

## Recommended template for the main workflow

Use `analysis.mode: fullrate_aligned`.

Create this NeuroExplorer template:

- `RateHist_FullSession`

Recommended settings:

- full recording from `t=0`
- `Bin = 1`
- `Histogram Units = Spikes per second`
- no reference event

In the main workflow:

- `Light_On` is not required in NeuroExplorer
- `Light_Interval` is not required in NeuroExplorer
- alignment is reconstructed later from `stim_schedule_master`

## 5. Level 3 Template Requirements

Create a PSTH template in NeuroExplorer, for example `PSTH_LightOn`.

Base settings:

- `Reference = Light_On`
- `X Minimum = -60`
- `X Maximum = 75`
- `Bin = 1`
- `Histogram Units = Spikes per second`

The skill will try to refine these settings with `nex.ModifyTemplate`, but only by using parameter names that exist in the current GUI.
`Light_Interval` is not assumed to be a valid PSTH reference unless your local NeuroExplorer build has been explicitly verified for that workflow.

Recommended priority:

1. For the recommended workflow, create and verify `RateHist_FullSession` first.
2. Treat `PSTH_LightOn` as experimental / legacy unless you specifically need NeuroExplorer-side PSTH.
3. Do not auto-assume a single interval variable is your light interval unless you explicitly enable that behavior.

## 5B. `fullrate_aligned` Mode

Create a full-session template in NeuroExplorer:

- `RateHist_FullSession`
- full recording from `t=0`
- `Bin = 1`
- `Histogram Units = Spikes per second`
- no reference event required

Then set:

- `analysis.mode: fullrate_aligned`

In this mode:

- the skill does not require `Light_On`
- the skill does not require `Light_Interval`
- the skill exports a full-session rate histogram and reconstructs the aligned trace in Python from `stim_schedule_master.xlsx`

## 6. Copy Properties Panel Parameter Names Exactly

`nex.ModifyTemplate` requires the exact parameter label shown in the Properties Panel left column.

Examples:

- use `Bin (sec)` instead of guessing `Bin`
- use `X Min (sec)` or `X Minimum` only if those are the actual labels in your build

Practical method:

1. Select the template property in NeuroExplorer
2. Copy the property label with `Ctrl+C`
3. Paste it into `config.yaml` under `neuroexplorer.templates.parameter_names`

## 7. Level 4 Export Behavior

For PSTH, the backend now tries this order:

1. `nex.SaveNumResults(doc, path)`
2. `nex.SaveResults(doc, path)` as fallback

The preferred raw output is saved as `*_raw.txt`, then the skill tries to normalize it into the standard PSTH CSV.

If raw PSTH parsing does not work for your local NeuroExplorer numerical-results format:

- the raw file is kept
- a warning is logged
- you can inspect the raw file and adapt the parser later

If not:

1. The skill still runs smoke test, template configuration attempts, and template execution.
2. It records what `nex` functions were actually available.
3. It tells you exactly where to manually export CSV:
   - `03_nex_exports/psth/...`
   - `03_nex_exports/fullrate/...`
4. After the CSV files exist, the rest of the pipeline can continue automatically.

If you see:

`Template FullRate was not found in NeuroExplorer`

then either:

- create and save a `FullRate` template in the NeuroExplorer GUI
- or set `neuroexplorer.fullrate.enabled=false`
