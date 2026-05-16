import unittest

from money_maker_3000.contracts import (
    DEFAULT_ALLOCATION_POLICY,
    DEFAULT_SIMULATION_CONFIG,
    SIMULATION_CONFIG_CONTRACT,
    build_allocation_policy,
    default_simulation_config_for_strategy,
    validate_allocation_policy,
    validate_run_mode,
    validate_simulation_config,
)
from money_maker_3000.providers import DisabledExecutionGateway, build_provider_metadata_snapshot
from money_maker_3000.strategies import STRATEGY_REGISTRY, validate_strategy_registry


class ContractTests(unittest.TestCase):
    def test_strategy_registry_is_predefined_and_simulation_safe(self):
        result = validate_strategy_registry(STRATEGY_REGISTRY)

        self.assertTrue(result.ok)
        self.assertEqual(len(STRATEGY_REGISTRY), 3)
        self.assertIn("news-aware-watchlist", {strategy["strategyId"] for strategy in STRATEGY_REGISTRY})
        self.assertTrue(all(strategy["status"] != "live" for strategy in STRATEGY_REGISTRY))

    def test_strategy_registry_rejects_unsafe_entries(self):
        result = validate_strategy_registry(
            [
                STRATEGY_REGISTRY[0],
                {
                    **STRATEGY_REGISTRY[0],
                    "name": "Operator uploaded strategy",
                    "status": "live",
                    "cadence": "minute",
                    "allowedMarkets": ["US_EQUITIES", "FOREX", "CRYPTO"],
                    "allowedInstrumentClasses": ["EQUITY", "FOREX", "CFD"],
                    "expectedHoldingPeriod": "intraday scalping",
                },
            ]
        )

        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("unique", joined)
        self.assertIn("simulation-only or context-only", joined)
        self.assertIn("daily or weekly", joined)
        self.assertIn("unknown markets", joined)
        self.assertIn("unknown instrument classes", joined)
        self.assertIn("low-frequency", joined)

    def test_run_mode_policy_rejects_execution_and_trade_aliases(self):
        self.assertTrue(validate_run_mode("backtest").ok)
        for mode in ("execute", "trade", "trading"):
            result = validate_run_mode(mode)
            self.assertFalse(result.ok)
            self.assertIn("execution mode is disabled", result.errors[0])

    def test_simulation_config_validates_strategy_and_selected_instrument(self):
        result = validate_simulation_config(DEFAULT_SIMULATION_CONFIG)

        self.assertTrue(result.ok)
        self.assertEqual(SIMULATION_CONFIG_CONTRACT["source"], "Money-maker-3000/src/money_maker_3000/contracts.py")
        self.assertEqual(SIMULATION_CONFIG_CONTRACT["runModes"], ["backtest", "execute"])
        self.assertTrue(SIMULATION_CONFIG_CONTRACT["runModePolicy"]["backtest"]["enabled"])
        self.assertFalse(SIMULATION_CONFIG_CONTRACT["runModePolicy"]["execute"]["enabled"])

    def test_simulation_config_rejects_incompatible_instrument_and_budget_above_allocation(self):
        allocation = build_allocation_policy(bot_allocation_usd=1000.0, reserved_usd=100.0)
        config = default_simulation_config_for_strategy("dca-cash-reserve")
        config["selectedInstrument"] = {
            "symbol": "GLD",
            "market": "COMMODITIES",
            "instrumentClass": "COMMODITY",
        }
        config["budgetUsd"] = 1500.0
        result = validate_simulation_config(config, allocation_policy=allocation)

        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("budget cannot exceed the internal bot allocation", joined)
        self.assertIn("selected instrument market must be included", joined)
        self.assertIn("selected instrument class must be included", joined)
        self.assertIn("selected instrument market is not allowed", joined)

    def test_allocation_policy_separates_provider_demo_balance_from_bot_allocation(self):
        policy = build_allocation_policy(
            bot_allocation_usd=1000.0,
            reserved_usd=200.0,
            provider_demo_balance_usd=1_000_000.0,
            max_order_usd=250.0,
        )
        result = validate_allocation_policy(policy)

        self.assertTrue(result.ok)
        self.assertEqual(policy["botAllocationUsd"], 1000.0)
        self.assertEqual(policy["reservedUsd"], 200.0)
        self.assertEqual(policy["availableUsd"], 800.0)
        self.assertEqual(policy["maxOrderUsd"], 250.0)
        self.assertEqual(policy["providerDemoBalanceUsd"], 1_000_000.0)
        self.assertEqual(policy["providerBalanceUse"], "ignored-for-budget")
        self.assertEqual(DEFAULT_ALLOCATION_POLICY["accountBalancePersistence"], "redacted")

    def test_provider_metadata_and_gateway_block_all_execution(self):
        snapshot = build_provider_metadata_snapshot()
        self.assertEqual(snapshot["providerCalls"], "blocked")
        self.assertEqual(snapshot["executionRoutes"], "absent")
        self.assertEqual(snapshot["credentials"], "not-loaded")
        self.assertTrue(snapshot["validation"]["ok"])

        gateway = DisabledExecutionGateway()
        with self.assertRaises(PermissionError):
            gateway.preview_order({"symbol": "SPY"})
        with self.assertRaises(PermissionError):
            gateway.submit_order({"symbol": "SPY"})


if __name__ == "__main__":
    unittest.main()
