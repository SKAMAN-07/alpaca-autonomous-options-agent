import json
from openai import OpenAI
from config import config

class FeatherlessIntelligence:
    def __init__(self):
        self.client = OpenAI(
            base_url=config.featherless_base_url,
            api_key=config.featherless_api_key,
            timeout=30.0
        )
        self.model = config.featherless_model

    def evaluate_market_regime(
        self,
        symbol: str,
        current_price: float,
        realized_vol: float,
        implied_vol: float,
        vrp_spread: float
    ) -> dict:
        """
        Uses serverless open-source model inference via Featherless AI to evaluate
        volatility regime and provide quantitative trade confirmation.
        """
        prompt = f"""
You are an institutional quantitative options researcher. Analyze the following market state:
- Asset: {symbol}
- Spot Price: ${current_price:.2f}
- 30-Day Realized Volatility (RV): {realized_vol * 100:.2f}%
- At-The-Money Implied Volatility (IV): {implied_vol * 100:.2f}%
- Variance Risk Premium Spread (IV - RV): {vrp_spread * 100:.2f}%

Determine if the market conditions favor an options selling strategy (e.g., selling defined-risk credit spreads to harvest VRP) or if risk is elevated.

Respond ONLY with valid JSON in this exact structure:
{{
    "market_regime": "VOLATILITY_EXPANSION" | "VOLATILITY_HARVESTING" | "NEUTRAL",
    "recommendation": "PROPOSE_CREDIT_SPREAD" | "HOLD" | "PROPOSE_DEBIT_SPREAD",
    "confidence": 0.85,
    "volatility_bias": "OVERPRICED" | "FAIR" | "UNDERPRICED",
    "rationale": "one concise sentence explaining the quantitative rationale"
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a quantitative options researcher that returns ONLY clean JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=150,
                temperature=0.1
            )
            raw_text = response.choices[0].message.content.strip()
            # Clean possible markdown formatting
            if raw_text.startswith("```"):
                lines = raw_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw_text = "\n".join(lines).strip()
            
            result = json.loads(raw_text)
            return result
        except Exception as e:
            # Deterministic fallback if API has transient latency
            return {
                "market_regime": "VOLATILITY_HARVESTING" if vrp_spread > 0.02 else "NEUTRAL",
                "recommendation": "PROPOSE_CREDIT_SPREAD" if vrp_spread > 0.02 else "HOLD",
                "confidence": 0.80,
                "volatility_bias": "OVERPRICED" if vrp_spread > 0 else "UNDERPRICED",
                "rationale": f"Rule-based fallback due to Featherless parse error ({e}); VRP spread is {vrp_spread*100:.2f}%."
            }

if __name__ == "__main__":
    fi = FeatherlessIntelligence()
    print("Testing Featherless Intelligence market extraction...")
    res = fi.evaluate_market_regime(
        symbol="SPY",
        current_price=580.0,
        realized_vol=0.12,
        implied_vol=0.16,
        vrp_spread=0.04
    )
    print("Result:", json.dumps(res, indent=2))
