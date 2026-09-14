# V2 Explicit Measurement-timing Proxy Sensitivity Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN BEFORE FIRST NO-TIMING-PROXY MODEL FIT OR RESULT`
- 模型名：`M2_NO_TIMING_PROXY_CAT`

## 目的与边界

Primary M2已排除documentation counts、raw measurement offsets和interface flags，但仍包含`oxygen_time_since_latest_h`。本敏感性仅移除这一显式测量时序代理，不能声称消除了所有由missingness或临床记录过程携带的measurement-intensity information。

## 冻结实现

1. rolling及fixed、MIMIC及eICU均使用与primary M2完全相同的风险集、outcome、outer folds和其余139个features；
2. 每个outer fold原样复用该fold primary M2已经由inner validation选出的`config_id`和`refit_iterations`；
3. 不重新调参，不读取当前outer test performance决定任何实现；
4. 使用相同CatBoost固定参数和seed；
5. 先永久保存raw held-out probabilities，再计算aggregate performance；
6. 后续与primary M2及M1做配对cluster bootstrap。

该分析只检验primary结论是否依赖`oxygen_time_since_latest_h`，不作为新主模型候选。
