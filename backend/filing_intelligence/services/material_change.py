"""Safe, narrow diff extraction for repeated corporate disclosures."""

import re


_RANGE_RE = re.compile(
    r"(?P<label>guidance|outlook|margin|growth)[^0-9%]{0,80}?"
    r"(?P<old>\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?)\s*%"
)


def _extract_range(text):
    match = _RANGE_RE.search(text or "")
    if not match:
        return None
    return f"{match.group('old').replace(' ', '')}%"


def detect_material_change(previous_text: str, current_text: str) -> str | None:
    previous = _extract_range(previous_text)
    current = _extract_range(current_text)
    if previous and current and previous != current:
        label_match = re.search(r"guidance|outlook|margin|growth", current_text or "", re.IGNORECASE)
        label = label_match.group(0).capitalize() if label_match else "Value"
        return f"{label} changed: {previous} → {current}"
    return None
