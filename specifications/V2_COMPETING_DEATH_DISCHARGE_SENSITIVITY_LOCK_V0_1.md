# V2 Competing Death / ICU Discharge Sensitivity Lock V0.1

- 冻结日期：2026-09-13
- 状态：`FROZEN BEFORE FIRST COMPETING-EVENT PERFORMANCE RESULT`
- 范围：rolling 24 h primary evaluable raw OOF，MIMIC-IV与eICU

## 1. 目的和主要边界

检验主要`M2_DYNAMIC_CAT - M1_SNAPSHOT_CAT`比较对horizon内先于IMV documentation的death/ICU discharge处理是否稳健。主要prediction target仍是observed IMV；死亡不合并为IMV，出院不解释为无风险。本敏感性只对held-out rows做预设过滤，不改变训练集、模型、fold、概率、结局定义或比较方向。

## 2. Phenotype-consistent competing-event重建

原outcome表的`competing_event_type`保留不改。为避免eICU B/C阳性仅与`first_strict_offset_h`比较造成的时间顺序误分，本分析从冻结字段重建`competing_event_type_corrected`：

- MIMIC阳性的IMV documentation time为`first_strict_start_offset_h`；
- eICU A、B、C分别使用`first_strict_offset_h`、`first_anchor_offset_h`、`first_treatment_offset_h`；None无IMV documentation time；
- death或ICU discharge仅在`(landmark, landmark+24 h]`且早于对应IMV documentation time时视为competing；无IMV documentation时，窗口内事件视为competing；
- death与discharge均competing且时间差小于1分钟时为`both_same_time`；否则death优先，再判定ICU discharge。

该重建仅用于本敏感性，不回写或替换冻结主要outcome。冻结前结构审计发现：MIMIC stored/derived分类有30行同刻分类差异；eICU有235行差异，主要来自B/C阳性发生IMV documentation后才离开ICU。上述为时间顺序QC，不是性能筛选。

## 3. 冻结情景

每库均使用下列四个情景：

1. `PRIMARY_COMPETING_AS_OBSERVED_NONE`：主要分析风险集；competing event若没有IMV仍保持结局0。
2. `DEATH_CENSORED`：排除corrected `death`或`both_same_time` rows；ICU discharge仍按主要分析保留。
3. `DISCHARGE_CENSORED`：排除corrected `ICU discharge` rows；death/both仍按主要分析保留。
4. `ANY_COMPETING_CENSORED`：排除所有corrected death、both_same_time或ICU discharge rows。

“censored”在此表示固定horizon complete-case deletion sensitivity，不是Kaplan-Meier/Fine-Gray/IPC-weighted estimand；因过滤受预后影响，结果仅用于稳健性边界，不能取代主要结果。

## 4. 评价

- 使用永久保存的M1/M2 raw OOF；不重拟合、不重校准、不重调参。
- 报告各情景groups、stays、landmarks、events、prevalence及competing-event counts。
- 报告M1/M2 AUPRC、AUROC、Brier、log loss及`M2-M1`差值。
- 1,000次cluster bootstrap percentile 95% CI；MIMIC按patient，eICU按hospital；seed 20260909。
- 报告raw calibration-in-the-large、joint intercept/slope、O/E；M2报告冻结阈值0.5%、1%、2%、3%、5%的DCA与alert burden。
- bootstrap replicate保存在restricted目录且权限0600；公开目录只保存聚合结果。

## 5. 解读限制

若过滤后M2−M1方向保持，只说明结果不完全由窗口内competing rows驱动。因ICU discharge构成大量rows，complete-case结果对应条件人群且可能有选择偏倚；不能写成无偏的competing-risk估计、因果效应、独立外部验证或部署证据。
