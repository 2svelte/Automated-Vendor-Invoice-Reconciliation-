"""AI-powered, audit-grounded carrier dispute drafting."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

MODEL = "gpt-4o-mini"
COMPANY_PROFILE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "company_profile.json"
)


def _load_company_profile() -> dict[str, Any]:
    with COMPANY_PROFILE_PATH.open(encoding="utf-8") as profile_file:
        profile = json.load(profile_file)
    contact = profile.get("dispute_contact")
    if not isinstance(contact, dict) or not all(
        isinstance(value, str) and value.strip()
        for value in (
            profile.get("company_name"),
            contact.get("name"),
            contact.get("position"),
            contact.get("phone"),
        )
    ):
        raise ValueError(f"Invalid company contact profile: {COMPANY_PROFILE_PATH}")
    return profile


def _prompt(exception: dict[str, Any]) -> str:
    facts = json.dumps(exception, default=str, sort_keys=True)
    company_profile = json.dumps(_load_company_profile(), ensure_ascii=False, sort_keys=True)
    return f"""Draft a concise, professional carrier dispute letter using only these verified reconciliation facts:
{facts}

Use this company profile document as the authoritative source for the sender's signature:
{company_profile}

Requirements:
- Reference the invoice and PO.
- Identify each supplied line-item variance and the total overbill.
- Explicitly state the approved remittance and the amount short-paid.
- Use the term "short-paid" and say the approved remittance is queued for payment.
- Request a credit memo or supporting contractual documentation.
- Sign the letter with the contact's name, position, company, and phone from the company profile document. Do not use placeholders.
- Do not invent dates, rates, terms, evidence, or legal conclusions.
- Include a two-sentence executive summary followed by the letter.
- Format the result as plain text for an email. Do not use Markdown or include
  double-asterisk markers (**), including around headings.
"""


def short_pay_statement(exception: dict[str, Any]) -> str:
    """Render the deterministic payment split and the positive disputed components."""
    def amount(value: object) -> float:
        try:
            parsed = float(str(value))
        except (TypeError, ValueError):
            return 0.0
        return parsed if math.isfinite(parsed) else 0.0

    total_invoiced = amount(exception.get("total_invoiced"))
    disputed = amount(exception.get("disputed_amount", exception.get("total_overbill")))
    remittance = amount(exception.get("approved_remittance", total_invoiced - disputed))
    po_number = str(exception.get("po_number") or "the purchase order").strip()

    reasons: list[str] = []
    base_variance = amount(exception.get("base_variance"))
    fuel_variance = amount(exception.get("fuel_variance"))
    if base_variance > 0:
        reasons.append(f"base rate variance (${base_variance:,.2f})")
    elif base_variance < 0:
        reasons.append(f"base rate credit (-${abs(base_variance):,.2f})")
    if fuel_variance > 0:
        reasons.append(f"fuel surcharge variance (${fuel_variance:,.2f})")
    elif fuel_variance < 0:
        reasons.append(f"fuel surcharge credit (-${abs(fuel_variance):,.2f})")

    unauthorized_items = exception.get("unauthorized_accessorial_items")
    if isinstance(unauthorized_items, list) and unauthorized_items:
        for item in unauthorized_items:
            if not isinstance(item, dict):
                continue
            overage = amount(item.get("overage"))
            if overage <= 0:
                continue
            description = str(item.get("description") or item.get("code") or "unauthorized accessorial")
            reasons.append(f"unauthorized {description.lower()} (${overage:,.2f})")
    elif amount(exception.get("accessorial_variance")) > 0:
        reasons.append(
            f"unauthorized accessorial variance "
            f"(${amount(exception.get('accessorial_variance')):,.2f})"
        )

    if not reasons and disputed > 0:
        reasons.append(f"the disputed billing variance (${disputed:,.2f})")
    if not reasons:
        reasons.append("no disputed overcharge")

    return (
        f"We have approved and queued a remittance of ${remittance:,.2f} for payment "
        f"under {po_number}. The remaining ${disputed:,.2f} has been short-paid due "
        f"to {' and '.join(reasons)}."
    )


def draft_dispute(exception: dict[str, Any], api_key: str | None = None) -> str:
    """Draft a dispute letter using OpenAI."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OpenAI API key is not configured.")

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
    if not content or not content.strip():
        raise RuntimeError("OpenAI returned an empty dispute letter.")
    return f"{content.strip()}\n\n{short_pay_statement(exception)}"
