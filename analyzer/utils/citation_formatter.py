"""
citation_formatter.py — turn Citation objects into human-readable text
for the executive summary UI, e.g.:

    "Page 8, TABLE_3, row 5 (column: Net income)"
"""

from __future__ import annotations

from models.schemas import Citation, FinalReport


def format_citation(citation: Citation, fallback: str = "Source not cited") -> str:
    """Render one citation as readable prose."""
    if citation is None:
        return fallback

    parts = [f"Page {citation.page}"]

    if citation.table_id:
        parts.append(citation.table_id)
        if citation.row is not None:
            parts.append(f"row {citation.row}")
    if citation.column:
        parts.append(f"column: {citation.column}")

    return ", ".join(parts)


def format_report_citations(report: FinalReport) -> str:
    """Render a complete citation appendix for the whole report."""
    lines = []
    for m in report.extracted_metrics:
        lines.append(
            f"- {m.metric}: {format_citation(m.citation)}"
            + (f"  (note: {m.note})" if m.note else "")
        )
    for r in report.risk_flags:
        lines.append(
            f"- {r.risk} [{r.severity}]: {format_citation(r.citation)}"
            + (f"  (note: {r.note})" if r.note else "")
        )
    return "\n".join(lines) if lines else "No citations available."


def format_metrics_table(report: FinalReport) -> str:
    """Render metrics as a compact Markdown table for the summary tab."""
    if not report.extracted_metrics:
        return "_No metrics extracted._"
    rows = []
    for m in report.extracted_metrics:
        value = "-" if m.value is None else f"{m.value:,.0f}"
        unit = m.unit or ""
        rows.append(
            f"| {m.metric} | {value} {unit} | {format_citation(m.citation)} |"
        )
    header = "| Metric | Value | Citation |\n| --- | --- | --- |"
    return "\n".join([header] + rows)


def format_risks_table(report: FinalReport) -> str:
    """Render risk flags as a compact Markdown table for the summary tab."""
    if not report.risk_flags:
        return "_No risk flags identified._"
    rows = []
    for r in report.risk_flags:
        rows.append(
            f"| {r.risk} | **{r.severity}** | {r.justification} | "
            f"{format_citation(r.citation)} |"
        )
    header = "| Risk | Severity | Justification | Citation |\n| --- | --- | --- | --- |"
    return "\n".join([header] + rows)


_STATUS_LABELS = {
    "verified": "✅ verified",
    "value_mismatch": "❌ value mismatch",
    "label_mismatch": "⚠️ label mismatch",
    "not_found": "❌ not found",
    "invalid_citation": "❌ invalid citation",
    "missing_citation": "⚠️ no citation",
}


def format_verification_table(items: list[dict]) -> str:
    """Render verification results as a Markdown table."""
    if not items:
        return "_Nothing to verify._"
    rows = []
    for item in items:
        status = _STATUS_LABELS.get(item["status"], item["status"])
        claimed = item.get("claimed_value") or "-"
        found = item.get("found_value") or "-"
        kind = "metric" if item["kind"] == "metric" else "risk"
        rows.append(
            f"| {kind} | {item['label']} | **{status}** | {claimed} | {found} | "
            f"{item.get('detail', '')} |"
        )
    header = ("| Type | Item | Status | Claimed | Found at citation | Detail |\n"
              "| --- | --- | --- | --- | --- | --- |")
    return "\n".join([header] + rows)