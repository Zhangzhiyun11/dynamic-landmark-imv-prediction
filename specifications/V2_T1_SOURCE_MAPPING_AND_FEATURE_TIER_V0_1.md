# V2 T1 Source Mapping and Feature Tier V0.1

- 日期：2026-09-09
- 性质：outcome-blind mapping lock
- 性能信息：未读取、未计算

## 1. Tier A：两库核心动态生理域

以下变量进入 M1 的 latest snapshot，并以同样来源进入 M2 的 trajectory block：

| 域 | MIMIC-IV | eICU | 当前决定 |
|---|---|---|---|
| RR | `mimiciv_derived.vitalsign.resp_rate` | `vitalperiodic.respiration`，nurseCharting fallback | Tier A |
| SpO2 | `vitalsign.spo2` | `vitalperiodic.sao2`，`O2 Saturation/SpO2` fallback | Tier A |
| HR | `vitalsign.heart_rate` | `vitalperiodic.heartrate`，nurseCharting fallback | Tier A |
| SBP | `vitalsign.sbp/sbp_ni` | `vitalperiodic.systemicsystolic`，`vitalaperiodic.noninvasivesystolic` | Tier A |
| MAP | `vitalsign.mbp/mbp_ni` | `vitalperiodic.systemicmean`，`vitalaperiodic.noninvasivemean`，nurseCharting fallback | Tier A |
| Temperature | `vitalsign.temperature` | nurseCharting C/F 优先，`vitalperiodic.temperature` 补充 | Tier A |
| GCS total | `mimiciv_derived.gcs.gcs` | nurseCharting `Glasgow coma score/GCS Total` | Tier A with systematic-missingness audit |

共同规则：12 h lookback 分为 6 个 2 h bins；同 timestamp 来源优先级和单位转换先于分箱；空 bin 不设为正常、不 forward fill。latest snapshot 只使用 `<= landmark` 的最后有效值，并在 T2 冻结 staleness limit。

## 2. Tier B：临床重要、接口异质的增强域

### Oxygen-support block

MIMIC 使用 `oxygen_delivery` 的 device/flow；eICU 合并 nurseCharting `O2 Admin Device`、`O2 L/%`、respiratoryCharting `O2 Device/LPM O2/FiO2` 及明确的 HFNC/NIV treatment signal。

有序候选层级：room air=0；low-flow conventional oxygen=1；high-concentration mask=2；HFNC=3；NIV/CPAP/BiPAP=4。IMV/ventilator/ETT 不编码为 5，而用于风险集排除/结局判定。trach collar 和不明确 device 先置 Unknown，待 phenotype/慢性气切审计。

`O2 L/%` 数值同时混合 L/min 与百分比，不允许仅凭数值盲目当作 flow 或 FiO2；必须结合 device/同时间 respiratoryCharting 进行来源特异解析，不能解析则只标记“oxygen documentation present”。

### Urine-output block

MIMIC 使用 `mimiciv_derived.urine_output.urineoutput`。eICU 只采用 `intakeoutput.cellpath` 位于 `I&O|Output (ml)` 且 celllabel 明确为 urine/urine catheter/voided amount 的体积记录；`Urine Count/Occurrence` 不作为 ml。体重归一化只在有效 pre-landmark weight 可用时进行。

### Vasopressor block

MIMIC 使用 `vasoactive_agent`。eICU 使用 `infusiondrug` 的 norepinephrine/Levophed、epinephrine、phenylephrine、vasopressin、dopamine。首先构造 current exposure；NE-equivalent dose 只对单位和浓度可可靠转换的记录计算。医院无 infusion interface 时保持 Unknown，绝不编码为未使用。

Tier B 的 primary inclusion 需要 T2 映射断言通过；同时预设“排除 Tier B”和“仅 interface-supported hospitals”敏感性分析。

## 3. Tier C：扩展分析

- Routine laboratory snapshots/deltas：WBC、hemoglobin、platelet、electrolytes、bicarbonate、BUN、creatinine、glucose。
- ABG extension：pH、PaO2、PaCO2、lactate；与 non-ABG core 分开报告。
- FiO2/SF/ROX-like：只有在 FiO2 来源、单位和时间配对通过审计的记录中计算；不得把未记录 FiO2 设为 0.21。
- RASS：eICU 仅 6 家医院有相应接口，不能进入跨医院 primary core。

## 4. Baseline/static context

M1/M2 共同包括 age、sex、admission source、ICU type、pre-ICU LOS。BMI 作为共同 predictor 保留但允许 training-fold missingness handling；当前 MIMIC 既有 t24 derivative 的 BMI coverage 约 32.7%，eICU height+weight source coverage 约 99.98%，该巨大不对称必须报告。

## 5. Dynamic summaries

每个适用域预设：latest、median、min/max、IQR 或 SD、Theil-Sen slope、异常 burden、longest run、onset/transition count、nonempty-bin count。support/vasopressor 另加 current/max level、time-weighted burden、escalation count、time since escalation。动态模型不允许拥有 M1 没有的同一时点 latest 值；其新增信息必须真正来自时间轨迹。

## 6. Leakage exclusions

RSI drugs、插管/机械通气医嘱、ETT/ventilator status、插管准备和 post-IMV ventilator settings 全部禁止进入 predictor table。含“ventilator”的氧疗 device 行用于判定已不在风险集，不作为 support predictor。
