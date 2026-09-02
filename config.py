import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env from current directory
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

class TradingConfig(BaseModel):
    # Credentials
    alpaca_api_key: str = Field(default_factory=lambda: os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret_key: str = Field(default_factory=lambda: os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY", ""))
    alpaca_base_url: str = Field(default_factory=lambda: os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets"))
    alpaca_data_url: str = Field(default_factory=lambda: os.getenv("APCA_API_DATA_URL", "https://data.alpaca.markets"))
    
    featherless_api_key: str = Field(default_factory=lambda: os.getenv("FEATHERLESS_API_KEY", ""))
    featherless_base_url: str = Field(default_factory=lambda: os.getenv("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1"))
    featherless_model: str = "Qwen/Qwen2.5-7B-Instruct"

    # Paper Account & Capital Bounds
    account_target_capital: float = 100_000.0
    max_risk_per_trade: float = 5_000.0  # Max 5% of account per trade
    max_portfolio_exposure: float = 0.25  # Max 25% total allocated to active option risk
    max_open_positions: int = 5

    # Strategy / VRP Parameters
    target_underlyings: list[str] = ["SPY", "QQQ", "AAPL", "MSFT"]
    min_vrp_spread: float = 0.02  # 2% spread: IV - RV > 0.02 to trigger premium harvesting
    min_dte: int = 7             # Avoid extreme 0-6 DTE gamma risk
    max_dte: int = 45            # Focus on 7 to 45 DTE cycle
    target_short_delta_min: float = 0.15
    target_short_delta_max: float = 0.30
    max_bid_ask_spread_pct: float = 0.20  # Max 20% bid-ask spread to avoid predatory slippage

config = TradingConfig()

if __name__ == "__main__":
    print(f"Configuration loaded successfully:")
    print(f"Alpaca Base URL: {config.alpaca_base_url}")
    print(f"Alpaca API Key (masked): {config.alpaca_api_key[:6]}...{config.alpaca_api_key[-4:] if config.alpaca_api_key else ''}")
    print(f"Featherless Model: {config.featherless_model}")
    print(f"Max Risk Per Trade: ${config.max_risk_per_trade:,.2f}")
