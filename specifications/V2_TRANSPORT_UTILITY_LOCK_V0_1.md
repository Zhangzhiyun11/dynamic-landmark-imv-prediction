# V2 Transport Utility Lock V0.1

- 冻结日期：2026-09-11
- 状态：`FROZEN AFTER TRANSPORT RECALIBRATION BEFORE FIRST TRANSPORT UTILITY RESULT`
- 性质：沿用2026-09-09已冻结的clinical-utility阈值，不新增或择优阈值

## 输入与版本

- rolling 24 h与fixed t24→96；
- M0、M1、M2；
- raw、eICU医院折cross-fit intercept-only、eICU医院折cross-fit intercept+slope probabilities全部并列评价；
- base model及raw probability不作任何改变。

## 阈值与指标

- 固定absolute-risk thresholds：0.5%、1%、2%、3%、5%；
- 报告sensitivity、specificity、PPV、NPV、alerts per 100 landmarks、alerted stays per 100、episodes per 100 stays、alerts per detected event、net benefit及treat-all net benefit；
- rolling alert episode沿用8 h cooldown定义；
- 不依据eICU结果选择最佳阈值，不反向更改模型。

## 声明边界

- cross-fit probabilities属于eICU本地model updating后的估计；
- DCA阳性仅支持`potential clinical utility`，不能证明临床结局获益或可部署性；
- 无前瞻性silent validation/impact trial时，不作`improves outcomes`或`ready for deployment`声明。
