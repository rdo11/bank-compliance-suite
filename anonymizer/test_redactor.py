"""Unit tests for the redaction engine (redactor.py) + shared audit core."""

import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redactor import (
    RULE_ORDER,
    apply_custom_words,
    changed_spans,
    iban_check,
    luhn_check,
    redact,
)
from core.audit import AuditLogger


class TestLuhn(unittest.TestCase):
    def test_valid_card_number(self):
        self.assertTrue(luhn_check("4111111111111111"))

    def test_invalid_card_number(self):
        self.assertFalse(luhn_check("1234567812345678"))

    def test_ignores_non_digits(self):
        self.assertTrue(luhn_check("4111 1111 1111 1111"))


class TestCpr(unittest.TestCase):
    def test_hyphen_form(self):
        out, counts = redact("CPR 150290-1234")
        self.assertIn("[CPR-REDACTED]", out)
        self.assertEqual(counts["cpr"], 1)

    def test_raw_form(self):
        out, counts = redact("CPR 1502901234")
        self.assertIn("[CPR-REDACTED]", out)
        self.assertEqual(counts["cpr"], 1)

    def test_impossible_date_is_not_cpr(self):
        out, counts = redact("Konto 1234567890")
        self.assertNotIn("[CPR-REDACTED]", out)
        self.assertEqual(counts["cpr"], 0)

    def test_date_form(self):
        out, counts = redact("Born 12/05/2024")
        self.assertIn("[CPR-REDACTED]", out)
        self.assertEqual(counts["cpr"], 1)


class TestAccount(unittest.TestCase):
    def test_dash_form(self):
        out, counts = redact("Reg. 5320-1234567890")
        self.assertIn("[ACCOUNT-REDACTED]", out)
        self.assertEqual(counts["account"], 1)

    def test_space_form(self):
        out, counts = redact("Reg. 5320 1234567890")
        self.assertIn("[ACCOUNT-REDACTED]", out)
        self.assertEqual(counts["account"], 1)


class TestIban(unittest.TestCase):
    def test_checksum_valid(self):
        self.assertTrue(iban_check("DK50 0040 0440 1162 43"))
        self.assertTrue(iban_check("DK5000400440116243"))

    def test_checksum_invalid(self):
        self.assertFalse(iban_check("DK50 0040 0440 1162 44"))

    def test_redact_spaced_iban(self):
        out, counts = redact("IBAN DK50 0040 0440 1162 43 end")
        self.assertIn("[IBAN-REDACTED]", out)
        self.assertEqual(counts["iban"], 1)

    def test_redact_compact_iban(self):
        out, counts = redact("IBAN DK5000400440116243")
        self.assertIn("[IBAN-REDACTED]", out)
        self.assertEqual(counts["iban"], 1)

    def test_invalid_iban_not_redacted(self):
        out, counts = redact("IBAN DK50 0040 0440 1162 44")
        self.assertNotIn("[IBAN-REDACTED]", out)
        self.assertEqual(counts["iban"], 0)

    def test_iban_not_eaten_by_phone_or_card(self):
        # digit groups of the IBAN must not be redacted as phone/card first
        out, counts = redact("IBAN DK50 0040 0440 1162 43")
        self.assertIn("[IBAN-REDACTED]", out)
        self.assertEqual(counts["phone"], 0)
        self.assertEqual(counts["card"], 0)


class TestPhone(unittest.TestCase):
    def test_international(self):
        out, counts = redact("Call +45 21 34 56 78 now")
        self.assertIn("[PHONE-REDACTED]", out)
        self.assertEqual(counts["phone"], 1)

    def test_international_no_spaces(self):
        out, counts = redact("Call +4521345678 now")
        self.assertIn("[PHONE-REDACTED]", out)
        self.assertEqual(counts["phone"], 1)

    def test_national_spaced(self):
        out, counts = redact("Ring 21 34 56 78")
        self.assertIn("[PHONE-REDACTED]", out)
        self.assertEqual(counts["phone"], 1)

    def test_bare_8_digits_is_cvr_not_phone(self):
        out, counts = redact("CVR 12345678")
        self.assertIn("[CVR-REDACTED]", out)
        self.assertEqual(counts["cvr"], 1)
        self.assertEqual(counts["phone"], 0)


class TestEmail(unittest.TestCase):
    def test_email(self):
        out, counts = redact("mail jens.hansen@example.dk")
        self.assertIn("[EMAIL-REDACTED]", out)
        self.assertEqual(counts["email"], 1)


class TestCard(unittest.TestCase):
    def test_valid_card_redacted(self):
        out, counts = redact("Card 4111 1111 1111 1111")
        self.assertIn("[CARD-REDACTED]", out)
        self.assertEqual(counts["card"], 1)

    def test_invalid_card_not_redacted(self):
        out, counts = redact("Card 1234 5678 1234 5678")
        self.assertNotIn("[CARD-REDACTED]", out)
        self.assertEqual(counts["card"], 0)

    def test_card_with_dashes(self):
        out, counts = redact("Card 4111-1111-1111-1111")
        self.assertIn("[CARD-REDACTED]", out)
        self.assertEqual(counts["card"], 1)


class TestCvr(unittest.TestCase):
    def test_cvr(self):
        out, counts = redact("Firma CVR 12345678")
        self.assertIn("[CVR-REDACTED]", out)
        self.assertEqual(counts["cvr"], 1)


class TestCustomWords(unittest.TestCase):
    def test_custom_word_case_insensitive(self):
        out, counts = redact("Project Vela is live", custom_words=["vela"])
        self.assertIn("[CUSTOM-REDACTED]", out)
        self.assertEqual(counts["custom"], 1)

    def test_custom_word_with_space(self):
        out, counts = redact("Project Vela done", custom_words=["project vela"])
        self.assertIn("[CUSTOM-REDACTED]", out)
        self.assertEqual(counts["custom"], 1)

    def test_no_partial_word_match(self):
        out, counts = redact("velociraptor", custom_words=["vela"])
        self.assertNotIn("[CUSTOM-REDACTED]", out)
        self.assertEqual(counts["custom"], 0)


class TestMaskMode(unittest.TestCase):
    def test_asterisk_preserves_length(self):
        out, _ = redact("CPR 150290-1234", mask_mode="asterisks")
        self.assertEqual(out, "CPR ***********")

    def test_label_mode(self):
        out, _ = redact("CPR 150290-1234", mask_mode="label")
        self.assertEqual(out, "CPR [CPR-REDACTED]")


class TestDisabledRules(unittest.TestCase):
    def test_disabled_cpr_stays(self):
        out, counts = redact("CPR 150290-1234", enabled=[k for k in RULE_ORDER if k != "cpr"])
        self.assertIn("150290-1234", out)
        self.assertEqual(counts["cpr"], 0)


class TestOrder(unittest.TestCase):
    def test_account_not_eaten_by_card(self):
        out, counts = redact("5320-1234567890")
        self.assertIn("[ACCOUNT-REDACTED]", out)
        self.assertEqual(counts["account"], 1)
        self.assertEqual(counts["card"], 0)

    def test_phone_not_eaten_by_card(self):
        out, counts = redact("+45 21 34 56 78")
        self.assertIn("[PHONE-REDACTED]", out)
        self.assertEqual(counts["card"], 0)


class TestSpans(unittest.TestCase):
    def test_changed_spans_found(self):
        before = "CPR 150290-1234"
        after = "CPR [CPR-REDACTED]"
        sb, sa = changed_spans(before, after)
        self.assertTrue(any(sb))
        self.assertTrue(any(sa))

    def test_no_changes_means_no_spans(self):
        sb, sa = changed_spans("hello world", "hello world")
        self.assertEqual(sb, [])
        self.assertEqual(sa, [])


class TestAudit(unittest.TestCase):
    def test_log_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(Path(tmp) / "audit.jsonl")
            logger.log(app="anonymizer", source="test", items=3, counts={"cpr": 1})
            logger.log(app="anonymizer", source="test2", items=0)
            events = logger.read()
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["items"], 3)
            self.assertEqual(events[0]["counts"]["cpr"], 1)
            self.assertEqual(events[0]["ts"][-1], "Z")  # ISO UTC

    def test_csv_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(Path(tmp) / "audit.jsonl")
            logger.log(app="anonymizer", source="test", items=2,
                       counts={"cpr": 1, "iban": 1}, mask_mode="label")
            out = logger.export_csv(Path(tmp) / "audit.csv")
            content = out.read_text()
            self.assertIn("app,counts,items,mask_mode,source,ts", content)
            self.assertIn("test", content)
            self.assertIn('""cpr"": 1', content)  # nested dict JSON-serialised (CSV-quoted)
            rows = content.strip().splitlines()[1:]
            self.assertEqual(len(rows), 1)

    def test_export_empty_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(Path(tmp) / "audit.jsonl")
            with self.assertRaises(ValueError):
                logger.export_csv(Path(tmp) / "audit.csv")


class TestFullSample(unittest.TestCase):
    def test_sample_redacts_everything(self):
        sample = (
            "Contact jens.hansen@example.dk, +45 21 34 56 78. "
            "CPR 150290-1234. Reg. 5320-1234567890. "
            "IBAN DK50 0040 0440 1162 43. "
            "Card 4111 1111 1111 1111. CVR 12345678. "
            "Project Vela."
        )
        out, counts = redact(sample, custom_words=["vela"])
        for label in ("[CPR-REDACTED]", "[IBAN-REDACTED]", "[ACCOUNT-REDACTED]",
                      "[PHONE-REDACTED]", "[EMAIL-REDACTED]", "[CARD-REDACTED]",
                      "[CVR-REDACTED]", "[CUSTOM-REDACTED]"):
            self.assertIn(label, out)
        self.assertEqual(counts["cpr"], 1)
        self.assertEqual(counts["iban"], 1)
        self.assertEqual(counts["account"], 1)
        self.assertEqual(counts["phone"], 1)
        self.assertEqual(counts["email"], 1)
        self.assertEqual(counts["card"], 1)
        self.assertEqual(counts["cvr"], 1)
        self.assertEqual(counts["custom"], 1)


if __name__ == "__main__":
    unittest.main()