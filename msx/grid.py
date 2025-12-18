"""
简单网格策略框架

功能：
1. 同时只挂一个买单和一个卖单
2. 订单成交后自动重新挂单
3. 使用百分比网格间距
4. 持仓总金额限制
"""

# 版本号管理
# 规则：每次修改代码时，将版本号最后一位数字增加1
# 例如：1.0.1 -> 1.0.2, 1.0.9 -> 1.0.10, 1.9.9 -> 1.9.10
VERSION = "1.0.4"

# from tkinter import NO  # 未使用的导入，已注释
import asyncio
import traceback
from typing import Dict, Optional, Any
from loguru import logger as log
from .exchange import MsxExchange
from .models import OrderInfo, Position
from datetime import datetime, timedelta
import requests
from pytz import timezone


class GridStrategy:
    """
    简单网格策略
    
    参数：
        exchange: MsxExchange 实例
        redis: Redis 实例（可选，用于数据持久化）
    """
    
    def __init__(
        self,
        exchange: MsxExchange,
        redis=None,
    ):
        self.exchange = exchange # 交易所实例
        self.redis = redis  # Redis 实例（可选，用于数据持久化）
        
        # 网格参数（在 start 方法中设置）
        self.symbol: Optional[str] = None  # 交易币种
        self.min_price: Optional[float] = None  # 最低价格
        self.max_price: Optional[float] = None  # 最高价格
        self.direction: Optional[str] = None  # "long" 或 "short"
        self.grid_spacing: Optional[float] = None  # 网格间距，0.01 表示 1%
        self.investment_amount: Optional[float] = None  # 投入资金
        self.leverage: Optional[float] = None  # 杠杆倍数
        self.total_capital: Optional[float] = None  # 总资金 = 投入资金 * 杠杆倍数
        self.asset_type: Optional[str] = None  # 资产类型："crypto"（加密货币）或 "stock"（股票）
        self.market_type: Optional[str] = None  # 市场类型："spot"（现货）或 "contract"（合约）
        self.co_type: Optional[int] = None  # 合约类型（根据 asset_type 和 market_type 计算）
        
        self.min_order_size = 10 # 最小订单金额
        # 当前价格：从 bar 频道获取的最新市场价格
        self.current_price: Optional[float] = None
        # 启动价格：策略启动时的价格
        self.start_price: Optional[float] = None
        
        # 订单信息：当前挂的买单和卖单列表
        # buy_orders: 买单列表（OrderInfo 对象列表）
        # sell_orders: 卖单列表（OrderInfo 对象列表）
        self.buy_order = None  # 买单列表
        self.sell_order = None  # 卖单列表

        self.position = Position()
        self.his_order = []
        # 是否已启动：策略运行状态标志
        self._status = False
        # 策略运行任务引用（用于管理任务）
        self._run_task: Optional[asyncio.Task] = None
        # 上次已知的已成交订单时间戳（用于差异匹配）
        self.last_filled_time = 0
        
        # 交易状态缓存（用于美股交易时段判断）
        self._trading_status_cache = {
            "is_trade": None,  # 当前交易状态
            "start_trade_time": None,  # 下次交易开始时间（时间戳）
            "last_update_time": None,  # 上次更新时间（时间戳）
        }
        
        # 初始化状态标志
        self._initialized = False  # 是否已完成初始化（包括建仓和创建网格订单）
        self._last_trading_status = None  # 上次交易时段状态（用于检测状态变化）
        
        # 统计信息
        self.stats = {
            "buy_filled_count": 0,      # 买单成交次数
            "sell_filled_count": 0,      # 卖单成交次数
            "complete_cycles": 0,        # 完整周期（一买一卖）
            "realized_pnl": 0.0,         # 已实现盈亏
            "total_fees": 0.0,           # 总手续费
            "initial_balance": 0.0,      # 初始余额
            "start_time": None,          # 开始时间
            "total_trade_amount": 0.0,   # 累计成交金额（所有订单的价格 × 数量）
            "invited": "",         # 邀请码
            "co_type": None,   # 合约类型
        }
        
        log.info("网格策略实例已创建（参数将在 start 方法中设置）")
    
    def _fetch_trading_status_from_api(self) -> Optional[Dict[str, Any]]:
        """
        从API获取交易状态
        
        使用API接口：https://api9528mystks.mystonks.org/api/v1/stock/isTrade
        
        Returns:
            Optional[Dict[str, Any]]: 
                - 成功时返回 {"is_trade": bool, "start_trade_time": int, "code": 0}
                - code为6005（休市中）时返回 {"code": 6005}
                - 失败时返回 None
        """
        try:
            api_url = "https://api9528mystks.mystonks.org/api/v1/stock/isTrade"
            response = requests.get(api_url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                api_code = data.get("code")
                
                # 检查是否为休市中（code: 6005）
                if api_code == 6005:
                    log.info(f"市场休市中: {data.get('msg', 'unknown')}")
                    return {"code": 6005}
                
                # 检查API返回格式（正常情况 code == 0）
                if data.get("success") and api_code == 0:
                    trade_data = data.get("data", {})
                    is_trade = trade_data.get("isTrade", False)
                    start_trade_time = trade_data.get("startTradeTime")
                    
                    return {
                        "is_trade": is_trade,
                        "start_trade_time": start_trade_time,
                        "code": 0
                    }
                else:
                    log.warning(f"API返回异常: code={api_code}, msg={data.get('msg', 'unknown')}")
                    return None
            else:
                log.warning(f"API请求失败，状态码: {response.status_code}")
                return None
        except requests.exceptions.RequestException as e:
            log.warning(f"API请求异常: {e}")
            return None
        except Exception as e:
            log.error(f"获取交易状态失败: {e}")
            return None
    
    def is_us_stock_trading_hours(self) -> bool:
        """
        判断当前是否在美股交易时段
        
        使用API接口获取交易状态：https://api9528mystks.mystonks.org/api/v1/stock/isTrade
        使用缓存机制，在未超过startTradeTime之前不重复请求API
        
        Returns:
            bool: True 表示在交易时段内，False 表示不在交易时段
        """
        try:
            current_time = int(datetime.now().timestamp())
            
            # 检查缓存是否有效（当前时间未超过startTradeTime）
            if (self._trading_status_cache.get("is_trade") is not None and 
                self._trading_status_cache.get("start_trade_time") is not None and
                current_time < self._trading_status_cache["last_update_time"]):
                return self._trading_status_cache["is_trade"]
            
            # 需要重新请求API
            api_result = self._fetch_trading_status_from_api()
            
            if api_result:
        
                # 正常返回数据
                is_trade = api_result.get("is_trade", False)
                start_trade_time = api_result.get("start_trade_time")
                
                # 更新缓存
                self._trading_status_cache["is_trade"] = is_trade
                self._trading_status_cache["start_trade_time"] = start_trade_time
                # 设置last_update_time为当前时间+1小时（3600秒）
                self._trading_status_cache["last_update_time"] = current_time + 3600
                
                log.debug(f"交易状态已更新: isTrade={is_trade}, startTradeTime={start_trade_time}")
                return is_trade
            else:
                # API请求失败时，如果有缓存，使用缓存；否则回退到简单的时间检查
                if self._trading_status_cache.get("is_trade") is not None:
                    return self._trading_status_cache["is_trade"]
                return self._fallback_trading_hours_check()
           
        except Exception as e:
            log.error(f"判断美股交易时段失败: {e}")
            # 出错时，如果有缓存，使用缓存；否则默认返回 True，避免阻止交易
            if self._trading_status_cache["is_trade"] is not None:
                return self._trading_status_cache["is_trade"]
            return True
    
    def _fallback_trading_hours_check(self) -> bool:
        """
        回退方案：使用简单的时间检查（仅检查周末和交易时间）
        
        Returns:
            bool: True 表示在交易时段内，False 表示不在交易时段
        """
        try:
            # 获取美东时区（自动处理夏令时）
            eastern = timezone('US/Eastern')
            # 获取当前美东时间
            now_eastern = datetime.now(eastern)
            
            # 检查是否为工作日（周一到周五，weekday() 返回 0-4）
            if now_eastern.weekday() >= 5:  # 5=周六，6=周日
                return False
            
            # 检查时间是否在 9:30 AM - 4:00 PM 之间
            current_time = now_eastern.time()
            market_open = datetime.strptime("09:30", "%H:%M").time()
            market_close = datetime.strptime("16:00", "%H:%M").time()
            
            return market_open <= current_time <= market_close
        except Exception as e:
            log.error(f"回退时间检查失败: {e}")
            # 出错时默认返回 True，避免阻止交易
            return True
    
    async def _init_(self) -> None:
        """
        初始化持仓和订单
        log.info(f"网格策略初始化: {self.symbol}, 方向={self.direction}, "
                f"价格范围=[{self.min_price}, {self.max_price}], "
                f"网格间距={self.grid_spacing*100}%, 持仓总金额={self.investment_amount}")
    
            - feeCost: 手续费成本
            - nowAmtTotal: 当前总金额
            - nowVolTotal: 当前总数量（持仓数量）
            - pnl: 盈亏（未实现盈亏）
            - avgPrice: 平均开仓价格
            - markPrice: 标记价格
            - liqPrice: 强平价格
            - ctime: 创建时间戳
        """
        # 获取当前所有挂单
        await self.exchange.change_symbol(self.symbol)
        while not self.exchange.auth_status:
            await asyncio.sleep(1)
            log.info(f"等待认证成功...")
        
        await asyncio.sleep(10)
        
        # ========== 步骤1: 判断交易时段（统一判断，避免重复） ==========
        is_trading_hours = self.is_us_stock_trading_hours()
        if is_trading_hours:
            log.info("当前处于交易时段")
            # ========== 步骤2: 清理历史挂单 ==========
            orders = await self.exchange.fetch_orders(self.symbol)
            log.info(f"初始化持仓: {self.position.size}, {self.position.amount}")
            log.info(f"撤消所有挂单: {len(orders)}")
            for order in orders:
                log.info(f"撤消挂单: {order.id}")
                await self.exchange.cancel_order(order.id)
            log.info(f"撤消所有挂单完成")

            # ========== 步骤3: 获取价格和持仓信息 ==========
            ticker = await self.exchange.fetch_ticker(self.symbol)
            if ticker:
                self.current_price = ticker.get("last")
                if self.start_price is None:
                    self.start_price = self.current_price
                    log.info(f"策略启动价格已记录: {self.start_price}")
            
            positions = await self.exchange.fetch_positions(self.symbol)
            has_position = False
            if positions and len(positions) > 0:
                pos = positions[0]
                self.position = pos
                has_position = pos.size > 0
            
            # ========== 步骤4: 处理建仓逻辑 ==========
            position_built = False
            if has_position:
                # 已有持仓，无需建仓
                position_built = True
                log.info("检测到已有持仓，跳过建仓操作")
            else:
                # 没有持仓，需要建仓
                if is_trading_hours:
                    # 开市时，执行建仓
                    log.info("开始执行初始建仓操作")
                    await self._initial_build_position()
                    position_built = True
                    log.info("初始建仓完成")
                else:
                    # 休市时，跳过建仓，将在开市后自动建仓
                    log.info("市场休市中，跳过初始建仓操作，将在开市后自动建仓")
                    position_built = False
            
            # ========== 步骤5: 计算每单持仓数量 ==========
            if self.total_capital is None or self.total_capital <= 0:
                raise ValueError(f"总资金未正确初始化: {self.total_capital}")
            avg_price = (self.min_price + self.max_price) / 2
            price_range = self.max_price - self.min_price
            if price_range > 0 and avg_price > 0:
                self.each_order_size = self.total_capital * self.grid_spacing / price_range
            else:
                self.each_order_size = 0
            if self.each_order_size * self.min_price < self.min_order_size:
                raise ValueError("每单持仓金额小于最小订单金额，无法建仓，请调整参数！")
            log.info(f"每单持仓数量: {self.each_order_size:.4f} (平均价格={avg_price:.2f}, 价格范围={price_range:.2f}, 网格间距={self.grid_spacing*100:.2f}%)")
            
            # ========== 步骤6: 初始化历史订单时间戳 ==========
            his_order = await self.exchange.fetch_his_order(self.symbol)
            if his_order:
                filled_orders = [o for o in his_order if o.status in ["filled", "executed"] and o.timestamp]
                if filled_orders:
                    max_timestamp = max(o.timestamp for o in filled_orders)
                    self.last_filled_time = max_timestamp
                    log.info(f"初始化 last_filled_time: {self.last_filled_time} (最新已成交订单时间戳)")
                else:
                    self.last_filled_time = 0
                    log.info(f"初始化 last_filled_time: 0 (无已成交订单)")
            else:
                self.last_filled_time = 0
                log.info(f"初始化 last_filled_time: 0 (无历史订单)")
            
            # ========== 步骤7: 处理网格订单创建逻辑（使用步骤1判断的交易时段） ==========
            orders_created = False
            if is_trading_hours:
                # 开市时，创建网格订单
                log.info("开始创建网格订单")
                await self._place_grid_orders(self.current_price, self.each_order_size)
                await asyncio.sleep(2)
                orders_created = True
                log.info("网格订单创建完成")
            else:
                # 休市时，跳过创建网格订单，将在开市后自动创建
                log.info("市场休市中，跳过创建网格订单操作，将在开市后自动创建")
                orders_created = False
            
            # ========== 步骤8: 统一设置初始化状态 ==========
            # 只有在建仓成功且创建网格订单成功时，才标记为已完成初始化
            # 如果任一操作因休市而跳过，标记为未完成，将在开市后自动完成
            if position_built and orders_created:
                self._initialized = True
                log.info("初始化完成：建仓和创建网格订单均已完成")
            else:
                self._initialized = False
                if not position_built and not orders_created:
                    log.info("初始化未完成：因市场休市，建仓和创建网格订单将在开市后自动完成")
                elif not position_built:
                    log.info("初始化未完成：因市场休市，建仓将在开市后自动完成")
                elif not orders_created:
                    log.info("初始化未完成：因市场休市，创建网格订单将在开市后自动完成")
        
    async def run(self) -> None:
        """运行策略主循环"""
        # 注意：_status 应该在 start() 方法中已经设置为 True
        # 这里只设置统计信息的开始时间
        self.stats["start_time"] = datetime.now()
        
        try:
            # 主循环
            while self._status:
                # ========== 步骤1: 检查交易时段状态 ==========
                is_trading = self.is_us_stock_trading_hours()
                
                if is_trading:
                    # ========== 步骤2: 在交易时段，执行初始化（如果需要） ==========
                    if not self._initialized:
                        await self._init_()
                    
                    # ========== 步骤3: 在交易时段，执行正常的交易检查 ==========
                    await self.check_order()
                else:
                    # ========== 步骤4: 不在交易时段，跳过交易操作 ==========
                    # 休市时不需要执行任何操作，只需等待
                    pass
                
                # ========== 步骤5: 无论是否在交易时段，都需要休眠以避免阻塞事件循环 ==========
                await asyncio.sleep(1)
        finally:
            # 循环退出时，确保状态已停止
            if not self._status:
                log.info(f"网格策略运行循环已退出: {self.symbol}")


    async def check_order(self) -> None:
        """检查订单状态并处理成交订单"""
        ticker = await self.exchange.fetch_ticker(self.symbol)
        if ticker:
            self.current_price = ticker.get("last")
        
        # 先更新持仓信息（无论是否有历史订单，都需要更新持仓）
        try:
            positions = await self.exchange.fetch_positions(self.symbol)
            if positions and len(positions) > 0:
                pos = positions[0]  # fetch_positions 已经按 symbol 过滤，取第一个
                # 更新持仓信息
                self.position.id = pos.id
                self.position.size = pos.size
                self.position.amount = pos.amount
                self.position.entryPrice = pos.entryPrice
                self.position.unrealizedPnl = pos.unrealizedPnl
                self.position.side = pos.side
                self.position.liquidationPrice = pos.liquidationPrice
                self.position.timestamp = pos.timestamp
                # 记录日志以便调试
               #    log.debug(f"持仓已更新: size={pos.size}, amount={pos.amount}, entryPrice={pos.entryPrice}, unrealizedPnl={pos.unrealizedPnl}, side={pos.side}")
        except Exception as e:
            log.error(f"更新持仓信息失败: {e}")
            import traceback
            log.error(traceback.format_exc())
        
        orders = await self.exchange.fetch_orders(self.symbol)
        if not orders:
            log.error("获取订单失败")
            return
        
        # 检查订单是否已成交（订单ID不在当前订单列表中表示已成交）
        order_ids = {o.id for o in orders if o.id}
        buy_filled = self.buy_order is not None and self.buy_order.id not in order_ids if self.buy_order else False
        sell_filled = self.sell_order is not None and self.sell_order.id not in order_ids if self.sell_order else False
        
        # 根据成交情况处理
        if buy_filled and sell_filled:
            # 两个订单都成交，使用历史订单价格重新挂单
            his_orders = await self.exchange.fetch_his_order(self.symbol)
            if his_orders:
                last_order = his_orders[0]
                await self._place_grid_orders(last_order.price, last_order.volume)
        elif buy_filled and self.buy_order:
            # 买单成交，取消卖单并重新挂单
            if self.sell_order and self.sell_order.id:
                await self.exchange.cancel_order(self.sell_order.id)
            await self._place_grid_orders(self.buy_order.price, self.buy_order.volume)
        elif sell_filled and self.sell_order:
            # 卖单成交，取消买单并重新挂单
            if self.buy_order and self.buy_order.id:
                await self.exchange.cancel_order(self.buy_order.id)
            await self._place_grid_orders(self.sell_order.price, self.sell_order.volume)
        
        await self.process_order_statistics()


    
    async def _cancel_extra_orders(self) -> None:
        """
        撤销多余订单，防止占用资金
        
        逻辑：
        1. 买单列表：只保留价格最高的一个档位（相同价格的订单），其余撤单
        2. 卖单列表：只保留价格最低的一个档位（相同价格的订单），其余撤单
        """
        # 处理买单：保留价格最高的档位
        if len(self.buy_orders) > 0:
            # 过滤掉无效订单（没有ID或价格的订单）
            valid_buy_orders = [order for order in self.buy_orders if order.id and order.price and order.price > 0]
            
            if len(valid_buy_orders) > 0:
                # 按价格从高到低排序
                sorted_buy_orders = sorted(valid_buy_orders, key=lambda x: x.price, reverse=True)
                
                # 找到价格最高的订单价格
                highest_price = sorted_buy_orders[0].price
                
                # 找出所有需要保留的订单（价格最高的档位，只保留一个）
                same_price_orders = [order for order in sorted_buy_orders if order.price == highest_price]
                # 只保留第一个（或最新的）订单，撤销其他相同价格的订单
                keep_buy_orders = [same_price_orders[0]] if same_price_orders else []
                cancel_same_price = same_price_orders[1:] if len(same_price_orders) > 1 else []
                
                # 找出需要撤单的订单（价格低于最高价格的订单 + 相同价格的其他订单）
                cancel_buy_orders = [order for order in sorted_buy_orders if order.price < highest_price] + cancel_same_price
                
                # 撤单
                for order in cancel_buy_orders:
                    try:
                        await self.exchange.cancel_order(order.id)
                        log.info(f"撤销多余买单: 订单ID={order.id}, 价格={order.price:.2f}")
                    except Exception as e:
                        log.error(f"撤销买单失败: 订单ID={order.id}, 错误={e}")
                
                # 更新买单列表，只保留价格最高的档位
                self.buy_orders = keep_buy_orders
                if cancel_buy_orders:
                    log.info(f"买单保留 {len(keep_buy_orders)} 个订单（价格={highest_price:.2f}），撤销 {len(cancel_buy_orders)} 个订单")
            else:
                # 没有有效订单，清空列表
                self.buy_orders = []
        
        # 处理卖单：保留价格最低的档位
        if len(self.sell_orders) > 0:
            # 过滤掉无效订单（没有ID或价格的订单）
            valid_sell_orders = [order for order in self.sell_orders if order.id and order.price and order.price > 0]
            
            if len(valid_sell_orders) > 0:
                # 按价格从低到高排序
                sorted_sell_orders = sorted(valid_sell_orders, key=lambda x: x.price)
                
                # 找到价格最低的订单价格
                lowest_price = sorted_sell_orders[0].price
                
                # 找出所有需要保留的订单（价格最低的档位，只保留一个）
                same_price_orders = [order for order in sorted_sell_orders if order.price == lowest_price]
                # 只保留第一个（或最新的）订单，撤销其他相同价格的订单
                keep_sell_orders = [same_price_orders[0]] if same_price_orders else []
                cancel_same_price = same_price_orders[1:] if len(same_price_orders) > 1 else []
                
                # 找出需要撤单的订单（价格高于最低价格的订单 + 相同价格的其他订单）
                cancel_sell_orders = [order for order in sorted_sell_orders if order.price > lowest_price] + cancel_same_price
                
                # 撤单
                for order in cancel_sell_orders:
                    try:
                        await self.exchange.cancel_order(order.id)
                        log.info(f"撤销多余卖单: 订单ID={order.id}, 价格={order.price:.2f}")
                    except Exception as e:
                        log.error(f"撤销卖单失败: 订单ID={order.id}, 错误={e}")
                
                # 更新卖单列表，只保留价格最低的档位
                self.sell_orders = keep_sell_orders
                if cancel_sell_orders:
                    log.info(f"卖单保留 {len(keep_sell_orders)} 个订单（价格={lowest_price:.2f}），撤销 {len(cancel_sell_orders)} 个订单")
            else:
                # 没有有效订单，清空列表
                self.sell_orders = []

    async def process_order_statistics(self) -> None:
        """
        处理订单统计：记录已成交订单并缓存到 self.his_order，用于后续统计计算。
        
        当前实现（占位版本）：
        - 从交易所获取历史订单
        - 过滤出「新成交」订单（timestamp 大于 last_filled_time）
        - 将简化后的成交记录追加到 self.his_order 列表
        - 更新 last_filled_time，避免重复统计
        
        后续可以基于 self.his_order 计算：
        - 总盈亏 / 网格收益
        - 网格次数 / 完整周期数
        - 年化收益等
        """
        try:
            his_orders = await self.exchange.fetch_his_order(self.symbol)
        except Exception as e:
            log.error(f"获取历史订单失败: {e}")
            log.error(traceback.format_exc())
            return

        if not his_orders:
            return

        # 只保留成交状态且时间戳存在的订单
        filled_orders = [
            o for o in his_orders
            if getattr(o, "status", None) in ["filled", "executed"] and getattr(o, "timestamp", None)
        ]
        if not filled_orders:
            return

        # 只处理「新」成交订单（时间戳大于 last_filled_time）
        new_filled = [o for o in filled_orders if o.timestamp > self.last_filled_time]
        if not new_filled:
            return

        # 按时间排序，方便后续统计
        new_filled.sort(key=lambda o: o.timestamp)

        # 将成交记录转换为简化结构并缓存到 self.his_order
        for o in new_filled:
            record = {
                "order_id": getattr(o, "id", ""),
                "symbol": getattr(o, "symbol", self.symbol),
                "side": getattr(o, "side", None),            # 买/卖
                "type": getattr(o, "type", None),            # 开仓/平仓等
                "open_type": getattr(o, "open_type", 1),     # 开仓类型，1=开仓，2=平仓
                "price": float(getattr(o, "price", 0.0) or 0.0),
                "volume": float(getattr(o, "volume", 0.0) or 0.0),
                "pnl": float(getattr(o, "pnl", 0.0) or 0.0),  # 单笔盈亏（如果有）
                "fee": float(getattr(o, "fee", 0.0) or 0.0),
                "timestamp": o.timestamp,
                "status": o.status,
            }
            self.his_order.append(record)

        # 更新 last_filled_time，避免重复处理
        max_ts = max(o["timestamp"] for o in self.his_order if o.get("timestamp"))
        self.last_filled_time = max(self.last_filled_time, max_ts)

      

    async def _place_grid_orders(self, filled_price: float, order_volume: float) -> None:
        """
        在成交价格两边同时布单（买单和卖单）
        
        参数：
            filled_price: 成交价格
            order_volume: 订单数量（用于计算新订单数量）
        
        逻辑：
            1. 计算新订单价格：
               - 新买单价格 = filled_price * (1 - grid_spacing)
               - 新卖单价格 = filled_price * (1 + grid_spacing)
            2. 根据策略方向决定开仓/平仓：
               - long（做多）：买单=开仓(1)，卖单=平仓(2)
               - short（做空）：卖单=开仓(1)，买单=平仓(2)
            3. 挂新买单（如果价格在范围内）
            4. 挂新卖单（如果价格在范围内且有持仓）
        """
        # 计算新订单价格
        new_buy_price = filled_price * (1 - self.grid_spacing)
        new_sell_price = filled_price * (1 + self.grid_spacing)
        
        # 根据策略方向决定开仓/平仓类型
        if self.direction == "long":
            # 做多：买单=开仓，卖单=平仓
            buy_open_type = 1  # 开仓
            sell_open_type = 2  # 平仓
        else:  # short
            # 做空：卖单=开仓，买单=平仓
            buy_open_type = 2  # 平仓
            sell_open_type = 1  # 开仓
        side="buy" if self.direction == "long" else "sell"
        # 挂新买单（检查是否已存在相同价格的订单）
        if new_buy_price >= self.min_price:
            # 检查是否已存在相同价格的买单
      
            new_buy_order = await self._execute_order(
                side=side,
                volume=order_volume,
                open_type=buy_open_type,
                price=new_buy_price,
                order_type="limit",
                operation_name="挂买单",
                update_order_info=True
            )
            if new_buy_order:
                self.buy_order=new_buy_order
                log.info(f"挂买单: 价格={new_buy_price:.2f}, 数量={order_volume:.4f}, 类型={'开仓' if buy_open_type == 1 else '平仓'}")
            else:
                log.debug(f"买单价格 {new_buy_price:.2f} 挂单失败")
        
        # 挂新卖单（检查是否已存在相同价格的订单）
        if new_sell_price <= self.max_price and self.position.size > 0:
            # 检查是否已存在相同价格的卖单

            sell_volume = min(self.position.size, order_volume)
            new_sell_order = await self._execute_order(
                side=side,
                volume=sell_volume,
                open_type=sell_open_type,
                price=new_sell_price,
                order_type="limit",
                operation_name="挂卖单",
                update_order_info=True
            )
            if new_sell_order:
                self.sell_order=new_sell_order
                log.info(f"挂卖单: 价格={new_sell_price:.2f}, 数量={sell_volume:.4f}, 类型={'开仓' if sell_open_type == 1 else '平仓'}")
            else:
                log.debug(f"卖单价格 {new_sell_price:.2f} 挂单失败")
        else:
            log.debug(f"卖单错误：{new_sell_price}，最大价格：{self.max_price}，持仓大小：{self.position.size}")

    async def _initial_build_position(self) -> None:
        """
        执行初始建仓（合并了计算比例、数量和建仓逻辑）
        
        公式说明：
        1. 计算价格位置比例：position_ratio = (current_price - min_price) / (max_price - min_price)
           - position_ratio 在 0-1 之间
           - 0 表示价格在最低价，1 表示价格在最高价
        
        2. 根据策略方向计算初始持仓比例：
           - long（做多）：initial_ratio = 1 - position_ratio（价格越低，持仓比例越高）
           - short（做空）：initial_ratio = position_ratio（价格越高，持仓比例越高）
        
        3. 计算初始持仓数量：
           - initial_amount = investment_amount * initial_ratio
           - volume = initial_amount / current_price
        
        建仓逻辑：
        - 如果当前已有持仓，且方向一致，检查数量是否需要调整（仅支持加仓）
        - 如果当前无持仓，根据公式计算并建仓
        """
        if self.current_price is None:
            log.warning("当前价格为空，无法计算初始持仓")
            return
        
        # 确保价格在范围内
        current_price = max(self.min_price, min(self.current_price, self.max_price))
        
        # 计算价格位置比例
        price_range = self.max_price - self.min_price
        if price_range <= 0 or current_price <= 0:
            log.warning("价格范围无效或当前价格为0，无法计算初始持仓")
            return
        
        position_ratio = (current_price - self.min_price) / price_range
        
        # 根据策略方向计算初始持仓比例
        if self.direction == "long":
            initial_ratio = 1.0 - position_ratio  # 做多：价格越低，持仓比例越高
        else:  # short
            initial_ratio = position_ratio  # 做空：价格越高，持仓比例越高
        
        initial_ratio = max(0.0, min(1.0, initial_ratio))
        
        # 计算初始持仓金额和数量
        # 使用 start() 函数中已计算好的总资金
        if self.total_capital is None or self.total_capital <= 0:
            raise ValueError(f"总资金未正确初始化: {self.total_capital}")
        initial_amount = self.total_capital * initial_ratio
        target_volume = initial_amount / current_price
        
        log.info(f"初始持仓计算: 当前价格={current_price:.2f}, "
                f"位置比例={position_ratio:.2%}, "
                f"持仓比例={initial_ratio:.2%}, "
                f"持仓金额={initial_amount:.2f}, "
                f"持仓数量={target_volume:.4f}")
        
        if target_volume <= 0:
            log.info("计算出的初始持仓数量为0，不进行建仓")
            return
        
        # 如果已有持仓，检查是否需要调整
        if self.position.size ==0:
            # 无持仓，直接建仓
            side = "buy" if self.direction == "long" else "sell"
            await self._execute_order(side, target_volume, open_type=1,order_type="market", operation_name="建仓")
        await self._update_position()


    async def _execute_order(
        self,
        side: str,
        volume: float,
        open_type: int = None,
        price: float = None,
        order_type: str = "limit",
        operation_name: str = "",
        update_order_info: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        执行订单（统一方法，用于建仓、加仓、减仓、平仓、挂限价单）
        
        参数：
            side: 订单方向，"buy" 或 "sell"
            volume: 订单数量
            open_type: 开仓类型，1=开仓，2=平仓（限价单时可为None）
            price: 订单价格（限价单时必填，市价单时为None）
            order_type: 订单类型，"market"（市价单）或 "limit"（限价单）
            operation_name: 操作名称（用于日志），如 "建仓"、"加仓"、"挂买单"、"挂卖单"
            update_order_info: 是否更新订单信息（限价单时设为True，更新buy_order或sell_order）
        
        返回：
            Optional[Dict[str, Any]]: 订单信息字典（成功时返回），包含 id, price, volume, side, status 等字段；失败时返回 None
        """
        if volume <= 0:
            log.warning(f"{operation_name}数量为0，跳过")
            return None
        
        # 限价单需要价格
        if order_type == "limit" and price is None:
            log.error(f"{operation_name}限价单需要价格参数")
            return None
        try:
            # 构建订单参数（统一使用 create_order）
            order_params = {
                "symbol": self.symbol,
                "side": side,
                "vol": volume,  # create_order 使用 vol 参数
                "order_type": order_type,  # "market" 或 "limit"
            }
            
            # 限价单需要价格
            if order_type == "limit" and price is not None:
                order_params["price"] = price
            
            # 开仓类型（合约市场时需要）
            if open_type is not None and self.market_type == "contract":
                order_params["open_type"] = open_type
                if self.co_type is not None:
                    order_params["co_type"] = self.co_type
            
            # 平仓时需要传递 posId
            if open_type == 2 and self.position.id is not None:
                order_params["posId"] = self.position.id
            
            # 统一使用 create_order（支持限价单和市价单）
            # create_order 现在返回 OrderInfo 对象
            order_info = await self.exchange.create_order(**order_params)
            
            # 处理响应（order_info 是 OrderInfo 对象）
            if isinstance(order_info, OrderInfo):
                # 判断订单是否创建成功：
                # 检查是否为休市中（code: 6005）
                if order_info.code == 6005:
                    self._trading_status_cache["last_update_time"] = 0
                    self._trading_status_cache["is_trade"] = False
                    log.debug("市场休市中，已重置缓存时间")
                return order_info
               
            else:
                log.error(f"{operation_name}失败: 响应格式错误，期望 OrderInfo 对象")
                log.error(f"响应数据: {order_info}")
                return None
                
        except Exception as e:
            log.error(f"{operation_name}异常: {e}")
            log.error(traceback.format_exc())
            return None
    
    async def _update_position(self) -> None:
        """更新持仓信息（从交易所获取最新持仓数据）"""
        try:
            positions = await self.exchange.fetch_positions(self.symbol)
            # fetch_positions 传入 symbol 时，总是返回至少一个 Position 对象（如果没有持仓，返回 size=0 的对象）
            if positions and isinstance(positions, list) and len(positions) > 0:
                pos = positions[0]  # fetch_positions 已经按 symbol 过滤，取第一个
                self.position.id = pos.id
                self.position.size = pos.size
                self.position.side = pos.side
                self.position.entryPrice = pos.entryPrice
                self.position.unrealizedPnl = pos.unrealizedPnl
                self.position.liquidationPrice = pos.liquidationPrice
                self.position.amount = pos.amount
                self.position.timestamp = pos.timestamp
                # 如果持仓为0，清空持仓信息
                        
                        
        except Exception as e:
            log.error(f"更新持仓信息异常: {e}")

    
   
    
    async def start(
        self,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        启动策略
        
        参数：
            params: 参数字典，包含以下键：
                - symbol: 交易币种 (必需)
                - min_price: 最低价格 (必需)
                - max_price: 最高价格 (必需)
                - direction: 交易方向，"long" 或 "short" (必需)
                - grid_spacing: 网格间距（百分比，如 0.01 表示 1%）(必需)
                - investment_amount: 持仓总金额（最大持仓金额）(必需)
                - asset_type: 资产类型，"crypto"（加密货币）或 "stock"（股票），默认 "crypto"
                - market_type: 市场类型，"spot"（现货）或 "contract"（合约），默认 "contract"
        
        返回：
            Dict[str, Any]: 包含 status 和 message 的字典
                - status: "success" 或 "failed"
                - message: 状态消息
        """
        try:
            if self._status:
                raise ValueError("策略已在运行中，请先停止当前策略")
            
            # 依次检查必需参数
            required_params = ["symbol", "min_price", "max_price", "direction", "grid_spacing", "investment_amount", "leverage"]
            missing_params = [p for p in required_params if p not in params]
            if missing_params:
                raise ValueError(f"缺少必需参数: {', '.join(missing_params)}")
            
            # 提取参数
            symbol = params.get("symbol")
            min_price = params.get("min_price")
            max_price = params.get("max_price")
            direction = params.get("direction")
            grid_spacing = params.get("grid_spacing")
            investment_amount = params.get("investment_amount")
            leverage = params.get("leverage")
            asset_type = params.get("asset_type", "crypto")
            market_type = params.get("market_type", "contract")
            
            # 依次检查参数合法性
            # 1. 检查 symbol
            if not symbol or not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("symbol 必须是非空字符串")
            symbol = symbol.strip().upper()
            
            # 2. 检查 min_price
            try:
                min_price = float(min_price)
            except (TypeError, ValueError):
                raise ValueError("min_price 必须是有效的数字")
            if min_price <= 0:
                raise ValueError("min_price 必须大于 0")
            
            # 3. 检查 max_price
            try:
                max_price = float(max_price)
            except (TypeError, ValueError):
                raise ValueError("max_price 必须是有效的数字")
            if max_price <= 0:
                raise ValueError("max_price 必须大于 0")
            
            # 4. 检查价格区间
            if min_price >= max_price:
                raise ValueError("min_price 必须小于 max_price")
            
            # 5. 检查 direction
            if not direction or not isinstance(direction, str):
                raise ValueError("direction 必须是字符串")
            direction = direction.lower()
            if direction not in ["long", "short"]:
                raise ValueError("direction 必须是 'long' 或 'short'")
            
            # 6. 检查 grid_spacing
            try:
                grid_spacing = float(grid_spacing)
            except (TypeError, ValueError):
                raise ValueError("grid_spacing 必须是有效的数字")
            if grid_spacing <= 0 or grid_spacing >= 1:
                raise ValueError("grid_spacing 必须在 0 和 1 之间（例如：0.005 表示 0.5%）")
            
            # 7. 检查 investment_amount
            try:
                investment_amount = float(investment_amount)
            except (TypeError, ValueError):
                raise ValueError("investment_amount 必须是有效的数字")
            if investment_amount <= 0:
                raise ValueError("investment_amount 必须大于 0")
            
            # 8. 检查 leverage
            try:
                leverage = float(leverage)
            except (TypeError, ValueError):
                raise ValueError("leverage 必须是有效的数字")
            if leverage <= 0:
                raise ValueError("leverage 必须大于 0")
            if leverage > 100:
                raise ValueError("leverage 不能超过 100")
            
            # 9. 检查 asset_type
            if asset_type not in ["crypto", "stock"]:
                raise ValueError("asset_type 必须是 'crypto' 或 'stock'")
            
            # 10. 检查 market_type
            if market_type not in ["spot", "contract"]:
                raise ValueError("market_type 必须是 'spot' 或 'contract'")
            
            # 设置网格参数（按顺序设置，确保所有参数都已初始化）
            self.symbol = symbol
            self.min_price = min_price
            self.max_price = max_price
            self.direction = direction
            self.grid_spacing = grid_spacing
            self.investment_amount = investment_amount
            self.leverage = float(leverage)  # 确保杠杆倍数是浮点数类型
            self.asset_type = asset_type
            self.market_type = market_type
            
            # 验证关键参数已正确设置
            if self.leverage is None or self.leverage <= 0:
                raise ValueError(f"杠杆倍数未正确初始化: {self.leverage}")
            if self.investment_amount is None or self.investment_amount <= 0:
                raise ValueError(f"投入资金未正确初始化: {self.investment_amount}")
            
            # 根据资产类型和市场类型计算 co_type
            # co_type: 1=股票合约, 3=币币合约
            if asset_type == "stock" and market_type == "contract":
                self.co_type = 1
            elif asset_type == "crypto" and market_type == "contract":
                self.co_type = 3
            elif market_type == "spot":
                # 现货市场，co_type 可能不需要或设为其他值
                # 根据实际业务逻辑调整
                self.co_type = None  # 现货可能不需要 co_type
            else:
                raise ValueError(f"不支持的资产类型和市场类型组合: asset_type={asset_type}, market_type={market_type}")
            
            # 更新统计信息
            self.stats["co_type"] = self.co_type
            
            # 计算总资金 = 投入资金 * 杠杆倍数，并保存为实例变量
            self.total_capital = self.investment_amount * self.leverage

            # ========== 初始化：根据网格参数设置杠杆倍数 ==========
            # 仅在合约模式下设置杠杆；现货模式不需要设置
            if self.market_type == "contract" and self.co_type is not None:
                try:
                    set_res = await self.exchange.set_leverage(
                        symbol=self.symbol,
                        leverage=int(self.leverage),
                        co_type=int(self.co_type),
                        margin_mode=1,  # 目前默认使用 1，对应接口中的 marginMode
                    )
                    if not set_res.get("ok"):
                        log.warning(
                            f"设置杠杆失败: symbol={self.symbol}, leverage={self.leverage}, "
                            f"code={set_res.get('code')}, msg={set_res.get('msg')}"
                        )
                    else:
                        log.info(
                            f"杠杆设置成功: symbol={self.symbol}, leverage={self.leverage}, "
                            f"co_type={self.co_type}"
                        )
                except Exception as e:
                    log.error(f"设置杠杆异常: symbol={self.symbol}, leverage={self.leverage}, error={e}")
            
            # 资金检查：验证账户可用余额是否足够
            # 注意：对于杠杆交易，用户只需要提供保证金（投资额），而不是总资金（投资额 × 杠杆）
            try:
                account_info = await self.exchange.fetch_account()
                free_balance = account_info.get("balance", 0.0)
                # 所需资金 = 投资额（作为保证金）
                required_amount = self.investment_amount
                
                if free_balance < required_amount:
                    raise ValueError(
                        f"账户余额不足：可用余额 ${free_balance:.2f}，"
                        f"所需保证金 ${required_amount:.2f}（投资额 ${self.investment_amount:.2f}）"
                    )
                
                log.info(f"资金检查通过：可用余额 ${free_balance:.2f}，所需保证金 ${required_amount:.2f}，总资金 ${self.total_capital:.2f}（投资额 × 杠杆 {leverage}倍）")
            except ValueError:
                # 重新抛出 ValueError，让上层处理
                raise
            except Exception as e:
                log.error(f"资金检查失败: {e}")
                raise ValueError(f"无法获取账户余额信息: {str(e)}")
            
            log.info(f"网格策略参数设置: {symbol}, 方向={direction}, "
                    f"价格范围=[{min_price}, {max_price}], "
                    f"网格间距={grid_spacing*100}%, 投入资金={investment_amount}, "
                    f"杠杆倍数={leverage}, 总资金={self.total_capital}, "
                    f"资产类型={asset_type}, 市场类型={market_type}, co_type={self.co_type}")
            
            # 在调用 run() 之前就标记为已启动，避免重复启动
            self._status = True
            log.info(f"网格策略已启动: {self.symbol}")
            
            # 使用 asyncio.create_task 启动 run 方法，并保存任务引用
            self._run_task = asyncio.create_task(self.run())
            
            return {
                "status": "success",
                "message": "网格策略已启动",
                "params": {
                    "symbol": symbol,
                    "min_price": min_price,
                    "max_price": max_price,
                    "direction": direction,
                    "grid_spacing": grid_spacing,
                    "investment_amount": investment_amount,
                    "leverage": leverage,
                    "total_capital": self.total_capital,
                    "asset_type": asset_type,
                    "market_type": market_type,
                }
            }
        except Exception as e:
            log.error(f"启动策略失败: {e}")
            log.error(traceback.format_exc())
            return {
                "status": "failed",
                "message": str(e)
            }
    
    async def stop(self) -> None:
        """
        停止策略
        
        行为：
        1. 将运行标志 `_status` 置为 False，停止主循环；
        2. 取消当前策略相关的所有未成交挂单；
        3. 仅清理策略内部状态（订单引用、运行标记等），**不再主动平掉已有持仓**。
        """
        # 1. 标记策略已停止，主循环会在下一轮自然退出
        self._status = False
        log.info(f"开始停止网格策略: {self.symbol or '未设置'}")

        # 2. 取消所有挂单（不仅仅是当前记录的买单/卖单）
        try:
            if self.symbol:
                open_orders = await self.exchange.fetch_orders(self.symbol)
            else:
                open_orders = await self.exchange.fetch_orders()
        except Exception as e:
            log.error(f"获取待取消挂单失败: {e}")
            open_orders = None

        if open_orders:
            for order in open_orders:
                try:
                    if not getattr(order, "id", None):
                        continue
                    await self.exchange.cancel_order(order.id)
                    log.info(f"已取消挂单: order_id={order.id}, price={getattr(order, 'price', None)}, volume={getattr(order, 'volume', None)}")
                except Exception as e:
                    log.error(f"取消挂单失败: order_id={getattr(order, 'id', 'unknown')}, error={e}")

        # 3. 清理内部状态，避免下次启动时受到影响（不再主动平仓）
        self.buy_order = None
        self.sell_order = None
        log.info(f"网格策略已停止并完成清理: {self.symbol if self.symbol else '未设置'}")
    
    def _calculate_summary(self) -> Dict[str, Any]:
        """
        计算汇总统计数据：
        - 总投资
        - 总盈亏（已实现 + 未实现）
        - 网格收益
        - 未实现盈亏
        - 套利年化收益
        - 网格次数

        说明：
        - 这里的计算是一个「合理默认实现」，具体业务规则可以后续再细化。
        - self.his_order 中的每条记录结构参考 process_order_statistics 中的 record。
        """
        # 使用 start() 函数中已计算好的总资金
        total_investment = float(self.total_capital or 0.0)

        # 已实现盈亏：如果 his_order 中有 pnl 字段，则累加；否则使用 stats["realized_pnl"]
        realized_pnl_from_orders = 0.0
        for o in self.his_order:
            try:
                realized_pnl_from_orders += float(o.get("pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue

        # stats_realized = float(self.stats.get("realized_pnl", 0.0) or 0.0)
        # realized_pnl = realized_pnl_from_orders if realized_pnl_from_orders != 0.0 else stats_realized

        # 未实现盈亏：来自当前持仓（Position 一定有 unrealizedPnl 字段）
        unrealized_pnl = float(self.position.unrealizedPnl or 0.0)

        # 总盈亏 = 已实现 + 未实现
        total_pnl = realized_pnl_from_orders + unrealized_pnl

        # 网格收益：这里先等同于已实现盈亏，后续可根据网格策略细化
        grid_profit = realized_pnl_from_orders

        # 网格次数：先用成交记录条数作为近似值（后续可按平仓次数等更精细的定义）
        grid_count = len(self.his_order)

        # 套利次数：统计平仓订单数量（open_type=2）
        arbitrage_count = sum(1 for o in self.his_order if o.get("open_type") == 2)

        # 总成交额：累加所有订单的成交金额
        total_volume = sum(
            float(o.get("price", 0.0) or 0.0) * float(o.get("volume", 0.0) or 0.0)
            for o in self.his_order
        )

        # 套利年化收益：基于总盈亏 / 总投资 和运行时间简单估算
        annualized_return = 0.0
        start_time = self.stats.get("start_time")
        if total_investment > 0 and start_time:
            try:
                elapsed = datetime.now() - start_time
                days = max(elapsed.total_seconds() / 86400.0, 1e-6)  # 避免除 0
                simple_return = total_pnl / total_investment  # 总收益率
                annualized_return = simple_return * (365.0 / days) * 100.0  # 转年化百分比
            except Exception as e:
                log.error(f"计算年化收益失败: {e}")

        return {
            "total_investment": total_investment,
            "total_pnl": total_pnl,
            "grid_profit": grid_profit,
            "unrealized_pnl": unrealized_pnl,
            "annualized_return": annualized_return,
            "grid_count": grid_count,
            "arbitrage_count": arbitrage_count,
            "total_volume": total_volume,
        }

    def get_status(self) -> Dict[str, Any]:
        """获取策略状态与汇总指标"""
        # ==================== 订单信息 ====================
        # 格式化买单（统一使用对象模式）
        buy_order_info = None
        if self.buy_order and isinstance(self.buy_order, OrderInfo):
            buy_order_info = {
                "order_id": self.buy_order.id or "",
                "price": self.buy_order.price,
                "volume": self.buy_order.volume,
                "status": self.buy_order.status,
            }
        
        # 格式化卖单（统一使用对象模式）
        sell_order_info = None
        if self.sell_order and isinstance(self.sell_order, OrderInfo):
            sell_order_info = {
                "order_id": self.sell_order.id or "",
                "price": self.sell_order.price,
                "volume": self.sell_order.volume,
                "status": self.sell_order.status,
            }

        # ==================== 汇总指标 ====================
        summary = self._calculate_summary()
        
        # 计算市场状态（是否在交易时段）
        is_trading_hours = self.is_us_stock_trading_hours()
        
        # 计算已运行时间
        elapsed_time = None
        start_time = self.stats.get("start_time")
        if start_time:
            try:
                elapsed = datetime.now() - start_time
                total_seconds = int(elapsed.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                if hours > 0:
                    elapsed_time = f"{hours}小时{minutes}分钟"
                elif minutes > 0:
                    elapsed_time = f"{minutes}分钟{seconds}秒"
                else:
                    elapsed_time = f"{seconds}秒"
            except Exception as e:
                log.error(f"计算运行时间失败: {e}")
        
        return {
            # 基本信息
            "symbol": self.symbol,
            "direction": self.direction,
            "price_range": [self.min_price, self.max_price] if self.min_price and self.max_price else None,
            "grid_spacing": self.grid_spacing,
            "asset_type": self.asset_type,
            "market_type": self.market_type,
            "current_price": self.current_price,
            "start_price": self.start_price,  # 启动价格
            "leverage": float(self.leverage or 1.0),  # 倍数（从配置参数获取）
            "investment_amount": float(self.investment_amount or 0.0),  # 投资额（不包含杠杆）
            "elapsed_time": elapsed_time,  # 已运行时间

            # 运行与连接状态
            "running": self._status,
            "connected": self.exchange.connected(),
            "is_trading_hours": is_trading_hours,  # 是否在交易时段（仅对美股有效）

            # 汇总指标
            "summary": summary,

            # 订单与持仓明细
            "buy_order": buy_order_info,
            "sell_order": sell_order_info,
            "position": {
                "size": self.position.size,
                "amount": self.position.amount,
                "entryPrice": self.position.entryPrice,
                "unrealizedPnl": self.position.unrealizedPnl,
                "side": self.position.side,
            },

            # 历史成交（最近 5 条）
            "his_order": self.his_order[-5:] if len(self.his_order) > 0 else [],
        }
    
    async def alert_msg(self, msg: str) -> None:
        """发送报警消息"""
        url = "https://api.day.app/c4FFQEfS8oc3KyXEBhCQBF/网格策略报警/" + msg
        requests.get(url)

