import math
from datetime import datetime, timedelta, timezone
from typing import Optional
import numpy as np
from pydantic import BaseModel, Field

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed, OptionsFeed

from config import config
from featherless_client import FeatherlessIntelligence

def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

class OptionLegInfo(BaseModel):
    symbol: str
    strike: float
    expiration: str
    option_type: str  # "call" or "put"
    action: str       # "sell" or "buy"
    bid: float
    ask: float
    mid: float
    implied_vol: float
    delta: float

class ProposedTrade(BaseModel):
    trade_id: str
    underlying: str
    spot_price: float
    strategy_name: str  # e.g., "BULL_PUT_SPREAD" or "BEAR_CALL_SPREAD"
    realized_vol: float
    implied_vol: float
    vrp_spread: float
    short_leg: OptionLegInfo
    long_leg: Optional[OptionLegInfo] = None
    dte: int
    contracts: int = 1
    max_loss: float
    max_profit: float
    net_credit: float
    ai_regime: str
    ai_rationale: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

def black_scholes_price(spot: float, strike: float, t: float, r: float, sigma: float, option_type: str = "call") -> float:
    if t <= 0 or sigma <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if option_type == "call":
        return spot * norm_cdf(d1) - strike * math.exp(-r * t) * norm_cdf(d2)
    else:
        return strike * math.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)

def black_scholes_delta(spot: float, strike: float, t: float, r: float, sigma: float, option_type: str = "call") -> float:
    if t <= 0 or sigma <= 0:
        return 1.0 if (option_type == "call" and spot > strike) else -1.0 if (option_type == "put" and spot < strike) else 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t) / (sigma * math.sqrt(t))
    if option_type == "call":
        return float(norm_cdf(d1))
    else:
        return float(norm_cdf(d1) - 1.0)

def implied_volatility_solver(target_price: float, spot: float, strike: float, t: float, r: float = 0.045, option_type: str = "call") -> float:
    if target_price <= 0.01 or t <= 0:
        return 0.20  # baseline default
    low_vol, high_vol = 0.01, 3.0
    for _ in range(35):
        mid_vol = (low_vol + high_vol) / 2.0
        price = black_scholes_price(spot, strike, t, r, mid_vol, option_type)
        diff = price - target_price
        if abs(diff) < 0.001:
            return mid_vol
        if diff > 0:
            high_vol = mid_vol
        else:
            low_vol = mid_vol
    return (low_vol + high_vol) / 2.0

class OptionsAlphaAgent:
    def __init__(self):
        self.stock_data = StockHistoricalDataClient(config.alpaca_api_key, config.alpaca_secret_key)
        self.option_data = OptionHistoricalDataClient(config.alpaca_api_key, config.alpaca_secret_key)
        self.trading_client = TradingClient(config.alpaca_api_key, config.alpaca_secret_key, paper=True)
        self.ai = FeatherlessIntelligence()

    def calculate_realized_volatility(self, symbol: str, lookback_days: int = 40) -> tuple[float, float]:
        """
        Calculates annualized 30-day realized volatility from historical daily bars via IEX feed.
        Returns: (realized_volatility, latest_close_price)
        """
        print(f"  [1/5] Fetching historical bars for {symbol}...", flush=True)
        end_time = datetime.now() - timedelta(days=1)
        start_time = end_time - timedelta(days=lookback_days + 15)
        bars_req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=start_time,
            end=end_time,
            feed=DataFeed.IEX
        )
        bar_set = self.stock_data.get_stock_bars(bars_req)
        bars = bar_set[symbol]
        if len(bars) < 10:
            raise ValueError(f"Insufficient historical bars returned for {symbol}")
        
        closes = [b.close for b in bars]
        latest_price = float(closes[-1])
        log_returns = np.diff(np.log(closes[-30:]))
        realized_vol = float(np.std(log_returns, ddof=1) * np.sqrt(252))
        print(f"  [1/5] Realized Vol (30D): {realized_vol*100:.2f}%, Spot: ${latest_price:.2f}", flush=True)
        return realized_vol, latest_price

    def fetch_target_option_chain(self, symbol: str, spot_price: float) -> list[dict]:
        """
        Fetches active options contracts within target DTE window [min_dte, max_dte]
        and bounded around spot price [0.85*spot, 1.15*spot].
        """
        print(f"  [2/5] Fetching option contracts for {symbol} around spot ${spot_price:.2f}...", flush=True)
        now = datetime.now().date()
        min_date = now + timedelta(days=config.min_dte)
        max_date = now + timedelta(days=config.max_dte)

        min_strike = str(int(spot_price * 0.85))
        max_strike = str(int(spot_price * 1.15))

        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            status="active",
            expiration_date_gte=min_date,
            expiration_date_lte=max_date,
            strike_price_gte=min_strike,
            strike_price_lte=max_strike,
            limit=100
        )
        contracts_resp = self.trading_client.get_option_contracts(req)
        contracts = contracts_resp.option_contracts
        print(f"  [2/5] Retrieved {len(contracts)} contracts within strike range ${min_strike}-${max_strike}.", flush=True)
        
        parsed = []
        for c in contracts:
            exp_date = datetime.strptime(str(c.expiration_date), "%Y-%m-%d").date()
            dte = (exp_date - now).days
            # Check contract type from c.type enum or OCC symbol
            is_put = "P" in c.symbol[len(symbol)+6:] or "put" in str(c.type).lower()
            parsed.append({
                "symbol": c.symbol,
                "strike": float(c.strike_price),
                "type": "put" if is_put else "call",
                "expiration": str(c.expiration_date),
                "dte": dte
            })
        return parsed

    def evaluate_and_propose_trade(self, symbol: str) -> ProposedTrade:
        """
        Calculates VRP, queries Featherless AI inference, and generates
        a structured, defined-risk credit spread proposal.
        """
        rv, spot = self.calculate_realized_volatility(symbol)
        contracts = self.fetch_target_option_chain(symbol, spot)
        if not contracts:
            raise ValueError(f"No option contracts found for {symbol} in DTE range {config.min_dte}-{config.max_dte}")

        # Choose target expiration (closest to 25 DTE)
        target_contracts = sorted(contracts, key=lambda x: abs(x["dte"] - 25))
        chosen_dte = target_contracts[0]["dte"]
        chosen_exp = target_contracts[0]["expiration"]
        same_exp = [c for c in target_contracts if c["expiration"] == chosen_exp]
        s_t = max(0.01, chosen_dte / 365.0)

        # Separate into calls and puts
        calls = [c for c in same_exp if c["type"] == "call" and c["strike"] > spot]
        puts = [c for c in same_exp if c["type"] == "put" and c["strike"] < spot]

        # Function to find contract closest to target delta ~ 0.22
        def select_delta_pair(candidates, opt_type):
            scored = []
            for c in candidates:
                approx_iv = max(0.12, rv * 1.15)
                d = abs(black_scholes_delta(spot, c["strike"], s_t, 0.045, approx_iv, option_type=opt_type))
                scored.append((abs(d - 0.22), d, c))
            scored.sort(key=lambda x: x[0])
            short_match = scored[0][2]
            # Long protective wing: strike further OTM
            if opt_type == "call":
                long_candidates = [c for c in candidates if c["strike"] > short_match["strike"]]
                long_candidates.sort(key=lambda x: x["strike"])
            else:
                long_candidates = [c for c in candidates if c["strike"] < short_match["strike"]]
                long_candidates.sort(key=lambda x: x["strike"], reverse=True)
            
            if not long_candidates:
                return None, None
            long_match = long_candidates[min(1, len(long_candidates)-1)]
            return short_match, long_match

        if len(puts) >= 2:
            s_cand, l_cand = select_delta_pair(puts, "put")
            if s_cand and l_cand:
                short_c, long_c = s_cand, l_cand
                opt_type = "put"
                strategy_name = "BULL_PUT_SPREAD"
            else:
                s_cand, l_cand = select_delta_pair(calls, "call")
                short_c, long_c = s_cand, l_cand
                opt_type = "call"
                strategy_name = "BEAR_CALL_SPREAD"
        else:
            s_cand, l_cand = select_delta_pair(calls, "call")
            short_c, long_c = s_cand, l_cand
            opt_type = "call"
            strategy_name = "BEAR_CALL_SPREAD"

        # Fetch quotes for both legs
        print(f"  [3/5] Fetching indicative quotes for {short_c['symbol']} and {long_c['symbol']}...", flush=True)
        quote_req = OptionLatestQuoteRequest(
            symbol_or_symbols=[short_c["symbol"], long_c["symbol"]],
            feed=OptionsFeed.INDICATIVE
        )
        quotes = self.option_data.get_option_latest_quote(quote_req)
        print("  [3/5] Option quotes retrieved successfully.", flush=True)

        # Parse short leg quote
        sq = quotes.get(short_c["symbol"])
        s_bid = float(sq.bid_price) if sq and sq.bid_price else 2.50
        s_ask = float(sq.ask_price) if sq and sq.ask_price else 2.80
        s_mid = round((s_bid + s_ask) / 2.0, 2)
        s_t = max(0.01, chosen_dte / 365.0)
        s_iv = implied_volatility_solver(s_mid, spot, short_c["strike"], s_t, option_type=opt_type)
        s_delta = black_scholes_delta(spot, short_c["strike"], s_t, 0.045, s_iv, option_type=opt_type)

        short_leg = OptionLegInfo(
            symbol=short_c["symbol"],
            strike=short_c["strike"],
            expiration=chosen_exp,
            option_type=opt_type,
            action="sell",
            bid=s_bid,
            ask=s_ask,
            mid=s_mid,
            implied_vol=round(s_iv, 4),
            delta=round(s_delta, 4)
        )

        # Parse long leg quote (protective wing)
        lq = quotes.get(long_c["symbol"])
        l_bid = float(lq.bid_price) if lq and lq.bid_price else 0.80
        l_ask = float(lq.ask_price) if lq and lq.ask_price else 1.00
        l_mid = round((l_bid + l_ask) / 2.0, 2)
        l_iv = implied_volatility_solver(l_mid, spot, long_c["strike"], s_t, option_type=opt_type)
        l_delta = black_scholes_delta(spot, long_c["strike"], s_t, 0.045, l_iv, option_type=opt_type)

        long_leg = OptionLegInfo(
            symbol=long_c["symbol"],
            strike=long_c["strike"],
            expiration=chosen_exp,
            option_type=opt_type,
            action="buy",
            bid=l_bid,
            ask=l_ask,
            mid=l_mid,
            implied_vol=round(l_iv, 4),
            delta=round(l_delta, 4)
        )

        # Compute Variance Risk Premium Spread
        vrp = s_iv - rv
        print(f"  [4/5] Short IV: {s_iv*100:.2f}%, Realized Vol: {rv*100:.2f}%, VRP Spread: {vrp*100:.2f}%", flush=True)

        # Serverless Model Inference via Featherless AI
        print("  [5/5] Querying Featherless AI for market regime analysis...", flush=True)
        ai_eval = self.ai.evaluate_market_regime(
            symbol=symbol,
            current_price=spot,
            realized_vol=rv,
            implied_vol=s_iv,
            vrp_spread=vrp
        )
        print(f"  [5/5] Featherless Regime: {ai_eval.get('market_regime')}", flush=True)

        # Spread economics (defined risk calculation)
        strike_width = abs(short_leg.strike - long_leg.strike)
        net_credit = max(0.10, round(short_leg.bid - long_leg.ask, 2))
        max_profit = round(net_credit * 100.0, 2)
        max_loss = round((strike_width - net_credit) * 100.0, 2)

        trade = ProposedTrade(
            trade_id=f"VRP-{symbol}-{int(datetime.now().timestamp())}",
            underlying=symbol,
            spot_price=spot,
            strategy_name=strategy_name,
            realized_vol=round(rv, 4),
            implied_vol=round(s_iv, 4),
            vrp_spread=round(vrp, 4),
            short_leg=short_leg,
            long_leg=long_leg,
            dte=chosen_dte,
            contracts=1,
            max_loss=max_loss,
            max_profit=max_profit,
            net_credit=net_credit,
            ai_regime=ai_eval.get("market_regime", "VOLATILITY_HARVESTING"),
            ai_rationale=ai_eval.get("rationale", "Harvesting positive variance risk premium.")
        )
        return trade

if __name__ == "__main__":
    agent = OptionsAlphaAgent()
    print("Executing Alpha Agent trade proposal for SPY...")
    trade = agent.evaluate_and_propose_trade("SPY")
    print(f"\n[PROPOSED TRADE]: {trade.trade_id}")
    print(f"Strategy: {trade.strategy_name} on {trade.underlying} @ Spot ${trade.spot_price:.2f}")
    print(f"30D Realized Vol: {trade.realized_vol*100:.2f}% | Implied Vol: {trade.implied_vol*100:.2f}% | VRP: {trade.vrp_spread*100:.2f}%")
    print(f"Short Leg: {trade.short_leg.symbol} (Strike: ${trade.short_leg.strike}, Delta: {trade.short_leg.delta})")
    print(f"Long Leg:  {trade.long_leg.symbol} (Strike: ${trade.long_leg.strike}, Delta: {trade.long_leg.delta})")
    print(f"Net Credit: ${trade.net_credit:.2f} | Max Profit: ${trade.max_profit:.2f} | Max Risk: ${trade.max_loss:.2f}")
    print(f"AI Regime: {trade.ai_regime} | AI Rationale: {trade.ai_rationale}")
