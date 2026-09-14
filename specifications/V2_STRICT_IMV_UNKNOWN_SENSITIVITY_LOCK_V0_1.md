# V2 Strict IMV / Unknown Sensitivity Lock V0.1

- 冻结日期：2026-09-13
- 状态：`FROZEN BEFORE FIRST STRICT-IMV/UNKNOWN SENSITIVITY RESULT`
- 范围：rolling 24 h raw outer-fold OOF，MIMIC-IV patient-grouped CV与eICU hospital-grouped IECV

## 1. 目的与边界

本分析检验主要`M2_DYNAMIC_CAT - M1_SNAPSHOT_CAT`比较对IMV证据严格度及Unknown处理的稳健性。它不改变主要结局、模型、变量、窗口、fold、调参结果或比较方向，不用于模型再选择。Unknown绝不在主要分析中编码为0；仅在两个预先规定的极端情景中分别作为阴性或阳性进行边界分析。

## 2. 冻结情景

### MIMIC-IV

- `MIMIC_PRIMARY_A_VS_NONE`：`A=1`、`None=0`，排除`Unknown`；复核主要分析。
- `MIMIC_UNKNOWN_AS_NEGATIVE`：`A=1`、`None/Unknown=0`。
- `MIMIC_UNKNOWN_AS_POSITIVE`：`A/Unknown=1`、`None=0`。

### eICU

- `EICU_PRIMARY_ABC_VS_NONE`：`A/B/C=1`、`None=0`，排除`Unknown`；复核主要分析。
- `EICU_STRICT_AB_VS_NONE`：`A/B=1`、`None=0`，排除`C/Unknown`。
- `EICU_DISTINCT_TIME_VS_NONE`：使用已冻结的`outcome_imv_distinct_time`，保留`0/1`、排除`9`；其中C需至少两个去重治疗时间。
- `EICU_UNKNOWN_AS_NEGATIVE`：`A/B/C=1`、`None/Unknown=0`。
- `EICU_UNKNOWN_AS_POSITIVE`：`A/B/C/Unknown=1`、`None=0`。

上述情景、方向及全部纳排规则在读取任何新敏感性性能结果前冻结。

## 3. 概率生成

- M1、M2训练集始终只使用原主要结局`outcome_imv in {0,1}`；Unknown不进入训练或调参。
- 每个outer fold复用原主要分析已选择的`selected_config_id`和`refit_iterations`，使用同一seed、features、CatBoost参数及训练fold；不重做inner tuning。
- 重新拟合每个原主要模型仅为给held-out Unknown行生成raw probabilities；同一拟合同时重算原主要held-out rows。
- 每fold在原主要行上与永久保存的primary raw OOF逐键比较；要求最大绝对概率差`<=1e-12`。未通过即停止，不生成敏感性结果。
- row-level扩展OOF、fold checkpoint及bootstrap replicate均保存在restricted目录并设权限0600；公开目录只保存聚合结果。

## 4. 指标与不确定性

- 每个情景报告landmarks、stays/groups、events、prevalence。
- 报告M1/M2 raw AUPRC、AUROC、Brier、log loss及`M2-M1`差值。
- 1,000次cluster bootstrap percentile 95% CI：MIMIC按`patient_key`，eICU按`hospital_id`；seed 20260909。
- 报告raw calibration-in-the-large、joint calibration intercept/slope和O/E。
- 对M2报告预设0.5%、1%、2%、3%、5%阈值的DCA与alert burden。

## 5. 解释边界

Unknown-as-negative/positive是不可识别标签的极端边界，不是替代主要结局；strict AB与distinct-time是证据定义敏感性。结果只支持或削弱稳健性，不作因果、真实病因、独立外部验证或部署声明；eICU仍称hospital-grouped IECV。
