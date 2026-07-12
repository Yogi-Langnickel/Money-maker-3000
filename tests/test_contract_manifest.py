import json
import tempfile
import unittest
from pathlib import Path

from money_maker_3000.contract_manifest import (
    DEFAULT_MANIFEST_PATH,
    MANIFEST_SCHEMA_VERSION,
    build_dashboard_contract_manifest,
    check_dashboard_contract_manifest,
    render_dashboard_contract_manifest,
    write_dashboard_contract_manifest,
)


class ContractManifestTests(unittest.TestCase):
    def test_committed_manifest_matches_canonical_contract(self):
        self.assertTrue(check_dashboard_contract_manifest(DEFAULT_MANIFEST_PATH))
        parsed = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(parsed, build_dashboard_contract_manifest())

    def test_manifest_exposes_redacted_backtest_only_dashboard_contract(self):
        manifest = build_dashboard_contract_manifest()
        serialized = json.dumps(manifest).lower()

        self.assertEqual(manifest["schemaVersion"], MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["runModes"], ["backtest"])
        self.assertEqual(
            manifest["strategyIds"],
            [
                "dca-cash-reserve",
                "threshold-rebalance",
                "volatility-band-accumulator",
                "slow-trend-allocation",
                "news-aware-watchlist",
            ],
        )
        self.assertEqual(manifest["disabledRunModes"], ["execute", "trade", "trading"])
        self.assertEqual(manifest["safety"]["providerCalls"], "blocked")
        self.assertEqual(manifest["safety"]["credentials"], "absent")
        self.assertEqual(manifest["safety"]["demoExecution"], "blocked")
        self.assertEqual(manifest["safety"]["liveExecution"], "blocked")
        self.assertEqual(
            manifest["strategyRules"]["slow-trend-allocation"]["parameterSchema"]["longLookbackDays"]["default"],
            200,
        )
        for forbidden in ("apikey", "userkey", "accountid", "positionid", "orderid", "oauthtoken"):
            self.assertNotIn(forbidden, serialized)

    def test_manifest_check_detects_missing_or_modified_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            self.assertFalse(check_dashboard_contract_manifest(path))
            write_dashboard_contract_manifest(path)
            self.assertEqual(path.read_text(encoding="utf-8"), render_dashboard_contract_manifest())
            self.assertTrue(check_dashboard_contract_manifest(path))
            path.write_text("{}\n", encoding="utf-8")
            self.assertFalse(check_dashboard_contract_manifest(path))


if __name__ == "__main__":
    unittest.main()
