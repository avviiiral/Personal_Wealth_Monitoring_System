"""Match indianapi.in mutual fund records to stored watch-list products.

The provider's ``/mutual_funds`` response is nested by category, and its fund
names differ from AMFI scheme names (for example ``Aditya Birla SL Multi-Cap
Fund-Reg(G)`` versus ``Aditya Birla Sun Life Multi Cap Fund - Regular Plan -
Growth``). Matching therefore compares normalized word sets. A match needs a
Jaccard score of at least ``MIN_MATCH_SCORE`` and a single clear best record;
ties are reported as unmatched rather than guessed.
"""

from __future__ import annotations

import re

MIN_MATCH_SCORE = 0.75

_STOP_TOKENS = frozenset(
    {"fund", "funds", "plan", "option", "regular", "direct", "growth", "reg", "g", "scheme", "the", "of", "and", "mf"}
)
# Abbreviations used by one source but spelled out in the other.
_TOKEN_EXPANSIONS = {
    "sl": ("sun", "life"),
    "rob": ("robeco",),
    "multicap": ("multi", "cap"),
}


def flatten_fund_records(payload) -> list[dict]:
    """Collect every fund record from the provider's nested category payload.

    A record is any dict that carries a ``fund_name`` key, wherever it sits in
    the nesting, so the walk does not depend on the exact category layout.
    """
    records: list[dict] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "fund_name" in node:
                records.append(node)
            else:
                stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return records


def normalize_fund_name(name: str | None) -> frozenset[str]:
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9]+", (name or "").lower()):
        tokens.extend(_TOKEN_EXPANSIONS.get(token, (token,)))
    return frozenset(token for token in tokens if token not in _STOP_TOKENS)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def match_fund_records(names_by_product_id: dict[int, str], records: list[dict]):
    """Return ``(matches, unmatched_names)``.

    ``matches`` maps product id to ``{"record": <provider record>, "score": float}``.
    """
    indexed = [(record, normalize_fund_name(record.get("fund_name"))) for record in records]
    matches: dict[int, dict] = {}
    unmatched: list[str] = []

    for product_id, product_name in names_by_product_id.items():
        wanted = normalize_fund_name(product_name)
        scored = sorted(
            ((_jaccard(wanted, tokens), record) for record, tokens in indexed),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best_record = scored[0] if scored else (0.0, None)
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best_record is None or best_score < MIN_MATCH_SCORE or best_score == runner_up:
            unmatched.append(product_name)
            continue
        matches[product_id] = {"record": best_record, "score": round(best_score, 4)}

    return matches, unmatched
