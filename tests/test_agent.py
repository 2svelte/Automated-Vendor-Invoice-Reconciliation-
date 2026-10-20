from types import SimpleNamespace

import pytest

from src.agent import _prompt, draft_dispute, fuel_dispute_line, short_pay_statement


def exception_record():
    return {
        "invoice_number": "INV-1",
        "po_number": "PO-1",
        "carrier_name": "Carrier One",
        "base_variance": 10,
        "fuel_variance": 20,
        "accessorial_variance": 30,
        "total_overbill": 60,
        "total_invoiced": 500,
        "disputed_amount": 60,
        "approved_remittance": 440,
        "variance_pct": 0.04,
    }


def test_draft_without_key_fails_without_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="API key is not configured"):
        draft_dispute(exception_record(), api_key="")


def test_draft_uses_llm_and_returns_generated_text(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="AI-generated letter"))]
    )
    create_calls = []

    class FakeOpenAI:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: create_calls.append(kwargs) or response
                )
            )

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    assert draft_dispute(exception_record(), api_key="test-key") == (
        "AI-generated letter\n\n"
        "We have approved and queued a remittance of $440.00 for payment under "
        "PO-1. The remaining $60.00 has been short-paid due to base rate variance "
        "($10.00) and fuel surcharge variance ($20.00) and unauthorized accessorial "
        "variance ($30.00)."
    )
    assert create_calls[0]["model"] == "gpt-4o-mini"


def test_draft_raises_when_llm_returns_empty_text(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="  "))]
    )

    class FakeOpenAI:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    with pytest.raises(RuntimeError, match="empty dispute letter"):
        draft_dispute(exception_record(), api_key="test-key")


def test_prompt_requires_plain_text_without_markdown_bold_markers():
    prompt = _prompt(exception_record())

    assert "plain text for an email" in prompt
    assert "Do not use Markdown" in prompt
    assert "double-asterisk markers (**)" in prompt
    assert "company profile document" in prompt
    assert "Nadia R." in prompt
    assert "0412345678" in prompt
    assert "short-paid" in prompt


def test_prompt_only_includes_positive_overcharge_line_items():
    prompt = _prompt(
        {
            "invoice_number": "INV-1",
            "po_number": "PO-1",
            "total_invoiced": 195,
            "disputed_variance": 5,
            "disputed_amount": 5,
            "approved_remittance": 190,
            "base_variance": -5,
            "fuel_variance": 5,
            "overcharge_line_items": [
                {
                    "description": "Fuel surcharge",
                    "amount": 5,
                    "is_overcharge": True,
                }
            ],
            "accessorial_items": [
                {"description": "Liftgate", "amount": 95}
            ],
            "approved_accessorial_items": [
                {"description": "Liftgate", "amount": 100}
            ],
        }
    )

    assert "Fuel surcharge" in prompt
    assert "Liftgate" not in prompt
    assert "Do not dispute underbilled or exact-match line items." in prompt


def test_prompt_omits_underbilled_fuel_details():
    prompt = _prompt(
        {
            "invoice_number": "INV-1",
            "po_number": "PO-1",
            "total_invoiced": 95,
            "disputed_amount": 5,
            "approved_remittance": 90,
            "base_variance": 10,
            "fuel_variance": -5,
            "fuel_overcharge": 0,
            "billed_fuel_surcharge": 95,
            "authorized_fuel_surcharge": 100,
        }
    )

    assert '"Fuel surcharge"' not in prompt
    assert "billed_fuel_surcharge" not in prompt
    assert "authorized_fuel_surcharge" not in prompt


def test_fuel_within_tolerance_is_not_added_to_dispute_memo():
    exception = {
        "invoice_number": "INV-1",
        "po_number": "PO-1",
        "disputed_amount": 0,
        "fuel_overcharge": 0.5,
        "fuel_disputed_overcharge": 0,
        "billed_fuel_surcharge": 45.5,
        "authorized_fuel_surcharge": 45,
        "fuel_index_price": 1.95,
    }

    assert fuel_dispute_line(exception) == ""


def test_short_pay_statement_uses_itemized_unauthorized_accessorials():
    statement = short_pay_statement(
        {
            **exception_record(),
            "po_number": "PO-1007",
            "total_invoiced": 3645,
            "disputed_amount": 557.5,
            "approved_remittance": 3087.5,
            "unauthorized_accessorial_items": [
                {"description": "Driver Detention", "overage": 90}
            ],
            "base_variance": 430,
        }
    )

    assert statement == (
        "We have approved and queued a remittance of $3,087.50 for payment under "
        "PO-1007. The remaining $557.50 has been short-paid due to base rate "
        "variance ($430.00) and fuel surcharge variance ($20.00) and unauthorized "
        "driver detention ($90.00)."
    )


def test_short_pay_statement_does_not_list_underbilled_components():
    statement = short_pay_statement(
        {
            "po_number": "PO-1",
            "total_invoiced": 95,
            "disputed_variance": 0,
            "disputed_amount": 0,
            "approved_remittance": 95,
            "base_variance": -5,
            "fuel_variance": 0,
            "accessorial_variance": -5,
            "unauthorized_accessorial_items": [],
        }
    )

    assert "base rate credit" not in statement
    assert "accessorial" not in statement
    assert "no disputed overcharge" in statement


def test_fuel_dispute_line_uses_verified_aip_values():
    line = fuel_dispute_line(
        {
            "invoice_number": "INV-1007",
            "billed_fuel_surcharge": 285,
            "authorized_fuel_surcharge": 123.75,
            "fuel_index_price": 1.95,
            "fuel_base_peg": 1.5,
            "fuel_overcharge": 161.25,
        }
    )

    assert line == (
        "Fuel surcharge disputed on INV-1007: Billed at $285.00 AUD vs. contractual "
        "allowance of $123.75 AUD based on published AIP Diesel Index of $1.95 AUD/L "
        "(Agreed contract peg $1.50 AUD/L @ 0.5% per 5¢ step). Overcharge of "
        "$161.25 AUD has been deducted from remittance."
    )


def test_generated_memo_appends_the_deterministic_fuel_dispute_line(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="AI-generated letter"))]
    )

    class FakeOpenAI:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    invoice = {
        **exception_record(),
        "invoice_number": "INV-1007",
        "billed_fuel_surcharge": 285,
        "authorized_fuel_surcharge": 123.75,
        "fuel_index_price": 1.95,
        "fuel_base_peg": 1.5,
        "fuel_overcharge": 161.25,
    }

    memo = draft_dispute(invoice, api_key="test-key")

    assert memo.endswith(fuel_dispute_line(invoice))
