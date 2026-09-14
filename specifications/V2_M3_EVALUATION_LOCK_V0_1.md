# V2 M3 Elastic Net Evaluation Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE COMPLETE M3 OOF PERFORMANCE REVIEW`
- 范围：rolling 24 h；MIMIC 与 eICU；M3 使用与 M2 完全相同的 140-feature information set

## 1. 预先规定的比较

1. `M2_DYNAMIC_CAT - M3_DYNAMIC_EN`：同一 rows、outer folds 和 information set 下，量化 CatBoost 相对 Elastic Net 的算法/函数形式增量；这是 M3 的主要用途。
2. `M3_DYNAMIC_EN - M1_SNAPSHOT_CAT`：探索透明线性动态模型能否超过静态 CatBoost。由于信息域与算法同时改变，此比较不能单独归因于动态变量。

不根据 M3 结果新增 feature、改变 grid、筛选数据库或更换比较方向。M3 不替代 primary `M2_DYNAMIC_CAT - M1_SNAPSHOT_CAT` estimand。

## 2. 指标与不确定性

- raw held-out OOF AUPRC、AUROC、Brier score、log loss；
- paired difference 按上述方向计算；
- 1,000 次 cluster bootstrap：MIMIC 按 `patient_key`，eICU 按 `hospital_id`；
- percentile 95% CI；同一数据库的两项比较使用同一组 cluster bootstrap draws；
- eICU 继续称 hospital-grouped IECV/cross-database revalidation，不称独立外部验证。

## 3. Calibration 与临床效用

对 raw M3 OOF probability 报告 calibration-in-the-large、joint calibration intercept/slope、O/E、decile calibration、DCA 与既有阈值 0.5%、1%、2%、3%、5% 的 alert burden。未经 cross-fit model updating 不做 recalibration；DCA 不能替代前瞻性临床效用验证。

## 4. 稳定性和收敛审计

- 报告每个 outer fold 的 selected `l1_ratio`、`C`、inner mean AUPRC/log loss、final `n_iter` 和 convergence warning；
- 报告 outer-fold performance；
- 对 fold-local coefficients 按编码后 feature 汇总出现折数、非零折数、非零选择频率、符号一致率、中位 coefficient、中位绝对 coefficient 和 IQR；
- 系数只作稳定性/可解释性描述，不作因果或机制解释。

## 5. 解读边界

- 若 M2 优于 M3，只支持在相同信息域下存在非线性/交互或算法贡献，不证明某一变量的机制；
- 若 M3 接近 M2，支持更简约透明模型具有竞争性，不自动等于可部署；
- 若 M3 不优于 M1，不能据此否定动态信息，因为算法同时不同；动态增量的正式判断仍来自同算法 `M2 - M1`；
- 所有效应量同时结合 CI、calibration、DCA 和 alert burden，不以单一 AUROC 阈值裁决临床价值。
