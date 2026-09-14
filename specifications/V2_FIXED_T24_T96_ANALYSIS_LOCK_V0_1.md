# V2 Fixed t24 to t96 Analysis Lock V0.1

- 冻结日期：2026-09-10
- 性质：预设 Liu-compatible secondary；任何 fixed-window performance 产生之前

## Design

- single landmark：ICU入科后24 h；
- outcome window：`(24 h,96 h]`，即 horizon=72 h；
- risk set：t24仍在 ICU、未死亡、t24及以前无敏感 IMV evidence；
- primary evaluable：outcome 0/1，Unknown排除；
- MIMIC：31,751 rows、1,488 events；eICU：78,409 rows、1,418 events；
- predictors：M0/M1/M2 使用 exact registry；M1/M2 的动态生理窗口仍为 `(12 h,24 h]`；
  M0 PaO2 使用 `(0 h,24 h]` latest arterial value；
- folds、CatBoost 6-configuration grid、inner group split、seed、missing handling 与 rolling
  primary 完全相同；
- allowed claim：Liu-compatible time-window comparison；
- prohibited claim：exact Liu replication，或直接把本研究估计与 Liu published AUROC 当作
  同一测试集上的显著性比较。
