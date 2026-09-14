# V2 M3 Elastic Net Implementation Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE FIRST M3 OUTCOME-BASED FIT OR RESULT`
- 范围：rolling 24 h；MIMIC与eICU；140个与M2完全相同的features

## Fold-local preprocessing

每个inner-training或outer-training fold独立拟合：

1. 连续变量：0.5th/99.5th percentile winsorization、median imputation、mean/SD standardization；全缺失列固定变换为0；
2. 类别变量：训练折内类别建立one-hot schema；missing为`__MISSING__`，训练折未见类别映射为显式`__OTHER__`；
3. 所有preprocessing参数只应用于对应validation/test fold，不使用held-out rows拟合；
4. 不添加未在feature registry中的缺失指示器或变量。

## Model与grid

- `LogisticRegression(solver="saga", penalty="elasticnet", max_iter=3000, tol=1e-4)`；
- no class weighting、no over-sampling；
- `l1_ratio ∈ {0, 0.25, 0.5, 0.75, 1}`；
- `C ∈ {0.01, 0.03, 0.1, 0.3, 1}`；
- primary seed `20260909`；
- 每个outer training set内按既有group ID的确定性hash建立3个inner folds。

选择顺序：较高mean inner AUPRC、较低mean inner log loss、较高l1_ratio（更稀疏）、较低C、较小config ID。除非数值完全相同，后四项不改变AUPRC优先级。

## 输出与声明

- 每个outer fold保存tuning摘要、raw held-out probability和系数；
- 先合并并永久保存raw OOF probability，再计算aggregate metrics；
- M3用于量化算法贡献与系数稳定性，不取代primary M2；
- 若出现convergence warning，完整记录`n_iter`/warning而不扩展搜索空间或改变tol/max_iter。
