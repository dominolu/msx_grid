from pathlib import Path
from typing import AsyncIterator, Dict, Any, Optional
import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from loguru import logger as log

from msx.exchange import MsxExchange
from msx.grid import GridStrategy
from msx.config_loader import load_config

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# 全局变量：交易所和策略实例
exchange: Optional[MsxExchange] = None
strategy: Optional[GridStrategy] = None


# 请求模型
class GridStartRequest(BaseModel):
    """启动网格策略的请求参数
    
    使用 params 字典传入参数，支持灵活配置：
    - 必需参数：symbol, min_price, max_price, direction, grid_spacing, investment_amount
    - 可选参数：asset_type (默认 "crypto"), market_type (默认 "contract")
    """
    params: Dict[str, Any]
    
    class Config:
        # 允许使用字典直接传入
        extra = "allow"


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    - 启动时：加载配置，连接 exchange，实例化 grid 策略
    - 关闭时：停止所有运行中的策略并清理资源。
    """
    global exchange, strategy
    
    # 启动阶段：加载配置并初始化
    try:
        log.info("正在加载配置文件...")
        config = load_config("config.yaml")
        
        # 获取 cdp_url
        cdp_url = config.get("cdp_url")
        if not cdp_url:
            raise ValueError("配置文件中未找到 cdp_url")
        
        log.info(f"正在连接交易所 (cdp_url: {cdp_url})...")
        exchange = MsxExchange(cdp_url=cdp_url)
        await exchange.connect()
        log.info("✅ 交易所连接成功")
        
        # 实例化网格策略
        log.info("正在创建网格策略实例...")
        strategy = GridStrategy(exchange=exchange)
        log.info("✅ 网格策略实例创建成功")
        
    except Exception as e:
        log.error(f"初始化失败: {e}")
        raise
    
    yield
    
    # 关闭阶段：清理资源
    log.info("正在清理资源...")
    
    # 停止策略（stop 方法内部会取消任务）
    if strategy and strategy._status:
        log.info("正在停止网格策略...")
        await strategy.stop()
        
       
    # 断开交易所连接
    if exchange:
        log.info("正在断开交易所连接...")
        await exchange.disconnect()
    
    log.info("✅ 资源清理完成")


app = FastAPI(title="MSX Grid", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root_page() -> FileResponse:
    """返回前端入口页面（纯静态 HTML）。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/hello")
async def hello() -> Dict[str, Any]:
    """最简单的 JSON API 示例。"""
    return {"message": "Hello from FastAPI!", "status": "ok"}


@app.post("/api/start")
async def start_grid(request: GridStartRequest) -> Dict[str, Any]:
    """启动网格策略
    
    接收网格参数并启动策略
    
    参数说明：
    - params: 字典形式传入网格参数
      - 必需参数：symbol, min_price, max_price, direction, grid_spacing, investment_amount
      - 可选参数：asset_type (默认 "crypto"), market_type (默认 "contract")
    
    示例：
    {
        "params": {
            "symbol": "ETHUSDT",
            "min_price": 3000,
            "max_price": 3700,
            "direction": "long",
            "grid_spacing": 0.005,
            "investment_amount": 10000,
            "asset_type": "crypto",
            "market_type": "contract"
        }
    }
    """
    global strategy
    
    if not strategy:
        raise HTTPException(status_code=500, detail="策略实例未初始化")
    
    if strategy._status:
        raise HTTPException(status_code=400, detail="策略已在运行中")
    
    try:
        # 从请求中提取参数
        params = request.params
        
        # 启动策略，返回结果字典（任务由 strategy 对象内部管理）
        result = await strategy.start(params)
        log.info(f"启动策略结果: {result}")
        # 直接返回策略的返回结果（包含 status 和 message）
        return result
    except HTTPException:
        raise
    except ValueError as e:
        log.error(f"参数验证失败: {e}")
        raise HTTPException(status_code=400, detail=f"参数验证失败: {str(e)}")
    except Exception as e:
        log.error(f"启动策略失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动策略失败: {str(e)}")
 

@app.post("/api/stop")
async def stop_grid() -> Dict[str, Any]:
    """停止网格策略"""
    global strategy
    
    if not strategy:
        raise HTTPException(status_code=500, detail="策略实例未初始化")
    
    if not strategy._status:
        return {
            "status": "success",
            "message": "策略未在运行"
        }
    
    try:
        # 停止策略（stop 方法内部会取消任务）
        await strategy.stop()
        
        return {
            "status": "success",
            "message": "网格策略已停止"
        }
    except Exception as e:
        log.error(f"停止策略失败: {e}")
        raise HTTPException(status_code=500, detail=f"停止策略失败: {str(e)}")


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    """获取网格策略的运行状态"""
    global strategy
    
    if not strategy:
        raise HTTPException(status_code=500, detail="策略实例未初始化")
    
    try:
        status =  strategy.get_status()
        return {
            "status": "success",
            "data": status
        }
    except Exception as e:
        log.error(f"获取状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


@app.get("/api/free_balance")
async def get_free_balance() -> Dict[str, Any]:
    """获取账户可用余额
    
    返回账户的可用余额信息，包括：
    - balance: 可用余额
    - acctBalance: 账户总余额
    - assetValuation: 资产估值
    - pnlTotal: 总盈亏
    """
    global exchange
    
    if not exchange:
        raise HTTPException(status_code=500, detail="交易所实例未初始化")
    
    try:
        account_info = await exchange.fetch_account()
        
        # 提取可用余额（通常 balance 就是可用余额）
        free_balance = account_info.get("balance", 0.0)
        acct_balance = account_info.get("acctBalance", 0.0)
        asset_valuation = account_info.get("assetValuation", 0.0)
        pnl_total = account_info.get("pnlTotal", 0.0)
        
        return {
            "status": "success",
            "data": {
                "free_balance": free_balance,
                "acct_balance": acct_balance,
                "asset_valuation": asset_valuation,
                "pnl_total": pnl_total,
                "raw": account_info.get("raw", {})
            }
        }
    except Exception as e:
        log.error(f"获取可用余额失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取可用余额失败: {str(e)}")


@app.get("/api/symbols")
async def get_symbols(market_type: str = None, co_type: int = None) -> Dict[str, Any]:
    """获取交易对列表
    
    参数:
        market_type: 市场类型，可选值 "spot"（现货）或 "contract"（合约），不传则返回所有
        co_type: 合约类型，1=股票，3=加密货币，仅合约市场有效，默认 None（使用默认值1）
    
    返回所有可用的交易对符号列表
    """
    global exchange
    
    if not exchange:
        raise HTTPException(status_code=500, detail="交易所实例未初始化")
    
    try:
        # 验证 market_type 参数
        if market_type and market_type not in ["spot", "contract"]:
            raise HTTPException(
                status_code=400, 
                detail=f"market_type 参数无效，必须是 'spot' 或 'contract'，当前值: {market_type}"
            )
        
        # 验证 co_type 参数（仅合约市场有效）
        if co_type is not None and market_type == "contract":
            if co_type not in [1, 3]:
                raise HTTPException(
                    status_code=400,
                    detail=f"co_type 参数无效，必须是 1（股票）或 3（加密货币），当前值: {co_type}"
                )
        
        # 默认使用合约
        market_type = market_type or "contract"
        
        # 调用 exchange.get_symbols() 获取交易对（现在是异步方法）
        # 如果是合约市场，传递 co_type 参数（如果未指定则使用默认值 1）
        if market_type == "contract":
            # co_type 为 None 时，exchange.get_symbols 会使用默认值 1
            symbols = await exchange.get_symbols(market_type=market_type, co_type=co_type)
        else:
            symbols = await exchange.get_symbols(market_type=market_type)
        
        return {
            "status": "success",
            "data": {
                "symbols": symbols,
                "count": len(symbols),
                "market_type": market_type or "all",
                "co_type": co_type if market_type == "contract" else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取交易对列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取交易对列表失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)


