import math
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from alpaca.trading.client import TradingClient

from config import config
from alpha_agent import ProposedTrade

class RiskAuditItem(BaseModel):
    gate_name: str
    passed: bool
    limit_value: str
    observed_value: str
    details: str

class ComplianceAuditReport(BaseModel):
    trade_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approved: bool
    status: str  # "APPROVED" or "VETOED"
    passed_checks: int
    total_checks: int
    audit_trail: list[RiskAuditItem]
    veto_reasons: list[str] = Field(default_factory=list)

class ComplianceVetoException(Exception):
    def __init__(self, report: ComplianceAuditReport):
        self.report = report
        reasons = "; ".join(report.veto_reasons)
        super().__init__(f"COMPLIANCE SHIELD VETO: Trade {report.trade_id} rejected. Violations: {reasons}")

class ComplianceShield:
    """
    Deterministic Python-based Compliance Shield.
    Audits proposed trades against strict mathematical and portfolio risk gates.
    Operates with zero LLM dependence to prevent hallucinations in the execution path.
    """
    def __init__(self, trading_client: Optional[TradingClient] = None):
        self.trading_client = trading_client or TradingClient(
            api_key=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            paper=True
        )

    def audit_trade(self, trade: ProposedTrade) -> ComplianceAuditReport:
        audit_items: list[RiskAuditItem] = []
        veto_reasons: list[str] = []

        account = self.trading_client.get_account()
        cash = float(account.cash)
        buying_power = float(account.buying_power)

        # GATE 1: Paper Account Verification
        is_paper = "paper-api.alpaca.markets" in config.alpaca_base_url
        passed_g1 = is_paper and (str(account.status).lower() in ["active", "accountstatus.active"])
        audit_items.append(RiskAuditItem(
            gate_name="GATE_1_PAPER_ACCOUNT_VERIFICATION",
            passed=passed_g1,
            limit_value="Target endpoint must be strictly Paper Trading (paper-api.alpaca.markets)",
            observed_value=f"Endpoint={config.alpaca_base_url}, AccountStatus={account.status}",
            details="Confirmed strictly paper trading environment to protect actual capital." if passed_g1 else "NON-PAPER TRADING ATTEMPT DETECTED!"
        ))
        if not passed_g1:
            veto_reasons.append("Non-paper or inactive account target.")

        # GATE 2: Max Loss / Trade Sizing (Max 5% of $100k = $5,000)
        passed_g2 = trade.max_loss <= config.max_risk_per_trade
        audit_items.append(RiskAuditItem(
            gate_name="GATE_2_MAX_DEFINED_RISK",
            passed=passed_g2,
            limit_value=f"Max loss <= ${config.max_risk_per_trade:,.2f}",
            observed_value=f"Trade Max Loss = ${trade.max_loss:,.2f}",
            details="Defined risk fits within 5% per-trade allocation budget." if passed_g2 else "Proposed trade exceeds max risk limit!"
        ))
        if not passed_g2:
            veto_reasons.append(f"Max risk ${trade.max_loss:,.2f} exceeds limit of ${config.max_risk_per_trade:,.2f}")

        # GATE 3: Buying Power Gate
        passed_g3 = trade.max_loss <= buying_power
        audit_items.append(RiskAuditItem(
            gate_name="GATE_3_BUYING_POWER_COLLATERAL",
            passed=passed_g3,
            limit_value=f"Required collateral <= Buying Power (${buying_power:,.2f})",
            observed_value=f"Required = ${trade.max_loss:,.2f}",
            details="Account has sufficient margin/buying power." if passed_g3 else "Insufficient buying power!"
        ))
        if not passed_g3:
            veto_reasons.append(f"Required margin ${trade.max_loss:,.2f} exceeds buying power ${buying_power:,.2f}")

        # GATE 4: Defined-Risk Enforcement (Strictly forbids naked options)
        has_protection = trade.long_leg is not None
        defined_risk_valid = False
        if has_protection:
            if trade.short_leg.option_type == "put":
                defined_risk_valid = trade.long_leg.strike < trade.short_leg.strike
            else:
                defined_risk_valid = trade.long_leg.strike > trade.short_leg.strike
        
        passed_g4 = has_protection and defined_risk_valid
        audit_items.append(RiskAuditItem(
            gate_name="GATE_4_DEFINED_RISK_PROTECTION",
            passed=passed_g4,
            limit_value="Protective long leg mandatory (Naked options strictly prohibited)",
            observed_value=f"LongLegPresent={has_protection}, StrikeRelationValid={defined_risk_valid}",
            details="Trade is defined-risk spread with guaranteed capped downside." if passed_g4 else "VETO: Naked options or inverted protective strikes detected!"
        ))
        if not passed_g4:
            veto_reasons.append("Trade lacks required protective long wing (naked risk violation).")

        # GATE 5: Expiration / DTE Bounds (Min 7 DTE, Max 45 DTE)
        passed_g5 = config.min_dte <= trade.dte <= config.max_dte
        audit_items.append(RiskAuditItem(
            gate_name="GATE_5_DTE_SAFETY_WINDOW",
            passed=passed_g5,
            limit_value=f"{config.min_dte} <= DTE <= {config.max_dte} days",
            observed_value=f"DTE = {trade.dte} days",
            details="DTE is optimal for steady theta decay without 0-6 DTE extreme gamma risk." if passed_g5 else "DTE outside safety envelope!"
        ))
        if not passed_g5:
            veto_reasons.append(f"DTE {trade.dte} outside allowed range [{config.min_dte}, {config.max_dte}]")

        # GATE 6: Delta Boundary (Probability of Profit Filter)
        short_delta_abs = abs(trade.short_leg.delta)
        passed_g6 = config.target_short_delta_min <= short_delta_abs <= config.target_short_delta_max
        audit_items.append(RiskAuditItem(
            gate_name="GATE_6_PROBABILITY_DELTA_BOUNDS",
            passed=passed_g6,
            limit_value=f"{config.target_short_delta_min} <= |Delta| <= {config.target_short_delta_max}",
            observed_value=f"|Delta| = {short_delta_abs:.4f}",
            details="Option is sufficiently out-of-the-money (65%-85% probability of profit)." if passed_g6 else "Delta outside probability band!"
        ))
        if not passed_g6:
            veto_reasons.append(f"Short leg delta {short_delta_abs:.4f} outside bounds [{config.target_short_delta_min}, {config.target_short_delta_max}]")

        # GATE 7: Liquidity & Bid-Ask Slippage
        s_spread_pct = (trade.short_leg.ask - trade.short_leg.bid) / max(0.01, trade.short_leg.mid)
        l_spread_pct = (trade.long_leg.ask - trade.long_leg.bid) / max(0.01, trade.long_leg.mid) if trade.long_leg else 0.0
        max_observed_spread = max(s_spread_pct, l_spread_pct)
        passed_g7 = max_observed_spread <= config.max_bid_ask_spread_pct
        audit_items.append(RiskAuditItem(
            gate_name="GATE_7_LIQUIDITY_AND_SLIPPAGE",
            passed=passed_g7,
            limit_value=f"Max bid-ask spread <= {config.max_bid_ask_spread_pct * 100:.1f}%",
            observed_value=f"Max spread = {max_observed_spread * 100:.1f}% (Short: {s_spread_pct*100:.1f}%, Long: {l_spread_pct*100:.1f}%)",
            details="Option chain has tight liquidity to prevent predatory market-maker slippage." if passed_g7 else "Excessive bid-ask spread!"
        ))
        if not passed_g7:
            veto_reasons.append(f"Bid-ask spread {max_observed_spread*100:.1f}% exceeds slippage ceiling {config.max_bid_ask_spread_pct*100:.1f}%")

        # GATE 8: Portfolio Concentration Limit (Max 5 open positions)
        positions = self.trading_client.get_all_positions()
        passed_g8 = len(positions) < config.max_open_positions
        audit_items.append(RiskAuditItem(
            gate_name="GATE_8_PORTFOLIO_CONCENTRATION",
            passed=passed_g8,
            limit_value=f"Active positions < {config.max_open_positions}",
            observed_value=f"Current open positions = {len(positions)}",
            details="Portfolio capacity available." if passed_g8 else "Portfolio at max capacity limit!"
        ))
        if not passed_g8:
            veto_reasons.append(f"Open positions count {len(positions)} at or above max limit {config.max_open_positions}")

        # Final Decision Assembly
        total_checks = len(audit_items)
        passed_checks = sum(1 for item in audit_items if item.passed)
        is_approved = (len(veto_reasons) == 0)

        report = ComplianceAuditReport(
            trade_id=trade.trade_id,
            approved=is_approved,
            status="APPROVED" if is_approved else "VETOED",
            passed_checks=passed_checks,
            total_checks=total_checks,
            audit_trail=audit_items,
            veto_reasons=veto_reasons
        )

        return report

    def verify_or_veto(self, trade: ProposedTrade) -> ComplianceAuditReport:
        """
        Executes audit and raises ComplianceVetoException if any risk gate fails.
        """
        report = self.audit_trade(trade)
        if not report.approved:
            raise ComplianceVetoException(report)
        return report

if __name__ == "__main__":
    from alpha_agent import OptionsAlphaAgent, OptionLegInfo
    print("Testing Deterministic Compliance Shield...")
    shield = ComplianceShield()
    agent = OptionsAlphaAgent()

    # 1. Propose real trade
    real_trade = agent.evaluate_and_propose_trade("SPY")
    print(f"\n--- AUDITING REAL COMPLIANT TRADE: {real_trade.trade_id} ---")
    rep = shield.audit_trade(real_trade)
    print(f"Status: {rep.status} ({rep.passed_checks}/{rep.total_checks} gates passed)")
    for item in rep.audit_trail:
        mark = "[PASS]" if item.passed else "[FAIL]"
        print(f"  {mark} {item.gate_name}: {item.observed_value}")

    # 2. Test intentional naked option violation to verify VETO mechanism
    print("\n--- AUDITING INTENTIONALLY DANGEROUS TRADE (NAKED CALL, NO PROTECTION) ---")
    bad_trade = real_trade.model_copy(deep=True)
    bad_trade.trade_id = "VETO-TEST-NAKED-CALL"
    bad_trade.long_leg = None  # Strip protection!
    bad_trade.max_loss = 50_000.0  # Excessive risk!
    
    try:
        shield.verify_or_veto(bad_trade)
        print("[CRITICAL BUG] Veto failed to trigger!")
    except ComplianceVetoException as e:
        print(f"[SUCCESS] Compliance Shield VETO triggered correctly!")
        print(f"  Error message: {e}")
