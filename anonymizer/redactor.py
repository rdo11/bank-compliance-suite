"""Offline GDPR Clipboard Anonymizer - pure redaction engine.

Danish-specific PII redaction with zero network access.
Standard library only (pyperclip is only needed by the GUI layer).

Rules run in a fixed order (CPR -> IBAN -> account -> phone -> email -> card
-> CVR -> custom) so longer / more specific identifiers win and never get
torn apart by a later, looser pattern. IBAN runs early because phone/card
patterns would otherwise split its digit groups.
"""

from __future__ import annotations

import difflib
import re

REDACTED_LABELS = {
    "cpr": "[CPR-REDACTED]",
    "iban": "[IBAN-REDACTED]",
    "account": "[ACCOUNT-REDACTED]",
    "phone": "[PHONE-REDACTED]",
    "email": "[EMAIL-REDACTED]",
    "card": "[CARD-REDACTED]",
    "cvr": "[CVR-REDACTED]",
    "custom": "[CUSTOM-REDACTED]",
}

# Display names used by the GUI / stats tab.
CATEGORY_LABELS = {
    "cpr": "CPR / dates",
    "iban": "IBAN",
    "account": "Bank account",
    "phone": "Phone",
    "email": "Email",
    "card": "Credit card",
    "cvr": "CVR",
    "custom": "Custom words",
}

RULE_ORDER = ("cpr", "iban", "account", "phone", "email", "card", "cvr", "custom")

_CPR_HYPHEN = re.compile(r"\b\d{6}\s*[-–—]\s*\d{4}\b")
_CPR_RAW = re.compile(r"(?<!\d)\d{10}(?!\d)")
_CPR_DATE = re.compile(r"\b\d{2}[-/.]\d{2}[-/.](?:19|20)\d{2}\b")
_IBAN_CANDIDATE = re.compile(r"(?<![A-Z0-9])([A-Z]{2}\d{2})(?:[ -]?\d{4}){2,7}[ -]?\d{1,4}(?![A-Z0-9])")
_ACCOUNT = re.compile(r"\b\d{4}[- ]\d{10}\b")
_PHONE_INTL = re.compile(
    r"(?<!\d)\+45\s*\d{2}\s*[ -]?\s*\d{2}\s*[ -]?\s*\d{2}\s*[ -]?\s*\d{2}(?!\d)"
)
_PHONE_NATIONAL = re.compile(r"(?<!\d)\d{2}[ -]\d{2}[ -]\d{2}[ -]\d{2}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_CVR = re.compile(r"(?<!\d)\d{8}(?!\d)")


def iban_check(value: str) -> bool:
    """Validate an IBAN with the ISO 7064 mod-97 checksum.

    `value` may contain spaces/dashes. Returns True only for structurally
    valid IBANs (2 letters + 2 check digits + >= 10 digit/letter BBAN).
    """
    s = re.sub(r"[ -]", "", value).upper()
    if len(s) < 15 or len(s) > 34 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    # move first 4 chars to the end, map A=10..Z=35, then mod-97 == 1
    reordered = s[4:] + s[:4]
    number = "".join(str(ord(c) - 55) if c.isalpha() else c for c in reordered)
    return int(number) % 97 == 1


def luhn_check(number: str) -> bool:
    """Return True if `number` (digits only) passes the Luhn checksum."""
    digits = [int(d) for d in number if d.isdigit()]
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _valid_cpr_prefix(prefix6: str) -> bool:
    """A CPR's first 6 digits encode DDMMYY; reject impossible dates.

    Rare historic CPRs may use 00 for day/month (unknown birth date), which we
    accept rather than risk missing a real identifier.
    """
    dd = int(prefix6[:2])
    mm = int(prefix6[2:4])
    if dd == 0 or mm == 0:
        return True
    if mm > 12 or dd > 31:
        return False
    if mm in (4, 6, 9, 11) and dd > 30:
        return False
    if mm == 2:
        yy = int(prefix6[4:6])
        leap = (yy % 4 == 0 and yy % 100 != 0) or yy % 400 == 0
        if dd > (29 if leap else 28):
            return False
    return True


def _repl(key: str, mode: str):
    label = REDACTED_LABELS[key]
    if mode == "label":
        return lambda m: label
    return lambda m: "*" * len(m.group(0))


def _apply_cpr(text: str, mode: str) -> tuple[str, int]:
    label = REDACTED_LABELS["cpr"]
    n = 0

    def repl_hyphen(m):
        nonlocal n
        if not _valid_cpr_prefix(m.group(0)[:6]):
            return m.group(0)
        n += 1
        return label if mode == "label" else "*" * len(m.group(0))

    def repl_raw(m):
        nonlocal n
        if not _valid_cpr_prefix(m.group(0)[:6]):
            return m.group(0)
        n += 1
        return label if mode == "label" else "*" * len(m.group(0))

    text = _CPR_HYPHEN.sub(repl_hyphen, text)
    text = _CPR_RAW.sub(repl_raw, text)
    text, extra = _CPR_DATE.subn(_repl("cpr", mode), text)
    return text, n + extra


def _apply_iban(text: str, mode: str) -> tuple[str, int]:
    label = REDACTED_LABELS["iban"]
    n = 0

    def repl(m):
        nonlocal n
        if not iban_check(m.group(0)):
            return m.group(0)
        n += 1
        return label if mode == "label" else "*" * len(m.group(0))

    return _IBAN_CANDIDATE.sub(repl, text), n


def _apply_account(text: str, mode: str) -> tuple[str, int]:
    return _ACCOUNT.subn(_repl("account", mode), text)


def _apply_phone(text: str, mode: str) -> tuple[str, int]:
    repl = _repl("phone", mode)
    text, n1 = _PHONE_INTL.subn(repl, text)
    text, n2 = _PHONE_NATIONAL.subn(repl, text)
    return text, n1 + n2


def _apply_email(text: str, mode: str) -> tuple[str, int]:
    return _EMAIL.subn(_repl("email", mode), text)


def _apply_card(text: str, mode: str) -> tuple[str, int]:
    label = REDACTED_LABELS["card"]
    n = 0

    def repl(m):
        nonlocal n
        number = re.sub(r"[ -]", "", m.group(0))
        if not (13 <= len(number) <= 19) or not luhn_check(number):
            return m.group(0)
        n += 1
        return label if mode == "label" else "*" * len(m.group(0))

    return _CARD_CANDIDATE.sub(repl, text), n


def _apply_cvr(text: str, mode: str) -> tuple[str, int]:
    return _CVR.subn(_repl("cvr", mode), text)


_APPLY = {
    "cpr": _apply_cpr,
    "iban": _apply_iban,
    "account": _apply_account,
    "phone": _apply_phone,
    "email": _apply_email,
    "card": _apply_card,
    "cvr": _apply_cvr,
}


def apply_custom_words(text: str, words: list[str], mode: str = "label") -> tuple[str, int]:
    """Redact user-supplied words (case-insensitive, whole-word)."""
    cleaned = [w.strip() for w in words if w and w.strip()]
    if not cleaned:
        return text, 0
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(w) for w in cleaned) + r")(?!\w)",
        re.IGNORECASE,
    )
    return pattern.subn(_repl("custom", mode), text)


def redact(
    text: str,
    enabled: list[str] | None = None,
    custom_words: list[str] | None = None,
    mask_mode: str = "label",
) -> tuple[str, dict[str, int]]:
    """Redact PII in `text`.

    - enabled: keys to apply (default: all rules in RULE_ORDER).
    - custom_words: extra terms to redact (whole-word, case-insensitive).
    - mask_mode: "label" -> [CATEGORY-REDACTED]; "asterisks" -> same-length ***.

    Returns (redacted_text, counts_per_category).
    """
    if enabled is None:
        enabled = list(RULE_ORDER)
    counts = {key: 0 for key in REDACTED_LABELS}
    result = text
    for key in RULE_ORDER:
        if key == "custom" or key not in enabled:
            continue
        result, n = _APPLY[key](result, mask_mode)
        counts[key] += n
    if "custom" in enabled and custom_words:
        result, counts["custom"] = apply_custom_words(result, custom_words, mask_mode)
    return result, counts


def changed_spans(before: str, after: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return (before_spans, after_spans) of character ranges that differ.

    Used by the GUI to highlight changes in yellow in both preview panes.
    """
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    spans_before: list[tuple[int, int]] = []
    spans_after: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 != i2:
            spans_before.append((i1, i2))
        if j1 != j2:
            spans_after.append((j1, j2))
    return spans_before, spans_after