"""Audit-grounded dispute drafting with an offline deterministic fallback."""

from __future__ import annotations

import json
import os
from typing import Any

MODEL = "gpt-4o-mini"


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def fallback_dispute(exception: dict[str, Any]) -> str:
    """Create an audit-ready draft without an external model."""
    carrier = exception.get("carrier_name", "the carrier")
    invoice = exception.get("invoice_number", "the invoice")
    po_number = exception.get("po_number", "the purchase order")
    total_overbill = _money(exception.get("total_overbill"))
    variance_pct = float(exception.get("variance_pct", 0) or 0) * 100
    return f"""[DETERMINISTIC FALLBACK DRAFT]
Subject: Credit memo request for invoice {invoice} / PO {po_number}

Dear {carrier} billing team,

Our audit of invoice {invoice} against the contracted terms in purchase order {po_number} identified a potential overbilling of {total_overbill} ({variance_pct:.2f}% of contracted value). The exception includes base freight variance of {_money(exception.get('base_variance'))}, fuel surcharge variance of {_money(exception.get('fuel_variance'))}, and authorized-accessorial variance of {_money(exception.get('accessorial_variance'))}.

Please review the attached reconciliation and issue a credit memo for the unsupported amount, or provide the contractual documentation supporting these charges. Please confirm receipt and your resolution timeline within five business days.

Regards,
Freight Audit Operations
"""


def _prompt(exception: dict[str, Any]) -> str:
    facts = json.dumps(exception, default=str, sort_keys=True)
    return f"""Draft a concise, professional carrier dispute letter using only these verified reconciliation facts:
{facts}

Requirements:
- Reference the invoice and PO.
- Identify each supplied line-item variance and the total overbill.
- Request a credit memo or supporting contractual documentation.
- Do not invent dates, rates, terms, evidence, or legal conclusions.
- Include a two-sentence executive summary followed by the letter.
"""


def draft_dispute(exception: dict[str, Any], api_key: str | None = None) -> str:
    """Draft a dispute using OpenAI when configured, otherwise use the local fallback."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        return fallback_dispute(exception)
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are an audit-compliant freight invoice dispute writer."},
                {"role": "user", "content": _prompt(exception)},
            ],
        )
        content = response.choices[0].message.content
        return content.strip() if content else fallback_dispute(exception)
    except Exception as error:  # External API failures must not stop AP operations.
        return f"[AI DRAFT UNAVAILABLE: {type(error).__name__}]\n\n{fallback_dispute(exception)}"
