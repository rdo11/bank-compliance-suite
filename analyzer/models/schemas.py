"""
Pydantic schemas for the Annual Report Analyzer.

These models define the EXACT JSON contract that the LLM response must
satisfy. Every financial figure and risk flag carries a machine-verifiable
citation (page number + table id + row) to satisfy the EU AI Act
"explainability" requirement for high-risk financial systems (2026).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Citation(BaseModel):
    """A machine-verifiable pointer into the source document.

    ``page`` is a 1-based page number (of the original PDF, not the
    internal page count), ``table_id`` is the unique table identifier
    assigned during extraction (e.g. "TABLE_7"), and ``row`` is the
    1-based row index *inside that table* where the figure appears.
    ``column`` (optional) names the column the figure sits in.
    """

    page: int = Field(..., description="1-based page number in the original PDF")
    table_id: Optional[str] = Field(
        None, description="Table ID (e.g. TABLE_3) or null if the figure came from text"
    )
    row: Optional[int] = Field(
        None, description="1-based row number inside the cited table (null for text citations)"
    )
    column: Optional[str] = Field(
        None, description="Column header of the figure inside the cited table"
    )


class FinancialMetric(BaseModel):
    """One extracted financial figure with its source citation."""

    metric: str = Field(..., description="Human-readable metric name, e.g. 'Revenue'")
    value: Optional[float] = Field(
        None, description="Numeric value, or null if not found in the provided context"
    )
    unit: Optional[str] = Field(
        "DKK", description="Unit of the value, e.g. DKK, EUR, '%', 'million'"
    )
    citation: Citation = Field(
        ..., description="Exact source citation for this metric (required)"
    )
    note: Optional[str] = Field(
        None, description="e.g. 'Data not found in provided context' when value is null"
    )

    @field_validator("value")
    @classmethod
    def _value_or_note(cls, v: Optional[float]) -> Optional[float]:
        return v


class RiskFlag(BaseModel):
    """One identified risk with severity, justification and citation."""

    risk: str = Field(..., description="Short risk name, e.g. 'Declining profit margin'")
    severity: str = Field(
        ..., description="One of: Low, Medium, High, Critical"
    )
    justification: str = Field(
        ..., description="Data-backed reasoning, e.g. 'Net income dropped 40% YoY'"
    )
    citation: Citation = Field(
        ..., description="Exact source citation supporting this risk (required)"
    )
    note: Optional[str] = Field(
        None, description="e.g. 'Data not found in provided context' when unverifiable"
    )

    @field_validator("severity")
    @classmethod
    def _severity_choices(cls, v: str) -> str:
        v = v.strip().title()
        if v not in {"Low", "Medium", "High", "Critical"}:
            raise ValueError(f"severity must be Low/Medium/High/Critical, got {v!r}")
        return v


class FinalReport(BaseModel):
    """Top-level output: metrics + risk flags + executive summary."""

    company_name: str = Field(..., description="Legal name of the reporting company")
    fiscal_year: str = Field(..., description="Fiscal year covered by the report, e.g. '2024'")
    extracted_metrics: list[FinancialMetric] = Field(
        default_factory=list, description="All extracted financial metrics"
    )
    risk_flags: list[RiskFlag] = Field(
        default_factory=list, description="All identified risk flags"
    )
    executive_summary: str = Field(
        ..., description="Concise 3-sentence summary of the company's financial health"
    )

    def to_dict(self) -> dict:
        """Serialise to a plain JSON-ready dict (satisfies the JSON contract)."""
        return self.model_dump(mode="json")