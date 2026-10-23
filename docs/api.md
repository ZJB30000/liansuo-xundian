# 接口说明

本项目当前只在本地对样例数据做归类建议，**不调用、不写回任何业务系统**。以下为对接试点时需要遵守的接口约定，供后续实现参考。

## 一、数据读取（只读）

| 项 | 约定 |
| --- | --- |
| 方向 | 只读巡店记录、培训资料、门店档案 |
| 禁止 | 写回处罚、发布合格结论、修改原始记录 |
| 频率 | 已授权业务状态每小时更新；超过一个周期标为待刷新 |
| 断连 | 使用带时间戳的上一份只读快照，重连后按业务编号去重补齐 |

## 二、记录字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| business_id | string | 业务编号，格式 `XX-YYYY-NNNN` |
| event_time | string | 事件时间，`YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM` |
| store_id | string | 门店主编号，改名后保留原编号 |
| description | string | 问题描述，用于规则匹配 |
| rule_version | string | 规则库版本，空值视为异常 |
| version_conflict | bool | 可选，版本冲突标记 |
| system_unavailable | bool | 可选，关键系统不可用标记 |

## 三、建议输出

```json
{
  "business_id": "XD-2026-0001",
  "category": "温度记录缺失",
  "severity": "高",
  "confidence": 0.95,
  "needs_human": false,
  "rule_version": "v1",
  "advice": "现场复测温度并补记……",
  "basis": ["f11", "f21"],
  "reason": "命中规则 temperature_missing；证据字段完整"
}
```

字段说明：

- `needs_human`：为 true 时必须转人工，并标注触发原因、接手人、确认时间。
- `basis`：对应访谈事实编号，用于审计重建。
- `confidence`：仅供参考，不得作为业务风险判断依据。

## 四、错误码

| 情形 | 处理 |
| --- | --- |
| 必填字段缺失 | 转人工修复队列，不入自动样本 |
| 编号格式不规范 | 同上，并回写校验结果给平台侧 |
| 规则未命中 | 按低置信处理，转人工 |
| 系统不可用 | 继续用快照，界面显示“暂未更新” |

## 五、本地调用

```python
from my_tool.core.classifier import classify_record

advice = classify_record(record, rule_version="v1", threshold=0.6)
print(advice.to_dict())
```
