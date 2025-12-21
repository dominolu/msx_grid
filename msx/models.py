"""
数据模型定义

包含订单和持仓的数据结构
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderInfo:
    """
    订单信息数据结构（符合 ccxt 标准）
    
    字段说明：
        id: 订单ID，交易所返回的唯一标识符（ccxt 标准：id）
        price: 订单价格（限价单的挂单价格）
        volume: 订单数量（下单数量）
        side: 订单方向，"buy"（买入）或 "sell"（卖出）
        status: 订单状态，"pending"（挂单中）、"filled"（已成交）、"cancelled"（已取消）
        timestamp: 订单创建时间戳（毫秒，ccxt 标准）
        msg: 错误信息或提示信息（订单失败时包含错误信息）
    """
    id: Optional[str] = None        # 订单ID（ccxt 标准）
    price: float = 0.0              # 订单价格
    volume: float = 0.0             # 订单数量
    side: str = ""                  # 订单方向："buy" | "sell"
    status: str = "pending"         # 订单状态："pending" | "filled" | "cancelled"
    timestamp: int = 0              # 订单创建时间戳（毫秒，ccxt 标准）
    msg: Optional[str] = None       # 错误信息或提示信息
    pnl: float = 0.0                # 订单盈亏（已实现盈亏，来自交易所，API字段：realPnl）
    fee: float = 0.0                # 订单手续费（来自交易所，API字段：realFee）
    avgPrice: float = 0.0           # 订单成交均价（来自交易所，API字段：avgPrice）
    amount: float = 0.0             # 订单金额（订单数量 * 订单价格）
    open_type: int = 1              # 开仓类型，1=开仓，2=平仓
    code: int = 0                   # 错误码
    posId: Optional[int] = None     # 持仓ID（平仓订单包含此字段）
    raw: Optional[dict] = None      # 原始订单数据（用于提取额外信息）


@dataclass
class Position:
    """
    持仓信息数据结构（符合 ccxt 标准）
    
    字段说明：
        id: 持仓ID（用于平仓时指定持仓）
        size: 持仓数量（ccxt 标准：size 或 contracts）
        amount: 持仓金额（持仓数量 * 当前价格）
        
    注意：完整的持仓数据可能包含以下字段（来自交易所API）：
        - id: 持仓ID
        - posNo: 持仓编号
        - symbol: 交易币种
        - side: 方向，"long"（做多）或 "short"（做空）
        - longFlag: 是否做多（1=做多，0=做空）
        - marginMode: 保证金模式（1=全仓，2=逐仓）
        - leverage: 杠杆倍数
        - posMargin: 持仓保证金
        - useMargin: 已用保证金
        - feeCost: 手续费成本
        - entryPrice: 平均开仓价格（ccxt 标准）
        - markPrice: 标记价格（用于计算盈亏）
        - liquidationPrice: 强平价格（ccxt 标准）
        - unrealizedPnl: 盈亏（未实现盈亏，ccxt 标准）
        - timestamp: 更新时间戳（ccxt 标准）
    """
    id: Optional[int] = None        # 持仓ID（用于平仓时指定持仓）
    size: float = 0.0              # 持仓数量（ccxt 标准）
    amount: float = 0.0             # 持仓金额
    entryPrice: float = 0.0        # 平均开仓价格（ccxt 标准）
    unrealizedPnl: float = 0.0     # 盈亏（未实现盈亏，ccxt 标准）
    liquidationPrice: float = 0.0   # 强平价格（ccxt 标准）
    timestamp: int = 0              # 更新时间戳（ccxt 标准）
    raw: dict = None               # 原始数据
    side: str = ""                 # 方向，"long"（做多）或 "short"（做空）

