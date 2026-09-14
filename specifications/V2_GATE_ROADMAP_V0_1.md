# Study B / V2 Gate Roadmap V0.1

## T0 / Gate 0：研究治理

交付：protocol、SAP、model matrix、candidate dictionary、Study A isolation manifest。

通过标准：estimand、time zero、lookback/horizon、fair comparisons、验证层级、泄漏清单和 claim boundary 均已冻结。

## T1 / Gate 1：Outcome-blind feasibility

交付：两库 source-to-variable mapping、单位/时间戳审计、医院接口覆盖、候选域分层、synthetic tests、fold construction spec、clinical action/threshold spec。

通过标准：每个核心变量存在可靠时间锚点和跨库映射；coverage rule 预先执行；未知不编码为无；fold 可估计；没有读取 V2 outcome-linked performance。

Gate 决定只能为：`PASS AND PERFORMANCE EVALUATION AUTHORIZED`、`PASS WITH RESTRICTED FEATURE SET` 或 `FAIL/REVISE WITHOUT PERFORMANCE`。

## T2 / Gate 2：Rolling phenotype and cohort lock

交付：patient-landmark cohort、每时点 at-risk assertions、source-specific IMV phenotype、Unknown/competing-risk audit。患者级文件受限。

通过标准：landmark 前后物理隔离；first IMV/time ordering 可复现；随机抽样和 edge-case 测试通过。

## T3 / Gate 3：Predictor/fold/model implementation lock

交付：静态快照与动态特征、outer/inner folds、CatBoost/ElasticNet budget、all-data-independent manifest、hash lock。

通过标准：M1/M2 公平性断言通过；同一 patient/hospital 不跨 fold；所有预处理在训练折；禁止变量为零行。

## T4 / Gate 4：Nested development and IECV

交付：held-out predictions、paired metrics/CI、校准与异质性；先永久保存 raw predictions。

通过标准：无 fold leakage；可重复；任何失败不通过追加调参追逐性能。

## T5 / Gate 5：Locked transport and clinical utility

交付：MIMIC→eICU locked transfer、raw/recalibrated 结果、DCA、alert burden、fixed t24→96 Liu-compatible comparison。

通过标准：thresholds 未按 eICU 结果选择；recalibration 明确为 model updating；性能与临床效用分开解释。

## T6 / Gate 6：Sensitivity, audit and manuscript

交付：预设敏感性、TRIPOD+AI/PROBAST+AI 审计、claim-to-source map、result lock、手稿与投稿前检查。

最终状态分开报告：`publication_ready`、`independent_external_validation_ready`、`clinical_deployment_ready`。
