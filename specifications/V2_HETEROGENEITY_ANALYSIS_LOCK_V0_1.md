# V2 Heterogeneity Analysis Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE FIRST STRATIFIED PERFORMANCE RESULT`
- 性质：SAP预设的exploratory heterogeneity analysis，不用于模型/变量/阈值选择

## 输入分析

1. rolling与fixed的MIMIC、eICU nested held-out raw probabilities；
2. rolling与fixed的MIMIC→eICU locked raw transport probabilities；
3. M0、M1、M2全部报告，重点差值为M2−M1和M2−M0。

## 预设分层

- 所有分析：既有`outer_fold`；
- 两库：`icu_type`；
- MIMIC：`anchor_year_group`；
- eICU：`oxygen_interface_supported`；
- eICU：按当前分析风险集landmark数对208家医院进行outcome-blind三等分volume stratum。

不进行医院质量排名，不以单家医院AUROC为主要结果，不根据分层性能合并或拆分层级。

## 可估计性与报告

- 每层至少1000 rows、50 events且同时存在0/1结局，才报告AUROC/AUPRC及差值；
- 不满足者保留N、events、prevalence并标记`NOT_ESTIMABLE_BY_LOCKED_RULE`；
- 报告AUROC、AUPRC、Brier、log loss、O/E、calibration-in-the-large、calibration slope；
- 分层结果为描述性探索，不据此追加调参或宣称正式interaction。
