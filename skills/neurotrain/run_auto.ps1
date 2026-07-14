param(
    [string]$Config = "config.yaml"
)

$ErrorActionPreference = "Stop"
python "$PSScriptRoot\run_pipeline.py" --config $Config
