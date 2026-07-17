# NeuroTrain 帮助

本文列出 `neurotrain` 当前可用的终端命令、输入输出和 Codex/代理指令示例。

```text
.pl2 文件
-> 根据文件名创建 stim_schedule_master
-> 根据 NeuroExplorer NeuronNames 创建 unit_quality_table
-> NeuroExplorer RateHist_FullSession
-> SaveNumResults 导出全时段放电率
-> Python fullrate_aligned 重建
-> FullRate / AlignedRate / PreLightPost / Summary 图像
-> PPTX
```

推荐 `analysis.mode: "auto"`。`auto` 优先使用 `fullrate_aligned`；旧的 NeuroExplorer 直接 PSTH 路径仅作实验性回退。

## 1. 初始化项目

```powershell
python run_pipeline.py --module init_project --project-dir "D:\Data\my_ephys_project"
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project"
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --force
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --with-example
python scripts/init_project.py --project-dir "D:\Data\my_ephys_project" --pre-margin 60 --post-margin 60 --bin-width 1
```

`init_project` 只创建目录和模板，不会移动、复制、重命名或删除 `.pl2`。检测到根目录 `sorted_*.pl2` 时，只警告并写入 `99_logs/root_pl2_detected_files.txt`。请手工执行：

```powershell
Move-Item ".\sorted_*.pl2" ".\00_raw_pl2\"
```

代理指令：`请使用 neurotrain skill，在 D:\Data\my_ephys_project 初始化一个新项目。`

## 2. 创建 stim_schedule_master

```powershell
python run_pipeline.py --config config.yaml --module build_stim_schedule
python scripts/build_stim_schedule_from_filenames.py --config config.yaml
```

输入为 `00_raw_pl2/*.pl2`，输出为 `02_stim_events/stim_schedule_master.xlsx`。命名规则：

```text
sorted_<file_index>_<light_on_s>light<duration_s>_<sorted_channels>.pl2
sorted_<file_index>_nolight_<sorted_channels>.pl2
```

例如 `sorted_01_200light25_1,5,9.pl2` 生成 `file_id=01`、`has_light=yes`、`light_on_s=200`、`duration_s=25`、`light_off_s=225`；`sorted_02_nolight_1,5,9.pl2` 生成 `has_light=no`、`condition=no_light`，时间字段留空。支持 `sorted_03_120.5light15_2,4.pl2` 等小数时间。

代理指令：`请根据 00_raw_pl2 中的 .pl2 文件名自动生成或更新 stim_schedule_master。`

## 3. 创建 unit_quality_table

```powershell
python run_pipeline.py --config config.yaml --module build_unit_table
python scripts/build_unit_quality_table.py --config config.yaml
```

输出为 `01_sorting_info/unit_quality_table.xlsx`。模块读取每个 `.pl2` 的 `NeuronNames`，按文件创建 `unit01`、`unit02` 等编号。新 Unit 默认 `include=yes`；`preserve_manual_edits: true` 时自动更新只追加缺失行，并保留人工 `include=no`、`exclusion_reason`、`representative_unit`、`duplicate_of` 和 `note`。无需 `Light_On` 或 `Light_Interval`，无光文件也正常创建单元行。

所有下游分析只纳入字面值 `include: yes`。`no`、空值、其他值或缺失行均排除。缺表、当前数据存在未匹配 Unit、或没有任何 `yes` 时，独立命令会明确失败；先运行上面的 `build_unit_table`，人工复核后再分析。不要删除 fullrate/aligned CSV 来筛选 Unit。

## 4. 验证项目

```powershell
python run_pipeline.py --config config.yaml --module validate
python validate_project.py --config config.yaml
```

验证目录结构、必需配置、刺激计划、单元表、有光/无光语义和对齐窗口。`has_light=no` 不要求三个光照时间字段，仍可导出 fullrate，但跳过对齐和 PreLightPost。

## 5. 准备事件辅助文件

```powershell
python run_pipeline.py --config config.yaml --module prepare_events
python prepare_events.py --config config.yaml
```

有光文件输出 `{file_id}_Light_On.txt`、`{file_id}_Light_Off.txt` 和 `{file_id}_Light_Interval.csv` 到 `02_stim_events/exported_events/`。稳定 `fullrate_aligned` 不需要这些文件；它们只用于手工 NeuroExplorer 和旧 PSTH 流程。无光文件跳过。

## 6. 导出 NeuroExplorer 全时段放电率

```powershell
python run_pipeline.py --config config.yaml --module neuroexplorer_export
python export_from_neuroexplorer.py --config config.yaml
```

模块打开或连接 `.pl2`，应用 `RateHist_FullSession`，在可用时调用 `nex.SaveNumResults`，并规范化为：

```text
03_nex_exports/fullrate/{file_id}_FullRate_bin1s_raw.txt
03_nex_exports/fullrate/{file_id}_FullRate_bin1s.csv
```

稳定路径不调用 `Light_On`、`Light_Interval` 或 `PSTH_LightOn`。

## 7. 根据 fullrate 创建对齐放电率

```powershell
python run_pipeline.py --config config.yaml --module aligned_rate
python build_aligned_rate_from_fullrate.py --config config.yaml
```

默认窗口为 pre `[-60,0]`、light `[5,20]`、post `[25,85]`；并集为 `[-60,85]`，输出标签为 `pre60_post85`，光照带为 `[0,duration_s]`。

```text
03_nex_exports/aligned_rate/{file_id}_LightAlignedRate_pre60_post85_bin1s.csv
03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv
```

Summary 包含 `baseline_hz`、`light_hz`、`post_hz` 和窗口字段。普通分支使用 `aligned_time_s = time_bin_center_s - light_on_s`，0 秒可以是分箱中心；不使用时间簇边界策略。无光文件跳过并记录 `No light event; aligned rate skipped.`。

## 7A. 独立时间簇分支

该分支读取相同且未修改的 fullrate CSV，但写入专用边界对齐目录，完全独立于普通 `aligned_rate`。专用 aligned CSV 保留全部 Unit；`time_cluster_permutation` 入口才按质量表筛选。

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
```

```powershell
python build_time_cluster_aligned_rate.py --config config.yaml
python time_cluster_permutation.py --config config.yaml
python run_pipeline.py --config config.yaml --module time_cluster_aligned_rate
python run_pipeline.py --config config.yaml --module time_cluster_permutation
```

`source_bin_width_s: null` 默认继承普通 fullrate 宽度；也可显式指定另一套现有 fullrate 导出。例如普通分支保持 10 秒，专用分支设置 `source_bin_width_s: 1.0` 和 `bin_width_s: 30`，会读取 `*_FullRate_bin1.0s.csv` 并精确聚合为 30 秒，而不会读取普通 aligned CSV。RateHist fullrate 本身仍是已分箱数据，不等于原始脉冲时间戳。

专用分箱为 `[kΔ,(k+1)Δ)`，中心为 `(k+0.5)Δ`，0 秒始终是边界。刺激起点不在源边界时，`nearest` 记录边界和偏移，`error` 终止，`interpolate` 因缺少脉冲时间戳而被拒绝。目标宽度必须是源宽度整数倍并由连续源分箱完整覆盖；部分覆盖报错。

记录长度不一致时，默认 `incomplete_target_bin_policy: "error"` 会停止。显式设置为 `"nan"` 后，不完整目标 bin 保留边界但写入缺失 firing rate，同时记录实际源分箱数、覆盖时长和不完整方法；不会从部分覆盖计算伪目标值。置换分析逐 bin 使用有效单元，Heatmap 将其显示为缺失色。

输出为 `03_nex_exports/time_cluster_aligned_rate/{file_id}_TimeClusterAlignedRate_*.csv` 和 `07_statistics/time_cluster_permutation/`。两处均写入 cohort CSV/JSON；`analysis_metadata.json` 记录发现/纳入/排除数量、原因统计和实际 `duplicate_policy`。热图颜色只表示 `delta_rate_hz`，缺失值单独着色。旧项目必须添加专用配置并重建专用 CSV；不要指向普通 aligned 目录，也不要改名旧 `LightAlignedRate`。

## 8. 绘制图像

```powershell
python run_pipeline.py --config config.yaml --module export_figures
python plot_in_origin.py --config config.yaml
```

稳定输出为 `05_exported_figures/` 下的 fullrate、aligned_rate、prepost_summary 和 summary PNG。无光文件仍有 fullrate 图，但不画光照带；其余面板写入 `_no_light_skipped` 占位图。`origin.save_opju: true` 可选创建 OPJU 归档。

## 8b. 创建 OriginPro 模板种子

```powershell
python run_pipeline.py --config config.yaml --module origin_create_templates
python origin_create_templates.py --config config.yaml
```

输出种子 OPJU、三个 `.otpu` 及 `99_logs/origin_template_creation_probe.txt`。若无法自动导出 `.otpu`，在 OriginPro 中打开种子 OPJU 并手工使用 `Save Template As...`。模板只保存样式，不得固定 `duration_s`；光照带来自 manifest。

## 8c. OriginPro 原生绘图

```powershell
python run_pipeline.py --config config.yaml --module origin_native_plot
python origin_native_plot.py --config config.yaml
```

此路径直接导入 CSV、创建工作簿和图页、应用 `.otpu`、保存 `.opju` 并导出图像。配置 `origin.backend: "origin_native"` 或 `"both"`，默认 `opju_mode: "per_file"`。manifest 位于 `04_origin_projects/origin_input/origin_plot_manifest.xlsx`，包含数据源、X/Y 列、模板、图页名、光照带、轴范围和输出路径。Origin 不可用时，除非 `origin.require_opju_success: true`，否则不阻塞 matplotlib、PPTX 或统计。

## 9. 构建 PPTX

```powershell
python run_pipeline.py --config config.yaml --module build_pptx
python build_pptx.py --config config.yaml
```

输出为 `06_pptx/PSTH_summary_auto.pptx`。有光页面包含全时段、对齐和 Pre/light/post 三个面板；无光页面保留 fullrate，并显示“不适用”占位内容。元数据包含分析模式、有无光照、事件组和各窗口。

## 10. 构建 Pre / Light / Post 统计

此模块只读取已有 Summary，不调用 NeuroExplorer、不重建 aligned-rate、不绘图、不构建 PPTX。修改窗口后必须先重新运行 `aligned_rate`。

```powershell
python run_pipeline.py --config config.yaml --module prelightpost_stats
python scripts/build_prelightpost_statistics.py --config config.yaml
```

主要输入为 `03_nex_exports/aligned_rate/{file_id}_PreLightPostSummary.csv`。输出：

```text
07_statistics/all_units_pre_light_post_wide.csv
07_statistics/all_units_pre_light_post_wide_qc.csv
07_statistics/all_units_pre_light_post_qc_excluded.csv
07_statistics/skipped_or_missing_prelightpost.csv
07_statistics/all_units_pre_light_post_statistics.xlsx
```

长表每行对应 `file_id + unit_id + trial_id + phase`；宽表每行对应 `file_id + unit_id + trial_id`。QC 同时要求 `max(pre_hz, light_hz, post_hz) >= 0.5 Hz` 和 `total_expected_spikes >= 10`。

派生指标包括 light/post 相对 baseline 的差值、比值和百分比变化。`baseline_hz` 为零或缺失时，比值与百分比留空。无光行以 `reason=no_light_control` 记录，缺少 Summary 的有光行以 `reason=missing_summary_file` 记录。完整自动流程默认 `run.modules.prelightpost_stats: false`。

## 11. 完整自动流程

```powershell
python run_pipeline.py --config config.yaml
```

顺序为 `build_stim_schedule`、`build_unit_table`、`validate`、`prepare_events`、`neuroexplorer_export`、`aligned_rate`、`time_cluster_aligned_rate`、`time_cluster_permutation`、`prelightpost_stats`、`export_figures`、`origin_create_templates`、`origin_native_plot`、`build_pptx`。只有配置启用的可选模块才会运行。

完整 auto pipeline 会先创建或增量更新质量表，并保留人工字段。`--module aligned_rate`、`--module prelightpost_stats`、`--module time_cluster_aligned_rate` 和 `--module time_cluster_permutation` 等独立运行不会替用户改表；它们严格读取项目 `config.yaml` 指定的质量表并记录 cohort。

## 12. 运行参数

```powershell
python run_pipeline.py --config config.yaml --dry-run
python run_pipeline.py --config config.yaml --overwrite
python run_pipeline.py --config config.yaml --module build_stim_schedule
python run_pipeline.py --config config.yaml --module build_unit_table
python run_pipeline.py --config config.yaml --module validate
python run_pipeline.py --config config.yaml --module prepare_events
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

## 13. 重要默认配置

```yaml
analysis:
  mode: "auto"
aligned_rate:
  pre_window_s: [-60, 0]
  light_window_s: [5, 20]
  post_window_s: [25, 85]
neuroexplorer:
  export_psth: false
  export_fullrate: true
  fullrate:
    template_name: "RateHist_FullSession"
```

只有三个 `aligned_rate` 窗口字段控制 PreLightPost 数值。新配置不要添加 `summary_window_mode`、`baseline_window_s`、`post_window_mode` 或 `post_window_after_light_s`。初始化器默认只检测根目录 PL2 并警告，不自动移动或复制。

## 14. 常见问题与自检

- PPTX 与统计不一致：修改窗口后依次重跑 `aligned_rate`、`export_figures`、`build_pptx` 和 `prelightpost_stats`。
- 统计仍显示旧窗口：`prelightpost_stats` 读取已有 Summary；先重跑 `aligned_rate`。
- 唯一有效窗口：`aligned_rate.pre_window_s`、`light_window_s`、`post_window_s`。
- 稳定路径无需 `prepare_events`；它使用 `stim_schedule_master.xlsx`。
- `pre60_post85` 来自三个窗口并集的最小起点 `-60` 和最大终点 `85`。

## 15. OriginPro OPJU

OPJU 默认关闭，只是可选归档。PPTX 读取 `05_exported_figures/` 的 PNG；除非 `origin.require_opju_success: true`，否则不依赖 OPJU。

## 16. 实验性与旧功能

`analysis.mode: neuroexplorer_psth`、自动创建 `Light_On`/`Light_Interval`、NexVar 探针和 GUI 自动化回退仅用于调试。冒烟测试位于 `scripts/smoke_tests/`。

## 17. 当前不是稳定模块的功能

`batch_gui_export_fullrate` 可能出现在旧规划或配置笔记中，但当前 `run_pipeline.py` 未提供该稳定模块。请使用：

```powershell
python run_pipeline.py --config config.yaml --module neuroexplorer_export
```

## 18. 独立原始脉冲 Raster

Raster 不由 `run_pipeline.py` 自动触发，也不读取主 `config.yaml`。先从 NeuroExplorer 导出包含 `session_id`、`unit_id`、可选 `channel_id` 和 spike `timestamp` 的 long CSV，再单独导出包含 `session_id`、`event_name` 和 event `timestamp` 的 Event CSV。两类时间戳必须使用 `config/raster_config.yaml` 声明的同一原始单位。

```powershell
python raster_plot.py --config config/raster_config.yaml --validate-only
python raster_plot.py --config config/raster_config.yaml
python raster_plot.py --config config/raster_config.yaml --session session-A --unit unit-1
```

找不到输入、字段缺失、时间单位非法、缺少指定事件、禁止的窗口重叠或 `overwrite: false` 冲突时，命令以非零状态结束。`fail_on_empty_unit: false` 默认为空 unit 输出保留所有 trial 的空图；设为 `true` 时写入 exclusions。完整说明与真实 NeuroExplorer 人工验收清单见 `docs/raster_plots.md`。
