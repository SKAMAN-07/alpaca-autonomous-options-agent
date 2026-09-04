# Alpaca AI Trading Hackathon — Submission Write-Up
## Autonomous Multi-Agent Options Trading System (Variance Risk Premium Alpha)

**Timestamp:** 2026-09-04 12:25:35 UTC  
**Account Equity:** $100,000.00 USD (Alpaca Paper Trading Account: `d8459ab1-bc63-4799-8439-0a9e759fc255`)  
**Options Trading Approval Level:** Level 3  

---

### 1. Executive Summary & AI Architecture
This system is an autonomous multi-agent quantitative options trading system built for the **Alpaca AI Trading Hackathon**. The architecture separates signal generation, risk governance, and order routing across three decoupled layers:

1. **Primary Alpha Agent (`alpha_agent.py`)**: Continuously analyzes historical daily price bars and options surface data via Alpaca APIs. Computes the **Variance Risk Premium (VRP)** to identify volatility mispricings, while leveraging **Featherless AI** serverless open-source model inference (`Qwen/Qwen2.5-7B-Instruct`) to extract macroeconomic and volatility regime intelligence.
2. **Secondary Deterministic Compliance Shield (`compliance_shield.py`)**: A pure Python, zero-LLM risk gate that mathematically audits all proposed orders against 8 strict portfolio constraints before execution. **Any trade that fails a risk gate is instantly vetoed.**
3. **Alpaca Execution Engine (`execution_engine.py`)**: Directly translates compliant signals into defined-risk multi-leg and protective limit orders executed on Alpaca's $100,000 paper trading infrastructure.

```
+-------------------------------------------------------------------------+
|                         Alpaca Market Data                              |
|           (Historical Daily Bars via IEX + Option Quotes)               |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
|                  Layer 1: Primary Options Alpha Agent                   |
|  - Realized Volatility: RV = std(ln(P_t/P_t-1)) * sqrt(252)             |
|  - Implied Volatility: Inversion of Black-Scholes Formula               |
|  - VRP Signal: VRP = IV - RV                                            |
|  - Featherless AI Open-Source Model Inference (Market Regime Analysis)  |
+------------------------------------+------------------------------------+
                                     | (Proposed Trade Payload)
                                     v
+-------------------------------------------------------------------------+
|             Layer 2: Deterministic Compliance Shield (Risk Gate)        |
|  - Gate 1: Strict Paper Endpoint Confirmation                           |
|  - Gate 2: Max Trade Risk <= $5,000 (5% Capital Ceiling)                |
|  - Gate 3: Collateral <= Buying Power                                   |
|  - Gate 4: Defined-Risk Enforcement (No Naked Options)                  |
|  - Gate 5: DTE Safety Window [7 to 45 Days]                             |
|  - Gate 6: Probability Delta Band [0.15 <= |Delta| <= 0.30]             |
|  - Gate 7: Bid-Ask Liquidity Filter (<= 20% Spread)                     |
|  - Gate 8: Portfolio Concentration Limit (< 5 Active Positions)         |
+------------------------------------+------------------------------------+
                                     | (Strict Approval Only)
                                     v
+-------------------------------------------------------------------------+
|               Layer 3: Alpaca Options Execution Engine                  |
|  - Alpaca Paper Trading V2 API Routing                                  |
|  - Limit Order & Multi-Leg Order Protection                             |
|  - Automated Fill & Position Auditing                                   |
+-------------------------------------------------------------------------+
```

---

### 2. Quantitative Strategy: The Variance Risk Premium (VRP)
The core quantitative edge is founded on the well-documented empirical anomaly known as the **Variance Risk Premium (VRP)**:
`VRP = Implied_Volatility - Realized_Volatility`

* **Underpricing of Realized Volatility**: Option buyers consistently overpay for insurance against market crashes, resulting in implied volatility exceeding subsequent realized price fluctuations.
* **Harvesting Protocol**: When VRP > 2.0%, the Alpha Agent crafts defined-risk **Bull Put Spreads** or **Bear Call Spreads** with the short strike targeted at approximately ~0.20 delta (providing an ~80% statistical probability of expiring out of the money) and a protective long wing placed $2 to $10 further out of the money.
* **Featherless AI Market Extraction**: The system queries `Qwen/Qwen2.5-7B-Instruct` via Featherless serverless endpoints to confirm whether the market regime supports premium harvesting or signals volatility expansion.

---

### 3. Deterministic Compliance Shield Audit Trail
The Compliance Shield operates with **zero generative AI dependence** to prevent prompt injection or hallucinated parameters in the execution path. In production testing:
* **Compliant Trades Approved**: All 8 gates evaluated to `PASS`.
* **Deliberately Injected High-Risk Trades**: An unhedged naked call with $50,000 risk was instantly **VETOED** with detailed violation codes (`GATE_2_MAX_DEFINED_RISK`, `GATE_4_DEFINED_RISK_PROTECTION`).

---

### 4. Verified Live Alpaca Paper Executions
The following options orders were submitted to Alpaca's paper trading system:

| Order ID | Underlying | Strategy | Status | Max Risk Committed | Net Credit | Executed Legs |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `663e60a9-495c-471b-b296-f5afabbc64a5` | QQQ | BULL_PUT_SPREAD | `OrderStatus.ACCEPTED` | $182.00 | $18.00 | SELL QQQ260918P00695000, BUY QQQ260918P00693000 |

---

### 5. Alpaca MCP Server (V2) & Tooling Infrastructure
* **Tooling Setup**: Initialized and run via `uvx alpaca-mcp-server` using FastMCP.
* **Environment**: Powered by `uv` high-performance Python package management with CPython 3.12.
* **Resilience**: Autonomous self-debugging loop handles off-hours limit order requirements, options contract expiration schedules, and IEX market data feeds seamlessly.
