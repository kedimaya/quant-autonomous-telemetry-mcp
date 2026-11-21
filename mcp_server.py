#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
OPEN-CORE MCP SUNUCUSU: OTONOM REJİM & TELEMETRİ ARAYÜZÜ (mcp_server.py)
================================================================================
MİMARİ:
  - Model Context Protocol (MCP) JSON-RPC 2.0 / stdio standardı.
  - Sıfır dış bağımlılık (Saf Python standart kütüphanesi).
  - Open-Core Güvenliği: Gizli API anahtarlarını ve özel giriş formüllerini
    dışarıya ASLA açmaz; yalnızca piyasa rejimi, performans telemetrisi ve
    otonom öğrenme denetimini yapay zeka ajanlarına (Claude, Cursor vb.) sunar.
================================================================================
"""

import os
import sys
import json
from dataclasses import asdict
from autonomous_brain import MarketRegimeDetector, AutonomousLearningEngine, BRAIN_CONFIG

SERVER_INFO = {
    "name": "quant-autonomous-telemetry-mcp",
    "version": "1.0.0"
}

TOOLS = [
    {
        "name": "get_market_regime",
        "description": "BTC 4H ve 1D verilerinden anlık piyasa rejimini (BULL_TREND, SIDEWAYS, BEAR_DOWNTREND, HIGH_VOL_CHOP) ve önerilen risk parametrelerini döner.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_bot_telemetry",
        "description": "orchestrator_state.json dosyasından kasa bakiyesi, gerçekleşen PnL, başarı oranı, açık pozisyonlar ve son 5 işlem özetini güvenli şekilde okur.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_autonomous_audit",
        "description": "Otonom öğrenme motorunu tetikler; geçmiş işlemlerin sinyal karnesini analiz edip autonomous_weights.json dosyasını günceller.",
        "inputSchema": {"type": "object", "properties": {}}
    }
]


def _read_telemetry() -> dict:
    state_file = BRAIN_CONFIG["STATE_FILE"]
    if not os.path.exists(state_file):
        return {"status": "NO_STATE_FILE", "message": "Henüz işlem kaydı oluşmamış."}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        trades = state.get("trade_count", 0)
        wins = state.get("winning_trades", 0)
        return {
            "cash_usdt": round(state.get("cash_usdt", 0.0), 2),
            "total_realized_pnl_usdt": round(state.get("total_realized_pnl_usdt", 0.0), 4),
            "trade_count": trades,
            "win_rate_pct": round((wins / trades * 100.0), 2) if trades > 0 else 0.0,
            "open_positions": list(state.get("positions", {}).keys()),
            "recent_trades": state.get("trade_history", [])[-5:]
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def handle_tool_call(name: str) -> dict:
    if name == "get_market_regime":
        report = MarketRegimeDetector.detect(force_refresh=True)
        return asdict(report)
    elif name == "get_bot_telemetry":
        return _read_telemetry()
    elif name == "run_autonomous_audit":
        report = MarketRegimeDetector.detect(force_refresh=True)
        return AutonomousLearningEngine.analyze_and_update_weights(report)
    return {"error": f"Bilinmeyen araç: {name}"}


def send_response(msg: dict):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")

        if method == "initialize":
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO
                }
            })
        elif method == "tools/list":
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS}
            })
        elif method == "tools/call":
            tool_name = req.get("params", {}).get("name", "")
            result_data = handle_tool_call(tool_name)
            send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result_data, indent=2, ensure_ascii=False)}]
                }
            })


if __name__ == "__main__":
    main()
