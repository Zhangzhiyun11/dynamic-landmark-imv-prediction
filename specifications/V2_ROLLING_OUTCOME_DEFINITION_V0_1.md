# V2 Rolling Outcome Definition V0.1

- 冻结日期：2026-09-09
- 状态：`FROZEN BEFORE ROLLING LABEL COUNTS AND MODELING`

## 1. Target

在每个 landmark 时仍未接受 IMV 的成人 ICU 患者中，预测未来 24 h 内 first observed new IMV documentation。次要 horizon 为 12 h；固定 t24→96 只用于 Liu-compatible secondary comparison。

## 2. Risk-set rule

Landmarks：24、28、…、72 h。每行必须满足：患者仍在 ICU、未死亡、在 `t` 及之前没有任何敏感 IMV/人工气道证据、存在 `[t-12,t]` 内至少一个核心生命体征。`t` 时刻的 IMV 证据属于既往状态并排除；结局窗口左开右闭 `(t,t+h]`。

每位患者沿用 Study A 已选择的 first eligible ICU stay；同一 stay 可以贡献多个 landmark rows，首次 IMV、ICU discharge 或 death 后停止贡献。

## 3. MIMIC outcome

- `A/1`：`mimiciv_derived.ventilation` 的 `InvasiveVent` 严格 interval 首次 start 位于结局窗口。
- `Unknown/9`：无严格 start，但窗口内存在既有高特异性 broad airway/invasive-mode/procedure evidence。
- `None/0`：窗口内无上述证据。

Primary evaluable rows 排除 Unknown。Sensitivity：Unknown-as-negative、Unknown-as-positive，以及 broad-evidence outcome。慢性气切/landmark 已有人工气道证据用于风险集排除，不作为预测变量。

## 4. eICU outcome

每个 `(landmark,horizon)` 独立按 `A>B>C>Unknown>None` 分类：

- A：明确 start anchor 并有 ±6 h 独立来源支持，或两类高特异性来源配对；strict start 位于窗口。
- B：无 A，但明确 start anchor 位于窗口。
- C：无 A/B，窗口内至少两条按 stay+offset+normalized treatment string 去重后的 invasive mechanical ventilation treatment records。
- Unknown：无 A/B/C，但窗口内至少一条 IMV support record。
- None：窗口内未观察到上述证据。

Primary `A+B+C=1`、`None=0`、`Unknown=9`。Sensitivity 包括 A+B-only、distinct-treatment-time C 和 Unknown bounds。任何医院接口缺失不得把 Unknown 改成 0。

## 5. Competing events

Death 或 ICU discharge 若发生在 horizon 内且早于首次 IMV documentation，记录 competing-event type。主要 prediction target 仍为 observed IMV；另行进行 competing-event handling sensitivity，不把死亡自动合并为 IMV。

## 6. Prohibited use

Outcome evidence、ventilator status、ETT、IMV treatment strings 和 start anchors 只允许进入 risk-set/label table，禁止进入 predictor table。患者级 outcome 文件只写入 `study_b_v2/restricted/`。
