"""
Resolves a mutual fund's AMC (fund house) name purely from its
scheme name - no external lookup, no per-security research file.

Every Indian mutual fund's registered scheme name is required to
start with its AMC's brand (a SEBI naming rule), so this is a
structural fact about the name itself, not something that needs
fetching or manually researching per security - unlike sector/P-E/
P-B/ROE (see yahoo_quant_enrichment.py) or cap_type (see
management/commands/import_amfi_cap_classification.py).

The one wrinkle: a scheme's own name often uses a SHORTER prefix
than the AMC's full registered name (e.g. a scheme named "Kotak
Liquid Fund..." belongs to "Kotak Mahindra Mutual Fund", not a fund
house literally called "Kotak Mutual Fund"). AMC_PREFIXES below
maps each known scheme-name prefix to the AMC's actual full name,
built from AMFI's own list of registered AMCs. It's necessarily a
static, hand-maintained list (there are only ~45 AMCs in India and
new ones don't appear often), but it never fabricates a match: any
scheme name that doesn't start with a known prefix returns None
rather than guessing.
"""

import re

# Ordered longest-prefix-first so "ICICI Prudential" is tried before
# the shorter "ICICI" would otherwise wrongly match it, etc.
AMC_PREFIXES = [
    ("360 ONE", "360 ONE Mutual Fund"),
    ("Aditya Birla Sun Life", "Aditya Birla Sun Life Mutual Fund"),
    ("Angel One", "Angel One Mutual Fund"),
    ("Axis", "Axis Mutual Fund"),
    ("Bajaj Finserv", "Bajaj Finserv Mutual Fund"),
    ("Bandhan", "Bandhan Mutual Fund"),
    ("Bank of India", "Bank of India Mutual Fund"),
    ("Baroda BNP Paribas", "Baroda BNP Paribas Mutual Fund"),
    ("Canara Robeco", "Canara Robeco Mutual Fund"),
    ("Capitalmind", "Capitalmind Mutual Fund"),
    ("Choice", "Choice Mutual Fund"),
    ("DSP", "DSP Mutual Fund"),
    ("Edelweiss", "Edelweiss Mutual Fund"),
    ("Franklin", "Franklin Templeton Mutual Fund"),
    ("Groww", "Groww Mutual Fund"),
    ("HDFC", "HDFC Mutual Fund"),
    ("Helios", "Helios Mutual Fund"),
    ("HSBC", "HSBC Mutual Fund"),
    ("ICICI Prudential", "ICICI Prudential Mutual Fund"),
    ("Invesco", "Invesco Mutual Fund"),
    ("ITI", "ITI Mutual Fund"),
    ("Jio BlackRock", "Jio BlackRock Mutual Fund"),
    ("JM Financial", "JM Financial Mutual Fund"),
    ("Kotak", "Kotak Mahindra Mutual Fund"),
    ("LIC", "LIC Mutual Fund"),
    ("Mahindra Manulife", "Mahindra Manulife Mutual Fund"),
    ("Mirae Asset", "Mirae Asset Mutual Fund"),
    ("Motilal Oswal", "Motilal Oswal Mutual Fund"),
    ("Navi", "Navi Mutual Fund"),
    ("Nippon India", "Nippon India Mutual Fund"),
    ("Old Bridge", "Old Bridge Mutual Fund"),
    ("PGIM India", "PGIM India Mutual Fund"),
    ("PPFAS", "PPFAS Mutual Fund"),
    ("Parag Parikh", "PPFAS Mutual Fund"),
    ("Quant", "Quant Mutual Fund"),
    ("Quantum", "Quantum Mutual Fund"),
    ("Samco", "Samco Mutual Fund"),
    ("SBI", "SBI Mutual Fund"),
    ("Shriram", "Shriram Mutual Fund"),
    ("Sundaram", "Sundaram Mutual Fund"),
    ("Tata", "Tata Mutual Fund"),
    ("Taurus", "Taurus Mutual Fund"),
    ("Trust", "Trust Mutual Fund"),
    ("Union", "Union Mutual Fund"),
    ("UTI", "UTI Mutual Fund"),
    ("Unifi", "Unifi Mutual Fund"),
    ("WhiteOak Capital", "WhiteOak Capital Mutual Fund"),
    ("Zerodha", "Zerodha Mutual Fund"),
    ("Bank of Baroda", "Baroda BNP Paribas Mutual Fund"),
]

# Sorted by prefix length descending so multi-word prefixes are
# always tried before a shorter prefix that would also match.
_SORTED_PREFIXES = sorted(
    AMC_PREFIXES,
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def resolve_amc_name(scheme_name):
    """
    Return the AMC's full registered name if `scheme_name` starts
    with a known AMC prefix (case-insensitive, tolerant of the
    scheme name running straight into the fund descriptor with no
    space, e.g. "BANDHANDynamic..." never happens in practice but
    "BANDHAN Dynamic..." and "Bandhan-Dynamic..." both should
    match). Returns None - never a guess - when no known prefix
    matches.
    """

    if not scheme_name:
        return None

    normalized = scheme_name.strip()

    for prefix, amc_name in _SORTED_PREFIXES:

        pattern = r"^" + re.escape(prefix) + r"(\s|-|$)"

        if re.match(pattern, normalized, re.IGNORECASE):
            return amc_name

    return None
