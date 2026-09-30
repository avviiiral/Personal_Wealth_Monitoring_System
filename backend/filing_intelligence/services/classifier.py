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

RULES = [
("FRAUD", "CRITICAL", [r"fraud (?:detected|committed|identified)", r"fraud by (?:the )?(?:company|promoter|director|kmp)"]),
("FRAUD_ALLEGATION", "HIGH", [r"alleged fraud", r"fraud allegation", r"accused of fraud", r"suspected fraud"]),
("INSOLVENCY", "CRITICAL", [r"insolvency resolution", r"corporate insolvency", r"insolvency proceedings"]),
("BANKRUPTCY", "CRITICAL", [r"bankruptcy", r"winding[- ]up", r"liquidation order"]),
("DEFAULT", "HIGH", [r"default(?:ed|ing)? on (?:loan|debt|payment)", r"event of default", r"payment default"]),
("REGULATORY_ACTION", "HIGH", [r"sebi (?:order|action|penalty|proceedings|investigation)", r"regulatory action", r"trading suspension"]),
("INVESTIGATION", "HIGH", [r"investigation (?:by|into)", r"investigated by", r"search and seizure"]),
("AUDITOR_RESIGNATION", "HIGH", [r"auditor.*resign", r"resignation.*auditor", r"change in auditor"]),
("AUDITOR_QUALIFICATION", "HIGH", [r"audit qualification", r"qualified opinion", r"qualification in auditor"]),
("RATING_DOWNGRADE", "HIGH", [r"credit rating.*downgrade", r"rating.*downgraded", r"downgrade.*rating"]),
("PROMOTER_PLEDGE", "MEDIUM", [r"promoter.*pledge", r"pledge.*promoter", r"invocation of pledged"]),
("MANAGEMENT_RESIGNATION", "MEDIUM", [r"resignation of (?:ceo|cfo|md|managing director|key managerial personnel|director)", r"ceo.*resign", r"cfo.*resign"]),
("MATERIAL_LITIGATION", "MEDIUM", [r"material litigation", r"material dispute", r"material legal proceeding"]),
("RELATED_PARTY_TRANSACTION", "MEDIUM", [r"related party transaction", r"related-party transaction"]),
("DEBT_RESTRUCTURING", "MEDIUM", [r"debt restructuring", r"restructuring of debt", r"resolution plan"]),
("ACQUISITION", "MEDIUM", [r"acquisition", r"agreement to acquire"]),
("MERGER", "MEDIUM", [r"merger", r"amalgamation"]),
("DEMERGER", "MEDIUM", [r"demerger", r"scheme of arrangement"]),
("PLANT_SHUTDOWN", "MEDIUM", [r"plant shutdown", r"closure of plant", r"shutdown of plant"]),
("STRIKE", "MEDIUM", [r"strike action", r"workers.*strike", r"labour strike"]),
("REGULATORY_BAN", "HIGH", [r"regulatory ban", r"banned by sebi", r"license cancellation", r"licence cancellation"]),
("BUYBACK", "LOW", [r"buyback"]),
("BONUS", "LOW", [r"bonus issue", r"bonus shares"]),
("STOCK_SPLIT", "LOW", [r"stock split", r"split of shares"]),
("RIGHTS_ISSUE", "LOW", [r"rights issue", r"rights entitlement"]),
("QIP", "LOW", [r"qualified institutional placement", r"\bqip\b"]),
("DIVIDEND", "INFO", [r"dividend declared", r"interim dividend", r"final dividend", r"dividend"]),
("RESULTS", "INFO", [r"financial results", r"quarterly results", r"audited results"]),
("GUIDANCE_CHANGE", "MEDIUM", [r"guidance revised", r"guidance change", r"withdraws guidance", r"raises guidance", r"lowers guidance"]),
("BOARD_MEETING", "INFO", [r"board meeting", r"meeting of board"]),
]

SEVERITY_SCORE={"INFO":10,"LOW":30,"MEDIUM":55,"HIGH":75,"CRITICAL":95}
IMPACT={"INFO":"very_low","LOW":"low","MEDIUM":"moderate","HIGH":"high","CRITICAL":"critical"}
MATERIALITY={"INFO":"trivial","LOW":"low","MEDIUM":"moderate","HIGH":"high","CRITICAL":"critical"}
NEGATIONS=("risk of","potential","possible","may be","could be","no evidence of")

def classify(subject, details=""):
    text=" ".join((subject or "",details or "")).strip().lower()
    for event,severity,patterns in RULES:
        for pattern in patterns:
            match=re.search(pattern,text,re.I)
            if not match: continue
            prefix=text[max(0,match.start()-24):match.start()].strip()
            if any(prefix.endswith(x) for x in NEGATIONS): continue
            category="OTHER"
            if "REGULATORY" in event or event in {"INVESTIGATION","RATING_DOWNGRADE"}: category="REGULATORY"
            elif "AUDITOR" in event or "MANAGEMENT" in event: category="MANAGEMENT"
            elif event in {"ACQUISITION","MERGER","DEMERGER"}: category="M_AND_A"
            elif event=="PROMOTER_PLEDGE": category="PROMOTER"
            elif event in {"DIVIDEND","BUYBACK","BONUS","STOCK_SPLIT","RIGHTS_ISSUE","QIP"}: category="CAPITAL_ALLOCATION"
            return FilingClassification(event,severity,category,IMPACT[severity],MATERIALITY[severity],f"Matched configured deterministic rule for {event} using filing subject/details.",[subject] if subject else [])
    return FilingClassification("OTHER","INFO","OTHER","very_low","trivial","No configured material event rule matched the filing context.",[subject] if subject else [])
