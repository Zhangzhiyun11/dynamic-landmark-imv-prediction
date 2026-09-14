# V2 Model Implementation Budget V0.1

- 冻结日期：2026-09-09
- 状态：`FROZEN BEFORE FIRST OUTCOME MERGE OR MODEL PERFORMANCE`

## 1. 公平比较

`M0_LIU_ADAPTED`、`M1_SNAPSHOT_CAT`、`M2_DYNAMIC_CAT` 使用完全相同的
CatBoost 搜索空间、inner folds、selection metric、early-stopping 规则和随机种子。
M1 与 M2 使用完全相同的 patient-landmark rows；M2 的唯一系统差别为预先冻结的
trajectory/Tier B block。`M3_DYNAMIC_EN` 使用与 M2 相同的信息域。

## 2. CatBoost nested budget

- objective: binary Logloss；inner selection: mean AUPRC，tie-break 依次为 lower logloss、
  shallower depth、smaller configuration id；
- maximum iterations: 700；early stopping patience: 75；
- fixed: `random_strength=1`, `l2_leaf_reg=10`, `rsm=0.8`,
  `bootstrap_type=Bayesian`, `bagging_temperature=1`, `loss_function=Logloss`,
  `thread_count=4`, `allow_writing_files=false`, `verbose=false`；
- grid（6个 configuration，全部评估，不依据 outer test performance 改动）：
  depth ∈ {4,6,8} × learning_rate ∈ {0.03,0.07}；
- no class weighting、no SMOTE；验证概率保持真实 prevalence；
- outer refit iterations 使用 winning configuration 在 inner folds 的 median best iteration，
  下限 50、上限 700；outer test 不参与 iteration 或参数选择；
- primary seed: 20260909；repeat-seed sensitivity: 20260910、20260911，仅在 primary
  pipeline 完成后执行，不用于挑选主结果。

## 3. Inner folds

每个 outer training set 内使用3折 group-preserving inner validation：MIMIC 按 patient，
eICU 按 hospital。inner assignment 由既有 frozen outer-group IDs 的确定性 hash 生成，
同一 patient/hospital 不跨 inner folds。类别可估计性在每个 outer run 前断言。

## 4. Elastic Net budget

连续变量的 median imputation、winsorization（0.5th/99.5th percentiles）与标准化均仅在
inner/outer training data 拟合；类别 one-hot schema 同样仅由 training data 建立，未知层级
映射为 explicit `__OTHER__`。

- solver: saga；penalty: elasticnet；max_iter=3000；tol=1e-4；
- `l1_ratio ∈ {0,0.25,0.5,0.75,1}`；
- `C ∈ {0.01,0.03,0.1,0.3,1}`；
- selection metric 与 tie-break 同上；no class weighting。

## 5. Missingness and categorical rules

CatBoost 连续 missing 保持 NaN，类别 missing 映射为 `__MISSING__`。Unknown 不编码为 0。
eICU 无 infusion interface 时 vasopressor features 保持 missing；支持接口且没有匹配药物
记录时可判定 exposure=0。hospital ID、patient/stay ID、time-group label、outer fold、
documentation counts、raw measurement offsets 和 interface flags 不进入 primary predictors。

## 6. Held-out output rule

先永久保存每个 outer-fold patient-landmark 的 raw held-out probability，再计算 aggregate
metrics。bootstrap/resampling 按 patient/hospital cluster，禁止把 landmark rows 当独立样本。
任何失败只能修复实现错误并形成版本化 addendum，不能根据 outer/eICU 结果扩展搜索空间。
