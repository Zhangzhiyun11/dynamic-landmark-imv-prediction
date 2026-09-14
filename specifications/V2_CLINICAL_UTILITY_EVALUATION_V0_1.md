# V2 Clinical-Utility Evaluation V0.1

- 冻结日期：2026-09-09
- 状态：`TECHNICAL EVALUATION LOCKED; DEPLOYMENT CLAIM NOT AUTHORIZED`

## Intended low-risk action

模型警报只对应“在床旁重新评估呼吸状态并核实氧疗装置、FiO2、意识和循环变化”，不对应自动插管、不对应自动升级呼吸支持，也不替代 clinician judgement。

## Prespecified evaluation grid

- Absolute-risk thresholds：0.5%、1%、2%、3%、5%。
- Development-derived targets：80% 和 90% sensitivity，对应阈值只在相应训练/开发数据中确定，然后原样转移。
- Rolling alert burden：alerts per 100 patient-landmarks、patients alerted per 100 stays、alerts per detected event。
- Alert episode：同一 stay 在 8 h cooldown 内的连续阳性只计为一个 episode；原始每-landmark 指标仍完整报告。

这些阈值用于完整展示，不允许按 eICU 结果选择“最好”的阈值。由于尚无本地 prospective workflow data，本方案不预设“可接受”的最终警报数，也不允许仅凭 DCA 宣称临床可部署。

## Clinical-utility claim rule

只有当 raw/cross-fit probability calibration、net benefit、PPV 和 alert burden 在同一阈值上共同支持，且该阈值对应明确的可逆行动时，才可写“potential clinical utility”。在没有前瞻性 silent validation 或 impact trial 时，不得写“improves outcomes”。
