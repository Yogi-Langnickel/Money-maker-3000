import json
import shutil
import tempfile
import unittest
from pathlib import Path

from money_maker_3000.fixture_provenance import (
    DEFAULT_FIXTURE_ROOT,
    DEFAULT_MANIFEST_PATH,
    FIXTURE_PROVENANCE_ENTRIES,
    MANIFEST_SCHEMA_VERSION,
    build_fixture_provenance_manifest,
    check_fixture_provenance_manifest,
    render_fixture_provenance_manifest,
    validate_fixture_provenance_inventory,
    write_fixture_provenance_manifest,
)


class FixtureProvenanceTests(unittest.TestCase):
    def test_committed_manifest_and_every_csv_match_canonical_inventory(self):
        self.assertEqual(validate_fixture_provenance_inventory(), [])
        self.assertTrue(check_fixture_provenance_manifest())
        parsed = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(parsed, build_fixture_provenance_manifest())

    def test_manifest_is_offline_only_and_classifies_current_fixtures_as_synthetic(self):
        manifest = build_fixture_provenance_manifest()
        serialized = json.dumps(manifest).lower()

        self.assertEqual(manifest["schemaVersion"], MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["scope"], "offline-test-fixtures-only")
        self.assertEqual(manifest["providerCalls"], "blocked")
        self.assertEqual(manifest["credentials"], "absent")
        self.assertEqual(manifest["accountData"], "absent")
        self.assertEqual(manifest["executionRoutes"], "absent")
        self.assertTrue(all(not entry["observedMarketData"] for entry in manifest["entries"]))
        self.assertTrue(all(entry["classification"] == "synthetic-contract" for entry in manifest["entries"]))
        self.assertTrue(all(entry["redistributionApproved"] for entry in manifest["entries"]))
        for forbidden in ("apikey", "userkey", "accountid", "positionid", "orderid", "oauthtoken"):
            self.assertNotIn(forbidden, serialized)

    def test_check_detects_modified_fixture_and_does_not_rewrite_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "market_history"
            shutil.copytree(DEFAULT_FIXTURE_ROOT, root)
            artifact = Path(temp_dir) / "fixture-provenance.json"
            artifact.write_text(render_fixture_provenance_manifest(), encoding="utf-8")
            fixture = root / "spy-daily.csv"
            original = fixture.read_text(encoding="utf-8")
            fixture.write_text(original.replace("522.50", "522.51"), encoding="utf-8")
            modified = fixture.read_text(encoding="utf-8")

            self.assertFalse(check_fixture_provenance_manifest(artifact, root))
            self.assertEqual(fixture.read_text(encoding="utf-8"), modified)
            self.assertIn("SHA-256 drift detected", " ".join(validate_fixture_provenance_inventory(root)))

    def test_check_rejects_unlisted_fixture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "market_history"
            shutil.copytree(DEFAULT_FIXTURE_ROOT, root)
            extra = root / "unreviewed.csv"
            extra.write_text((root / "spy-daily.csv").read_text(encoding="utf-8"), encoding="utf-8")

            errors = validate_fixture_provenance_inventory(root)

        self.assertIn("fixture provenance inventory does not exactly cover committed CSV files", errors)

    def test_inventory_rejects_extra_or_inconsistent_metadata(self):
        entries = [dict(entry) for entry in FIXTURE_PROVENANCE_ENTRIES]
        entries[0]["accountId"] = "sensitive-value"
        errors = validate_fixture_provenance_inventory(DEFAULT_FIXTURE_ROOT, entries)
        self.assertIn("fixture provenance entry 1 has invalid fields", errors)

        entries = [dict(entry) for entry in FIXTURE_PROVENANCE_ENTRIES]
        entries[0]["classification"] = "observed-licensed"
        entries[0]["observedMarketData"] = True
        errors = validate_fixture_provenance_inventory(DEFAULT_FIXTURE_ROOT, entries)
        self.assertIn("fixture provenance entry 1 observed classification metadata is invalid", errors)

        entries = [dict(entry) for entry in FIXTURE_PROVENANCE_ENTRIES]
        entries[0].update(
            {
                "classification": "observed-licensed",
                "dataOrigin": "observed-market",
                "observedMarketData": True,
                "source": "licensed-data-source",
                "sourceUrl": "https://data.example.test/dataset",
                "license": "CC BY 4.0",
                "licenseUrl": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "api-secret-abcdef12",
            }
        )
        errors = validate_fixture_provenance_inventory(DEFAULT_FIXTURE_ROOT, entries)
        self.assertIn("fixture provenance entry 1 contains sensitive metadata", errors)

        entries = [dict(entry) for entry in FIXTURE_PROVENANCE_ENTRIES]
        entries[0]["classification"] = []
        errors = validate_fixture_provenance_inventory(DEFAULT_FIXTURE_ROOT, entries)
        self.assertIn("fixture provenance entry 1 metadata types are invalid", errors)

    def test_inventory_is_immutable_and_parser_errors_do_not_echo_unsafe_content(self):
        with self.assertRaises(TypeError):
            FIXTURE_PROVENANCE_ENTRIES[0]["classification"] = "observed-licensed"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "market_history"
            shutil.copytree(DEFAULT_FIXTURE_ROOT, root)
            fixture = root / "spy-daily.csv"
            fixture.write_text(
                fixture.read_text(encoding="utf-8").replace("SPY,", "SECRET_TOKEN,"),
                encoding="utf-8",
            )
            errors = validate_fixture_provenance_inventory(root)
            serialized = " ".join(errors)

        self.assertIn("invalid market-history CSV", serialized)
        self.assertNotIn("SECRET_TOKEN", serialized)

    def test_write_refuses_drift_and_check_detects_missing_or_modified_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "market_history"
            shutil.copytree(DEFAULT_FIXTURE_ROOT, root)
            artifact = Path(temp_dir) / "fixture-provenance.json"
            self.assertFalse(check_fixture_provenance_manifest(artifact, root))
            write_fixture_provenance_manifest(artifact, root)
            self.assertTrue(check_fixture_provenance_manifest(artifact, root))
            artifact.write_text("{}\n", encoding="utf-8")
            self.assertFalse(check_fixture_provenance_manifest(artifact, root))

            (root / "spy-daily.csv").write_text("corrupted\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "drift"):
                write_fixture_provenance_manifest(artifact, root)


if __name__ == "__main__":
    unittest.main()
