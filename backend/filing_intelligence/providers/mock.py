from datetime import datetime

class MockFilingProvider:
    exchange = "MOCK"
    def __init__(self, filings=None):
        self.filings = list(filings or [])
    def fetch_filings(self, since: datetime):
        return [f for f in self.filings if f.published_at >= since]
