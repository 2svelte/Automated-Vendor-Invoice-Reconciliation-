from types import SimpleNamespace

import pytest

from src.agent import _prompt, draft_dispute, short_pay_statement


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
