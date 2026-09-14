# V2 First-landmark-only Sensitivity Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE FIRST FIRST-LANDMARK PERFORMANCE RESULT`
- 性质：预设的重复观测敏感性分析；不重新训练、不重新校准、不重选变量或阈值

## 风险集

- 从rolling 24 h raw nested held-out predictions中仅保留`landmark_h=24`；
- outcome仍为`(24 h, 48 h]`首次observed IMV，而不是fixed `(24 h, 96 h]`；
- MIMIC与eICU分别评价M0、M1、M2。

## 统计规则

- 主要比较M2−M1，次要比较M2−M0；
- AUPRC、AUROC、Brier、log loss使用完全配对的行；
- 1000次cluster bootstrap：MIMIC按`patient_key`，eICU按`hospital_id`；
- 固定随机种子`20260909`，percentile 95% CI；
- 同时报告raw calibration与既有0.5%、1%、2%、3%、5%阈值的DCA/alert burden。

结果仅判断rolling主结论对重复landmark的稳健性，不用于选择primary time point。
