# V2 Transport Post-evaluation Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN AFTER RAW TRANSPORT RESULTS BEFORE FIRST TRANSPORT BOOTSTRAP OR UPDATED RESULT`
- 性质：raw MIMIC→eICU transport结果已永久保存；本锁仅规定不确定性评价与eICU本地校准更新

## 适用范围

- rolling 24 h与fixed t24→96；
- M0、M1、M2；
- transport source为MIMIC-IV v3.1，target为eICU-CRD v2.0。

## Raw transport配对比较

1. 仅比较M2 vs M1、M2 vs M0；
2. 每项比较使用完全相同的eICU行级结局和raw transport probability；
3. 以`hospital_id`为cluster进行1000次有放回bootstrap；
4. 固定随机种子`20260909`；
5. 报告AUPRC、AUROC、Brier、log loss的点估计、差值及percentile 95% CI；
6. 不用bootstrap结果选择模型、特征、窗口、阈值或超参数。

## eICU cross-fit recalibration

对每个范围和模型，逐既有eICU `outer_fold`执行：

1. 当前fold为更新测试集，其余fold为更新训练集；
2. 核验训练与测试医院没有重叠；
3. 同时拟合intercept-only及intercept+slope logistic recalibration；
4. calibrator原样应用于held-out fold，合并后每行仅有一次updated probability；
5. raw、intercept-only、intercept+slope全部并列报告，不择优；
6. 更新前raw probability永久保留且优先报告。

该步骤属于target-database model updating，不得称为原始外部验证，不得称为判别力提高，也不得反向修改base model。

## 解释边界

- raw transport回答跨数据库直接可迁移性；
- cross-fit recalibration回答在eICU进行预先规定的本地校准更新后的性能；
- eICU曾用于Study A，因此全文使用`locked cross-database revalidation`或`internal-external cross-database transport assessment`，不称pristine independent external validation。
