# NeuroExplorer Template Setup

## 1. Recommended Main Template

The recommended workflow is now:

- `analysis.mode: fullrate_aligned`
- NeuroExplorer template: `RateHist_FullSession`

This route does not require `Light_On` or `Light_Interval` inside NeuroExplorer.

Suggested base settings:

- full-session firing-rate analysis over the whole recording
- start from recording time `0`
- `Bin = 1`
- `Histogram Units = Spikes per second`
- no reference event required

## 2. Experimental / Legacy Event Setup

If you intentionally use the older NeuroExplorer-side PSTH workflow and the `.pl2` file does not already contain clean light event markers:

1. Prepare separate CSV files for:
   - `Light_On`
   - `Light_Off`
2. Import them into NeuroExplorer as separate event variables.

The skill-generated helper event files are:

- headerless
- single-column
- one timestamp per line
- seconds as the unit

The skill-generated interval file is:

- first line = interval variable name
- no standard header such as `start,end`
- two-column data rows after the first line
- comma-separated
- one interval per line as `light_on_s,light_off_s`

Example `02_Light_On.txt`:

```text
120
```

Example `02_Light_Off.txt`:

```text
135
```

Example `02_Light_Interval.csv`:

```text
Light_Interval
120,135
```

When importing:

- import `02_Light_Interval.csv` with `Data -> Add Interval Variable from .csv File...`
- confirm or set the NeuroExplorer interval variable name to `Light_Interval`
- create `Light_On` with `Data -> Add Event or Neuron Variable...` and paste timestamps from `02_Light_On.txt`
- create `Light_Off` with `Data -> Add Event or Neuron Variable...` and paste timestamps from `02_Light_Off.txt`

Important:

- Do not merge `Light_On` and `Light_Off` into one event.
- Do not import the single-column `Light_On.txt` file as an Interval Variable, or NeuroExplorer will report `file contains no commas`.
- Do not replace the first line of the interval CSV with `start,end` or `light_on_s,light_off_s`.
- PSTH reference event should be `Light_On`.

Experimental interval-mode workflow:

1. `Data -> Add Interval Variable from .csv File...`
   - file: `{file_id}_Light_Interval.csv`
   - variable name: `Light_Interval`
2. `Data -> Add Event or Neuron Variable...`
   - variable name: `Light_On`
   - paste the timestamps from `{file_id}_Light_On.txt`
3. Use `Light_On` as the PSTH template Reference Event.
4. Use `Light_Interval` for full-session light-band shading and interval-aware metadata.

## 3. Experimental PSTH Template

Suggested template name:

- `PSTH_LightOn`

Suggested base settings:

- `Reference = Light_On`
- `X Minimum = -60`
- `X Maximum = 75`
- `Bin = 1`
- `Histogram Units = Spikes per second`

## 4. Save Templates

Save the templates in NeuroExplorer so that:

- the PSTH template name matches `config.yaml`
- the full-rate template name matches `config.yaml`

## 5. Confirm Template Names Match `config.yaml`

Check:

- `neuroexplorer.templates.psth_template_name`
- `neuroexplorer.templates.fullrate_template_name`
- `neuroexplorer.templates.raster_template_name`
- `neuroexplorer.fullrate.template_name`

Mismatch here is one of the most common causes of failed `ApplyTemplate` calls.

## 6. Common Errors

- `Template RateHist_FullSession was not found`
  - Fix: create and save the full-session rate histogram template before running `fullrate_aligned`.
- `Light_On` does not exist
  - Fix: only relevant for the experimental PSTH workflow; import or create a dedicated `Light_On` event variable.
- `Light_On` and `Light_Off` are mixed into one event
  - Fix: keep them separate.
- PSTH shading incorrectly uses `120-135 s`
  - Fix: PSTH is event-aligned, so the shaded interval should be `0-15 s` for a 15-second light pulse.
- Full-session shading incorrectly uses `0-15 s`
  - Fix: full-session plots use absolute time, so the shaded interval should be `120-135 s` in that example.
- `Histogram Units` is not `Spikes per second`
  - Fix: set the NeuroExplorer template accordingly.
- Bin width is inconsistent
  - Fix: match GUI template settings to `config.yaml`.
- `unit_quality_table.original_name` does not match the NeuroExplorer variable name
  - Fix: use the exact spike variable names from NeuroExplorer for included units.
