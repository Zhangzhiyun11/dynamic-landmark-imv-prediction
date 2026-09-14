# Study B / V2 Statistical Analysis Plan V0.1

## 1. Estimands

Primary estimand: 在 eligible patient-landmarks 中，`M2_DYNAMIC_CAT` 相对 `M1_SNAPSHOT_CAT` 的 paired AUPRC difference，用 hospital-grouped IECV held-out predictions 计算。

Key secondary estimands:

- paired AUROC difference（M2-M1）；
- Brier score 和 log-loss difference（M2-M1）；
- M2 对 M0_LIU_ADAPTED 的 paired discrimination difference；
- M2 对 M3_DYNAMIC_EN 的 paired difference；
- fixed t24→96 Liu-compatible 分析；
- MIMIC 内部/近似时间 transport 与 eICU fold/hospital heterogeneity。

## 2. 重复观测

每个 stay 可贡献多个 landmark rows。拆分单位至少为 patient；eICU 的 outer split 单位为 hospital。bootstrap/置信区间不得按 landmark row 独立抽样：MIMIC 按 patient cluster，eICU 主要按 hospital cluster；必要时使用 hospital 内 patient 的两阶段 resampling 作为敏感性分析。

## 3. Outer validation

eICU 采用确定性 hospital grouping。构建 folds 时允许仅用医院 ID、样本量、结局事件数、ICU type 和接口覆盖来保证可估计性；fold 在任何模型拟合/性能查看前冻结。医院不能被拆分。若单个医院事件过少，不报告不稳定的医院级 AUROC；以 fold-level/层级校准异质性为主。

MIMIC outer folds 按 patient 分组。若时间映射 Gate 通过，预先按 `anchor_year_group` 构建较早开发、较晚验证；否则只称 grouped internal validation。

## 4. Inner training and tuning

所有步骤嵌入 outer training data：范围检查、winsorization（如启用）、类别编码、缺失处理、缺失指示器资格、特征降级、hyperparameter tuning 和校准。CatBoost 与 Elastic Net 的搜索空间在 T3 冻结，M1 与 M2 的 CatBoost 搜索预算完全相同。

不使用 outcome-informed univariate screening、全数据 SHAP screening 或验证折再调参。不以 SMOTE/随机过采样改变验证分布；若训练加权，权重规则预先固定且概率需在真实 prevalence 下校准。

## 5. Feature representation

对每个连续动态域预设：latest、median、min/max、IQR/SD、Theil-Sen slope、异常 burden、longest run、transition/onset、observed-bin count 和 measurement count。主要 12 h lookback 分 6 个 2 h bins；不跨 bin carry forward。快照陈旧上限和异常阈值在 T1 后按临床/来源规则冻结。

support escalation 使用有序支持等级的最新值、最大值、时间加权 burden、升级次数和距最近升级时间。S/F 与 ROX-like 仅在时间配对满足容差时计算；FiO2 不可靠时保持缺失，不用室内空气默认值替代。

## 6. Performance

每个数据源/outer fold 报告 event prevalence、N patients、N stays、N landmarks、events。主要指标和 CI：

- AUPRC、AUROC 及 paired differences；
- Brier score、scaled Brier（作为补充）、log loss；
- calibration intercept、slope、flexible calibration curve；
- DCA net benefit；
- PPV、specificity、alerts/100 landmarks、unique alert episodes/100 stays、alerts per detected event。

AUPRC 必须与 prevalence 同时解释；AUROC <0.70 不自动等于无价值，AUROC >0.80 也不自动等于可部署。

## 7. Dynamic superiority rule

可写“dynamic features improved prediction”需同时满足：

1. M2-M1 paired AUPRC difference 的 95% CI 下限 >0；
2. AUROC difference 同方向，并完整报告 CI；
3. calibration/Brier 没有预设意义上的恶化；
4. 预先定义行动阈值区间内至少存在一致的正净获益，且 alert burden 完整报告。

若仅统计学显著而绝对差值/净获益很小，只写“incremental predictive information”，不写 clinically meaningful improvement。

## 8. Liu comparison rule

只在同一风险集、同一 outcome、同一 outer folds、同一 CatBoost budget 下比较 M2 与 M0。固定 t24→96 分析最接近 Liu 的时间结构，但因原文实现细节、表型和数据库版本可能无法完全重建，名称固定为 `LIU_ADAPTED`/`LIU_COMPATIBLE`，禁止使用 exact replication。

## 9. Calibration

首先报告 untouched held-out probabilities。任何 intercept-only 或 intercept+slope recalibration 必须 cross-fit，标记为 model updating，并与 raw performance 并列；recalibration 不能被描述为提高 discrimination。无第三独立数据时，updated model 不声称独立验证。

## 10. Sensitivity analyses

- 12 h vs 24 h horizon；
- 6 h vs 12 h vs 24 h lookback（不通过结果挑选主要窗口）；
- 核心 non-ABG vs ABG extension；
- 严格 IMV evidence vs Route C/Unknown bounds；
- interface-complete hospitals；
- competing death/discharge handling；
- 首个 landmark only vs all rolling landmarks；
- measurement-intensity excluded；
- potential decision-proxy fields 的更严格 lag/exclusion。

## 11. Multiplicity and interpretation

主比较只有 M2-M1 的 AUPRC difference。其他比较为 key secondary/exploratory，报告 effect sizes 与 CI，不以多重 p-value 筛选结论。亚组至少考虑 ICU type、hospital volume/event stratum、respiratory-support interface coverage；不作医院质量排名。

## 12. 临床效用阈值待决项

在 V2 performance 解盲前，需由临床场景明确：警报触发后执行何种可逆行动、最低可接受 sensitivity、每 100 patient-landmarks/每班可接受警报数、cooldown period。未完成前只允许技术开发，不允许 clinical-utility superiority 的正式检验。
