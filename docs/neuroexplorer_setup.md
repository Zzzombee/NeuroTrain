# NeuroExplorer Setup

Use a `RateHist_FullSession` template for the stable workflow.

Recommended template settings:

- full-session rate histogram
- bin width: `1 s` unless changed in `config.yaml`
- units: spikes per second
- selected variables: sorted neuron variables to analyze
- no required `Light_On`
- no required `Light_Off`
- no required `Light_Interval`

The stable workflow reconstructs light-aligned traces in Python from full-session rate exports and the filename-derived stimulation schedule.

If automatic `.pl2` opening is unreliable, open files manually in NeuroExplorer and export fullrate CSVs into `03_nex_exports/fullrate/` using the expected file naming pattern in `config.yaml`.

