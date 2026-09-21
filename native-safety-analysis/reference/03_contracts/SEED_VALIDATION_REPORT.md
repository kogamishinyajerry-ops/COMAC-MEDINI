# 种子资产验证记录

执行日期：2026-09-21；本地Linux环境，Python 3.13.5，jsonschema 4.26.0。

## 已实际执行

3个JSON Schema通过Draft 2020-12元Schema检查；10个有效FTA输入通过格式校验；能力示例通过格式校验。5个非法模型由语义参考程序全部按预期拒绝。引用和图环等问题超出JSON Schema单独能力，需要语义层检查。

参考程序使用标准库Fraction与真值表，对10个有效模型逐一比较明确预期概率和结构最小割集，并核对5个负例错误类型。退出码：0。

```text
PASS M01_and: p=1/50; MCS=[['A', 'B']]
PASS M02_or: p=7/25; MCS=[['A'], ['B']]
PASS M03_repeated_event: p=11/250; MCS=[['A', 'B'], ['A', 'C']]
PASS M04_absorption: p=1/10; MCS=[['A']]
PASS M05_vote_2_of_3: p=49/500; MCS=[['A', 'B'], ['A', 'C'], ['B', 'C']]
PASS M06_probability_zero_one: p=1; MCS=[['A'], ['B']]
PASS M07_shared_subgraph: p=1/50; MCS=[['A', 'B']]
PASS M08_tiny_probability: p=1/1000000000000000000000000; MCS=[['A', 'B']]
PASS M09_duplicate_reference: p=1/50; MCS=[['A', 'B']]
PASS M10_shared_cause: p=157/500; MCS=[['A', 'B'], ['C']]
PASS N01_unknown_reference: UNKNOWN_REFERENCE: MISSING
PASS N02_cycle: CYCLE: TOP
PASS N03_out_of_range: PROBABILITY: Invalid probability for A: '1.1'
PASS N04_duplicate_id: DUPLICATE_ID: A
PASS N05_unsupported_gate: UNSUPPORTED_GATE: PAND

15/15 synthetic seed checks passed.
This is not a Medini test, B-line application test, or aviation acceptance.
```

## 尚未执行/不涵盖

没有访问或运行用户的Medini、DSH或现有Agent；没有实现或验收A/B正式应用；没有性能基准、工程Gold Case、独立专家签核或适航工具鉴定。3个Schema仅为首个子集草案；结果信封仍需完整语义约束和应用实现。

生产代码不能将本参考脚本作为唯一求解器后再用同源脚本自证正确。
