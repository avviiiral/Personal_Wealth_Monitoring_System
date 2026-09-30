from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

@dataclass(frozen=True)
class NormalizedFiling:
    exchange: str
    company_name: str
    symbol: str
    isin: str
    bse_code: str
    filing_type: str
    subject: str
    details: str
    filing_url: str
    source_url: str
    external_filing_id: str
    published_at: datetime

class FilingProvider(Protocol):
    exchange: str
    def fetch_filings(self, since: datetime) -> Iterable[NormalizedFiling]: ...
