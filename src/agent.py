"""AI-powered, audit-grounded carrier dispute drafting."""

from __future__ import annotations

import json
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
- Request a credit memo or supporting contractual documentation.
- Sign the letter with the contact's name, position, company, and phone from the company profile document. Do not use placeholders.
- Do not invent dates, rates, terms, evidence, or legal conclusions.
- Include a two-sentence executive summary followed by the letter.
- Format the result as plain text for an email. Do not use Markdown or include
  double-asterisk markers (**), including around headings.
"""


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
    return content.strip()
