"""Automated mutual-fund masterdata ingestion pipeline.

Modules:
    http        polite HTTP client (timeouts, retries with backoff, per-host rate limit)
    runlog      MFSourceRun-backed ingestion log, file hashing, skip-if-unchanged
    parsers     pure parsers for AMFI / mfapi.in / archive payloads
    identity    stored ID bridge for AMFI feeds keyed by parent scheme
    holdings_registry  pluggable per-AMC portfolio-disclosure parsers
    jobs        one function per scheduled job (also used by management commands)
    scheduler   in-process scheduler started with the backend
"""
