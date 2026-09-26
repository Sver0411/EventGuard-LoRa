# EventGuard-LoRa 最终研究总结

## 数据集与审计

`FINAL_BALANCED_HARDWARE_SET_V1` 包含 96 组真实硬件运行：ESP32-S3 + E220-400T22D，策略为 EVENTGUARD、IMPORTANCE_ONLY、UNIFORM_BUDGET、RANDOM_BUDGET；损失模型为 RANDOM_COPY、BURST_SAMPLE；配置损失率为 20%、30%；配对 seeds 为 31–36，每个条件 `n=6`。

这六个 seed 是 Stage 1 交错执行中最早连续完成且覆盖全部条件的 evaluation seeds。纳入规则只依据完成度与数据完整性，不依据结果。最新离线 checker 对 96 组逐一重验：raw SHA256、执行顺序、固件与算法配置 provenance、trace/loss-calendar hash、54 条 EVT/SAMPLE、逐副本 DATA/ACK 日历、importance/copy/link replay、END 与 UART_DIAG counters、预算和指标重算，全部通过。选中集没有非计划物理 DATA/ACK 缺失、策略分叉或样本投递结果分叉。

后续扩展 seed37 的 run103（UNIFORM_BUDGET / RANDOM_COPY / 30%）出现低频 ACK receive-path stall，相关 raw log 与诊断保留在原目录。根因未确认，也没有声称问题已修复。seed37 的不完整扩展不进入主配对统计。

## 三个研究问题

### 1. EventGuard 与 Importance Only

四种模型/损失率条件中，EVENTGUARD 与 IMPORTANCE_ONLY 的 critical delivery 在全部六个配对 seed 上完全相同。EventGuard 每次运行平均多发送：

| 条件 | 多出的 DATA copies | 多出的总字节 |
|---|---:|---:|
| RANDOM_COPY 20% | 30.50 | 1,098.5 |
| RANDOM_COPY 30% | 33.00 | 1,120.2 |
| BURST_SAMPLE 20% | 21.17 | 773.5 |
| BURST_SAMPLE 30% | 27.17 | 951.2 |

因此，本数据中 importance 机制解释了观察到的 critical delivery；被评估的 link adaptation 没有带来额外 critical delivery，并增加了传输成本。Importance Only 在四个条件下均以相同 critical delivery 使用更少的 DATA copies 和 bytes。

### 2. 相同 DATA-copy budget 下的盲分配对照

每个条件、seed 的 Uniform Budget 与 Random Budget 均与 EventGuard 精确匹配 DATA-copy 数。事后统计显示，EventGuard 发往 ground-truth CRITICAL 样本的 DATA copy 占比高于两个盲分配策略：

| 条件 | EventGuard | Uniform Budget | Random Budget | Importance Only |
|---|---:|---:|---:|---:|
| RANDOM_COPY 20% | 27.4% | 23.0% | 24.1% | 34.8% |
| RANDOM_COPY 30% | 26.9% | 24.0% | 24.0% | 34.8% |
| BURST_SAMPLE 20% | 29.3% | 21.1% | 23.9% | 34.8% |
| BURST_SAMPLE 30% | 28.1% | 22.7% | 23.9% | 34.8% |

ground-truth labels 只用于运行后的统计，没有提供给 Uniform/Random budget allocator。

在 critical delivery 上，EventGuard 对 Uniform Budget 在三个条件下六组全平；仅 RANDOM_COPY 30% 条件下有 2/6 个 seed 更高、4/6 持平，配对均值差为 +0.0256。对 Random Budget，RANDOM_COPY 20% 的均值差为 +0.0128（1 高、5 平），RANDOM_COPY 30% 为 +0.0256（2 高、4 平），两种 burst 条件均为 6/6 持平。没有该批次内的 paired loss，但 `n=6` 不足以支持普遍优势结论。

### 3. BURST_SAMPLE

四种策略的 critical delivery 均值在每个 burst 条件完全相同：20% 为 0.7821，30% 为 0.7179；EventGuard 对各 baseline 均为 6/6 配对持平。这符合该模型的定义：某个 logical sample 的所有 copy opportunity 同时被擦除时，同一 sample 的额外副本无法恢复它。该结论只针对本研究的 deterministic BURST_SAMPLE 模型。

## Pareto 与仿真一致性

- IMPORTANCE_ONLY 在 critical delivery 对 DATA copies 与总字节的四个条件 Pareto 分析中均位于前沿：4/4。
- EVENTGUARD 两种成本前沿均为 0/4。
- host 与硬件在 96 组上的 importance/copy/link replay 无分歧；每个样本的投递结果也与 frozen host reference 完全一致。各条件策略排序和 paired contrast 方向相同。
- 这是对 deterministic trace 与应用层注入路径的执行一致性证据，不代表对真实 RF fading、干扰、距离或传播损耗的验证。

## 研究定位

最稳妥的研究贡献表述是：**面向关键 IoT 事件的 importance-aware 冗余分配，并在等 DATA-copy budget 下与 event-blind 分配进行实机执行比较。** EventGuard 在盲预算基线之上确实把更大比例的副本分配给关键样本，但 critical delivery 的实际增益较小且只在部分 RANDOM_COPY 条件呈现。IMPORTANCE_ONLY 与 EventGuard 的关键事件投递相同而成本更低，因此 link adaptation 应作为已评估、在当前条件下没有显示稳定额外收益的消融组件，而不是主要贡献。

## 统计与限制

统计单位为 seed/run pair，而不是 packet 或 sample。`summary.csv` 含 mean、median、sample std、95% t CI；`paired_tests.csv` 含配对差值分布、胜/平/负、精确 Wilcoxon signed-rank p-value 与 rank-biserial effect size。n=6 时 p-value 取值粗，结论优先依据方向、幅度和配对一致性。

硬件运行覆盖一个 ESP32-S3/E220 设备对、短 synthetic trace 和 deterministic application-layer erasure；配置损失率不是测得的 RF packet-error rate。没有测 RF airtime、能耗、range 或干扰鲁棒性。运行时间仅为 UART serialization proxy，没有 Joule 能耗数据。Run103 stall 仍未定位，属于最大工程不确定性。

## 项目状态与文件

本轮最终数据审计、统计、Pareto 图、论文初稿和 README 状态更新已完成；主硬件数据集为 96/96 可用。项目研究原型可进入论文写作与后续真实信道实验设计，但论文需明确六个配对 seed、应用层注入及单设备对限制，不能写成广义 LoRa/RF 性能结论。

- [离线审计报告](../results/final_hardware_v1/audit_report.md)
- [最终硬件报告](../results/final_hardware_v1/final_hardware_report.md)
- [每策略描述统计](../results/final_hardware_v1/summary.csv)
- [配对检验](../results/final_hardware_v1/paired_tests.csv)
- [Simulation/hardware 对照](../results/final_hardware_v1/simulation_hardware_comparison.csv)
- [最终论文初稿](paper_draft_final.md)
