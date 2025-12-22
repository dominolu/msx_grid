# 支持多个币种的网格 - 需求分析与实现细节

## 一、需求概述

当前系统只支持单个币种的网格策略。需要扩展为支持同时运行多个币种的网格策略，每个币种独立配置、独立运行、独立监控。

## 二、当前系统架构分析

### 2.1 后端架构
- **单例模式**：`app.py` 中只有一个全局 `strategy: Optional[GridStrategy]` 实例
- **策略类**：`GridStrategy` 类设计为单币种策略（`self.symbol` 字段）
- **API 接口**：所有接口（`/api/start`, `/api/stop`, `/api/status`）都是针对单个策略的

### 2.2 前端架构
- **单策略显示**：界面只显示一个策略的状态卡片
- **单策略操作**：启动/停止按钮只操作当前策略
- **统计信息**：统计信息基于单个策略计算

### 2.3 持久化机制
- **策略文件**：每个策略保存为 `{pos_id}.json` 文件
- **订单文件**：每个策略的订单保存为 `{pos_id}_orders.csv` 文件
- **加载机制**：`load_strategy()` 方法只加载一个策略

## 三、需要实现的功能点

### 3.1 后端改造

#### 3.1.1 GridStrategy 类内部改造（核心改动）

**架构调整**：保持单个 `GridStrategy` 实例，在实例内部管理多个 symbol

**数据结构改造**：
```python
# 改造前（单 symbol）
class GridStrategy:
    def __init__(self, exchange):
        self.symbol = None
        self.min_price = None
        self.buy_order = None
        self.position = Position()
        # ... 其他单 symbol 字段

# 改造后（多 symbol）
class GridStrategy:
    def __init__(self, exchange):
        self.exchange = exchange
        # 使用字典存储多个 symbol 的策略数据
        self.symbols: Dict[str, Dict[str, Any]] = {}
        # 共享的交易所实例和 Redis（如果有）
```

**每个 symbol 的策略数据结构**：
```python
self.symbols[symbol] = {
    # 基础参数
    "symbol": str,                    # 交易对
    "min_price": float,               # 最低价格
    "max_price": float,               # 最高价格
    "direction": str,                 # "long" 或 "short"
    "grid_spacing": float,            # 网格间距
    "investment_amount": float,      # 投入资金
    "leverage": float,                # 杠杆倍数
    "total_capital": float,           # 总资金
    "asset_type": str,                # "crypto" 或 "stock"
    "market_type": str,               # "spot" 或 "contract"
    "co_type": Optional[int],         # 合约类型
    
    # 价格信息
    "current_price": Optional[float], # 当前价格
    "start_price": Optional[float],   # 启动价格
    
    # 订单信息
    "buy_order": Optional[OrderInfo], # 买单
    "sell_order": Optional[OrderInfo], # 卖单
    
    # 持仓信息
    "position": Position,             # 持仓对象
    
    # 历史订单
    "his_order": List[Dict],          # 历史订单列表
    
    # 状态标志
    "_status": bool,                  # 运行状态
    "_initialized": bool,              # 初始化状态
    "_run_task": Optional[asyncio.Task], # 运行任务（如果需要独立任务）
    
    # 统计信息
    "stats": Dict[str, Any],          # 统计信息字典
    
    # 其他
    "last_filled_time": int,          # 最后成交时间戳
    "each_order_size": float,         # 每单持仓数量
    "min_order_size": float,          # 最小订单金额
}
```

**主循环改造**：
```python
async def run(self) -> None:
    """运行策略主循环 - 遍历所有 symbol"""
    while True:  # 全局运行标志，或检查是否有运行中的策略
        for symbol in list(self.symbols.keys()):
            symbol_data = self.symbols[symbol]
            if not symbol_data.get("_status", False):
                continue  # 跳过未运行的策略
            
            try:
                # 检查交易时段（如果是股票）
                if symbol_data.get("co_type") == 1:
                    if not self.is_us_stock_trading_hours():
                        continue
                
                # 初始化检查
                if not symbol_data.get("_initialized", False):
                    await self._init_(symbol)
                
                # 检查订单
                await self.check_order(symbol)
                
            except Exception as e:
                log.error(f"处理 {symbol} 策略时出错: {e}")
                # 继续处理下一个 symbol，不中断主循环
        
        await asyncio.sleep(1)  # 休眠避免阻塞
```

**方法改造清单**：
- `start(params)` - 添加新 symbol 的策略
  - 检查 `symbol` 是否已存在
  - 如果存在，报错或更新（根据业务规则）
  - 创建新的策略数据字典，添加到 `self.symbols[symbol]`
  - 启动该 symbol 的策略运行
  
- `stop(symbol=None)` - 停止指定 symbol 的策略
  - 如果 `symbol` 为 `None`，停止所有 symbol
  - 否则只停止指定 `symbol`
  - 设置 `_status = False`，取消相关任务
  
- `get_status(symbol=None)` - 获取策略状态
  - 如果 `symbol` 为 `None`，返回所有 symbol 的状态列表
  - 否则返回指定 `symbol` 的状态
  
- `_init_(symbol)` - 初始化指定 symbol 的策略
  - 接收 `symbol` 参数
  - 从 `self.symbols[symbol]` 获取策略数据
  - 执行初始化逻辑（建仓、创建网格订单等）
  
- `check_order(symbol)` - 检查指定 symbol 的订单
  - 接收 `symbol` 参数
  - 从 `self.symbols[symbol]` 获取策略数据
  - 执行订单检查逻辑
  
- `process_order_statistics(symbol)` - 处理订单统计
  - 接收 `symbol` 参数
  - 更新指定 symbol 的统计信息
  
- `load_strategy()` - 加载所有持久化的策略
  - 扫描 `data/` 目录，加载所有 `*.json` 文件
  - 根据文件中的 `symbol` 字段，创建策略数据并添加到 `self.symbols`
  - 如果同一 `symbol` 有多个文件，选择最新的
  
- `_place_grid_orders(symbol, filled_price, order_volume)` - 创建网格订单
  - 接收 `symbol` 参数
  - 使用指定 symbol 的策略数据创建订单
  
- `_execute_order(symbol, ...)` - 执行订单
  - 接收 `symbol` 参数
  - 使用指定 symbol 的策略数据执行订单

#### 3.1.2 API 接口改造

**新增接口：**
- `POST /api/strategies` - 创建新策略（传入 symbol 参数）
- `GET /api/strategies` - 获取所有策略列表（所有 symbol）
- `GET /api/strategies/{symbol}` - 获取指定 symbol 的策略状态
- `POST /api/strategies/{symbol}/stop` - 停止指定 symbol 的策略
- `DELETE /api/strategies/{symbol}` - 删除指定 symbol 的策略

**改造现有接口（保持向后兼容）：**
- `POST /api/start` - 保持兼容，内部调用 `strategy.start(params)`，params 中必须包含 symbol
- `POST /api/stop` - 保持兼容，支持传入 `symbol` 参数停止指定策略，不传则停止所有
- `GET /api/status` - 保持兼容，支持传入 `symbol` 参数查询指定策略，不传则返回所有策略的汇总

#### 3.1.3 策略标识机制
- **策略标识**：直接使用 `symbol` 作为唯一标识（交易对名称）
- **策略查找**：通过 `symbol` 在 `self.symbols` 字典中查找
- **重复检查**：同一 `symbol` 只允许一个策略运行（启动时检查，如果已存在则报错或更新）
- **策略唯一性**：每个 `symbol` 对应一个策略配置，但可以通过不同的 `pos_id` 区分不同的持仓

#### 3.1.4 持久化改造
- **策略文件命名**：保持 `{pos_id}.json` 格式，支持多个文件
- **加载机制**：
  - 启动时扫描 `data/` 目录，加载所有 `*.json` 策略文件
  - 根据文件中的 `symbol` 字段，将策略数据加载到 `self.symbols[symbol]` 中
  - 如果同一 `symbol` 有多个策略文件（不同 `pos_id`），需要处理冲突（选择最新的或合并）
- **策略关联**：通过 `symbol` 关联策略数据，`pos_id` 作为持仓标识存储在策略数据中

### 3.2 前端改造

#### 3.2.1 策略列表视图（新增）
- **布局**：从单策略卡片改为策略列表
- **列表项**：每个策略显示：
  - 交易对（symbol）
  - 方向（做多/做空）
  - 杠杆倍数
  - 运行状态（运行中/已停止）
  - 当前价格
  - 盈亏情况
  - 操作按钮（启动/停止/删除/详情）

#### 3.2.2 策略详情视图（新增/改造）
- **详情卡片**：点击策略项展开详情
- **详情内容**：当前单策略卡片的所有信息
- **实时更新**：每个策略独立轮询状态

#### 3.2.3 统计信息汇总（改造）
- **多策略汇总**：统计所有策略的：
  - 总策略数
  - 运行中策略数
  - 已停止策略数
  - 总投入资金（所有策略投资额之和）
  - 总盈亏（所有策略盈亏之和）
  - 总成交额（所有策略成交额之和）
  - 平均收益率

#### 3.2.4 启动策略流程（改造）
- **启动前检查**：检查是否已有相同 `symbol` 的策略在运行（根据业务规则）
- **启动后刷新**：启动成功后刷新策略列表

### 3.3 数据模型

#### 3.3.1 策略数据结构（GridStrategy 内部）
```python
# GridStrategy 实例内部结构
self.symbols = {
    "ETHUSDT": {
        "symbol": "ETHUSDT",
        "pos_id": 12345,                    # 持仓ID
        "min_price": 3000,
        "max_price": 3700,
        "direction": "long",
        "grid_spacing": 0.005,
        "investment_amount": 10000,
        "leverage": 10,
        "total_capital": 100000,
        "asset_type": "crypto",
        "market_type": "contract",
        "co_type": 3,
        "current_price": 3500,
        "start_price": 3500,
        "buy_order": OrderInfo(...),
        "sell_order": OrderInfo(...),
        "position": Position(...),
        "his_order": [...],
        "_status": True,                   # 运行状态
        "_initialized": True,              # 初始化状态
        "stats": {...},
        "last_filled_time": 1234567890,
        "each_order_size": 100,
        ...
    },
    "BTCUSDT": {
        # 另一个 symbol 的策略数据
        ...
    }
}
```

#### 3.3.2 API 响应格式
```json
// GET /api/strategies - 获取所有策略
{
    "status": "success",
    "data": {
        "strategies": [
            {
                "symbol": "ETHUSDT",
                "status": "running",
                "summary": { ... },
                "position": { ... },
                "buy_order": { ... },
                "sell_order": { ... }
            },
            {
                "symbol": "BTCUSDT",
                "status": "running",
                ...
            }
        ],
        "total": 3,
        "running": 2,
        "stopped": 1
    }
}

// GET /api/status?symbol=ETHUSDT - 获取指定策略
{
    "status": "success",
    "data": {
        "symbol": "ETHUSDT",
        "direction": "long",
        "price_range": [3000, 3700],
        "running": true,
        "summary": { ... },
        ...
    }
}
```

## 四、实现步骤建议

### 阶段一：后端核心功能
1. ✅ 改造 `GridStrategy` 类，支持多 symbol 管理
   - 将单 symbol 字段改为 `self.symbols: Dict[str, Dict]` 字典结构
   - 改造 `start()` 方法，支持添加新 symbol
   - 改造 `stop()` 方法，支持停止指定 symbol
   - 改造 `run()` 主循环，遍历所有 symbol 执行检查
   - 改造 `check_order()` 等方法，支持指定 symbol 参数
2. ✅ 改造 `app.py`，保持单个 `GridStrategy` 实例
3. ✅ 实现新的 API 接口（`/api/strategies/*`）
4. ✅ 保持现有接口向后兼容（支持 symbol 参数）
5. ✅ 改造持久化机制，支持多策略加载

### 阶段二：前端核心功能
1. ✅ 创建策略列表视图组件
2. ✅ 改造统计信息，支持多策略汇总
3. ✅ 实现策略详情展开/收起
4. ✅ 实现策略的启动/停止/删除操作
5. ✅ 实现多策略状态轮询

### 阶段三：优化与测试
1. ✅ 性能优化（减少不必要的轮询）
2. ✅ 错误处理完善
3. ✅ UI/UX 优化
4. ✅ 单元测试和集成测试

## 五、技术难点与解决方案

### 5.1 策略标识唯一性
- **问题**：如何确保策略标识唯一且可追溯
- **方案**：直接使用 `symbol`（交易对名称）作为唯一标识，启动时检查 `self.symbols` 中是否已存在

### 5.2 同一币种多策略
- **问题**：是否允许同一 `symbol` 运行多个策略
- **方案**：**不允许**（简化设计）
  - 启动时检查 `symbol` 是否已存在于 `self.symbols` 中
  - 如果已存在，可以选择：
    - 选项A：报错提示（推荐）
    - 选项B：更新现有策略参数（需要用户确认）
  - 如果需要同一币种多个策略，可以通过不同的价格区间或参数创建不同的策略配置（但需要不同的标识符，当前设计不支持）

### 5.3 资源管理
- **问题**：多个策略同时运行时的资源占用
- **方案**：
  - 限制最大并发策略数
  - 监控每个策略的资源使用
  - 实现优雅降级机制

### 5.4 状态同步
- **问题**：多个策略的状态更新频率和同步
- **方案**：
  - 每个策略独立轮询（当前实现）
  - 或使用 WebSocket 推送更新（未来优化）

### 5.5 持久化冲突
- **问题**：多个策略文件可能对应同一个 `symbol`（不同 `pos_id`）
- **方案**：
  - 加载时如果发现同一 `symbol` 有多个策略文件，选择最新的（根据 `saved_at` 时间戳）
  - 或者合并策略数据（如果业务逻辑允许）
  - 记录警告日志，提示用户有多个策略文件

## 六、边界情况处理

1. **策略启动失败**：
   - 如果 `symbol` 已存在，返回错误提示
   - 如果启动过程中失败，清理已创建的资源，从 `self.symbols` 中移除
   - 返回详细的错误信息

2. **策略停止失败**：
   - 记录错误日志
   - 允许重试停止操作
   - 如果停止失败，策略状态标记为异常

3. **策略文件损坏**：
   - 跳过损坏文件，记录警告日志
   - 继续加载其他有效的策略文件
   - 不影响其他 symbol 的策略运行

4. **交易所连接断开**：
   - 所有 symbol 的策略暂停检查订单
   - 连接恢复后，所有运行中的策略自动恢复检查

5. **系统重启**：
   - 自动扫描 `data/` 目录，加载所有策略文件
   - 根据 `symbol` 恢复策略到 `self.symbols` 中
   - 自动启动所有之前运行中的策略（根据持久化的状态）

6. **主循环异常**：
   - 某个 symbol 的处理异常不应影响其他 symbol
   - 使用 try-except 包裹每个 symbol 的处理逻辑
   - 记录异常日志，继续处理下一个 symbol

## 七、测试要点

1. **功能测试**：
   - 创建多个不同币种的策略
   - 同时启动/停止多个策略
   - 策略状态查询和更新
   - 策略删除和清理

2. **性能测试**：
   - 10+ 策略同时运行的性能
   - API 响应时间
   - 前端渲染性能

3. **稳定性测试**：
   - 长时间运行稳定性
   - 异常情况恢复能力
   - 资源泄漏检查

## 八、后续优化方向

1. **策略模板**：支持保存和复用策略配置
2. **策略分组**：支持按市场类型、资产类型分组
3. **批量操作**：支持批量启动/停止策略
4. **策略监控**：更详细的监控和告警机制
5. **数据可视化**：策略收益曲线、持仓分布等图表
