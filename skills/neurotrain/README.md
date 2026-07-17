# NeuroTrain

NeuroTrain 是一套事件对齐的脉冲序列分析工作流，用于处理全时段放电率、类 PSTH 曲线、栅格图、统计结果以及可直接用于汇报的演示文稿。

当前稳定流程为：

```text
.pl2
-> NeuroExplorer RateHist_FullSession
-> SaveNumResults 全时段放电率
-> Python 重建光刺激对齐曲线
-> 图像
-> PPTX
```

## 开发源目录

本技能只在工作区根目录中维护。用户级 Codex `neurotrain` 技能目录应是指向本项目目录的 Windows junction，不应维护另一份独立副本。今后的代码、测试、配置模板和文档修改均应在工作区根目录完成，以保证项目源码与 Codex 技能始终同步。

## 推荐流程

1. 准备 `.pl2` 文件。
2. 根据文件名自动创建或更新 `stim_schedule_master`，必要时再手工编辑。
3. 根据 `.pl2` 中的神经元名称自动创建或更新 `unit_quality_table`。
4. 在 NeuroExplorer 中创建 `RateHist_FullSession` 模板。
5. 运行 `fullrate_aligned` 流程。
6. 检查 `FullRate`、`AlignedRate` 和 `PreLightPost` 图像。
7. 检查 PPTX；如有需要，再检查 OriginPro OPJU 归档。

## 默认模式

```yaml
analysis:
  mode: "auto"

aligned_rate:
  pre_window_s: [-60, 0]
  light_window_s: [5, 20]
  post_window_s: [25, 85]
```

`auto` 优先选择 `fullrate_aligned`，并默认启用 `neuroexplorer.export_fullrate: true`。该模式：

- 不要求 NeuroExplorer 文档内存在 `Light_On` 或 `Light_Interval`。
- 使用 `stim_schedule_master` 作为对齐时间来源。
- 从全时段放电率分箱重建类 PSTH 的光刺激对齐曲线。
- 使用三个窗口的并集作为重建与显示范围；默认标签为 `pre60_post85`。
- 图中的光照带为 `0` 到 `duration_s`。
- 将实际窗口边界写入 `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv`。

这里得到的不是基于脉冲时间戳的 PSTH；时间精度受全时段放电率分箱宽度限制。以上三个 `aligned_rate` 字段是主流程中控制 PreLightPost 数值窗口的唯一参数。`summary_window_mode`、`baseline_window_s`、`post_window_mode` 和 `post_window_after_light_s` 等旧字段只用于兼容，不应写入新项目配置。

普通 `aligned_rate` 分支保留原始定义：

```text
aligned_time_s = time_bin_center_s - light_on_s
start_s <= aligned_time_s < end_s
```

因此 0 秒可以是分箱中心。普通分支不使用时间簇分支的边界策略，也不写入边界对齐元数据。

## 配置参考

默认 `config_template.yaml` 只保留稳定 fullrate-aligned 主流程设置。新项目应优先修改本节字段，不要依赖旧 PSTH 或事件辅助字段。

### 项目与输入

```yaml
project:
  root_dir: "E:/example/project"
  file_id_column: "file_id"

input:
  pl2_dir: "00_raw_pl2"
  stim_schedule: "02_stim_events/stim_schedule_master.xlsx"
  unit_quality_table: "01_sorting_info/unit_quality_table.xlsx"
```

- `root_dir` 是项目绝对路径。
- `file_id_column` 用于连接刺激计划、单元表、导出、图像、PPTX 和统计表。
- 三个 `input` 路径均相对于 `root_dir`。

### 自动生成刺激计划

```yaml
stim_schedule:
  auto_build_from_filenames: true
  update_existing: true
  preserve_manual_edits: true
  output_path: "02_stim_events/stim_schedule_master.xlsx"
  source:
    pl2_dir: "00_raw_pl2"
    file_glob: "*.pl2"
  filename_parser:
    enabled: true
    pattern_name: "sorted_index_light_channels_or_no_light"
    regex: "^sorted_(?P<file_index>\\d+)_(?P<light_on>\\d+(?:\\.\\d+)?)light(?P<duration>\\d+(?:\\.\\d+)?)_(?P<channels>[0-9,]+)\\.pl2$"
    no_light_regex: "^sorted_(?P<file_index>\\d+)_nolight_(?P<channels>[0-9,]+)\\.pl2$"
    case_sensitive: false
  file_id:
    format: "{file_index}"
    zero_pad: 2
```

运行时会刷新已有行，同时保留人工填写的 `condition` 和 `note`。默认 `file_id` 使用两位 `file_index`，例如 `01`。

### 自动生成单元表

```yaml
unit_table:
  enabled: true
  auto_build_if_missing: true
  update_existing: true
  preserve_manual_edits: true
  source:
    backend: "nex"
    open_pl2: true
    fallback_to_existing_fullrate_exports: true

unit_selection:
  required: true
  include_value: "yes"
  fail_on_unmatched_data_units: true
  duplicate_policy: "keep_all"
```

该模块使用 `nex` 读取 `NeuronNames`。新发现 Unit 默认写入 `include: yes`；自动更新只追加缺失行，并在 `preserve_manual_edits: true` 时保留既有 `include: no`、重复单元、排除原因、代表单元和备注。如果 PL2 扫描失败，可从已有 fullrate CSV 读取 `unit_id`。

`unit_quality_table` 是所有下游分析的唯一 Unit cohort 来源。只有字面值 `include: yes` 才纳入；`no`、空值、其他值和缺失行均不纳入。独立命令遇到缺表、数据 Unit 无法匹配或没有任何纳入 Unit 时会终止，并提示先运行 `build_unit_table`。`duplicate_policy` 支持 `keep_all`、`exclude_duplicates` 和 `keep_representative_only`，实际策略与 cohort 计数会写入日志和元数据。

### NeuroExplorer 导出与对齐重建

```yaml
neuroexplorer:
  enabled: true
  backend: "nex_package"
  use_existing_csv_if_available: true
  export_fullrate: true
  fullrate:
    template_name: "RateHist_FullSession"
    bin_width_s: 1
    histogram_unit: "Spikes per second"
  export:
    output_fullrate_dir: "03_nex_exports/fullrate"
    output_aligned_rate_dir: "03_nex_exports/aligned_rate"
    expected_fullrate_pattern: "{file_id}_FullRate_bin{bin_width_s}s.csv"

aligned_rate:
  enabled: true
  pre_window_s: [-60, 0]
  light_window_s: [5, 20]
  post_window_s: [25, 85]
  align_to: "light_on_s"
  bin_width_s: 1
  multi_trial_aggregation: "mean"
  variable_duration_policy: "keep_trials"
```

若规范化 CSV 已存在，`use_existing_csv_if_available` 可跳过 NeuroExplorer。fullrate 与 aligned-rate CSV 保留所有发现的 Unit；筛选发生在 Summary、统计、绘图、PPTX 和时间簇分析入口。稳定路径用 `stim_schedule_master.light_on_s` 对齐；`multi_trial_aggregation` 控制多试次汇总，`variable_duration_policy` 控制同一文件存在多个光照时长时的行为。

### PreLightPost 统计

```yaml
statistics:
  enabled: true
  output_dir: "07_statistics"
  prelightpost:
    input_dir: "03_nex_exports/aligned_rate"
    input_pattern: "*_PreLightPostSummary.csv"
    output_wide_csv: "all_units_pre_light_post_wide.csv"
    output_wide_qc_csv: "all_units_pre_light_post_wide_qc.csv"

run:
  modules:
    prepare_events: false
    prelightpost_stats: false
```

`prelightpost_stats` 不调用 NeuroExplorer，也不重建对齐曲线。它读取已有 `PreLightPostSummary.csv` 数值，并使用同一组窗口配置补全旧文件缺失的窗口元数据、计算质控时长。默认不随完整流程自动运行，应在检查对齐结果后显式执行。稳定路径不需要 NeuroExplorer 事件变量，因此 `prepare_events` 默认关闭。

## OriginPro 输出

### matplotlib PNG 与 OPJU 归档

`export_figures` 是稳定的质控与回退绘图路径，在 `05_exported_figures/` 中生成 PNG。启用以下配置后，可把已有 CSV/PNG 归档进 OPJU；该模式不会在 Origin 中从数据重新创建最终图。

```yaml
origin:
  backend: "matplotlib_png"
  use_originpro: true
  save_opju: true
  opju_generation_mode: "archive_existing_pngs"
  opju_output_dir: "04_origin_projects/opju_outputs"
  opju_filename: "{project_name}_fullrate_aligned.opju"
  overwrite_opju: true
  require_opju_success: false
```

归档会导入 `stim_schedule_master`、`unit_quality_table`、`fullrate_all`、`aligned_rate_all` 和 `prepost_summary_all`，并尝试为各类 PNG 创建图页。

### OriginPro 原生绘图

`origin_native_plot` 根据流程 CSV 创建 manifest，将数据导入 OriginPro 工作簿，从数据创建图页，在可用时应用 `.otpu`，保存可编辑 `.opju`，并可导出 Origin 图像。

```powershell
python run_pipeline.py --config config.yaml --module origin_native_plot
python origin_native_plot.py --config config.yaml
```

```yaml
origin:
  enabled: true
  backend: "origin_native"  # matplotlib_png | origin_native | both
  save_opju: true
  export_images: true
  opju_mode: "per_file"
  max_graph_pages_per_opju: 80
  require_opju_success: false
  native:
    use_originpro: true
    manifest_path: "04_origin_projects/origin_input/origin_plot_manifest.xlsx"
    opju_output_dir: "04_origin_projects/opju_outputs"
    image_output_dir: "05_exported_figures_origin"
    image_format: "png"
    dpi: 300
    templates:
      fullrate: "04_origin_projects/templates/FullRate_template.otpu"
      aligned_rate: "04_origin_projects/templates/AlignedRate_template.otpu"
      prepost_summary: "04_origin_projects/templates/PreLightPost_template.otpu"
      summary: "04_origin_projects/templates/Summary_template.otpu"
```

manifest 位于 `04_origin_projects/origin_input/origin_plot_manifest.xlsx`，包含 `graph_type`、`file_id`、`unit_id`、`source_csv`、`x_col`、`y_col`、`template_path`、`graph_page_name`、光照带边界、X 轴范围和 `output_image_path`。默认按 `per_file` 分组 OPJU，避免触及 OriginPro 图页或窗口限制。除非 Origin 输出是必需交付物，否则保留 `require_opju_success: false`。

### 创建 OriginPro 模板种子

```powershell
python run_pipeline.py --config config.yaml --module origin_create_templates
python origin_create_templates.py --config config.yaml
```

输出：

```text
04_origin_projects/template_seed/origin_template_seed.opju
04_origin_projects/templates/FullRate_template.otpu
04_origin_projects/templates/AlignedRate_template.otpu
04_origin_projects/templates/PreLightPost_template.otpu
99_logs/origin_template_creation_probe.txt
```

如果 API 不支持自动导出 `.otpu`，请在 OriginPro 中打开 `origin_template_seed.opju`，并对各个种子图手工执行 `Save Template As...`。模板只保存样式，不应固定 `duration_s`；`origin_native_plot` 会从 manifest 读取 `light_band_start_s` 和 `light_band_end_s`。

如果未生成 OPJU，请检查 OriginPro 安装、`import originpro`、`origin.backend`、`origin.save_opju`/`origin.export_images` 以及 `99_logs/error_log.xlsx`。OriginPro 不可用时，matplotlib PNG 和 PPTX 仍可继续生成。

## 自动模式与 NeuroExplorer 模板

`analysis.mode: auto` 的顺序为：尝试 `RateHist_FullSession`、导出全时段数值、在 Python 中重建对齐曲线；仅在该路径失败时尝试实验性的 `neuroexplorer_psth`。

必须在 NeuroExplorer 中创建并保存 `RateHist_FullSession`。推荐设置为：从 `t=0` 覆盖完整记录，`Bin = 1`，`Histogram Units = Spikes per second`，且不设置参考事件。

## 常用命令

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python run_pipeline.py --config config.yaml
python run_pipeline.py --config config.yaml --module build_stim_schedule
python run_pipeline.py --config config.yaml --module build_unit_table
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python run_pipeline.py --config config.yaml --module aligned_rate
python run_pipeline.py --config config.yaml --module time_cluster_aligned_rate
python run_pipeline.py --config config.yaml --module time_cluster_permutation
python run_pipeline.py --config config.yaml --module export_figures
python run_pipeline.py --config config.yaml --module origin_create_templates
python run_pipeline.py --config config.yaml --module origin_native_plot
python run_pipeline.py --config config.yaml --module build_pptx
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

## 初始化项目

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --with-example --pre-margin 60 --post-margin 60 --bin-width 1
```

初始化后，将 `.pl2` 放入 `00_raw_pl2/`，确认文件名规则，准备 `RateHist_FullSession`，再运行完整流程。如果根目录已有 `sorted_*.pl2`，初始化器只警告，不会自动移动、复制、重命名或删除原始文件。需要时手工执行：

```powershell
Move-Item ".\sorted_*.pl2" ".\00_raw_pl2\"
```

## 自动生成项目表

刺激计划生成器扫描 `00_raw_pl2/` 并创建或更新 `02_stim_events/stim_schedule_master.xlsx`。

```text
sorted_<file_index>_<light_on_s>light<duration_s>_<sorted_channels>.pl2
sorted_<file_index>_nolight_<sorted_channels>.pl2
```

`sorted_01_200light25_1,5,9.pl2` 会得到 `file_id=01`、`event_group=200light25`、`light_on_s=200`、`duration_s=25`、`light_off_s=225`。`sorted_02_nolight_1,5,9.pl2` 会得到 `has_light=no`、`condition=no_light`，时间字段留空。生成器支持小数时间，保留人工 `condition`/`note`，追加新文件，并把未再次发现的旧行标记为 `detected_in_latest_scan=no`。

单元表生成器扫描每个 `.pl2` 的 `NeuronNames`，创建或更新 `01_sorting_info/unit_quality_table.xlsx`：在每个文件内分配 `unit01`、`unit02` 等编号，新单元默认 `include=yes`，保留人工质控字段，仅追加新单元，并把未再次发现的旧行标记为 `detected_in_latest_scan=no`。完整 auto pipeline 会按 `update_existing` 更新表；单独运行分析命令不会自动修改人工表，而是严格校验后运行。

## 无光对照

无光文件使用 `sorted_<file_index>_nolight_<sorted_channels>.pl2`。它们仍导出全时段放电率，但跳过真实对齐分析和 PreLightPost；相关面板使用无光占位图，PPTX 元数据标记 `has_light: no`。

## 关键输出

- `03_nex_exports/fullrate/{file_id}_FullRate_bin1s.csv`
- `03_nex_exports/aligned_rate/{file_id}_LightAlignedRate_pre60_post85_bin1s.csv`
- `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv`
- `03_nex_exports/time_cluster_aligned_rate/{file_id}_TimeClusterAlignedRate_*.csv`（可选）
- `07_statistics/all_units_pre_light_post_wide.csv`
- `07_statistics/all_units_pre_light_post_wide_qc.csv`
- `07_statistics/all_units_pre_light_post_qc_excluded.csv`
- `05_exported_figures/fullrate/{file_id}_{unit_id}_FullRate.png`
- `05_exported_figures/aligned_rate/{file_id}_{unit_id}_AlignedRate_pre60_post85.png`
- `05_exported_figures/prepost_summary/{file_id}_{unit_id}_PreLightPost.png`
- `05_exported_figures/summary/{file_id}_Summary_pre60_post85.png`
- `06_pptx/PSTH_summary_auto.pptx`
- `03_nex_exports/aligned_rate/unit_cohort.csv` 与 `unit_cohort_metadata.json`
- `03_nex_exports/time_cluster_aligned_rate/unit_cohort.csv` 与 `unit_cohort_metadata.json`
- 各分析输出目录中的 `unit_cohort.csv` 与 `unit_cohort_metadata.json`

## 单元级时间簇置换

这是可选且独立的分支。它读取未修改且包含全部 Unit 的 fullrate CSV，在 `03_nex_exports/time_cluster_aligned_rate/` 生成同样保留全部 Unit 的边界对齐数据，随后在置换入口按 `unit_quality_table` 筛选；不会读取普通 `LightAlignedRate`，也不会改变常规图像、PPTX 或 PreLightPost 流程。重复试次在每个 `(file_id, unit_id, time)` 内先求平均，使每个独立单元只贡献一个样本行。

```yaml
time_cluster_aligned_rate:
  enabled: true
  output_dir: "03_nex_exports/time_cluster_aligned_rate"
  window_s: [-60, 300]
  source_bin_width_s: null
  bin_width_s: null
  incomplete_target_bin_policy: "error"
  require_light_on_on_bin_boundary: false
  off_boundary_policy: "nearest"

time_cluster_permutation:
  enabled: true
  input_dir: "03_nex_exports/time_cluster_aligned_rate"
  input_pattern: "*_TimeClusterAlignedRate_*.csv"
  analysis_window_s: [-60, 300]
  baseline_window_s: [-60, 0]
  test_window_s: [0, 300]
  cluster_forming_alpha: 0.05
  cluster_alpha: 0.05
  n_permutations: 10000
  tail: 0
  seed: 20260714
  output_subdir: "time_cluster_permutation"
  include_in_pptx: false
```

`source_bin_width_s: null` 继承普通 `neuroexplorer.fullrate.bin_width_s`；显式设置后，time-cluster 可以读取另一套更细的现有 fullrate CSV，而不改变普通分支。例如普通分支保持 10 秒，专用分支可设 `source_bin_width_s: 1.0` 读取 `*_FullRate_bin1.0s.csv`，再用 `bin_width_s: 30` 聚合。这里的源 fullrate 仍是 NeuroExplorer RateHist 的已分箱输出，并非未分箱脉冲时间戳。

`bin_width_s: null` 继承源宽度。目标宽度只能是均匀源宽度的整数倍，且必须由连续源分箱完整覆盖；放电率按持续时间加权，部分覆盖会报错。专用分箱定义为 `[kΔ,(k+1)Δ)`，中心为 `(k+0.5)Δ`，因此 0 秒始终是边界。光刺激起点不在源边界时，`nearest` 记录所选边界和偏移，`error` 终止，`interpolate` 因缺少脉冲时间戳而被拒绝。

`incomplete_target_bin_policy: "error"` 是默认安全策略。设置为 `"nan"` 时，覆盖不足的目标 bin 保留真实边界，但 `firing_rate_hz` 为缺失值，并记录实际源分箱数量、覆盖时长和 `incomplete_source_coverage_nan`；不会用部分数据计算伪 30 秒数值。置换分析按 bin 使用有效单元，Heatmap 用独立缺失色显示这些格子。

该分支不继承 `aligned_rate` 窗口。选择规则为 `start_s <= bin_center_s < end_s`。窗口无效、重叠、为空或越界会报错。热图颜色只表示 `delta_rate_hz`，缺失值单独着色，0 秒以边界虚线表示。

无有效 baseline 或 test 分箱不足的单元会在 `unit_summary.csv` 中记录排除原因。有效单元不足两个，或均值非零但方差为零时 t 为 `NaN`；全零差值分箱为 `t=0, p=1`；不规则时间网格直接报错。

```powershell
python build_time_cluster_aligned_rate.py --config config.yaml
python time_cluster_permutation.py --config config.yaml
python run_pipeline.py --config config.yaml --module time_cluster_aligned_rate
python run_pipeline.py --config config.yaml --module time_cluster_permutation
```

旧项目必须增加专用配置并重建专用 CSV，不要复制或改名旧 `LightAlignedRate`。置换在每个单元的整个测试曲线上统一翻转符号，正负簇分开形成，簇质量为绝对 t 值之和，p 值采用 `(1 + exceedances) / (1 + permutations)` 校正。

输出位于 `07_statistics/time_cluster_permutation/`，包括 `cluster_table.csv`、`time_bin_statistics.csv`、`unit_time_analysis_matrix.csv`、`unit_summary.csv`、`unit_cohort.csv`、`unit_cohort_metadata.json`、`null_max_cluster_mass.csv`、`analysis_metadata.json` 和三类图像。

该分析假设各单元可交换且独立，不建模同一动物或会话内依赖，因此不能自动解释为动物层面推断。簇显著不表示每个分箱独立显著，簇边界也不是精确生理起始时间。当前应保持 `include_in_pptx: false`。

## PreLightPost 统计质控

```powershell
python run_pipeline.py --config config.yaml --module prelightpost_stats
```

该模块只读取 `03_nex_exports/aligned_rate/*_PreLightPostSummary.csv`，不调用 NeuroExplorer、不生成图像、不构建 PPTX，也不重新计算放电率。修改窗口后必须先重新运行 `aligned_rate`。

QC 保留同时满足以下条件的行：

```text
max(pre_hz, light_hz, post_hz) >= 0.5 Hz
total_expected_spikes >= 10
```

`pre_hz` 是 `baseline_hz` 的别名。无光对照不进入 QC 宽表，而是写入排除或跳过输出。当前不再生成 `summary_by_file` 和 `summary_by_condition`。

## 实验性与旧模式

以下功能仅保留用于调试和本地实验：`analysis.mode: neuroexplorer_psth`、自动创建 `Light_On`/`Light_Interval`、`nex.AddInterval`/`nex.AddTimestamp` 探针、空或克隆 `NexVar` 探针以及 GUI 自动化回退。相关脚本位于 `scripts/smoke_tests/`，不应作为稳定生产流程使用。
