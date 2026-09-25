# EventGuard-LoRa 算法规格 v1（冻结）

**版本：** `EventGuard-v1`

**冻结源码提交：** `84fe41701597b250c315002eabaa99638ef17a95`
**适用范围：** 当前 Python reference 与 ESP32 C firmware。`tools/run_research_analysis.py` 在分析前校验核心源码的 SHA-256；哈希不一致时停止生成结果。此规格是实验基线，不是性能保证。

## 输入、状态与输出

每个逻辑样本包含四路 sensor state：温度（°C）、湿度（%）、光照（lux）、土壤湿度（%），以及严格递增的采样时间。分类器保留前一样本、正常状态基线、连续事件计数。链路估计器保留最近首副本的 ACK 接受结果及连续首副本失败次数。

EventGuard 的直接输入为预测的 `importance level`（NORMAL/IMPORTANT/CRITICAL）和发送该样本前的 `first-copy link state`（GOOD/DEGRADED/BAD）；输出为预先发送的 DATA 副本数。所有副本携带同一逻辑序号，网关去重。ACK 到达不会取消已经选定的后续副本；因此这些是**主动冗余副本**，不是超时后重传。

## Importance classifier

四个传感器的归一化尺度分别为温度 `0.5`、湿度 `5`、光照 `100`、土壤湿度 `8`。每通道计算：

- `change = min(4, |current - previous| / scale)`
- `level = min(4, |current - normal_baseline| / scale)`
- `rate = min(4, change / max(0.25, elapsed_seconds / 5))`

`co_change` 在至少两个通道的 `change ≥ 0.25` 时为 1；否则为 0。分数：

```text
score = 0.45 × sum(change)
      + 0.25 × sum(level)
      + 0.40 × sum(rate)
      + 0.70 × co_change
      + 0.10 × min(persistence, 5)
```

另外计算具有方向性的危险水平：

```text
risk = max(
    max(0, baseline_soil - current_soil) / 8,
    max(0, current_temperature - baseline_temperature) / 3
      + max(0, current_humidity - baseline_humidity) / 10
)
```

先判 `risk ≥ 3.0` 为 CRITICAL；否则 `score ≥ 0.85` 为 IMPORTANT；其余为 NORMAL。正常样本以 `baseline_alpha = 0.06` 更新基线；事件样本不更新基线，并增加 persistence。初始样本为 NORMAL。正式 trace 的相邻时间间隔固定为 10 秒；非递增时间在 Python 中报错，在 C 中返回非有限分数并拒绝按正常间隔计算。

**Ground truth 与预测严格分开：**合成 trace 的标签来自阶段语义与物理危险条件（土壤湿度 `<35%`，或温度 `>30°C` 且湿度 `>64%`）；分类器只读取传感器值和自身历史状态。阈值敏感性实验只改变运行配置，不回写 v1 默认值。

## First-copy link estimator

每个实际发送的物理副本都记录 `copy_attempts`、`copy_ack_success`、`copy_failures` 与连续副本失败次数。**只有每个逻辑样本的第 0 份副本**会进入链路状态窗口；观察成功的定义是 DATA 到达、有效 ACK 被物理收到并通过应用层 fault injector 后被接受。这样同一 trace 与故障日历下，不同策略拥有相同的主链路观测，避免多发副本引入观察偏差。

窗口长度为 `12`。首副本接受率 `success_ratio = successful_first_copies / observed_first_copies`；尚无观察时取 1。若连续首副本失败 `≥3` 或比例 `<0.50`，状态为 BAD；否则若比例 `<0.80`，为 DEGRADED；其余为 GOOD。比例按滑动窗口计算，连续失败按首副本序列计算。

## EventGuard 冗余规则

GOOD 状态的基础副本数：

| Importance | 基础副本数 |
|---|---:|
| NORMAL | 1 |
| IMPORTANT | 2 |
| CRITICAL | 3 |

DEGRADED 状态在基础上 `+1`；BAD 状态仅对 IMPORTANT/CRITICAL `+2`，NORMAL 不增加。最终值限制在 `[1, max_redundancy=3]`。因此：

| Importance | GOOD | DEGRADED | BAD |
|---|---:|---:|---:|
| NORMAL | 1 | 2 | 1 |
| IMPORTANT | 2 | 3 | 3 |
| CRITICAL | 3 | 3 | 3 |

CRITICAL 在 GOOD 时已经达到上限 3；v1 的 link adaptation 无法再提高其副本数。这一结构性限制是消融结果的重要解释。

## 对照策略与预算

`FIXED_1/2/3` 对全部样本分别发送 1/2/3 份；`FIXED_1` 等价于旧名 `NO_PROTECTION`。`IMPORTANCE_ONLY` 按预测 NORMAL/IMPORTANT/CRITICAL 发送 1/2/3 份，不用链路状态。`LINK_ONLY` 按 GOOD/DEGRADED/BAD 发送 1/2/3 份，不用 importance。

`UNIFORM_BUDGET` 与 `RANDOM_BUDGET` 的每组总 DATA 副本数严格等于匹配的 EventGuard run。每个样本先分配 1 份，额外副本分别按固定轮转顺序或种子驱动的哈希排序分层分配，单样本最多 3 份；两者都不读取 ground truth、importance、分类分数或传感器值。比较必须保持相同 loss model、loss rate、seed、trace、三槽故障日历与模拟硬件条件。

## 故障日历与配置

每个逻辑样本固定保留 `sample_id × 3 + copy_index` 的三种 canonical copy opportunities；不同策略共享这些 DATA 与 ACK 日历。`RANDOM_COPY` 对机会独立哈希判丢；`BURST_COPY` 对 canonical 槽形成连续丢失；主实验 `BURST_SAMPLE` 对连续逻辑样本的**所有三个**机会丢失，代表共同故障。DATA 与 ACK 分别构造日历。`BURST_SAMPLE` 中同一样本内增加副本无法恢复 DATA，这属于模型定义，不应被解释为真实 RF 衰落的普遍结论。

| 参数 | v1 值 |
|---|---:|
| 正式 trace 密度 | 9 阶段 × 每阶段 6 样本 = 54 样本 |
| 采样间隔 | 10,000 ms |
| `max_redundancy` | 3 |
| `fixed_redundancy` | 2 |
| `burst_length` | 3 |
| `link_window` | 12 |
| `link_degraded_threshold` | 0.80 |
| `link_bad_threshold` | 0.50 |
| `link_bad_fail_streak` | 3 |
| `important_threshold` / `critical_threshold` | 0.85 / 3.0 |
| `importance_baseline_alpha` | 0.06 |
| `ack_timeout_ms` | 1000 |
| UART | 9600 baud |
| DATA / ACK frame | 26 / 13 bytes |
| 评估 loss rate | 0、5、10、20、30% |
| 校准 / 独立评估种子 | 1–30 / 31–130 |

v1 没有第 4 个 canonical copy opportunity，固件与策略也以 3 为上限。`max_redundancy=4` 不属于此冻结版本；直接用三槽故障日历模拟第 4 份会使其不受丢包，产生虚假的优势。若研究四副本，应另立算法和故障模型版本并重新验证 Python/C parity。
