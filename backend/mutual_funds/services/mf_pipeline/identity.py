"""Stored identity bridge for AMFI feeds that do not carry an ISIN.

AMFI's TER, risk-parameter and tracking-error feeds name the *parent* scheme
(e.g. "HDFC Mid Cap Fund") and use their own IDs (NSDL scheme code, AMFI
risk SchemeID). AMFI's own scheme-data file lists every AMFI scheme code with
its ISINs and that same parent scheme name, so the bridge is:

    external id -> (exact normalised parent name in AMFI scheme data) -> AMFI codes

Rules:
* a stored MFSchemeIdentityMap row always wins (ACTIVE -> use, REJECTED -> block);
* a new bridge is created only on an exact normalised match that belongs to a
  single AMC; anything ambiguous or unknown is reported, never guessed;
* once stored, later loads join by the stored IDs, not by the name.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from mutual_funds.master_models import MFSchemeIdentityMap
from mutual_funds.models import AMFIMasterScheme

from .parsers import normalize_parent_name, parse_scheme_data_csv

logger = logging.getLogger(__name__)


class IdentityResolver:
    def __init__(self, scheme_data_rows):
        self.by_parent = defaultdict(set)
        for row in scheme_data_rows:
            key = normalize_parent_name(row.get("parent_name"))
            if key:
                self.by_parent[key].add((row.get("amc", ""), row["amfi_code"]))
        self.stats = defaultdict(int)
        self.problems = []

    @classmethod
    def from_scheme_data_text(cls, text):
        return cls(parse_scheme_data_csv(text))

    def _note(self, kind, source, external_id, name):
        self.stats[kind] += 1
        if len(self.problems) < 100:
            self.problems.append({"kind": kind, "source": source, "external_id": external_id, "name": name})

    def resolve(self, source, external_id, external_name):
        external_id = str(external_id or "").strip()

        stored = list(
            MFSchemeIdentityMap.objects.filter(source=source, external_id=external_id).select_related("scheme")
        )
        active = [row.scheme for row in stored if row.status == MFSchemeIdentityMap.STATUS_ACTIVE]
        if active or stored:
            self.stats["resolved_by_stored_map" if active else "rejected"] += 1
            return active

        entries = self.by_parent.get(normalize_parent_name(external_name), set())
        if not entries:
            self._note("unmatched", source, external_id, external_name)
            return []
        if len({amc for amc, _ in entries}) > 1:
            self._note("ambiguous", source, external_id, external_name)
            return []

        schemes = list(AMFIMasterScheme.objects.filter(scheme_code__in=[code for _, code in entries]))
        if not schemes:
            self._note("not_in_universe", source, external_id, external_name)
            return []
        MFSchemeIdentityMap.objects.bulk_create(
            [
                MFSchemeIdentityMap(
                    source=source, external_id=external_id, external_name=(external_name or "")[:300],
                    scheme=scheme, match_method=MFSchemeIdentityMap.METHOD_PARENT_NAME_EXACT,
                )
                for scheme in schemes
            ],
            ignore_conflicts=True,
        )
        self.stats["resolved_by_parent_name"] += 1
        return schemes
