自动配置杠杆,下单的杠杆要与本配置的一致
收益率计算公式出错

现货 
-  https://api9528mystks.mystonks.org/api/v1/stock/trade
  市价
    {symbol: "QQQ", side: "BUY", price: 0, vol: 0, amt: 10, entrustType: 2}
    {
  "code": 0,
  "data": null,
  "msg": "success",
  "request_id": "697523e6-9aea-49c1-99cf-b762e71d7673",
  "success": true
}
现价
{symbol: "QQQ", side: "BUY", price: "600", vol: 0, amt: 10, entrustType: 1}
{symbol: "QQQ", side: "BUY", price: "600", vol: 0, amt: 60, entrustType: 1}

open_order
https://api9528mystks.mystonks.org/api/v1/stock/limitOrders
{symbol: "", userId: 1}
{
  "code": 0,
  "data": [
    {
      "id": 81184,
      "symbol": "QQQ",
      "price": "600",
      "volume": "0.0997",
      "side": "BUY",
      "tokenKey": "QQQ.M",
      "tokenVol": "0",
      "status": 1,
      "type": 1,
      "tradeRate": "0",
      "payAmt": "59.82",
      "paySymbol": "USD",
      "fee": "0.18",
      "createTime": 1765983878482
    }
  ],
  "msg": "success",
  "request_id": "77689e65-b1aa-41a7-bbb5-032b2eb4c88a",
  "success": true
}

cancel https://api9528mystks.mystonks.org/api/v1/stock/cancel
{
  "userId": 1,
  "orderId": 81184
}{code: 0, data: null, msg: "success", request_id: "4fafd460-d098-40ce-b090-095d12010d27",…}

余额
https://api9528mystks.mystonks.org/api/v1/stock/myAcctPos
{symbol: "", userId: 1}
{
  "code": 0,
  "data": {
    "nowAmtTotal": "125.04",
    "pnlRate": "0",
    "pnlTotal": "0",
    "items": [
      {
        "symbol": "QQQ",
        "tokenKey": "QQQ.M",
        "balance": "0.016336",
        "useBalance": "0.016336",
        "pnlAmt": "0.00196",
        "pnlRatio": "0.02",
        "price": "610.41",
        "sellPrice": "610.43",
        "amtTotal": "9.97",
        "day_close": "611.75",
        "costPrice": "610.29",
        "status": 1
      },
      {
        "symbol": "USDT",
        "tokenKey": "USD",
        "balance": "115.07",
        "useBalance": "115.07",
        "pnlAmt": "0",
        "pnlRatio": "0",
        "price": "1",
        "sellPrice": "1",
        "amtTotal": "115.07",
        "day_close": "0",
        "costPrice": "0",
        "status": 0
      }
    ]
  },
  "msg": "success",
  "request_id": "3013964a-7429-44b5-a80a-a3dc6cc6cf6c",
  "success": true
}

历史订单
https://api9528mystks.mystonks.org/api/v1/stock/hisOrders
{
  "symbol": "",
  "userId": 1,
  "pageIndex": 1,
  "pageSize": 100000
}
{
  "code": 0,
  "msg": "success",
  "data": {
    "count": 3,
    "list": [
      {
        "id": 81184,
        "symbol": "QQQ",
        "price": "600",
        "avgPrice": "0",
        "volume": "0.0997",
        "side": "BUY",
        "tokenKey": "QQQ.M",
        "status": 4,
        "type": 1,
        "tradeRate": "0",
        "fee": "0",
        "feeCoin": "USD",
        "createTime": 1765983878482
      },
      {
        "id": 81175,
        "symbol": "QQQ",
        "price": "600",
        "avgPrice": "0",
        "volume": "0.016616",
        "side": "BUY",
        "tokenKey": "QQQ.M",
        "status": 4,
        "type": 1,
        "tradeRate": "0",
        "fee": "0",
        "feeCoin": "USD",
        "createTime": 1765983122587
      },
      {
        "id": 81173,
        "symbol": "QQQ",
        "price": "610.29",
        "avgPrice": "610.29",
        "volume": "0.016336",
        "side": "BUY",
        "tokenKey": "QQQ.M",
        "status": 2,
        "type": 2,
        "tradeRate": "0",
        "fee": "0.03",
        "feeCoin": "USD",
        "createTime": 1765983043201
      }
    ],
    "pageIndex": 1,
    "pageSize": 100000
  },
  "timestamp": 1765984160
}

