# V2 Interface-complete Hospitals Sensitivity Lock V0.1

- 冻结日期：2026-09-13
- 状态：`FROZEN BEFORE FIRST INTERFACE-COMPLETE OUTCOME-LINKED RESULT`
- 范围：eICU rolling 24 h hospital-grouped IECV raw OOF

## 1. 目的

检验主要`M2_DYNAMIC_CAT - M1_SNAPSHOT_CAT`结论在三个Tier B数据接口均明确支持的医院中是否保持，从而评估oxygen、urine和vasopressor接口异质性对主要比较的影响。该分析为预设敏感性，不用于筛选医院、变量或主结果。

## 2. Interface-complete定义

医院只有在`V2_PREDICTOR_MATRIX_V0_1.parquet`中以下三个hospital-constant flags均恒为1时进入：

- `oxygen_interface_supported`；
- `urine_interface_supported`；
- `vaso_interface_supported`。

任何一个flag为0、缺失或院内不恒定均排除。定义由predictor-only flags形成，不读取结局、概率或性能。冻结前的outcome-blind审计显示208家医院中96家满足定义，覆盖312,313/572,055个predictor rows；该计数不作为性能选择门槛。

## 3. 模型与风险集

- 仅使用已永久保存的eICU raw hospital-held-out OOF probabilities；
- 主要比较为`M2_DYNAMIC_CAT - M1_SNAPSHOT_CAT`；
- 不重新训练、调参、校准或改变outer folds；
- 仅过滤held-out rows所属医院，不改变各outer run原始training set；
- 结果称interface-complete hospital sensitivity，不称pristine independent external validation。

## 4. 指标与不确定性

- 报告subset的医院数、landmarks、events和prevalence；
- 报告M1/M2 raw AUPRC、AUROC、Brier、log loss及M2−M1差值；
- 1,000次hospital-cluster bootstrap，seed 20260909，percentile 95% CI；
- 报告raw calibration-in-the-large、joint calibration intercept/slope和O/E；
- 对M2报告预设0.5%、1%、2%、3%、5%阈值的DCA与alert burden；
- restricted bootstrap replicate不得进入公开结果目录。

## 5. 解读边界

若M2−M1方向与全eICU一致，支持主要增量结论不完全由缺失接口医院驱动；若精度下降或方向改变，只说明interface-defined case mix/recording subset中的稳健性有限。该分析不是随机亚组比较，不作医院质量排名、interaction、因果或部署声明。
