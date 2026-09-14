# V2 No-timing-proxy Comparison Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN AFTER RAW NO-TIMING-PROXY OOF BEFORE FIRST COMPARISON RESULT`

## 比较

1. `M2_NO_TIMING_PROXY_CAT` vs `M2_DYNAMIC_CAT`：判断移除`oxygen_time_since_latest_h`的影响；
2. `M2_NO_TIMING_PROXY_CAT` vs `M1_SNAPSHOT_CAT`：判断移除后动态特征增益是否保留。

Rolling/fixed及MIMIC/eICU均使用完全配对的raw held-out rows。1000次cluster bootstrap固定seed `20260909`；MIMIC按patient、eICU按hospital。报告AUPRC、AUROC、Brier、log loss差值及percentile 95% CI。另报告no-timing-proxy模型的raw calibration和既有固定阈值DCA/alert burden。

不得依据比较结果将该敏感性模型升级为主模型或反向调整特征。
