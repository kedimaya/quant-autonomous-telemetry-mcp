# Quant Autonomous Telemetry & Regime MCP Server

Zero-dependency, pure Python **Model Context Protocol (MCP)** server and **4H/1D Market Regime Detector** designed for algorithmic spot trading systems.

Built with an **Open-Core** security architecture: keeps private execution strategies and exchange API credentials strictly isolated while exposing real-time market regime classification, live bot telemetry, and autonomous learning audits to AI agents (Claude Desktop, Cursor, etc.).

## Key Features

- **Zero External Dependencies:** Runs on 100% Python Standard Library (`urllib`, `json`, `dataclasses`, `concurrent.futures`). High portability, zero dependency bloat.
- **4H / 1D Market Regime Detection (`autonomous_brain.py`):** Classifies live BTC market structure into 4 actionable regimes (`BULL_TREND`, `SIDEWAYS`, `BEAR_DOWNTREND`, `HIGH_VOL_CHOP`) using EMA20/EMA50 alignment and 14-period ATR volatility.
- **Dynamic Risk Adaptation:** Automatically adjusts break-even triggers, trailing stops, L2 orderbook bid thresholds, cooldown timers, and position risk multipliers based on the active regime.
- **Autonomous Feedback Loop:** Evaluates closed trade `signal_snapshot` telemetry to detect parameter drift and updates `autonomous_weights.json` within anti-overfitting guardrails.
- **MCP JSON-RPC 2.0 Interface (`mcp_server.py`):** Exposes three read/audit tools over `stdio`:
  - `get_market_regime` — Live 4H/1D regime classification & recommended risk profile.
  - `get_bot_telemetry` — Safe summary of cash balance, realized PnL, win rate, and recent trades.
  - `run_autonomous_audit` — Triggers the self-learning evaluation cycle and updates weights.

## Architecture (Open-Core)

```text
├── autonomous_brain.py      # Public: 4H/1D Regime Detector & Autonomous Learning Engine
├── mcp_server.py            # Public: MCP JSON-RPC 2.0 (stdio) Server for AI Agents
├── .gitignore               # Enforces strict isolation of secrets & private modules
└── [Private Core]           # Isolated locally: Exchange execution, L2/CVD/Sweep engines & API keys
```

## Quick Start

1. **Test the Market Regime Detector:**
   ```bash
   python autonomous_brain.py
   ```

2. **Connect to Claude Desktop / Cursor (MCP Config):**
   ```json
   {
     "mcpServers": {
       "quant-telemetry": {
         "command": "python",
         "args": ["/path/to/mcp_server.py"]
       }
     }
   }
   ```

## Security Notice

This repository follows an **Open-Core** model. API credentials (`config_secret.json`), live state files (`orchestrator_state.json`), and proprietary execution modules are excluded via `.gitignore` and never exposed through the MCP layer.
