from django.test import SimpleTestCase

from market_data.services.security_resolver import SecurityResolver


class SecurityResolverHardeningTests(SimpleTestCase):
    def setUp(self):
        self.original = {
            "loaded": SecurityResolver._security_master_loaded,
            "isin": SecurityResolver._security_master_isin,
            "name": SecurityResolver._security_master_name,
            "ambiguous_isin": SecurityResolver._security_master_ambiguous_isin,
            "ambiguous_name": SecurityResolver._security_master_ambiguous_name,
        }
        SecurityResolver._security_master_loaded = True
        SecurityResolver._security_master_isin = {}
        SecurityResolver._security_master_name = {}
        SecurityResolver._security_master_ambiguous_isin = set()
        SecurityResolver._security_master_ambiguous_name = set()

    def tearDown(self):
        SecurityResolver._security_master_loaded = self.original["loaded"]
        SecurityResolver._security_master_isin = self.original["isin"]
        SecurityResolver._security_master_name = self.original["name"]
        SecurityResolver._security_master_ambiguous_isin = self.original["ambiguous_isin"]
        SecurityResolver._security_master_ambiguous_name = self.original["ambiguous_name"]

    def test_conflicting_master_isin_mappings_are_quarantined(self):
        mapping = {}
        ambiguous = set()

        SecurityResolver._record_master_mapping(
            mapping=mapping, ambiguous=ambiguous, key="INE000000001",
            value="FIRST.NS", identifier_type="ISIN",
        )
        SecurityResolver._record_master_mapping(
            mapping=mapping, ambiguous=ambiguous, key="INE000000001",
            value="SECOND.NS", identifier_type="ISIN",
        )

        self.assertNotIn("INE000000001", mapping)
        self.assertIn("INE000000001", ambiguous)

        SecurityResolver._record_master_mapping(
            mapping=mapping, ambiguous=ambiguous, key="INE000000001",
            value="THIRD.NS", identifier_type="ISIN",
        )
        self.assertNotIn("INE000000001", mapping)

    def test_identical_duplicate_mapping_is_not_marked_ambiguous(self):
        mapping = {}
        ambiguous = set()
        for _ in range(2):
            SecurityResolver._record_master_mapping(
                mapping=mapping, ambiguous=ambiguous, key="INE000000001",
                value="SAMPLE.NS", identifier_type="ISIN",
            )

        self.assertEqual(mapping["INE000000001"], "SAMPLE.NS")
        self.assertNotIn("INE000000001", ambiguous)

    def test_ambiguous_isin_fails_closed(self):
        SecurityResolver._security_master_ambiguous_isin = {"INE000000001"}

        with self.assertRaisesRegex(ValueError, "conflicting Security Master mappings"):
            SecurityResolver.resolve_yahoo_symbol(
                isin="INE000000001", symbol="SAMPLE",
            )

    def test_isin_and_explicit_symbol_conflict_is_rejected(self):
        SecurityResolver._security_master_isin = {
            "INE000000001": "FIRST.NS",
        }

        with self.assertRaisesRegex(ValueError, "Conflicting security identifiers"):
            SecurityResolver.resolve_yahoo_symbol(
                isin="INE000000001", symbol="SECOND",
            )

    def test_matching_isin_and_symbol_resolve(self):
        SecurityResolver._security_master_isin = {
            "INE000000001": "SAMPLE.NS",
        }

        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(
                isin="INE000000001", symbol="SAMPLE",
            ),
            "SAMPLE.NS",
        )

    def test_ambiguous_name_mapping_is_not_used(self):
        SecurityResolver._security_master_ambiguous_name = {"SAMPLE LIMITED"}
        self.assertIsNone(SecurityResolver.resolve_from_name("Sample Limited"))

    def test_candidate_generation_fails_closed_for_ambiguous_isin(self):
        SecurityResolver._security_master_ambiguous_isin = {"INE000000001"}

        with self.assertRaisesRegex(ValueError, "conflicting Security Master mappings"):
            SecurityResolver.candidate_symbols(
                isin="INE000000001", symbol="SAMPLE",
            )
