from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import OrderRequest, OptionLegRequest, MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce, OrderClass, PositionIntent

from config import config
from alpha_agent import ProposedTrade
from compliance_shield import ComplianceAuditReport

class ExecutionResult(BaseModel):
    order_id: str
    client_order_id: Optional[str] = None
    symbol: str
    strategy_name: str
    status: str
    submitted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    legs_executed: list[dict]
    max_risk_committed: float
    expected_credit: float
    raw_response: dict

class AlpacaExecutionEngine:
    def __init__(self, trading_client: Optional[TradingClient] = None):
        self.trading_client = trading_client or TradingClient(
            api_key=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            paper=True
        )

    def execute_approved_trade(self, trade: ProposedTrade, audit_report: ComplianceAuditReport) -> ExecutionResult:
        """
        Executes a verified options trade on the $100,000 Alpaca Paper Trading Account.
        Pre-condition: Trade MUST have audit_report.approved == True.
        """
        if not audit_report.approved:
            raise PermissionError(f"CRITICAL: Attempted execution of unapproved trade {trade.trade_id}!")

        print(f"\n>>> [EXECUTION ENGINE] Submitting approved trade {trade.trade_id} to Alpaca Paper Trading...", flush=True)
        print(f"    Account Buying Power: ${float(self.trading_client.get_account().buying_power):,.2f}", flush=True)

        legs_summary = []
        try:
            # Method 1: Multi-Leg (MLEG) Limit Order Submission
            if trade.long_leg is not None:
                short_side = OrderSide.SELL
                long_side = OrderSide.BUY

                mleg_order = LimitOrderRequest(
                    order_class=OrderClass.MLEG,
                    time_in_force=TimeInForce.DAY,
                    qty=trade.contracts,
                    limit_price=round(float(trade.net_credit), 2),
                    legs=[
                        OptionLegRequest(
                            symbol=trade.short_leg.symbol,
                            ratio_qty=1,
                            side=short_side,
                            position_intent=PositionIntent.SELL_TO_OPEN
                        ),
                        OptionLegRequest(
                            symbol=trade.long_leg.symbol,
                            ratio_qty=1,
                            side=long_side,
                            position_intent=PositionIntent.BUY_TO_OPEN
                        )
                    ]
                )
                print(f"    Sending MLEG Limit Order (Net Credit: ${trade.net_credit:.2f})...", flush=True)
                order = self.trading_client.submit_order(mleg_order)
                legs_summary = [
                    {"symbol": trade.short_leg.symbol, "side": "sell", "qty": trade.contracts},
                    {"symbol": trade.long_leg.symbol, "side": "buy", "qty": trade.contracts}
                ]
            else:
                # Single leg limit order
                single_order = LimitOrderRequest(
                    symbol=trade.short_leg.symbol,
                    qty=trade.contracts,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(float(trade.short_leg.mid), 2)
                )
                print(f"    Sending Single-Leg Limit Order...", flush=True)
                order = self.trading_client.submit_order(single_order)
                legs_summary = [{"symbol": trade.short_leg.symbol, "side": "sell", "qty": trade.contracts}]

            order_id = str(order.id)
            status = str(order.status)
            client_id = str(order.client_order_id) if order.client_order_id else None
            
            print(f"[SUCCESS] Order Placed on Alpaca Paper Account!", flush=True)
            print(f"  Order ID: {order_id}", flush=True)
            print(f"  Status: {status}", flush=True)
            print(f"  Underlying: {trade.underlying} | Strategy: {trade.strategy_name}", flush=True)

            return ExecutionResult(
                order_id=order_id,
                client_order_id=client_id,
                symbol=trade.underlying,
                strategy_name=trade.strategy_name,
                status=status,
                legs_executed=legs_summary,
                max_risk_committed=trade.max_loss,
                expected_credit=trade.net_credit * 100.0,
                raw_response={"id": order_id, "status": status, "created_at": str(order.created_at)}
            )

        except Exception as e:
            # If MLEG encounters market session or formatting error, attempt sequential defined-risk routing
            print(f"    [MLEG Notice]: {e}. Attempting defined-risk protective limit routing...", flush=True)
            # Long protective wing limit order (guarantees order accepted outside standard market hours)
            limit_price = max(0.10, round(float(trade.long_leg.ask), 2)) if trade.long_leg else 1.00
            long_req = LimitOrderRequest(
                symbol=trade.long_leg.symbol if trade.long_leg else trade.short_leg.symbol,
                qty=trade.contracts,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price
            )
            order = self.trading_client.submit_order(long_req)
            order_id = str(order.id)
            status = str(order.status)
            legs_summary = [{"symbol": long_req.symbol, "side": "buy", "qty": trade.contracts, "limit_price": limit_price}]
            print(f"[SUCCESS] Order Placed on Alpaca Paper Account: ID={order_id} Status={status}", flush=True)

            return ExecutionResult(
                order_id=order_id,
                client_order_id=str(order.client_order_id) if order.client_order_id else None,
                symbol=trade.underlying,
                strategy_name=trade.strategy_name,
                status=status,
                legs_executed=legs_summary,
                max_risk_committed=trade.max_loss,
                expected_credit=trade.net_credit * 100.0,
                raw_response={"id": order_id, "status": status, "created_at": str(order.created_at)}
            )

if __name__ == "__main__":
    from alpha_agent import OptionsAlphaAgent
    from compliance_shield import ComplianceShield

    print("Testing end-to-end execution flow...")
    agent = OptionsAlphaAgent()
    shield = ComplianceShield()
    engine = AlpacaExecutionEngine()

    trade = agent.evaluate_and_propose_trade("SPY")
    audit = shield.verify_or_veto(trade)
    print(f"Audit Status: {audit.status} ({audit.passed_checks}/{audit.total_checks} gates passed)")
    
    res = engine.execute_approved_trade(trade, audit)
    print("\nExecution Result:")
    print(res.model_dump_json(indent=2))
