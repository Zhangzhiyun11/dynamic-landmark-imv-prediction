# V2 Cross-fit Recalibration Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE FIRST CROSSFIT RECALIBRATED RESULT`
- 性质：预设 model updating；raw OOF probabilities 永久保留并优先报告

## 适用分析

- rolling 24 h：MIMIC与eICU的M0、M1、M2；
- fixed t24→96：MIMIC与eICU的M0、M1、M2。

## Cross-fit规则

对每个数据库、模型和分析范围，逐outer fold执行：

1. 当前outer fold作为recalibration test fold；
2. 仅使用其余outer folds的raw OOF probability和outcome拟合calibrator；
3. 分别拟合intercept-only logistic recalibration与intercept+slope logistic recalibration；
4. calibrator原样应用于当前held-out fold；
5. 合并所有fold，生成每行仅一次的cross-fit updated probability。

MIMIC outer folds按patient分组，eICU outer folds按hospital分组。当前test fold的outcome不得用于拟合其calibrator。

## 实现

- probability先裁剪至`[1e-6, 1-1e-6]`后取logit；
- intercept-only：固定raw logit系数为1，仅估计offset intercept；
- intercept+slope：无惩罚logistic regression，估计intercept与slope；
- 不选择两者中的“最佳”版本；raw、intercept-only、intercept+slope全部并列报告；
- 不改变任何base model、特征、fold、hyperparameter或raw probability；
- recalibration不得表述为独立外部验证或提高discrimination。

## 输出

- 受限行级文件：raw及两种cross-fit updated probabilities；
- 聚合公开文件：每种概率的AUROC、AUPRC、Brier、log loss、O/E、calibration-in-the-large、joint calibration intercept和slope；
- 每个held-out fold的calibrator参数。
