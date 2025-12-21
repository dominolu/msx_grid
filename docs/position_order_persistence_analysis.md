# 根据持仓ID持久化已成交订单数据 - 需求分析

## 一、需求概述

在处理成交订单时，根据持仓ID（posId）将已成交数据持久化到CSV文件。

## 二、实现方案

### 2.1 文件命名规则
- **格式**：`{pos_id}_orders.csv`
- **示例**：`12345_orders.csv`、`67890_orders.csv`
- **存储位置**：项目根目录或指定目录（如 `data/orders/`）

### 2.2 获取持仓ID的逻辑
1. **优先**：从当前持仓获取 `self.position.id`
2. **备选**：如果持仓为空（`self.position.id` 为 None 或 0），从平仓订单的原始数据中提取 `posId`
3. **兜底**：如果都没有，记录警告日志，使用默认值或跳过持久化

### 2.3 数据格式（CSV）
CSV文件包含以下列：
- order_id, symbol, side, open_type, price, volume, pnl, fee, timestamp, status, pos_id, avg_price

### 2.4 实现步骤
1. 修改 `fetch_his_order()`：提取订单原始数据中的 `posId`，添加到 `OrderInfo`
2. 修改 `process_order_statistics()`：
   - 为每个订单记录确定 `pos_id`
   - 调用持久化方法写入CSV文件
3. 实现 `_persist_order_to_csv()` 方法：
   - 根据 `pos_id` 确定文件名
   - 追加写入CSV（如果文件不存在则创建并写入表头）

## 三、关键代码修改点

### 3.1 OrderInfo 模型
- 添加 `posId` 字段（可选）

### 3.2 fetch_his_order
- 从订单原始数据提取 `posId` 字段

### 3.3 process_order_statistics
- 确定每个订单的 `pos_id`
- 调用 `_persist_order_to_csv()` 持久化

### 3.4 新增方法
```python
def _persist_order_to_csv(self, order_record: dict, pos_id: Optional[int]) -> None:
    """将订单记录追加到CSV文件"""
```

## 四、边界情况

- **没有持仓ID**：记录警告，跳过持久化
- **文件写入失败**：记录错误日志，不影响内存数据
- **持仓ID变化**：不同持仓ID的订单写入不同文件
