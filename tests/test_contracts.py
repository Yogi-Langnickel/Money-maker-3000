import json
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
    validate_strategy_parameters,
)
from money_maker_3000.providers import DisabledExecutionGateway, build_provider_metadata_snapshot
from money_maker_3000.strategies import STRATEGY_REGISTRY, validate_strategy_registry


class ContractTests(unittest.TestCase):
    def test_strategy_registry_is_predefined_and_simulation_safe(self):
        result = validate_strategy_registry(STRATEGY_REGISTRY)

        self.assertTrue(result.ok)
        self.assertEqual(len(STRATEGY_REGISTRY), 5)
        strategy_ids = {strategy["strategyId"] for strategy in STRATEGY_REGISTRY}
        self.assertIn("news-aware-watchlist", strategy_ids)
        self.assertIn("volatility-band-accumulator", strategy_ids)
        self.assertIn("slow-trend-allocation", strategy_ids)
        self.assertTrue(all(strategy["status"] != "live" for strategy in STRATEGY_REGISTRY))
        self.assertTrue(all(strategy["parameterSchema"] for strategy in STRATEGY_REGISTRY))
        self.assertIn("strategyParameters", default_simulation_config_for_strategy("dca-cash-reserve"))
        self.assertIn("parameterSchema", SIMULATION_CONFIG_CONTRACT["strategyRules"]["dca-cash-reserve"])

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

    def test_strategy_registry_requires_predefined_parameter_schema(self):
        unsafe_strategy = {**STRATEGY_REGISTRY[0], "parameterSchema": {"script": {"type": "string"}}}
        result = validate_strategy_registry([unsafe_strategy])

        self.assertFalse(result.ok)
        self.assertIn("parameter schema must match the predefined contract", " ".join(result.errors))

    def test_strategy_registry_rejects_schema_value_mutations(self):
        unsafe_schema = {
            **STRATEGY_REGISTRY[0]["parameterSchema"],
            "fixedOrderUsd": {
                **STRATEGY_REGISTRY[0]["parameterSchema"]["fixedOrderUsd"],
                "maximum": 10_000.0,
            },
        }
        unsafe_strategy = {**STRATEGY_REGISTRY[0], "parameterSchema": unsafe_schema}
        result = validate_strategy_registry([unsafe_strategy])

        self.assertFalse(result.ok)
        self.assertIn("parameter schema must match the predefined contract", " ".join(result.errors))

    def test_strategy_parameter_schema_rejects_unknown_and_unsafe_values(self):
        result = validate_strategy_parameters(
            "slow-trend-allocation",
            {
                "shortLookbackDays": 250,
                "longLookbackDays": 100,
                "confirmationBars": 6,
                "orderFractionPct": 0.5,
                "maxOrderUsd": 300,
                "providerUrl": "https://broker.example.test",
            },
        )

        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("unsupported strategy parameters", joined)
        self.assertIn("shortLookbackDays must be between", joined)
        self.assertIn("confirmationBars must be between", joined)
        self.assertIn("orderFractionPct must be between", joined)
        self.assertIn("maxOrderUsd must be between", joined)

    def test_weight_parameter_schema_requires_normalized_synthetic_weights(self):
        result = validate_strategy_parameters(
            "threshold-rebalance",
            {
                "targetWeights": {"SPY": 0.8, "bad symbol": 0.4},
                "rebalanceThresholdPct": 5.0,
                "maxOrderUsd": 250.0,
                "minCashReserveUsd": 100.0,
                "maxOpenPositions": 3,
            },
        )

        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("uppercase market symbols", joined)
        self.assertIn("weights must sum to 1.0", joined)

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
        self.assertEqual(SIMULATION_CONFIG_CONTRACT["runModes"], ["backtest"])
        self.assertEqual(SIMULATION_CONFIG_CONTRACT["allowedRunModes"], ["backtest"])
        self.assertEqual(
            sorted(SIMULATION_CONFIG_CONTRACT["disabledRunModes"]),
            ["execute", "trade", "trading"],
        )
        self.assertTrue(SIMULATION_CONFIG_CONTRACT["runModePolicy"]["backtest"]["enabled"])
        for mode in ("execute", "trade", "trading"):
            self.assertFalse(SIMULATION_CONFIG_CONTRACT["runModePolicy"][mode]["enabled"])
            self.assertEqual(SIMULATION_CONFIG_CONTRACT["runModePolicy"][mode]["providerCalls"], "blocked")

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

    def test_simulation_config_rejects_unknown_strategy_parameters(self):
        config = default_simulation_config_for_strategy("dca-cash-reserve")
        config["strategyParameters"]["executionRoute"] = "demo"
        result = validate_simulation_config(config)

        self.assertFalse(result.ok)
        self.assertIn("unsupported strategy parameters", " ".join(result.errors))

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
        self.assertIn("volatility-band-accumulator", policy["strategyAllocationIds"])
        self.assertIn("slow-trend-allocation", policy["strategyAllocationIds"])
        self.assertEqual(DEFAULT_ALLOCATION_POLICY["accountBalancePersistence"], "redacted")

    def test_provider_metadata_and_gateway_block_all_execution(self):
        snapshot = build_provider_metadata_snapshot()
        self.assertEqual(snapshot["providerCalls"], "blocked")
        self.assertEqual(snapshot["executionRoutes"], "absent")
        self.assertEqual(snapshot["credentials"], "not-loaded")
        self.assertEqual(snapshot["safetyPosture"]["credentialLoading"], "blocked")
        self.assertEqual(snapshot["safetyPosture"]["privateAccountData"], "absent")
        self.assertEqual(snapshot["safetyPosture"]["rawPayloadPersistence"], "blocked")
        self.assertEqual(snapshot["safetyPosture"]["portfolioBalanceUse"], "blocked-for-sizing")
        self.assertEqual(snapshot["safetyPosture"]["orderMutation"], "blocked")
        self.assertEqual(snapshot["safetyPosture"]["networkAccess"], "absent")
        self.assertEqual(snapshot["providers"][0]["safetyPosture"], snapshot["safetyPosture"])
        self.assertTrue(snapshot["validation"]["ok"])

        gateway = DisabledExecutionGateway()
        with self.assertRaises(PermissionError):
            gateway.preview_order({"symbol": "SPY"})
        with self.assertRaises(PermissionError):
            gateway.submit_order({"symbol": "SPY"})

    def test_provider_metadata_snapshot_never_echoes_unsafe_caller_values(self):
        snapshot = build_provider_metadata_snapshot(
            [
                {
                    "providerId": "acct-real-123",
                    "displayName": "operator@example.test",
                    "status": "ready",
                    "providerCalls": "enabled",
                    "credentials": {"apiKey": "api-secret-abcdef12"},
                    "accountData": {"accountId": "acct-real-123"},
                    "marketData": {"token": "token-secret-abcdef12"},
                    "orderPreview": {"orderId": "order-real-123"},
                    "demoExecution": "enabled",
                    "liveExecution": "enabled",
                    "supportedModes": ["simulation", "trading"],
                    "safetyPosture": {"privateAccountData": {"accountId": "acct-real-123"}},
                    "capabilities": {"portfolioRead": {"OAuthToken": "oauth-secret-abcdef12"}},
                }
            ]
        )
        serialized = json.dumps(snapshot, sort_keys=True)

        self.assertFalse(snapshot["validation"]["ok"])
        self.assertEqual(snapshot["providers"][0]["providerId"], "unknown")
        self.assertEqual(snapshot["providers"][0]["displayName"], "Unknown provider")
        self.assertEqual(snapshot["providers"][0]["credentials"], "not-loaded")
        self.assertEqual(snapshot["providers"][0]["accountData"], "absent")
        self.assertEqual(snapshot["providers"][0]["safetyPosture"], snapshot["safetyPosture"])
        self.assertNotIn("acct-real-123", serialized)
        self.assertNotIn("operator@example.test", serialized)
        self.assertNotIn("api-secret-abcdef12", serialized)
        self.assertNotIn("token-secret-abcdef12", serialized)
        self.assertNotIn("order-real-123", serialized)
        self.assertNotIn("oauth-secret-abcdef12", serialized)


if __name__ == "__main__":
    unittest.main()
