# 公共契约种子 v0.1.0

这里是两条线第一轮对齐的最小交换与数学验证资产，**不是完整安全软件、不是Medini适配器，也不是业务Gold Case**。

## 内容

schemas/static_fta.schema.json：固定概率、独立基本变量、相干静态FTA。AND/OR允许重复引用并保留同一变量；K_OF_N需不同输入引用，k不得大于输入数。常量通过概率0/1的基本事件表达，但割集仍按结构布尔语义计算，不删除概率为0的事件。

schemas/capabilities.schema.json：每项能力的验证状态。示例刻意标unverified，不能当作已实测结果。

schemas/analysis_result.schema.json：结构化结果信封。计算成功不授予审批；实现仍需补充概率/割集状态一致性等跨字段语义约束。

cases.json与examples：10个有效数学例题，含重复事件、共享子图、吸收、2/3表决、0/1和极小概率；5个非法模型。预期值为明确小模型的解析数学结果。

verify_seed_cases.py：仅依赖Python标准库，以有理数穷举和直接布尔求值检查种子例题。算法规模上限20个基本事件，供小模型参考，不是生产求解器或正式工具鉴定证据。它不能自行证明现实系统模型正确，尚需人工复核与独立工程案例。

## 运行

```bash
python 03_contracts/verify_seed_cases.py
```

从本目录运行时用`python verify_seed_cases.py`。该脚本检查本包的窄语义，不是完整JSON Schema引擎；开发项目应另用已审核并锁版的Schema实现进行格式验证。本包构建时已另外运行JSON Schema静态验证；报告见SEED_VALIDATION_REPORT.md。

## 刻意没有包含

没有真实航空模型/可靠性数据，没有Medini私有工程，没有官方API函数，没有DSH插件，没有失效率/修复/潜伏/共因/动态门语义，没有完整FMEA/需求/审批Schema，也没有任何正式适航批准。

字段/接口仅为项目建议，首次冻结前由两线技术负责人和安全专家共同评审。双方可以共享格式和案例；生产求解器与独立参考不可退化为同一份实现。
