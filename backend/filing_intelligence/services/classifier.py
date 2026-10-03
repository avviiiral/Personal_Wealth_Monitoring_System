"""Deterministic corporate-filing classifier used by PWMS.

No hosted model or external AI service is used. Classification combines filing
subject, details and filing type signals and deliberately requires contextual
evidence before assigning material event types.
"""

import re
from dataclasses import dataclass

from ..models import FilingSeverity


@dataclass(frozen=True)
class FilingClassification:
    event_type: str
    severity: str
    category: str
    impact: str
    materiality: str
    reason: str
    facts: list[str]


# event, base score, category, signal patterns
RULES = [
    ("FRAUD", 92, "LEGAL", (r"fraud detected", r"accounting fraud", r"accounting irregularity", r"fraud committed", r"fraud identified")),
    ("FRAUD_ALLEGATION", 72, "LEGAL", (r"alleged fraud", r"fraud allegation", r"accused of fraud", r"suspected fraud")),
    ("INSOLVENCY", 92, "LEGAL", (r"insolvency proceedings", r"corporate insolvency", r"insolvency resolution")),
    ("BANKRUPTCY", 95, "LEGAL", (r"bankruptcy", r"liquidation order", r"winding[- ]up")),
    ("DEFAULT", 90, "FINANCIAL", (r"event of default", r"payment default", r"default(?:ed|ing)? on (?:loan|debt|payment)")),
    ("REGULATORY_ACTION", 68, "REGULATORY", (r"sebi (?:order|action|penalty|proceedings|investigation)", r"regulatory action", r"regulatory penalty")),
    ("RATING_DOWNGRADE", 68, "REGULATORY", (r"credit rating.{0,40}downgrad", r"rating.{0,40}downgrad", r"downgrad.{0,40}rating")),
    ("REGULATORY_BAN", 90, "REGULATORY", (r"regulatory ban", r"banned by sebi", r"license cancellation", r"licence cancellation", r"trading ban")),
    ("MAJOR_REGULATORY_ACTION", 82, "REGULATORY", (r"major regulatory action", r"major sebi action", r"material sebi action")),
    ("AUDITOR_QUALIFICATION", 84, "MANAGEMENT", (r"material audit qualification", r"qualified opinion", r"auditor qualification")),
    ("CEO_RESIGNATION", 70, "MANAGEMENT", (r"\\bceo\\b.{0,50}\\bresign", r"resignation.{0,50}\\bceo\\b")),
    ("CFO_RESIGNATION", 70, "MANAGEMENT", (r"\\bcfo\\b.{0,50}\\bresign", r"resignation.{0,50}\\bcfo\\b")),
    ("DIRECTOR_RESIGNATION", 65, "MANAGEMENT", (r"director.{0,50}\\bresign", r"resignation.{0,50}director")),
    ("AUDITOR_RESIGNATION", 68, "MANAGEMENT", (r"auditor.{0,50}\\bresign", r"resignation.{0,50}auditor")),
    ("MAJOR_INVESTIGATION", 68, "REGULATORY", (r"major investigation", r"investigation by sebi", r"investigation by regulator", r"search and seizure")),
    ("MAJOR_ACQUISITION", 66, "M_AND_A", (r"major acquisition", r"acquisition.{0,50}(?:material|significant|substantial)", r"acquire.{0,50}(?:material|significant|substantial)")),
    ("ACQUISITION", 50, "M_AND_A", (r"acquisition", r"agreement to acquire", r"to acquire")),
    ("MERGER", 58, "M_AND_A", (r"merger", r"amalgamation")),
    ("DEMERGER", 58, "M_AND_A", (r"demerger", r"scheme of arrangement")),
    ("MAJOR_FUND_RAISING", 66, "CAPITAL_ALLOCATION", (r"major fund raising", r"major fundraising", r"material fund raising", r"qualified institutional placement.*(?:crore|million|billion)", r"fund raising.*(?:crore|million|billion)")),
    ("FUND_RAISING", 48, "CAPITAL_ALLOCATION", (r"fund raising", r"fundraising", r"qip", r"qualified institutional placement")),
    ("PREFERENTIAL_ISSUE", 50, "CAPITAL_ALLOCATION", (r"preferential issue", r"preferential allotment")),
    ("BUYBACK", 48, "CAPITAL_ALLOCATION", (r"buyback", r"buy-back")),
    ("BONUS", 45, "CAPITAL_ALLOCATION", (r"bonus issue", r"bonus shares")),
    ("STOCK_SPLIT", 45, "CAPITAL_ALLOCATION", (r"stock split", r"split of shares")),
    ("DIVIDEND", 45, "CAPITAL_ALLOCATION", (r"dividend declared", r"interim dividend", r"final dividend", r"dividend")),
    ("SIGNIFICANT_PROMOTER_PLEDGE", 66, "PROMOTER", (r"significant promoter pledge increase", r"major promoter pledge", r"substantial promoter pledge")),
    ("PROMOTER_PLEDGE_RELEASE", 48, "PROMOTER", (r"promoter.{0,50}(?:pledge release|release of pledge)", r"pledge.{0,50}released")),
    ("PROMOTER_PLEDGE", 50, "PROMOTER", (r"promoter.{0,50}pledge", r"pledge.{0,50}promoter", r"invocation of pledged")),
    ("CHANGE_IN_SHAREHOLDING", 48, "PROMOTER", (r"change in shareholding", r"shareholding pattern", r"change in promoter holding")),
    ("PROMOTER_TRANSACTION", 50, "PROMOTER", (r"promoter.{0,50}(?:buy|sell|purchase|sale|transaction)", r"promoter transaction")),
    ("MATERIAL_ORDER", 66, "ORDER", (r"major order", r"material order", r"significant order", r"large order win", r"major order win")),
    ("ORDER", 48, "ORDER", (r"order win", r"order book", r"wins order", r"purchase order")),
    ("MATERIAL_CONTRACT", 65, "CONTRACT", (r"major contract", r"material contract", r"significant contract")),
    ("CONTRACT", 48, "CONTRACT", (r"contract", r"agreement", r"partnership")),
    ("MATERIAL_LITIGATION", 55, "LEGAL", (r"material litigation", r"material dispute", r"material legal proceeding")),
    ("LITIGATION", 45, "LEGAL", (r"litigation", r"lawsuit", r"court proceeding")),
    ("FINANCIAL_RESULTS", 48, "EARNINGS", (r"financial results", r"quarterly results", r"audited results", r"results for the period")),
    ("BOARD_MEETING", 45, "CORPORATE_GOVERNANCE", (r"board meeting", r"meeting of board", r"outcome of board meeting")),
    ("INVESTOR_PRESENTATION", 35, "CORPORATE_GOVERNANCE", (r"investor presentation", r"analyst presentation", r"investor meet")),
    ("EARNINGS_CALL", 35, "CORPORATE_GOVERNANCE", (r"earnings call", r"conference call")),
    ("ROUTINE_CORPORATE_ANNOUNCEMENT", 22, "CORPORATE_GOVERNANCE", (r"general updates", r"trading window", r"routine announcement", r"compliance update")),
]


# Representative deterministic impact scores used when converting filing
# severity into the shared Portfolio News alert-scoring model. The values
# preserve the ordering of FilingSeverity without requiring a model/API.
SEVERITY_SCORE = {
    FilingSeverity.INFO: 20,
    FilingSeverity.LOW: 30,
    FilingSeverity.MEDIUM: 50,
    FilingSeverity.HIGH: 70,
    FilingSeverity.CRITICAL: 90,
}


NEGATION_PREFIXES = (
    "risk of",
    "potential",
    "possible",
    "may be",
    "could be",
    "no evidence of",
    "denies",
    "denied",
)


def _matched(pattern: str, text: str):
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    prefix = text[max(0, match.start() - 28):match.start()].strip().lower()
    if any(prefix.endswith(value) for value in NEGATION_PREFIXES):
        return None
    return match.group(0)


def _severity(score: int) -> str:
    if score >= 81:
        return FilingSeverity.CRITICAL
    if score >= 61:
        return FilingSeverity.HIGH
    if score >= 41:
        return FilingSeverity.MEDIUM
    if score >= 21:
        return FilingSeverity.LOW
    return FilingSeverity.INFO


def _impact(severity: str) -> str:
    return {
        FilingSeverity.CRITICAL: "critical",
        FilingSeverity.HIGH: "high",
        FilingSeverity.MEDIUM: "moderate",
        FilingSeverity.LOW: "low",
        FilingSeverity.INFO: "very_low",
    }[severity]


def _materiality(severity: str) -> str:
    return {
        FilingSeverity.CRITICAL: "critical",
        FilingSeverity.HIGH: "high",
        FilingSeverity.MEDIUM: "moderate",
        FilingSeverity.LOW: "low",
        FilingSeverity.INFO: "trivial",
    }[severity]


def classify(subject: str, details: str = "", filing_type: str = "") -> FilingClassification:
    subject = (subject or "").strip()
    details = (details or "").strip()
    filing_type = (filing_type or "").strip()
    subject_text = subject.lower()
    full_text = " ".join((subject, details, filing_type)).lower()

    hits = []
    for event, base_score, category, patterns in RULES:
        evidence = next((m for pattern in patterns if (m := _matched(pattern, full_text))), None)
        if evidence:
            # Subject and explicit filing type are stronger signals than body-only matches.
            subject_bonus = 8 if any(_matched(pattern, subject_text) for pattern in patterns) else 0
            type_bonus = 4 if filing_type and any(word in filing_type.lower() for word in event.lower().split("_") if len(word) > 3) else 0
            hits.append((min(100, base_score + subject_bonus + type_bonus), event, category, evidence))

    if not hits:
        return FilingClassification(
            "OTHER",
            FilingSeverity.INFO,
            "OTHER",
            "very_low",
            "trivial",
            "No configured material event rule matched the filing subject, details or filing type.",
            [subject] if subject else [],
        )

    hits.sort(reverse=True)
    score, event_type, category, evidence = hits[0]

    # Combined signals: independent adverse developments materially increase severity.
    adverse = {
        "FRAUD", "INSOLVENCY", "BANKRUPTCY", "DEFAULT", "REGULATORY_BAN",
        "MAJOR_REGULATORY_ACTION", "AUDITOR_QUALIFICATION", "CEO_RESIGNATION",
        "CFO_RESIGNATION", "MAJOR_INVESTIGATION", "CREDIT_RATING_DOWNGRADE",
        "PROMOTER_PLEDGE",
    }
    has_profit_pressure = bool(re.search(r"(profit|revenue|margin).{0,40}(fall|drop|declin|miss)", full_text))
    has_guidance_cut = bool(re.search(r"(guidance|outlook).{0,30}(cut|lower|withdraw|reduce)", full_text))
    has_investigation = bool(re.search(r"investigation|probe|regulatory action", full_text))
    has_management_exit = bool(re.search(r"ceo.{0,50}resign|cfo.{0,50}resign|management.{0,50}resign", full_text))

    if has_profit_pressure and has_guidance_cut:
        score += 12
        evidence += "; combined profit pressure + guidance cut"
    if has_investigation and has_management_exit:
        score += 12
        evidence += "; combined investigation + management resignation"
    if len([hit for hit in hits if hit[1] in adverse]) >= 2:
        score += 10
        evidence += "; multiple material risk signals"

    severity = _severity(min(100, score))
    facts = [f"Subject: {subject}"] if subject else []
    facts.append(f"Detected event: {event_type}")
    facts.append(f"Evidence: {evidence}")
    if filing_type:
        facts.append(f"Filing type: {filing_type}")

    reason = (
        f"Deterministic classification: {event_type} from filing subject, "
        f"details and filing type; matched {len(hits)} independent rule signal(s)."
    )

    return FilingClassification(
        event_type=event_type,
        severity=severity,
        category=category,
        impact=_impact(severity),
        materiality=_materiality(severity),
        reason=reason,
        facts=facts,
    )
