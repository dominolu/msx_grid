"""
监听 Chrome DevTools Protocol 数据

功能：
1. 监听 HTTP 请求和响应
2. 监听 WebSocket 发送和接收数据
3. 解析并缓存交易数据（BAR/KLINE、POSITION、ORDER、DEPTH、TICKER 等）
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
import traceback
from playwright.async_api import async_playwright
from typing import Optional, Dict, Any, List, Callable
from functools import wraps
from loguru import logger as log
from .models import OrderInfo, Position
import time
# ========== 配置区 ==========
URL_KEYWORDS = [
    "contract-trading", "api", "trade", "market", "depth",
    "quote", "ws", "socket", "kline", "ohlc", "order", "position",
]
MAX_PRINT_LEN = 4000  # 响应体最大打印长度
LOG_DIR = "logs"  # 日志保存目录
# ============================
PRODUCT="/co/stock/product/page"
POSITIONS="/co/pos/list"
ORDERS_LIMIT="/co/stock/order/limit"  # 查询订单列表API
ORDERS_TRADE="/co/stock/order/trade"  # 创建订单API
ORDERS_CANCEL="/co/stock/order/cancel"  # 取消订单API

def looks_like_json(text: str) -> bool:
    """判断文本是否看起来像 JSON"""
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))

def short(s: str, n: int = MAX_PRINT_LEN) -> str:
    """截断长字符串"""
    if s is None:
        return ""
    return s if len(s) <= n else s[:n] + f"...(truncated, len={len(s)})"

def try_parse_json(s: str) -> Optional[dict]:
    """尝试解析 JSON"""
    try:
        return json.loads(s)
    except Exception:
        return None

def format_timestamp() -> str:
    """格式化时间戳"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def retry(max_retries: int = 3, delay: float = 1.0):
    """
    重试装饰器，支持异步函数
    
    Args:
        max_retries: 最大重试次数（不包括首次尝试）
        delay: 重试之间的延迟时间（秒）
    
    Usage:
        @retry(max_retries=3, delay=1)
        async def my_function():
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        log.warning(
                            f"[RETRY] {func.__name__} 失败 (尝试 {attempt + 1}/{max_retries + 1}): {str(e)}，"
                            f"等待 {delay} 秒后重试..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            f"[RETRY] {func.__name__} 失败，已达最大重试次数 ({max_retries + 1}): {str(e)}"
                        )
                        raise last_exception
            # 理论上不会到达这里，但为了类型检查
            if last_exception:
                raise last_exception
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        log.warning(
                            f"[RETRY] {func.__name__} 失败 (尝试 {attempt + 1}/{max_retries + 1}): {str(e)}，"
                            f"等待 {delay} 秒后重试..."
                        )
                        time.sleep(delay)
                    else:
                        log.error(
                            f"[RETRY] {func.__name__} 失败，已达最大重试次数 ({max_retries + 1}): {str(e)}"
                        )
                        raise last_exception
            # 理论上不会到达这里，但为了类型检查
            if last_exception:
                raise last_exception
        
        # 根据函数是否为协程函数来决定返回哪个包装器
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

def analyze_data_type(data: dict, url: str = "") -> tuple[str, str]:
    """分析数据类型并返回标签和类型名
    返回: (显示标签, 类型名)
    """
    if not isinstance(data, dict):
        return ("unknown", "unknown")
    
    url_lower = url.lower()
    data_keys = list(data.keys()) if isinstance(data, dict) else []
    
    # 根据 URL 和数据结构推断类型
    if any(kw in url_lower for kw in ["bar", "kline", "candlestick", "ohlc"]):
        return ("📊 BAR/KLINE", "bar_kline")
    elif any(kw in url_lower for kw in ["position"]):
        return ("💼 POSITION", "position")
    elif any(kw in url_lower for kw in ["order"]):
        return ("📋 ORDER", "order")
    elif any(k in data_keys for k in ["kline", "bar", "ohlc", "candle"]):
        return ("📊 BAR/KLINE", "bar_kline")
    elif any(k in data_keys for k in ["positions", "position"]):
        return ("💼 POSITION", "position")
    elif any(k in data_keys for k in ["orders", "order"]):
        return ("📋 ORDER", "order")
    elif any(k in data_keys for k in ["depth", "bids", "asks"]):
        return ("📈 DEPTH", "depth")
    elif any(k in data_keys for k in ["ticker", "price", "last"]):
        return ("💰 TICKER", "ticker")
    else:
        return ("📦 DATA", "data")


class MsxExchange:
    """
    MSX Exchange 类 - 类似 ccxt 的接口设计
    
    用法：
        exchange = MsxExchange(
            cdp_url='http://localhost:9222',
            target_url='https://msx.com/contract-trading'
        )
        await exchange.connect()
        ticker = await exchange.fetch_ticker('AMAT')
        await exchange.watch_ticker('AMAT', callback=lambda data: print(data))
        await exchange.run()
    """
    
    def __init__(
        self,
        cdp_url: str = 'http://localhost:9222',
        target_url: str = 'https://msx.com/contract-trading',
        url_keywords: Optional[List[str]] = None,
        verbose: bool = False,
    ):
        """初始化 MSX Exchange
        
        Args:
            cdp_url: Chrome DevTools Protocol URL
            target_url: 目标交易页面 URL
            url_keywords: URL 过滤关键词列表
            verbose: 是否打印详细日志
        """
        self.cdp_url = cdp_url
        self.target_url = target_url
        self.verbose = verbose
        self.url_keywords = url_keywords or URL_KEYWORDS
        
        # Playwright 相关
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        
        # 数据缓存
        self._tickers = {}  # {symbol: ticker_data}
        self._orders = {}   # {symbol: [order_data]}
        self._positions = {} # {symbol: [position_data]} - 一个symbol可能有多个持仓（做多/做空）
        self._ohlcv = {}    # {symbol: {timeframe: [candle_data]}}
        self._bars = {}     # {symbol: bar_data} - 最新的K线数据
        self._markets = {}  # {symbol: market_info} - 市场信息（产品列表）
        self._account = {}  # 账户信息（余额、总盈亏等）
        self.invite_code = None
        self._checking_invite = False  # 防止 fetch_invite 递归调用标志
        # 订阅回调
        self._subscribers = {
            'ticker': {},   # {symbol: [callbacks]}
            'orders': {},  # {symbol: [callbacks]}
            'positions': {}, # {symbol: [callbacks]}
            'ohlcv': {},   # {symbol: {timeframe: [callbacks]}}
        }
        
        # 认证信息
        self._auth_headers = None
        self.auth_status=False
        self._api_endpoints = {}
        self._api_base = ""
        self._api_ctx = None  # Playwright APIRequestContext
        
        # 请求频率控制
        self._min_request_interval = 0.1  # 最小请求间隔（秒），默认 100ms
        self._last_request_time = 0.0  # 上次请求时间戳
        self._last_order_time = 0.0  # 上次下单时间戳
        self._last_his_order_time = 0.0  # 上次获取历史订单时间戳
        
        # 页面重载控制
        self._min_reload_interval = 300  # 最小重载间隔（秒），默认 30 秒，防止频繁重载
        self._last_reload_time = 0.0  # 上次重载页面时间戳
        self._reloading = False  # 是否正在重载中，防止并发重载
        self.configs = {} # 配置信息
        # 运行状态
        self._running = False
        self._connected = False
    
    def connected(self):
        return self._connected

    async def connect(self):
        """连接到 Chrome DevTools Protocol 并初始化浏览器"""
        if self._connected:
            return
        
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
            
            if not self._browser.contexts:
                self._context = await self._browser.new_context()
            else:
                self._context = self._browser.contexts[0]
            
            if self._context.pages:
                self._page = self._context.pages[0]
            else:
                self._page = await self._context.new_page()
            
            # 注册监听器
            self._wire_listeners()
            
            # 导航到目标页面
            if self.target_url:
                await self._page.goto(self.target_url, timeout=60000)
            
            self._connected = True
            
            if self.verbose:
                print("✅ 已连接到 Chrome DevTools Protocol")
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            self._connected = False
    
    async def disconnect(self):
        """断开连接并清理资源"""
        if not self._connected:
            return
        
        self._running = False
        
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            
            self._connected = False
            if self.verbose:
                print("✅ 已断开连接")
        except Exception as e:
            print(f"❌ 断开连接时出错: {e}")
    
    def _wire_listeners(self):
        """注册网络监听器"""
        if not self._page or not self._context:
            return
        
        def url_matches_local(url: str) -> bool:
            """本地 URL 匹配函数"""
            if not url:
                return False
            u = url.lower()
            return any(kw in u for kw in self.url_keywords)
        
        # HTTP 请求监听
        async def on_request(req):
            await self._handle_request(req)
           
        
        # HTTP 响应监听
        async def on_response(resp):
            if not url_matches_local(resp.url):
                return
            # if self.verbose:
            #     await self._handle_response(resp)
            try:
                body = await resp.text()
                parsed = try_parse_json(body) if body else None
                if parsed:
                    # 检查是否是产品列表API
                    if PRODUCT in resp.url:
                        asyncio.create_task(self.parse_product_page(parsed))
                    elif POSITIONS in resp.url:
                        asyncio.create_task(self.parse_positions(parsed))
                    elif ORDERS_LIMIT in resp.url:
                        # 只对查询订单列表的API调用parse_orders（返回列表格式）
                        asyncio.create_task(self.parse_orders(parsed))
                    # 注意：创建订单(ORDERS_TRADE)和取消订单(ORDERS_CANCEL)的响应格式不同，不需要调用parse_orders
                    
                    else:
                        await self._process_data(resp.url, parsed, 'http')
            except Exception:
                pass
        
        # WebSocket 监听
        def on_ws(ws):
            ws_url = ws.url

            def on_frame_received(frame):
                try:
                    payload_s = self._frame_to_string(frame)
                    parsed = try_parse_json(payload_s)
                    if parsed:
                        # 检查是否是K线数据（通过 ws_url 或数据内容判断）
                        # 将 ws_url 添加到 parsed 中以便后续处理
                        if isinstance(parsed, dict):
                            parsed["ws_url"] = ws_url
                        
                        # 判断是否是K线数据
                        is_kline = (
                            "kline" in ws_url.lower() or 
                            "kline" in str(parsed).lower() or
                            (isinstance(parsed, dict) and "data" in parsed and isinstance(parsed.get("data"), dict) and "symbol" in parsed.get("data", {}))
                        )
                        
                        if is_kline:
                            asyncio.create_task(self.parse_ticker(parsed))
                        else:
                            # 其他类型的数据可以在这里处理
                            if self.verbose:
                                log.debug(f"收到非K线WebSocket数据: {parsed}")
                except Exception as e:
                    if self.verbose:
                        log.error(f"❌ on_frame_received error: {e}")
            
            def on_close():
                """WebSocket 连接关闭时的回调"""
                if self.verbose:
                    log.warning(f"🔌 WebSocket 连接已断开: {ws_url}")
                
                # 异步重载页面（带间隔控制，防止频繁重载）
                async def reload_page_if_needed():
                    """在满足条件时重载页面"""
                    current_time = time.time()
                    
                    # 检查是否正在重载中
                    if self._reloading:
                        if self.verbose:
                            log.debug(f"⏳ 页面正在重载中，跳过本次重载请求")
                        return
                    
                    # 检查距离上次重载的时间间隔
                    time_since_last_reload = current_time - self._last_reload_time
                    if time_since_last_reload < self._min_reload_interval:
                        if self.verbose:
                            log.debug(f"⏳ 距离上次重载仅 {time_since_last_reload:.1f} 秒，还需等待 {self._min_reload_interval - time_since_last_reload:.1f} 秒")
                        return
                    
                    # 检查页面是否可用
                    if not self._page or not self._connected:
                        if self.verbose:
                            log.warning(f"⚠️ 页面不可用，无法重载")
                        return
                    
                    # 执行重载
                    try:
                        self._reloading = True
                        self._last_reload_time = current_time
                        
                        if self.verbose:
                            log.info(f"🔄 开始重载页面: {self.target_url}")
                        
                        await self._page.reload(timeout=60000, wait_until="networkidle")
                        
                        if self.verbose:
                            log.info(f"✅ 页面重载完成")
                    except Exception as e:
                        if self.verbose:
                            log.error(f"❌ 重载页面时出错: {e}")
                    finally:
                        self._reloading = False
                
                # 使用 create_task 异步执行重载
                try:
                    asyncio.create_task(reload_page_if_needed())
                except Exception as e:
                    if self.verbose:
                        log.error(f"❌ 创建重载任务失败: {e}")
            
            def on_error(error):
                """WebSocket 发生错误时的回调"""
                if self.verbose:
                    log.error(f"❌ WebSocket 发生错误 ({ws_url}): {error}")
            
            ws.on("framereceived", on_frame_received)
            ws.on("close", on_close)
            ws.on("socketerror", on_error)
        
        self._page.on("request", on_request)
        self._page.on("response", on_response)
        self._page.on("websocket", on_ws)
        self._context.on("websocket", on_ws)
    
    async def get_symbols(self, market_type: str = "contract", co_type: int = None) -> List[Any]:
        """
        获取交易对列表
        
        参数:
            market_type: 市场类型，"contract"（合约）或 "spot"（现货），默认 "contract"
            co_type: 合约类型，1=股票，3=加密货币，仅合约市场有效，默认 None（使用默认值1）
        
        返回:
            交易对符号列表
        """
        if market_type == "contract":
            # 合约：直接调用合约产品列表接口获取
            # 如果未指定 co_type，默认使用 1（股票）
            co_type = co_type if co_type is not None else 1
            return await self._fetch_contract_symbols(co_type=co_type)
        elif market_type == "spot":
            # 现货：从现货 API 获取
            return await self._fetch_spot_symbols()
        else:
            raise ValueError(f"不支持的 market_type: {market_type}，必须是 'contract' 或 'spot'")
    
    async def _fetch_contract_symbols(self, co_type: int = 1) -> List[Dict[str, Any]]:
        """
        从合约 API 获取交易对列表
        
        API: https://api9528mystks.mystonks.org/api/v1/co/stock/product/page
        
        参数:
            co_type: 合约类型，1=股票，3=加密货币，默认 1

        """
        try:
            # 使用 _request_api 方法调用合约产品列表 API（POST 请求）
            api_path = "https://api9528mystks.mystonks.org/api/v1/co/stock/product/page"
            payload = {
                "page": 1,
                "pageSize": 10000,
                "search": "",
                "favorite": 2,
                "lang": "zh",
                "coType": co_type,
            }
            res = await self._request_api("POST", api_path, json_body=payload)

            if not res.get("ok"):
                log.error(f"获取合约交易对失败: {res.get('msg', 'unknown error')}")
                return []

            data = res.get("data", {})
            if not isinstance(data, dict):
                log.warning(f"合约 API 数据格式异常: {data}")
                return []

            product_list = data.get("list", [])
            if not isinstance(product_list, list):
                log.warning(f"合约交易对列表格式异常: {product_list}")
                return []

            # 只返回前端需要的精简字段：id, symbol, name, type, leverTypes
            result: List[Dict[str, Any]] = []

            for product in product_list:
                symbol = (product.get("symbol") or "").strip()
                if not symbol:
                    continue

                item = {
                    "id": str(product.get("id", "")),
                    "symbol": symbol,
                    "name": product.get("name", ""),
                    "type": int(product.get("type", 1) or 1),
                    "leverTypes": product.get("leverTypes", ""),
                }
                result.append(item)

            log.info(f"成功获取 {len(result)} 个合约交易对")
            return result

        except Exception as e:
            log.error(f"获取合约交易对异常: {e}")
            log.exception(e)
            return []

    async def _fetch_spot_symbols(self) -> List[str]:
        """
        从现货 API 获取交易对列表
        
        API: https://api9528mystks.mystonks.org/api/v1/stockhome/home/1/1000
        """
        try:
            # 使用 _request_api 方法调用现货 API
            api_path = "https://api9528mystks.mystonks.org/api/v1/stockhome/home/1/1000"
            
            res = await self._request_api("GET", api_path)
            
            if not res.get("ok"):
                log.error(f"获取现货交易对失败: {res.get('msg', 'unknown error')}")
                return []
            
            # 提取数据
            data = res.get("data", {})
            if not isinstance(data, dict):
                log.warning(f"现货 API 数据格式异常: {data}")
                return []
            
            stock_list = data.get("data", [])
            if not isinstance(stock_list, list):
                log.warning(f"现货交易对列表格式异常: {stock_list}")
                return []
            
            # 只返回前端需要的精简字段：symbol, name
            result: List[Dict[str, Any]] = []
            for stock in stock_list:
                symbol = (stock.get("symbol") or "").strip()
                if not symbol:
                    continue
                
                item = {
                    "symbol": symbol,
                    "name": stock.get("name", ""),
                }
                result.append(item)
            
            log.info(f"成功获取 {len(result)} 个现货交易对")
            return result
            
        except Exception as e:
            log.error(f"获取现货交易对异常: {e}")
            log.exception(e)
            return []

    async def parse_ticker(self, parsed: Dict):
        """
        解析K线数据并保存到 _tickers[symbol] 中
        
        数据格式示例：
       '{"bid":358.46,"ask":359.03,"symbol":"kline_his_us:AVGO:15m","timestamp":1762389900000,"open":"358.9989","high":"359.03","low":"358.298","close":"358.9899","volume":3935,"change":"7.0499","change_prec":"2","mark_prices":{"AMD":255.51,"NVDA":196.37}}\n'
        """
        try:
            data=parsed 
            # 提取symbol（例如：从 "kline_his_us:NVDA:15m" 提取 "NVDA"）
            raw_symbol = parsed.get("symbol", "")
            if not raw_symbol:
                #log.warning(f"K线数据中没有symbol字段: {parsed}")
                return
            
            # 解析symbol，格式可能是 "kline_his_us:NVDA:15m" 或类似格式
            # 提取实际的交易对符号
            symbol_parts = raw_symbol.split(":")
            if len(symbol_parts) >= 2:
                symbol = symbol_parts[1]  # 提取 "NVDA"
                timeframe = symbol_parts[2] if len(symbol_parts) > 2 else "15m"  # 提取时间周期
            else:
                symbol = raw_symbol  # 如果格式不匹配，直接使用原始值
                timeframe = None
            
            # 构建标准化的K线数据
            bar_data = {
                "symbol": symbol,
                "raw_symbol": raw_symbol,
                "timeframe": timeframe,
                "timestamp": data.get("timestamp", 0),  # 毫秒时间戳
                "datetime": None,  # 可以转换为可读时间
                "open": float(data.get("open", 0)) if data.get("open") else 0,
                "high": float(data.get("high", 0)) if data.get("high") else 0,
                "low": float(data.get("low", 0)) if data.get("low") else 0,
                "close": float(data.get("close", 0)) if data.get("close") else 0,
                "volume": int(data.get("volume", 0)) if data.get("volume") else 0,
                "bid": float(data.get("bid", 0)) if data.get("bid") else 0,
                "ask": float(data.get("ask", 0)) if data.get("ask") else 0,
                "change": data.get("change", "0"),
                "change_prec": data.get("change_prec", "0"),
                "mark_prices": data.get("mark_prices", {}),
                "raw_data": data,  # 保留原始数据
                "received_at": parsed.get("timestamp", ""),  # WebSocket接收时间
            }
            
            # 转换时间戳为可读格式
            if bar_data["timestamp"]:
                try:
                    from datetime import datetime as dt_class
                    dt = dt_class.fromtimestamp(bar_data["timestamp"] / 1000)
                    bar_data["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception as e:
                    log.debug(f"时间戳转换失败: {e}")
            
   
            
            # 更新 ticker 数据（包含 bid/ask）
            ticker_data = {
                "symbol": symbol,
                "bid": bar_data["bid"],
                "ask": bar_data["ask"],
                "last": bar_data["close"],
                "timestamp": bar_data["timestamp"],
                "datetime": bar_data["datetime"],
            }
            self._tickers[symbol] = ticker_data
            
            # 触发订阅回调
            await self._emit('ticker', symbol, ticker_data)
            if self.verbose:
                log.info(f"✅ 解析K线数据成功: {symbol} | Bid: {bar_data['bid']} | Ask: {bar_data['ask']} | Close: {bar_data['close']}")
        
        except Exception as e:
            log.error(f"❌ 解析K线数据失败: {e}")
            log.exception(e)
    
    async def fetch_ticker(self, symbol: str):
        """
        获取K线数据
        """
        return self._tickers.get(symbol, {})

    async def change_symbol(self, symbol: str):
        """
        切换交易对
        """
        try:
            self._symbol = symbol
            await self._page.goto(f"{self.target_url}/{symbol}", timeout=60000)
            # self._auth_headers = None
            # self.auth_status=False
        except Exception as e:
            log.error(f"❌ 切换交易对失败: {e}")
            log.exception(e)

    async def parse_product_page(self, parsed: Dict):
        """
        解析产品列表API数据并保存到 _markets[symbol] 中
        
        API: https://api9528mystks.mystonks.org/api/v1/co/stock/product/page
        
        数据格式示例：
        {
            "code": 0,
            "msg": "success",
            "data": {
                "count": 12,
                "list": [
                    {
                        "id": "1",
                        "symbol": "AAPL",
                        "name": "Apple Inc",
                        "nameZh": "苹果",
                        "price": "269.95",
                        "diffValue": "-0.09",
                        "upDownsScope": "-0.03",
                        "leverTypes": "1,20",
                        "holdMarginRate": "0.001",
                        "pricePrecision": 2,
                        "volPrecision": 9,
                        "unitQuantity": "1",
                        "type": 1,
                        "is_favorite": 0
                    },
                    ...
                ],
                "pageIndex": 1,
                "pageSize": 100
            },
            "timestamp": 1762396089
        }
        """
        try:
            # 提取数据部分
            data = parsed.get("data", {})
            if not data or not isinstance(data, dict):
                log.warning(f"产品列表数据中没有data字段: {parsed}")
                return
            
            # 检查是否是标准格式 (code: 0, data: {...})
            if "code" in parsed and parsed.get("code") != 0:
                log.warning(f"API返回错误: {parsed.get('msg', 'unknown error')}")
                return
            
            # 提取产品列表
            product_list = data.get("list", [])
            if not product_list:
                log.debug(f"产品列表为空")
                return
            
            # 解析每个产品
            parsed_count = 0
            new_symbols = []
            updated_symbols = []
            
            for product in product_list:
                symbol = product.get("symbol", "").strip()
                if not symbol:
                    continue
                
                # 检查是否是新增的市场
                is_new = symbol not in self._markets
                
                # 构建标准化的市场信息
                market_info = {
                    "symbol": symbol,
                    "id": product.get("id", ""),
                    "name": product.get("name", ""),
                    "nameZh": product.get("nameZh", ""),
                    "price": float(product.get("price", 0)) if product.get("price") else 0,
                    "diffValue": float(product.get("diffValue", 0)) if product.get("diffValue") else 0,
                    "upDownsScope": float(product.get("upDownsScope", 0)) if product.get("upDownsScope") else 0,
                    "leverTypes": product.get("leverTypes", ""),
                    "holdMarginRate": float(product.get("holdMarginRate", 0)) if product.get("holdMarginRate") else 0,
                    "pricePrecision": int(product.get("pricePrecision", 2)),
                    "volPrecision": int(product.get("volPrecision", 9)),
                    "unitQuantity": float(product.get("unitQuantity", 1)) if product.get("unitQuantity") else 1,
                    "type": int(product.get("type", 1)),
                    "is_favorite": bool(product.get("is_favorite", 0)),
                    "favoriteId": int(product.get("favoriteId", 0)),
                    "quoteSymbol": product.get("quoteSymbol", ""),
                    "baseSymbol": product.get("baseSymbol", ""),
                    "orderBy": int(product.get("orderBy", 0)),
                    "raw_data": product,  # 保留原始数据
                }
                
                # 保存到 _markets[symbol] 中
                self._markets[symbol] = market_info
                
                # 如果有价格信息，也更新ticker数据
                if market_info["price"] > 0:
                    ticker_data = {
                        "symbol": symbol,
                        "last": market_info["price"],
                        "price": market_info["price"],
                        "change": market_info["diffValue"],
                        "change_percent": market_info["upDownsScope"],
                        "timestamp": parsed.get("timestamp", 0),
                    }
                    self._tickers[symbol] = ticker_data
                
                # parsed_count += 1
                # if is_new:
                #     new_symbols.append(symbol)
                # else:
                #     updated_symbols.append(symbol)
            
            # 获取分页信息
            # page_info = data.get("pageIndex", 0)
            # page_size = data.get("pageSize", 0)
            # total_count = data.get("count", 0)
            
            if self.verbose:
                log.info(
                    f"✅ 解析产品列表成功: "
                    f"本次接收 {len(product_list)} 个产品，"
                    f"成功解析 {parsed_count} 个，"
                    f"新增 {len(new_symbols)} 个，"
                    f"更新 {len(updated_symbols)} 个，"
                    f"当前总计 {len(self._markets)} 个市场  "
                )
                # if new_symbols:
                #     log.debug(f"   新增市场: {', '.join(new_symbols[:10])}{'...' if len(new_symbols) > 10 else ''}")
            
        except Exception as e:
            log.error(f"❌ 解析产品列表数据失败: {e}")
            log.exception(e)
    
    async def parse_positions(self, parsed: Dict):
        """
        解析持仓列表API数据并保存到 _positions[symbol] 中
        
        API: https://api9528mystks.mystonks.org/api/v1/co/pos/list
        
        数据格式示例：
        {
            "code": 0,
            "data": {
                "balance": "2204.42",
                "AcctBalance": "2204.42",
                "assetValuation": "0",
                "pnlTotal": "76.58",
                "posList": [
                    {
                        "id": 65331,
                        "symbol": "AMD",
                        "posNo": "PSTU-20251030140259127201",
                        "longFlag": 1,  # 1=做多, 2=做空
                        "marginMode": 1,
                        "leverage": "10",
                        "posMargin": "499.05",
                        "useMargin": "499.05",
                        "feeCost": "2.25",
                        "nowAmtTotal": "4999.99999981",
                        "nowVolTotal": "19.08608788",
                        "pnl": "-123.31",
                        "realPnl": "0",
                        "liqPrice": "83.61",
                        "avgPrice": "261.97092",
                        "markPrice": "255.51",
                        "rateReturn": "-24.71",
                        ...
                    },
                    ...
                ]
            },
            "msg": "success"
        }
        """
        try:
            # 提取数据部分
            data = parsed.get("data", {})
            if not data or not isinstance(data, dict):
                log.warning(f"持仓列表数据中没有data字段: {parsed}")
                return
            
            # 检查是否是标准格式 (code: 0, data: {...})
            if "code" in parsed and parsed.get("code") != 0:
                log.warning(f"API返回错误: {parsed.get('msg', 'unknown error')}")
                return
            
            # 提取账户信息并保存
            account_info = {
                "balance": float(data.get("balance", 0)) if data.get("balance") else 0,
                "acctBalance": float(data.get("AcctBalance", 0)) if data.get("AcctBalance") else 0,
                "assetValuation": float(data.get("assetValuation", 0)) if data.get("assetValuation") else 0,
                "pnlTotal": float(data.get("pnlTotal", 0)) if data.get("pnlTotal") else 0,
            }
            self._account = account_info
            
            # 提取持仓列表
            pos_list = data.get("posList", [])
            if not pos_list:
                if self.verbose:
                    log.info(f"✅ 解析持仓列表成功: 当前无持仓，账户余额: {account_info['balance']}")
                return
            
            # 清空旧持仓数据（因为这是完整的持仓列表）
            self._positions.clear()
            
            # 解析每个持仓
            for pos in pos_list:
                symbol = pos.get("symbol", "").strip()
                if not symbol:
                    continue
                
                # 构建标准化的持仓信息
                position_info = {
                    "symbol": symbol,
                    "id": int(pos.get("id", 0)),
                    "posNo": pos.get("posNo", ""),
                    "side": "long" if pos.get("longFlag") == 1 else "short",  # 1=做多, 2=做空
                    "longFlag": int(pos.get("longFlag", 1)),
                    "marginMode": int(pos.get("marginMode", 1)),
                    "leverage": float(pos.get("leverage", 1)) if pos.get("leverage") else 1,
                    "posMargin": float(pos.get("posMargin", 0)) if pos.get("posMargin") else 0,
                    "useMargin": float(pos.get("useMargin", 0)) if pos.get("useMargin") else 0,
                    "feeCost": float(pos.get("feeCost", 0)) if pos.get("feeCost") else 0,
                    "amount": float(pos.get("nowAmtTotal", 0)) if pos.get("nowAmtTotal") else 0,
                    "volume": float(pos.get("nowVolTotal", 0)) if pos.get("nowVolTotal") else 0,
                    "sellVolTotal": float(pos.get("sellVolTotal", 0)) if pos.get("sellVolTotal") else 0,
                    "sellAmtTotal": float(pos.get("sellAmtTotal", 0)) if pos.get("sellAmtTotal") else 0,
                    "buyVolTotal": float(pos.get("buyVolTotal", 0)) if pos.get("buyVolTotal") else 0,
                    "freezeVol": float(pos.get("freezeVol", 0)) if pos.get("freezeVol") else 0,
                    "pnl": float(pos.get("pnl", 0)) if pos.get("pnl") else 0,
                    "realPnl": float(pos.get("realPnl", 0)) if pos.get("realPnl") else 0,
                    "liqPrice": float(pos.get("liqPrice", 0)) if pos.get("liqPrice") else 0,
                    "avgPrice": float(pos.get("avgPrice", 0)) if pos.get("avgPrice") else 0,
                    "markPrice": float(pos.get("markPrice", 0)) if pos.get("markPrice") else 0,
                    "closePrice": float(pos.get("closePrice", 0)) if pos.get("closePrice") else 0,
                    "closeTime": int(pos.get("closeTime", 0)),
                    "updateTime": int(pos.get("ctime", 0)),
                    "rateReturn": float(pos.get("rateReturn", 0)) if pos.get("rateReturn") else 0,
                    "marginRate": float(pos.get("marginRate", 0)) if pos.get("marginRate") else 0,
                    "holdMarginRatio": float(pos.get("holdMarginRatio", 0)) if pos.get("holdMarginRatio") else 0,
                    "initMargin": float(pos.get("initMargin", 0)) if pos.get("initMargin") else 0,
                    "posStatus": int(pos.get("posStatus", 1)),
                    "pricePrecision": int(pos.get("pricePrecision", 2)),
                    "coType": int(pos.get("coType", 1)),
                    "profitPrice": float(pos.get("profitPrice", 0)) if pos.get("profitPrice") else 0,
                    "lossPrice": float(pos.get("lossPrice", 0)) if pos.get("lossPrice") else 0,
                    "raw_data": pos,  # 保留原始数据
                }
                
                # 计算可读时间
                if position_info["updateTime"]:
                    try:
                        from datetime import datetime as dt_class
                        dt = dt_class.fromtimestamp(position_info["updateTime"] / 1000)
                        position_info["openTime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        position_info["openTime"] = None
                else:
                    position_info["openTime"] = None
                
                # 保存到 _positions[symbol] 中
                # 注意：一个symbol可能有多个持仓（不同方向），这里用列表存储
                if symbol not in self._positions:
                    self._positions[symbol] = []
                
                # 检查是否已存在相同posNo的持仓，如果存在则更新，否则添加
                existing_index = None
                for i, existing_pos in enumerate(self._positions[symbol]):
                    if existing_pos.get("posNo") == position_info["posNo"]:
                        existing_index = i
                        break
                
                if existing_index is not None:
                    self._positions[symbol][existing_index] = position_info
                else:
                    self._positions[symbol].append(position_info)
                
                # 触发订阅回调
                await self._emit('positions', symbol, position_info)
            
            if self.verbose:
                total_positions = sum(len(positions) for positions in self._positions.values())
                log.info(
                    f"✅ 解析持仓列表成功: "
                    f"共 {len(pos_list)} 个持仓，"
                    f"涉及 {len(self._positions)} 个交易对，"
                    f"总持仓数 {total_positions} | "
                    f"账户余额: {account_info['balance']}, "
                    f"总盈亏: {account_info['pnlTotal']}"
                )
            
        except Exception as e:
            log.error(f"❌ 解析持仓列表数据失败: {e}")
            log.exception(e)
    
    async def parse_orders(self, parsed: Dict):
        """
        解析订单列表API数据并保存到 _orders[symbol] 中
        
        API: https://api9528mystks.mystonks.org/api/v1/co/stock/order/limit
        或: https://api9528mystks.mystonks.org/api/v1/co/stock/order/trade
        
        数据格式示例：
        {
            "code": 0,
            "data": [
                {
                    "id": "订单ID",
                    "symbol": "NVDA",
                    "side": "buy" 或 "sell",
                    "type": "limit" 或 "market",
                    "price": "196.50",
                    "vol": "0.1",
                    "amount": "10",
                    "filled": "0",
                    "status": "pending",
                    "timestamp": 1762396114000,
                    ...
                },
                ...
            ],
            "msg": "success",
            "request_id": "...",
            "success": true
        }
        
        注意：如果 data 为空数组，表示当前没有订单
        """
        try:
            # 提取数据部分
            data = parsed.get("data", [])
            
            # 检查是否是标准格式 (code: 0, data: [...])
            if "code" in parsed:
                code = parsed.get("code", 0)
                if code != 0:
                    msg = parsed.get("msg", "unknown error")
                    if self.verbose:
                        log.warning(f"订单API返回错误: code={code}, msg={msg}")
                    return
            
            # 如果 data 不是列表，可能是 null 或其他格式
            if not isinstance(data, list):
                if data is None:
                    if self.verbose:
                        log.info(f"✅ 解析订单列表: 当前无订单")
                    return
                else:
                    log.warning(f"订单列表数据格式异常: data不是列表类型: {type(data)}")
                    return
            
            # 如果订单列表为空
            if not data:
                if self.verbose:
                    log.info(f"✅ 解析订单列表: 当前无订单")
                return
            
            # 解析每个订单
            parsed_count = 0
            new_orders = []
            updated_orders = []
            
            for order in data:
                if not isinstance(order, dict):
                    continue
                
                symbol = order.get("symbol", "").strip()
                order_id = order.get("id") or order.get("orderId") or order.get("order_id", "")
                
                if not symbol or not order_id:
                    continue
                
                # 构建标准化的订单信息
                order_info = {
                    "id": str(order_id),
                    "symbol": symbol,
                    "side": order.get("side", "").lower(),  # buy/sell
                    "type": order.get("type", "").lower(),  # limit/market
                    "price": float(order.get("price", 0)) if order.get("price") else 0,
                    "volume": float(order.get("vol", 0)) if order.get("vol") else 0,
                    "amount": float(order.get("amount", 0)) if order.get("amount") else 0,
                    "filled": float(order.get("filled", 0)) if order.get("filled") else 0,
                    "remaining": float(order.get("remaining", 0)) if order.get("remaining") else 0,
                    "status": str(order.get("status", "")).lower(),  # pending/filled/cancelled
                    "timestamp": int(order.get("ctime", 0) or order.get("timestamp", 0) or 0),
                    "datetime": None,
                    "fee": float(order.get("fee", 0)) if order.get("fee") else 0,
                    "feeCurrency": order.get("feeCurrency", ""),
                    "raw_data": order,  # 保留原始数据
                }
                
                # 计算剩余数量（如果没有提供）
                if order_info["remaining"] == 0 and order_info["amount"] > 0:
                    order_info["remaining"] = order_info["amount"] - order_info["filled"]
                
                # 转换时间戳为可读格式
                if order_info["timestamp"]:
                    try:
                        from datetime import datetime as dt_class
                        dt = dt_class.fromtimestamp(order_info["timestamp"] / 1000)
                        order_info["datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        order_info["datetime"] = None
                
                # 保存到 _orders[symbol] 中
                if symbol not in self._orders:
                    self._orders[symbol] = []
                
                # 检查是否已存在相同ID的订单，如果存在则更新，否则添加
                existing_index = None
                for i, existing_order in enumerate(self._orders[symbol]):
                    if existing_order.get("id") == order_info["id"]:
                        existing_index = i
                        break
                
                if existing_index is not None:
                    self._orders[symbol][existing_index] = order_info
                    updated_orders.append(order_info["id"])
                else:
                    self._orders[symbol].append(order_info)
                    new_orders.append(order_info["id"])
                
                parsed_count += 1
                
                # 触发订阅回调
                await self._emit('orders', symbol, order_info)
            
            if self.verbose:
                total_orders = sum(len(orders) for orders in self._orders.values())
                log.info(
                    f"✅ 解析订单列表成功: "
                    f"本次接收 {len(data)} 个订单，"
                    f"成功解析 {parsed_count} 个，"
                    f"新增 {len(new_orders)} 个，"
                    f"更新 {len(updated_orders)} 个，"
                    f"当前总计 {total_orders} 个订单，"
                    f"涉及 {len(self._orders)} 个交易对"
                )
            
        except Exception as e:
            log.error(f"❌ 解析订单列表数据失败: {e}")
            log.exception(e)

    
    def _frame_to_string(self, frame) -> str:
        """将 WebSocket 帧转换为字符串"""
        if isinstance(frame, (bytes, bytearray)):
            try:
                return bytes(frame).decode("utf-8", errors="replace")
            except Exception:
                return str(frame)
        return str(frame)
    
    async def _handle_request(self, req):
        """处理 HTTP 请求（仅用于打印）"""        
        # 提取认证头
        if "authorization" in req.headers and self._auth_headers is None and "mystonks.org" in req.url:
                auth_headers = req.headers.get("authorization")
                if auth_headers!="":
                    self._auth_headers = req.headers
                    # 从URL中解析host部分（scheme + netloc）
                    from urllib.parse import urlparse
                    parsed = urlparse(req.url)  
                    self._api_base = f"{parsed.scheme}://{parsed.netloc}"
                    self.auth_status=True
                    if not self._running:
                        self._running = True
            
                    log.debug(f"提取认证头: {self._auth_headers},api_base: {self._api_base},{parsed}")
            
        
   
    
    async def _handle_response(self, resp):
        """处理 HTTP 响应（仅用于打印）"""
        timestamp = format_timestamp()
        status = resp.status
        headers = resp.headers
        ct = headers.get("content-type", "")
        
        print(f"\n{'='*80}")
        print(f"[{timestamp}] 🟢 HTTP RESPONSE")
        print(f"  Status: {status}")
        print(f"  URL: {resp.url}")
        print(f"  Content-Type: {ct}")
        
        try:
            body = await resp.text()
            if body:
                if "application/json" in ct or looks_like_json(body):
                    parsed = try_parse_json(body)
                    if parsed:
                        data_type_label, _ = analyze_data_type(parsed, resp.url)
                        print(f"  {data_type_label}")
                        json_str = json.dumps(parsed, ensure_ascii=False, indent=2)
                        if len(json_str) > MAX_PRINT_LEN:
                            print(f"     {short(json_str)}")
                        else:
                            for line in json_str.split('\n'):
                                print(f"     {line}")
                    else:
                        print(f"  📥 Response Body (text): {short(body)}")
                else:
                    print(f"  📥 Response Body (non-JSON): {short(body)}")
            else:
                print(f"  📥 Response Body: <empty or binary>")
        except Exception:
            print(f"  📥 Response Body: <cannot read>")
        
        print(f"{'='*80}")
    
    def _print_ws_frame(self, ws_url: str, payload_s: str, parsed: Optional[Dict], direction: str):
        """打印 WebSocket 帧（仅用于调试）"""
        timestamp = format_timestamp()
        if parsed:
            data_type_label, _ = analyze_data_type(parsed)
            print(f"\n[{timestamp}] {'🟡' if direction == 'SEND' else '🟢'} WS {direction} {data_type_label}")
            json_str = json.dumps(parsed, ensure_ascii=False, indent=2)
            if len(json_str) > MAX_PRINT_LEN:
                print(f"  {short(json_str)}")
            else:
                for line in json_str.split('\n'):
                    print(f"  {line}")
        else:
            print(f"\n[{timestamp}] {'🟡' if direction == 'SEND' else '🟢'} WS {direction} (raw): {short(payload_s)}")
    
    async def _process_data(self, url: str, data: Dict, source: str):
        """处理接收到的数据，更新缓存并触发回调"""
        # 根据数据类型分类处理
        data_type_label, data_type = analyze_data_type(data, url)
       # log.debug(f"处理接收到的数据: {url}, {data}")
        # TODO: 根据实际数据结构解析 symbol、ticker、orders、positions 等
        # 这里先实现框架，具体解析逻辑后续完善
        
        # 示例：如果是 ticker 数据
        if data_type == 'ticker':
            # symbol = self._extract_symbol(data, url)
            # if symbol:
            #     self._tickers[symbol] = data
            #     await self._emit('ticker', symbol, data)
            pass
        
        # 示例：如果是 orders 数据
        elif data_type == 'order':
            # symbol = self._extract_symbol(data, url)
            # if symbol:
            #     if symbol not in self._orders:
            #         self._orders[symbol] = []
            #     self._orders[symbol].append(data)
            #     await self._emit('orders', symbol, data)
            pass
        
        # 示例：如果是 positions 数据
        elif data_type == 'position':
            # symbol = self._extract_symbol(data, url)
            # if symbol:
            #     self._positions[symbol] = data
            #     await self._emit('positions', symbol, data)
            pass
    
    # ========== 公共 API 方法（类似 ccxt） ==========
    
    async def load_markets(self, reload: bool = False):
        """加载市场信息"""
        # 如果已有市场数据且不需要重新加载，直接返回
        if not reload and self._markets:
            return list(self._markets.keys())
        
        # 市场信息会在解析HTTP响应时自动加载
        # 这里返回已加载的市场列表
        return list(self._markets.keys())
    
    async def fetch_orders(self, symbol: str = None, limit: int = None) -> List[OrderInfo]:
        """获取订单列表（ccxt: fetchOrders）- 直接远端请求，返回 OrderInfo 对象列表"""
        param={"PageSize": 10000, "PageIndex": 1}
        res = await self._request_api("POST", "/api/v1/co/stock/order/limit", json_body=param)
        if not res.get("ok"):
         #   log.error(res)  
            return None
        data = res.get("data") or []
        if not isinstance(data, list):   
            return None
        # 标准化为 OrderInfo 对象
        result: List[OrderInfo] = []
        for o in data:
            if not isinstance(o, dict):
                continue
            sym = (o.get("symbol") or "").strip()
            order_id = str(o.get("id") or o.get("orderId") or o.get("order_id") or "")
            price = float(o.get("price", 0) or 0)
            volume = float(o.get("vol", 0) or 0) or float(o.get("amount", 0) or 0)  # 优先使用 vol，否则使用 amount
            side = (o.get("longFlag") or 1)
            open_type = (o.get("openFlag") or 1)
            amount=float(o.get("amtTotal", 0) or 0)
            if side == 1 and open_type == 1:
                side = "buy"
            elif side == 2 and open_type == 1:
                side = "sell"
            elif side == 1 and open_type == 2:
                side = "sell"
            elif side == 2 and open_type == 2:
                side = "buy"
            status = str(o.get("status") or "0").lower()
            # 获取创建时间戳（ctime 或 timestamp）
            timestamp = int(o.get("ctime", 0) or o.get("timestamp", 0) or 0)
            
            # 映射状态：将数字状态转换为字符串状态
            if status in ["1", "pending", "open"]:
                status = "pending"
            elif status in ["2", "filled", "executed", "closed"]:
                status = "filled"
            elif status in ["4", "cancelled", "canceled"]:
                status = "cancelled"
            self._last_order_time = max(self._last_order_time,timestamp)
            # 创建 OrderInfo 对象
            order_info = OrderInfo(
                id=order_id,
                price=price,
                volume=volume,
                side=side,
                status=status,
                timestamp=timestamp,
                amount=amount,
            )
           # log.debug(f"获取待成交订单: {order_info}")
            if not symbol or sym == symbol:
                result.append(order_info)
        # if len(result)==0:
        #     log.warning(f" {symbol}-获取待成交订单数量为0:")
        return result[: limit or None]
    
    async def fetch_his_order(self, symbol: str = None, limit: int = None) -> List[OrderInfo]:
        """获取历史订单列表（已成交或已取消的订单）- 直接远端请求，返回 OrderInfo 对象列表"""
        # 注意：如果API没有专门的历史订单接口，可以从 fetch_orders 中筛选已成交/已取消的订单
        # 或者调用专门的交易历史接口
        params={"PageSize":1000,"PageIndex":1}
        res = await self._request_api("POST", "/api/v1/co/stock/order/hisPage",json_body=params)
        try:
            if not res.get("ok"):
                return []
            data = res.get("data")
            
            # 标准化为 OrderInfo 对象
            result: List[OrderInfo] = []
            for o in data.get("list",[]):
                if not isinstance(o, dict):
                    continue
                sym = (o.get("symbol") or "").strip()
                order_id = str(o.get("id") or o.get("orderId") or o.get("order_id") or "")
                price = float(o.get("price", 0) or 0)
                volume = float(o.get("vol", 0) or 0) or float(o.get("amount", 0) or 0)
                open_type = (o.get("openFlag") or 1)
                long_flag = (o.get("longFlag") or 1)
                order_type = (o.get("orderType") or 1)
                status = str(o.get("status") or "0").lower()
                avgPrice=float(o.get("avgPrice", 0) or 0)
                # 获取创建时间戳（ctime 或 timestamp）
                timestamp = int(o.get("ctime", 0) or o.get("timestamp", 0) or 0)
                # 获取订单盈亏（realPnl）和手续费（realFee）
                pnl = float(o.get("realPnl", 0) or o.get("pnl", 0) or 0)
                fee = float(o.get("realFee", 0) or o.get("fee", 0) or 0)
                amount=float(o.get("amtTotal", 0) or 0)
                # 提取持仓ID（posId）
                pos_id = None
                if o.get("posId"):
                    try:
                        pos_id = int(o.get("posId"))
                    except (TypeError, ValueError):
                        pos_id = None
                if open_type == 1 and long_flag == 1:
                    side = "buy"
                elif open_type == 1 and long_flag == 2:
                    side = "sell"
                elif open_type == 2 and long_flag == 1:
                    side = "sell"
                elif open_type == 2 and long_flag == 2:
                    side = "buy"
                    
                # 映射状态：将数字状态转换为字符串状态
                if status in ["0", "pending", "open"]:
                    status = "pending"
                elif status in ["2", "filled", "executed", "closed"]:
                    status = "filled"
                elif status in ["4", "cancelled", "canceled"]:
                    status = "cancelled"
                self._last_his_order_time = max(self._last_his_order_time,timestamp)
                # 创建 OrderInfo 对象
                order_info = OrderInfo(
                    id=order_id,
                    price=price,
                    volume=volume,
                    side=side,
                    status=status,
                    timestamp=timestamp,
                    avgPrice=avgPrice,
                    amount=amount,
                    pnl=pnl,
                    fee=fee,
                    open_type=open_type,
                    posId=pos_id,
                    raw=o,  # 保存原始数据以便后续提取信息
                )
                
                if not symbol or sym == symbol:
                    result.append(order_info)
            
            return result[: limit or None]
        except Exception as e:
            log.error(f"获取历史订单失败: {e}")
            return None
    
    async def fetch_positions(self, symbol: str = None) -> List[Position]:
        """获取持仓列表（ccxt: fetchPositions）- 直接远端请求，返回 Position 对象列表
        
        参数：
            symbol: 交易币种，如果传入则只返回该币种的持仓；如果不传则返回所有持仓
        
        返回：
            List[Position]: 持仓列表
            - 如果传入 symbol 且没有持仓，返回一个 size=0 的 Position 对象
            - 如果不传 symbol 且没有持仓，返回空列表
        """
        res = await self._request_api("POST", "/api/v1/co/pos/list")
        if not res.get("ok"):
            # # 如果传入 symbol，返回一个 size=0 的 Position 对象
            # if symbol:
            #     return [Position(id=None, size=0.0, amount=0.0, side="")]
            return None
        
        raw = res.get("raw") or {}
        data = raw.get("data") or {}
        pos_list = data.get("posList") or []
        if not isinstance(pos_list, list):
            # 如果传入 symbol，返回一个 size=0 的 Position 对象
            if symbol:
                return [Position(id="no pos 1137", size=0.0, amount=0.0, side="")]
            return []
        
        result: List[Position] = []
        for p in pos_list:
            if not isinstance(p, dict):
                continue
            sym = (p.get("symbol") or "").strip()
            
            # 创建 Position 对象
            position = Position(
                id=int(p.get("id", 0)) if p.get("id") else None,
                size=float(p.get("nowVolTotal", 0) or 0),
                amount=float(p.get("nowAmtTotal", 0) or 0),
                entryPrice=float(p.get("avgPrice", 0) or 0),
                unrealizedPnl=float(p.get("pnl", 0) or 0),
                liquidationPrice=float(p.get("liqPrice", 0) or 0),
                timestamp=int(p.get("ctime", 0) or 0),
                side="long" if p.get("longFlag") == 1 else "short",
                raw=p,
            )
            
            if not symbol or sym == symbol:
                result.append(position)
        
        # 如果传入 symbol 但没有找到该 symbol 的持仓，返回一个 size=0 的 Position 对象
        if symbol and len(result) == 0:
            return [Position(id=None, size=0.0, amount=0.0, side="")]
    
        return result
    
    async def fetch_account(self) -> Dict:
        """获取账户信息（余额、总盈亏等）- 直接远端请求（复用 /pos/list 的账户段）"""
        res = await self._request_api("POST", "/api/v1/co/pos/list")
        if not res.get("ok"):
            return {}
        raw = res.get("raw") or {}
        data = raw.get("data") or {}
        return {
            "balance": float(data.get("balance", 0) or 0),
            "acctBalance": float(data.get("AcctBalance", 0) or 0),
            "assetValuation": float(data.get("assetValuation", 0) or 0),
            "pnlTotal": float(data.get("pnlTotal", 0) or 0),
            "raw": data,
        }
    
    async def fetch_kline(self, symbol: str, timeframe: str, stype: int = 1) -> List[Dict]:
        """
                    {
            "symbol": "SOLUSDT",
            "kType": "1h",
            "sType": 3,
            "pageIndex": 1,
            "pageSize": 600
            }
        """
        try:
            params={"symbol": symbol, "kType": timeframe, "sType": stype, "pageIndex": 1, "pageSize": 100}
            url=f"/api/v1/stockhome/newKline"
            res = await self._request_api("POST", url,json_body=params)
            if res.get("ok"):
                data = res.get("data")
                bars=[]
                for item in data:
                    bars.append([symbol,float(item["o"]),float(item["h"]),float(item["l"]),float(item["c"]),float(item["v"]),int(item["t"])])
                return bars
        except Exception as e:
            log.error(f"获取K线数据失败: {e}")
            return []

    # ----------- ccxt 风格补充方法 -----------
    # 其余 ccxt 风格方法按需再添加
    
    # 已按要求保留的仅有：fetchOrders / fetchPositions / fetchAccount

    # ----------- HTTP 请求封装 -----------
    async def _build_auth_headers(self, extra: Dict[str, str] | None = None) -> Dict[str, str]:
        if self._auth_headers is None:
            log.error("未登录，请登录后请求！")
            return False
        return self._auth_headers

    async def _request_api(
        self,
        method: str,
        path: str,
        json_body: Dict[str, Any] | None = None,
        params: Dict[str, Any] | None = None,
        headers_extra: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """
        发送 API 请求，自动处理请求频率和 1006 错误代码
        
        参数:
            method: HTTP 方法
            path: API 路径
            json_body: JSON 请求体
            params: URL 参数
            headers_extra: 额外的请求头
        """
        assert self._playwright is not None, "Playwright 未初始化，请先调用 connect()"
        
        # 计算与上次请求的时间差
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        
        # 如果时间差不够，等待
        if time_since_last < self._min_request_interval:
            wait_time = self._min_request_interval - time_since_last
            await asyncio.sleep(wait_time)
        
        # 更新请求时间
        self._last_request_time = time.time()
        
        headers = await self._build_auth_headers(headers_extra)
        if headers:
            # 重建 context 以确保头部与 Cookie 最新
            if self._api_ctx is not None:
                try:
                    await self._api_ctx.dispose()
                except Exception:
                    pass
            self._api_ctx = await self._playwright.request.new_context(extra_http_headers=headers)
        
            url = path if path.startswith("http") else f"{self._api_base}{path}"
           # log.info(f"请求URL: {url}, 请求参数: {params}, 请求体: {json_body}")
            try:
                if method.upper() == "GET":
                    resp = await self._api_ctx.get(url, params=params)
                else:
                    # Playwright 接受 data 或 json；此处统一发 data(str)
                    payload = json.dumps(json_body or {})
                    resp = await self._api_ctx.post(url, data=payload, params=params)
                # log.debug(f"请求响应:{resp.status}, {await resp.text()}")
                status = resp.status
                
                # 检查是否是 CORS 错误（状态码为 0 通常表示请求被阻止）
                if status == 0:
                    error_msg = "CORS 错误：请求被浏览器阻止（状态码 0）"
                    log.error(f"{error_msg} - URL: {url}")
                    return {"ok": False, "status": 0, "code": "CORS_ERROR", "msg": error_msg, "data": None, "raw": ""}
                
                try:
                    raw = await resp.json()
                except Exception:
                    raw = {"status": status, "text": await resp.text()}

                code = raw.get("code") 
                ok = (200 <= status < 300) and (code in (None, 0))
                msg = raw.get("msg") if isinstance(raw, dict) else None
                data = raw.get("data") if isinstance(raw, dict) else None
                
                # 检查是否是 1006 错误代码（请求频繁）
                if code == 1006 :
                    # 增加请求间隔阈值（翻倍）
                    self._min_request_interval *= 2
                    log.warning(f"请求频繁 (1006)，增加请求间隔至 {self._min_request_interval:.2f} 秒")
                    # 等待后重试一次
                    await asyncio.sleep(self._min_request_interval)
                    self._last_request_time = time.time()
                    
                    # 重试请求
                    if method.upper() == "GET":
                        resp = await self._api_ctx.get(url, params=params)
                    else:
                        payload = json.dumps(json_body or {})
                        resp = await self._api_ctx.post(url, data=payload, params=params)
                    
                    status = resp.status
                    try:
                        raw = await resp.json()
                    except Exception:
                        raw = {"status": status, "text": await resp.text()}
                    
                    code = raw.get("code") if isinstance(raw, dict) else None
                    ok = (200 <= status < 300) and (code in (None, 0))
                    msg = raw.get("msg") if isinstance(raw, dict) else None
                    data = raw.get("data") if isinstance(raw, dict) else None
                if  code==0:
                    return {"ok": True, "status": status, "code": code, "msg": msg, "data": data,"raw":raw}
                else:
                    log.error(f"请求失败: {resp.status}")
                    return {"ok": False, "status": status, "code": code, "msg": msg, "data": data,"raw":raw}
            except Exception as e:
                error_msg = str(e)
                error_lower = error_msg.lower()
                
                # 识别 CORS 错误
                is_cors_error = False
                cors_keywords = [
                    "cors",
                    "cross-origin",
                    "access-control",
                    "blocked by cors policy",
                    "no 'access-control-allow-origin'",
                    "net::err_failed",
                    "net::err_blocked_by_client",
                ]
                
                for keyword in cors_keywords:
                    if keyword in error_lower:
                        is_cors_error = True
                        break
                
                if is_cors_error:
                    log.error(f"CORS 错误检测到: {error_msg} - URL: {url}")
                    traceback.print_exc()
                    return {"ok": False, "status": None, "code": "CORS_ERROR", "msg": f"CORS 错误: {error_msg}", "data": None, "raw": ""}
                else:
                    traceback.print_exc()
                    return {"ok": False, "status": None, "code": None, "msg": error_msg, "data": None, "raw": ""}
        
    # ----------- 直连 API 的便捷方法 -----------
    # 直连方法不再提供缓存刷新版本
    async def get_config(self,symbol:str,co_type:int) -> None:
        url="api/v1/co/stock/user/config"
        if symbol in self.configs:
            return self.configs[symbol]
        else:
            payload={"symbol":symbol,"coType":co_type}
            res = await self._request_api("POST",url, json_body=payload)
            if res.get("ok"):
                self.configs[symbol] = res.get("data")
                return res.get("data")
            else:
                return None
    
    async def set_leverage(
        self,
        symbol: str,
        leverage: int,
        co_type: int = 1,
        margin_mode: int = 1,
    ) -> Dict[str, Any]:
        """
        设置杠杆倍数
        
        对应接口:
            POST /api/v1/co/stock/user/updateLeverage
        
        请求示例:
            {
              "coType": 1,
              "symbol": "QQQ",
              "marginMode": 1,
              "leverage": 5
            }
        
        统一返回:
            {"ok","code","msg","data","raw"}
        """
        url = "/api/v1/co/stock/user/updateLeverage"
        payload: Dict[str, Any] = {
            "coType": int(co_type),
            "symbol": symbol,
            "marginMode": int(margin_mode),
            "leverage": int(leverage),
        }
        try:
            res = await self._request_api("POST", url, json_body=payload)
            return {
                "ok": res.get("ok"),
                "code": res.get("code"),
                "msg": res.get("msg"),
                "data": res.get("data"),
                "raw": res.get("raw"),
            }
        except Exception as e:
            log.error(f"设置杠杆失败: {e}")
            return {
                "ok": False,
                "code": None,
                "msg": str(e),
                "data": None,
                "raw": None,
            }
             
    @retry(max_retries=3, delay=1)
    async def create_order(
        self,
        symbol: str,
        side: str = "buy",           # buy | sell
        order_type: str = "market",  # market | limit
        vol: float = 0,
        price: float = None,
        leverage: int = 10,
        margin_mode: int = 1,         # 1: cross? 2: isolated?（按实际定义调整）
        open_type: int = 1,           # 1: 开仓 2: 平仓（按实际定义调整）
        co_type: int = 3,
        posId: int = None,           # 持仓ID（平仓时必填）
        extra_params: Dict[str, Any] = None,
        async_mode: bool = True,
    ) -> OrderInfo:
        """创建订单（调用 trade 接口）- 返回 OrderInfo 对象

        请求示例（来源于抓包）:
        POST /api/v1/co/stock/order/trade
        {"symbol":"AVGO","orderType":2,"openType":1,"side":1,"marginMode":1,"coType":1,"amt":"100","leverage":"10"}

        返回：
            OrderInfo: 订单信息对象
            - 成功时：包含订单ID、价格、数量等信息，status="pending"
            - 失败时：id=None，msg包含错误信息，status="failed"
        """
        try:
            # 映射 side / order_type 到服务端枚举
            side_map = {"buy": 1, "sell": 2}
            type_map = {"limit": 1,"market": 2}
            side_v = side_map.get(side.lower())
            type_v = type_map.get(order_type.lower())
            if side_v is None or type_v is None:
                return OrderInfo(
                    id=None,
                    price=price or 0.0,
                    volume=vol,
                    side=side,
                    status="failed",
                    timestamp=0,
                    msg="invalid side or order_type"
                )

            payload: Dict[str, Any] = {
                "symbol": symbol,
                "orderType": type_v,
                "openType": int(open_type),
                "side": side_v,
                "marginMode": int(margin_mode),
                "coType": int(co_type),
                "vol": str(vol),
                "leverage": str(leverage),
            }
            if price is not None:
                payload["price"] = str(price)
            # 平仓时需要传递 posId
            if open_type == 2 and posId is not None:
                payload["posId"] = int(posId)
            if extra_params:
                payload.update(extra_params)
         
            current_timestamp = int(time.time() * 1000)  # 毫秒时间戳
            # 通过统一封装发起请求
            log.info(f"创建订单请求: {payload}")
            res = await self._request_api("POST", "/api/v1/co/stock/order/trade", json_body=payload)
            
            # 检查响应是否成功
            ok = res.get("ok", False)
            code = res.get("code")
            msg = res.get("msg")
            data = res.get("data")
            raw = res.get("raw")
            log.info(f"创建订单响应: {res}")  
            if ok:
                if open_type==2:
                    side="sell" if side == "buy" else "buy"
                order=await self.get_new_order(symbol,side,current_timestamp)
                return order
            else:
                # 创建失败的 OrderInfo 对象
                # 打印完整的返回数据以便分析问题
                log.error(f"创建订单失败，完整响应数据: {json.dumps(res, ensure_ascii=False, indent=2)},{payload}")
                return OrderInfo(
                    id=None,
                    price=price or 0.0,
                    volume=vol,
                    side=side,
                    status="failed",
                    timestamp=0,
                    msg=msg or "订单创建失败",
                    code=code
                )
    
                
        except Exception as e:
            # 异常保护：返回失败的 OrderInfo 对象
            log.error(f"创建订单异常: {e}")
            log.exception(e)
            
    
    async def get_new_order(self,symbol:str,side:str,lasttime:int) -> OrderInfo:
        orders = await self.fetch_orders(symbol)
        for order in orders:
            if order.timestamp>lasttime and order.side == side:
                return order
  
        his_orders = await self.fetch_his_order(symbol)
        for order in his_orders:
            if order.timestamp>lasttime and order.side == side:
                return order
       
    async def set_pl(self,pos_id:str,sl:float,sp:float) -> Dict:
        url="/api/v1/co/pos/setProfitLoss"
        payload={"posId":pos_id,"stopLossPrice":sl,"stopProfitPrice":sp}
        try:
            res = await self._request_api("POST", url, json_body=payload)
            if res.get("code")==0:
                return {"ok": True, "code": 0, "msg": "设置止盈止损成功", "data": None, "raw": res}
            else:
                return {"ok": False, "code": res.get("code"), "msg": res.get("msg"), "data": None, "raw": res}
        except Exception as e:
            log.error(f"设置止盈止损失败: {e}")
            return {"ok": False, "code": None, "msg": str(e), "data": None, "raw": None}

    def watch_ticker(self, symbol: str, callback: Callable):
        """订阅指定交易对的价格更新"""
        if symbol not in self._subscribers['ticker']:
            self._subscribers['ticker'][symbol] = []
        self._subscribers['ticker'][symbol].append(callback)
    
    def watch_orders(self, symbol: str, callback: Callable):
        """订阅指定交易对的订单更新"""
        if symbol not in self._subscribers['orders']:
            self._subscribers['orders'][symbol] = []
        self._subscribers['orders'][symbol].append(callback)
    
    def watch_positions(self, symbol: str, callback: Callable):
        """订阅指定交易对的持仓更新"""
        if symbol not in self._subscribers['positions']:
            self._subscribers['positions'][symbol] = []
        self._subscribers['positions'][symbol].append(callback)
    
    def watch_ohlcv(self, symbol: str, timeframe: str, callback: Callable):
        """订阅指定交易对的 K线数据更新"""
        if symbol not in self._subscribers['ohlcv']:
            self._subscribers['ohlcv'][symbol] = {}
        if timeframe not in self._subscribers['ohlcv'][symbol]:
            self._subscribers['ohlcv'][symbol][timeframe] = []
        self._subscribers['ohlcv'][symbol][timeframe].append(callback)
    

    
    async def cancel_order(self, order_id: str) -> Dict:
        """取消订单：POST /api/v1/co/stock/order/cancel

        请求体示例：{"orderId":306067}
        返回统一格式：{"ok","code","msg","data","raw"}
        """
        if order_id=="no_id":
            return {"ok": True, "code": 0, "msg": "订单不存在", "data": None, "raw": None}
        payload: Dict[str, Any] = {"orderId": int(order_id) if isinstance(order_id, (int, str)) else order_id}
        res = await self._request_api("POST", "/api/v1/co/stock/order/cancel", json_body=payload)
        return {"ok": res.get("ok"), "code": res.get("code"), "msg": res.get("msg"), "data": res.get("data"), "raw": res.get("raw")}
        
    
    async def close_position(self, symbol: str, **params) -> Dict:
        """平仓（辅助方法，供策略在停止时调用）

        当前实现：根据最新持仓信息自动生成反向市价单尝试一次性平仓。
        更细粒度的控制（部分平仓、限价平仓等）可以通过直接调用 create_order 实现。
        """
        try:
            positions = await self.fetch_positions(symbol)
            if not positions:
                return {"ok": True, "msg": "no position", "data": None}

            for pos in positions:
                size = getattr(pos, "size", 0.0) or 0.0
                if size == 0:
                    continue
                side = getattr(pos, "side", "")
                pos_id = getattr(pos, "id", None)
                close_side = "sell" if side == "long" else "buy"
                log.info(f"[close_position] 平仓: symbol={symbol}, side={side}, size={size}, pos_id={pos_id}")

                await self.create_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="market",
                    vol=size,
                    open_type=2,
                    co_type=params.get("co_type", 1),
                    posId=pos_id,
                )

            return {"ok": True, "msg": "close position triggered", "data": None}
        except Exception as e:
            log.error(f"[close_position] 平仓失败: symbol={symbol}, error={e}")
            return {"ok": False, "msg": str(e), "data": None}
    
    async def _emit(self, event: str, symbol: str, data: Any, timeframe: str = None):
        """触发订阅回调"""
        if event == 'ticker':
            callbacks = self._subscribers['ticker'].get(symbol, [])
            for cb in callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(data)
                    else:
                        cb(data)
                except Exception as e:
                    if self.verbose:
                        log.error(f"❌ ticker回调执行错误: {e}")
        elif event == 'orders':
            callbacks = self._subscribers['orders'].get(symbol, [])
            for cb in callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(data)
                    else:
                        cb(data)
                except Exception as e:
                    if self.verbose:
                        log.error(f"❌ orders回调执行错误: {e}")
        elif event == 'positions':
            callbacks = self._subscribers['positions'].get(symbol, [])
            for cb in callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(data)
                    else:
                        cb(data)
                except Exception as e:
                    if self.verbose:
                        log.error(f"❌ positions回调执行错误: {e}")
        elif event == 'ohlcv':
            if timeframe:
                callbacks = self._subscribers['ohlcv'].get(symbol, {}).get(timeframe, [])
                for cb in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(data)
                        else:
                            cb(data)
                    except Exception as e:
                        if self.verbose:
                            log.error(f"❌ ohlcv回调执行错误: {e}")
    
    async def run(self):
        """保持运行，监听数据"""
        if not self._connected:
            await self.connect()
        
        #self._running = True
        print("\n" + "="*80)
        print("🎯 开始监听数据，按 Ctrl+C 停止...")
        print("="*80 + "\n")
        
        try:
            while not self._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n\n⚠️  用户中断，正在关闭...")
        # finally:
        #     await self.disconnect()
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.disconnect()


# ========== 使用示例 ==========
async def main():
    """使用 MsxExchange 类的示例"""
    # 使用异步上下文管理器
    async with MsxExchange(
        cdp_url='http://localhost:9222',
        target_url='https://msx.com/contract-trading',
        verbose=True
    ) as exchange:
        # 订阅价格更新
        # 保持运行
        await exchange.run()
        orders = await exchange.fetch_orders()
        for order in orders:
            status = await exchange.cancel_order(order.get("id"))
            print(status)


if __name__ == "__main__":
    print("="*80)
    print("🚀 MSX Exchange - 使用 Chrome DevTools Protocol")
    print("="*80)
    print("\n📋 使用说明:")
    print("  1. 确保 Chrome 以调试模式启动:")
    print("     Google Chrome --remote-debugging-port=9222")
    print("  2. 本脚本将连接到已有的 Chrome 实例")
    print("  3. 监听并分析所有网络请求和 WebSocket 数据")
    print("\n" + "="*80 + "\n")
    
    asyncio.run(main())