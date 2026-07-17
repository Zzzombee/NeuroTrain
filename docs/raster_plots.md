# 原始脉冲 Raster 分支

## 用途与边界

该分支处理以下独立数据流：

```text
.pl2 -> NeuroExplorer -> Unit Train/Event CSV -> Python trial 对齐 -> raster + QC/manifest
```

Raster 使用原始 spike timestamps。它不读取主 `config.yaml`，不调用 rate reconstruction、figures、PPTX 或 time-cluster permutation，也不生成 PSTH、平滑曲线或统计推断。

## 输入契约

当前 Python 端只支持配置明确映射的 long CSV，不做模糊列名猜测。

Spike 表每行一个 spike，必需字段为：

```text
session_id, unit_id, timestamp
```

可选字段为 `channel_id`。Event 表每行一个事件，必需字段为：

```text
session_id, event_name, timestamp
```

可选 `trial_id`；未提供时按每个 session 内事件时间升序生成稳定 ID。unit 的稳定主键为 `session_id + unit_id`，同名 unit 不会跨 session 合并。两类表的时间单位必须一致，并由 `input.time_unit` 明确指定为 `seconds`、`milliseconds` 或 `minutes`；载入时只换算一次到秒。

解析器保留重复 timestamp，不静默去重；非数值或无穷 timestamp 会报错。逆序 spike 会在内存中稳定排序，并在 manifest 记录检测数量。缺少配置的 alignment event 时直接报错，不推测固定 trial 间隔，也不生成整段 session 的伪 raster。

## NeuroExplorer 导出要求

`raster_run.py` 使用 NeuroExplorer Python API 的 `NexDoc.NeuronVars()` 和 `NexVar.Timestamps()` 读取原始 spike timestamps，不应用 RateHist 模板，也不修改既有 RateHist Macro 或默认流程。该适配器已在本机真实 `.pl2` 项目完成一次导出核对。

自动导出要求项目已有：

1. `00_raw_pl2/*.pl2`。
2. `02_stim_events/stim_schedule_master.xlsx`，含 file、alignment event 和刺激持续时间。
3. `01_sorting_info/unit_quality_table.xlsx`，含 `original_name`、`unit_id` 和 `include`。

只有字面值 `include=yes` 的 unit 会导出。无 alignment event 的 recording 会进入 NeuroExplorer export manifest 的 exclusions。导出时间单位固定为秒，输入写入 `03_nex_exports/raster_input/`。

不要提交真实 `.pl2`、大型导出文件或批量 raster 图片。

## 独立配置

模板位于 `config/raster_config.yaml`。运行前至少修改路径和实际字段映射：

```yaml
schema_version: 1
paths:
  input_root: "D:/Data/project/neuroexplorer_raster_exports"
  output_root: "D:/Data/project/neurotrain_outputs"
  spike_table_glob: "**/*unit_train*.csv"
  event_table_glob: "**/*events*.csv"
  output_subdir: "raster"
input:
  format: "neuroexplorer_long_csv"
  delimiter: null
  encoding: "utf-8-sig"
  time_unit: "seconds"
  columns:
    session_id: "session_id"
    unit_id: "unit_id"
    channel_id: "channel_id"
    spike_time: "timestamp"
    event_name: "event_name"
    event_time: "timestamp"
    trial_id: null
    stimulus_duration: "stimulus_duration_s"
alignment:
  event_name: "Light_On"
  window_s: [-120.0, 360.0]
  boundary: "left_closed_right_open"
  trial_order: "event_time"
  minimum_inter_event_interval_s: null
  overlapping_windows: "allow"
  missing_event: "error"
  light_off_event_name: null
  fixed_stimulus_duration_s: null
trial_filter:
  first_trial: null
  last_trial: null
  include_trial_ids: null
  exclude_trial_ids: []
plot:
  formats: ["png"]
  dpi: 300
  figsize_inches: [10.0, 6.0]
  combined_width_inches: 12.0
  combined_row_height_inches: 0.45
  combined_min_height_inches: 4.0
  spike_color: "black"
  spike_linewidth: 0.6
  spike_height_fraction: 0.8
  alignment_line_color: "red"
  alignment_linewidth: 1.0
  show_alignment_line: true
  transparent_background: false
output:
  write_individual_figures: true
  write_combined_figure: true
  combined_filename: "project_combined_raster"
  write_combined_row_map_csv: true
  write_trial_summary_csv: true
  write_unit_summary_csv: true
  write_exclusion_csv: true
  write_aligned_spikes_long_csv: false
  write_manifest_json: true
  overwrite: false
runtime:
  fail_on_empty_unit: false
  continue_on_unit_error: true
```

Event 表的 `stimulus_duration_s` 优先控制每个 trial 的真实光照终点。所有光照带均从相对时间 `t=0` 开始，因此持续时间不同的 unit/trial 仍保持 onset 对齐。缺少逐 trial 时长时才回退到正数 `fixed_stimulus_duration_s`。`light_off_event_name` 当前保留为契约字段但尚未实现逐 trial off-event 配对，应保持为 `null`。

## 运行

标准项目的一键入口：

```powershell
python raster_run.py --project-dir "D:\Data\my_ephys_project" --init-only
python raster_run.py --project-dir "D:\Data\my_ephys_project"
python raster_run.py --project-dir "D:\Data\my_ephys_project" --overwrite
```

第一条只初始化独立配置和输入目录；第二条完成 NeuroExplorer 导出与全部输出；第三条用于显式覆盖重跑。无需主 `config.yaml`。如已有符合契约的 CSV，可使用 `--skip-export`。

仅运行解析/绘图时，先校验配置和输入，再正式生成：

```powershell
python raster_plot.py --config config/raster_config.yaml --validate-only
python raster_plot.py --config config/raster_config.yaml
```

可用 `--session` 和 `--unit` 限定单个对象。`--validate-only` 不写输出，也不受已有输出冲突影响。正式运行中 `overwrite: false` 会在任何目标文件已存在时停止，不静默覆盖。

## Trial 与图形语义

每个匹配事件 occurrence 是一个 trial，`relative_spike_time = spike_time - event_time`。默认窗口 `[start, end)` 包含左端点和 `x=0`，排除右端点。trial 按事件时间升序，trial 1 在图顶部；空 trial 保留为空行。

`overlapping_windows: allow` 时，同一绝对 spike 可以进入多个 trial，manifest 记录被标记的 trial 数。设为 `error` 时，只要事件间隔小于窗口长度就停止，并报告最短间隔和窗口长度。`minimum_inter_event_interval_s` 可另外设置实验设计要求的最小事件间隔。

整个 unit 在窗口内无 spike 时，默认仍输出空图；`fail_on_empty_unit: true` 时排除并记录原因。找不到某个 spike session 的对齐事件时整次运行失败。绘图使用批量 tick，不把不同 unit 叠加。

## 输出

```text
<output_root>/raster/
├── figures/<session_id>/<unit_id>_raster.png
├── figures/project_combined_raster.png
├── tables/combined_row_map.csv
├── tables/unit_summary.csv
├── tables/trial_summary.csv
├── tables/exclusions.csv
├── tables/aligned_spikes_long.csv   # 仅配置启用时
├── manifest.json
└── raster.log
```

combined 图中所有 unit 共用相对时间横轴，按稳定的 `session × unit` 顺序自上而下排列；多 trial unit 使用连续子行，并在 unit 中点显示一个标签。`combined_row_map.csv` 记录每个图行对应的 session、unit、trial、事件时间、刺激持续时间和 spike 数。

summary 记录源 spike/event 文件、trial/spike 数、空 trial、状态和图路径。manifest 记录解析后配置、输入到 session 映射、单位换算、边界/重叠策略、combined 路径、原始 ID 到安全文件名的图映射、软件版本、git commit、QC 计数与警告。表格、manifest、日志和图片均采用临时文件替换方式写入。

## 真实数据人工验收

新项目首次使用时仍应完成一次人工核对：

1. 任选一个 unit，确认 NeuroExplorer 总 spike 数与 `unit_summary.csv` 一致。
2. 任选 2–3 个事件，手算若干 `spike - event` 并与可选 `aligned_spikes_long.csv` 对照。
3. 确认筛选后的 trial 数等于事件 occurrence 数，空 trial 未被压缩。
4. 确认 session/unit 未串组，`x=0` 与事件对齐，文件名可由 manifest 反查原 ID。
5. 确认只有真实 off-event 配对实现或明确固定时长时才显示刺激区间。
6. 确认 summary、manifest 和实际图数量一致。
