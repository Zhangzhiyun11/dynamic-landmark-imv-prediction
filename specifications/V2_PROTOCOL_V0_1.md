# Study B / V2 Protocol V0.1

## 1. 研究目的

开发并验证一个在 ICU 早期重复更新的动态临床预测工具，用于估计当前未接受 IMV 的成人患者在未来 24 小时内发生新发、被记录 IMV 的风险；在完全公平的同算法比较中量化动态轨迹相对于静态/最新快照的增量价值。

## 2. 设计

回顾性、多数据库、多中心、重复 landmark prediction study。每个 eligible ICU stay 可贡献多个 landmark rows。研究遵循 patient/hospital grouping，避免同一患者或医院跨 outer-fold 泄漏。

## 3. 数据源与角色

- MIMIC-IV v3.1：单中心开发与患者级内部验证；可行时增加基于 `anchor_year_group` 的近似时间 transport 分析。
- eICU-CRD v2.0：多中心 hospital-grouped IECV 的主要来源；也用于锁定后的跨数据库再验证。
- eICU 并非未接触的全新外部数据，报告中必须明确其已在 Study A 使用。

## 4. 目标人群

基本资格：成人；每位患者仅采用预先定义的 index ICU stay；在相应 landmark 时仍在 ICU、存活且未接受 IMV；关键时间字段可判定。慢性气切/长期通气、landmark 状态不明和结局 Unknown 按表型规范处理。

逐 landmark 风险集：`t ∈ {24,28,...,72} h`。患者在首次 IMV、ICU 离开或死亡后不再贡献后续 landmark。

## 5. 预测时点、窗口和结局

主要输入窗口为 `[t-12,t]`，主要结局窗口为 `(t,t+24]`；次要结局窗口为 `(t,t+12]`。同一时间戳发生冲突时，结局/干预状态优先用于判定患者是否仍处于风险集，不作为 predictor。

固定 Liu-compatible 分析使用 t=24 h，预测 `(24,96]`。该分析与滚动主要任务分别报告，不能把不同窗口的 AUROC 当成同一 estimand。

## 6. 候选预测信息

基础信息包括 age、sex、BMI、pre-ICU LOS、admission source、ICU type，以及经过跨库审计的合并症/基础严重程度字段。landmark 快照使用 `t` 前最近的有效值，并设置最大陈旧时间；动态层使用 level、range、variability、robust slope、abnormal burden、persistence、transition、measurement density 和 support escalation。

核心层不依赖 ABG；ABG 为预先分开的扩展模型。所有派生值只使用 landmark 当时及之前数据。

## 7. 模型与比较

四模型定义见 model matrix。M1 和 M2 使用完全相同的 CatBoost 学习器、搜索预算和 outer folds，唯一系统差异是是否加入动态轨迹；M2 和 M3 使用相同的信息集，量化非线性算法贡献。

## 8. 验证与评价

主要结果由 eICU hospital-grouped IECV 的 pooled held-out predictions 估计，并同时报告 fold/hospital heterogeneity。MIMIC 结果作为内部/近似时间验证。所有比较使用 paired predictions 和 cluster-resampling confidence intervals。

主要指标 AUPRC；共同关键指标 AUROC、Brier、log loss、calibration intercept/slope。临床效用评价包括 DCA、固定 sensitivity 对应 PPV、alerts/100 patient-landmarks、alerts per detected event，以及同一 stay 的 alert episodes（预设冷却期后计算）。

## 9. 缺失与可实施性

Unknown 不是“无”。缺失值处理在训练折内完成；缺失指示器资格依据训练折。测量频率可作为 care-process proxy 单独检验，但不能解释为生理机制。核心动态域必须满足两个数据库和足够医院的覆盖 Gate；否则降为扩展或排除。

## 10. 伦理与解释边界

本研究预测 observed IMV，不能证明动态变量导致插管，也不能证明模型提醒改善结局。未进行 prospective silent validation、workflow impact trial 或 cost-effectiveness analysis 时，不提出部署主张。

## 11. 预设分析顺序

1. T0：设计和治理冻结。
2. T1：outcome-blind 映射、时间字段、覆盖率和合成测试。
3. T2：滚动 cohort/outcome phenotype 实现与独立 QC；仍不训练。
4. T3：predictor derivation、fold 构建和不可变 manifest。
5. T4：MIMIC/IECV nested model development。
6. T5：锁定后跨数据库评价、校准、DCA、alert burden。
7. T6：敏感性、异质性、审计、手稿和投稿前检查。
