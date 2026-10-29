from django.test import SimpleTestCase

from market_data.services.security_resolver import SecurityResolver


class SecurityResolverHardeningTests(SimpleTestCase):
    def setUp(self):
        self.original = {
            "loaded": SecurityResolver._security_master_loaded,
            "isin": SecurityResolver._security_master_isin,
            "name": SecurityResolver._security_master_name,
            "name_to_isin": SecurityResolver._security_master_name_to_isin,
            "isin_to_name": SecurityResolver._security_master_isin_to_name,
            "ambiguous_isin_to_name": SecurityResolver._security_master_ambiguous_isin_to_name,
            "ambiguous_isin": SecurityResolver._security_master_ambiguous_isin,
            "ambiguous_name": SecurityResolver._security_master_ambiguous_name,
            "ambiguous_name_to_isin": SecurityResolver._security_master_ambiguous_name_to_isin,
        }
        SecurityResolver._security_master_loaded = True
        SecurityResolver._security_master_isin = {}
        SecurityResolver._security_master_name = {}
        SecurityResolver._security_master_name_to_isin = {}
        SecurityResolver._security_master_isin_to_name = {}
        SecurityResolver._security_master_ambiguous_isin_to_name = set()
        SecurityResolver._security_master_ambiguous_isin = set()
        SecurityResolver._security_master_ambiguous_name = set()
        SecurityResolver._security_master_ambiguous_name_to_isin = set()

    def tearDown(self):
        SecurityResolver._security_master_loaded = self.original["loaded"]
        SecurityResolver._security_master_isin = self.original["isin"]
        SecurityResolver._security_master_name = self.original["name"]
        SecurityResolver._security_master_name_to_isin = self.original["name_to_isin"]
        SecurityResolver._security_master_isin_to_name = self.original["isin_to_name"]
        SecurityResolver._security_master_ambiguous_isin_to_name = self.original["ambiguous_isin_to_name"]
        SecurityResolver._security_master_ambiguous_isin = self.original["ambiguous_isin"]
        SecurityResolver._security_master_ambiguous_name = self.original["ambiguous_name"]
        SecurityResolver._security_master_ambiguous_name_to_isin = self.original["ambiguous_name_to_isin"]

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

    def test_normalized_legal_name_variant_resolves_without_isin(self):
        SecurityResolver._security_master_name = {
            "ANUP ENGINEERING LIMITED": "ANUP.NS",
        }

        self.assertEqual(
            SecurityResolver.resolve_from_name("ANUP ENGINEERING LTD"),
            "ANUP.NS",
        )
        self.assertEqual(
            SecurityResolver.resolve_from_name("The Anup Engineering Limited"),
            "ANUP.NS",
        )
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(name="ANUP ENGINEERING LTD"),
            "ANUP.NS",
        )

    def test_colliding_normalized_names_are_quarantined(self):
        mapping = {}
        ambiguous = set()
        key = SecurityResolver.normalize_security_name("The Anup Engineering Limited")

        SecurityResolver._record_master_mapping(
            mapping=mapping, ambiguous=ambiguous, key=key,
            value="ANUP.NS", identifier_type="security name",
        )
        SecurityResolver._record_master_mapping(
            mapping=mapping, ambiguous=ambiguous,
            key=SecurityResolver.normalize_security_name("ANUP ENGINEERING LTD"),
            value="OTHER.NS", identifier_type="security name",
        )

        self.assertNotIn(key, mapping)
        self.assertIn(key, ambiguous)
        SecurityResolver._security_master_name = mapping
        SecurityResolver._security_master_ambiguous_name = ambiguous
        self.assertIsNone(SecurityResolver.resolve_from_name("ANUP ENGINEERING LTD"))


    def test_abb_legal_name_and_punctuation_variants_normalize_identically(self):
        canonical = SecurityResolver.normalize_security_name("ABB India Limited")
        for variant in ("ABB India Ltd.", "ABB India Ltd", "ABB INDIA LIMITED", "ABB INDIA LTD.", "abb india limited"):
            with self.subTest(variant=variant):
                self.assertEqual(SecurityResolver.normalize_security_name(variant), canonical)

    def test_security_master_identity_resolves_by_name_without_uploaded_isin(self):
        SecurityResolver._security_master_name_to_isin = {"ABB INDIA LIMITED": "INE117A01022"}
        SecurityResolver._security_master_isin_to_name = {"INE117A01022": "ABB India Limited"}
        SecurityResolver._security_master_isin = {"INE117A01022": "ABB.NS"}
        result = SecurityResolver.resolve_security_from_master(name="ABB India Ltd.")
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["canonical_name"], "ABB India Limited")
        self.assertEqual(result["isin"], "INE117A01022")
        self.assertEqual(result["yahoo_symbol"], "ABB.NS")

    def test_security_master_identity_resolves_by_isin_without_uploaded_name(self):
        SecurityResolver._security_master_isin_to_name = {"INE117A01022": "ABB India Limited"}
        SecurityResolver._security_master_isin = {"INE117A01022": "ABB.NS"}
        result = SecurityResolver.resolve_security_from_master(isin="INE117A01022")
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["canonical_name"], "ABB India Limited")
        self.assertEqual(result["isin"], "INE117A01022")

    def test_security_master_identity_rejects_conflicting_name_and_isin(self):
        SecurityResolver._security_master_name_to_isin = {"ABB INDIA LIMITED": "INE117A01022"}
        result = SecurityResolver.resolve_security_from_master(name="ABB India Ltd.", isin="INE999Z01011")
        self.assertEqual(result["status"], "CONFLICT")

    def test_candidate_generation_fails_closed_for_ambiguous_isin(self):
        SecurityResolver._security_master_ambiguous_isin = {"INE000000001"}

        with self.assertRaisesRegex(ValueError, "conflicting Security Master mappings"):
            SecurityResolver.candidate_symbols(
                isin="INE000000001", symbol="SAMPLE",
            )

    def test_isin_and_exchange_conflict_is_rejected(self):
        SecurityResolver._security_master_isin = {
            "INE000000001": "SAMPLE.NS",
        }

        with self.assertRaisesRegex(ValueError, "supplied symbol/exchange"):
            SecurityResolver.resolve_yahoo_symbol(
                isin="INE000000001", symbol="SAMPLE", exchange="BSE",
            )


class SecurityMasterNameToIsinTests(SimpleTestCase):
    def setUp(self):
        from market_data.services.security_resolver import SecurityResolver
        self.resolver = SecurityResolver
        self.original = (SecurityResolver._security_master_name_to_isin, SecurityResolver._security_master_ambiguous_name_to_isin, SecurityResolver._security_master_loaded)
        SecurityResolver._security_master_name_to_isin = {}
        SecurityResolver._security_master_ambiguous_name_to_isin = set()
        SecurityResolver._security_master_loaded = True

    def tearDown(self):
        (self.resolver._security_master_name_to_isin, self.resolver._security_master_ambiguous_name_to_isin, self.resolver._security_master_loaded) = self.original

    def test_name_lookup_returns_isin_for_legal_name_variant(self):
        self.resolver._security_master_name_to_isin["ANUP ENGINEERING LIMITED"] = "INE000ANUP001"
        self.assertEqual(self.resolver.resolve_isin_from_name("ANUP ENGINEERING LTD"), "INE000ANUP001")

    def test_name_lookup_fails_closed_for_ambiguous_name(self):
        self.resolver._security_master_name_to_isin["ANUP ENGINEERING LIMITED"] = "INE000ANUP001"
        self.resolver._security_master_ambiguous_name_to_isin.add("ANUP ENGINEERING LIMITED")
        self.assertEqual(self.resolver.resolve_isin_from_name("ANUP ENGINEERING LTD"), "")
    def test_name_without_ltd_matches_security_master_canonical_name(self):
        canonical_name = "Ellenbarrie Industrial Gases Ltd"
        normalized_name = SecurityResolver.normalize_security_name(canonical_name)
        self.assertEqual(
            normalized_name,
            SecurityResolver.normalize_security_name("Ellenbarrie Industrial Gases"),
        )
        self._security_master_name_to_isin[normalized_name] = "INE000A01000"
        self._security_master_isin_to_name["INE000A01000"] = canonical_name

        result = SecurityResolver.resolve_security_from_master(
            name="Ellenbarrie Industrial Gases",
        )

        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["canonical_name"], canonical_name)
        self.assertEqual(result["isin"], "INE000A01000")

