# V2 MIMIC-to-eICU Transport Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE FIRST TRANSPORT PREDICTION`
- 性质：预设的锁定跨数据库再验证；eICU不是pristine independent external validation

## 范围

- rolling 24 h：M0、M1、M2；
- fixed t24→96：M0、M1、M2；
- development source：MIMIC-IV v3.1；
- transport target：eICU-CRD v2.0。

## 仅由MIMIC决定的refit规则

1. 候选配置限定为既有Gate 3 CatBoost六配置；
2. 对每个模型和分析范围，按`config_id`汇总MIMIC各outer-fold的inner-tuning记录；
3. 首先选择跨outer-fold平均`mean_auprc`最高的配置；
4. tie-break依次为较低平均`mean_logloss`、较浅depth、较小`config_id`；
5. 全队列refit iterations取获胜配置在MIMIC各outer-fold的`median_best_iteration`中位数，并限制在50–700；
6. 使用全部MIMIC evaluable rows按冻结参数重拟合一次；
7. 不更新参数、不重训地应用于全部eICU对应风险集。

配置选择和iterations不得读取eICU outcome、eICU transport probability或eICU transport performance。eICU outcome仅在raw transport probability永久保存后用于聚合评价。

## 评价与表述

- 保存MIMIC全队列CatBoost模型和eICU行级raw transport probability；
- 报告AUPRC、AUROC、Brier、log loss及raw calibration；
- 后续eICU cross-fit intercept-only/intercept+slope更新必须标记为model updating；
- transport结果不得称为全新、未接触的独立外部验证；
- 不得根据transport结果更改特征、窗口、grid或表型。
