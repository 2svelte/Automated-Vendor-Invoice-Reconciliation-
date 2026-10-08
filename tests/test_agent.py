from src.agent import draft_dispute, fallback_dispute


def exception_record():
    return {
        "invoice_number": "INV-1",
        "po_number": "PO-1",
        "carrier_name": "Carrier One",
        "base_variance": 10,
        "fuel_variance": 20,
        "accessorial_variance": 30,
        "total_overbill": 60,
        "variance_pct": 0.04,
    }


def test_fallback_contains_verified_exception_facts():
    draft = fallback_dispute(exception_record())
    assert "INV-1" in draft
    assert "PO-1" in draft
    assert "$60.00" in draft
    assert "DETERMINISTIC FALLBACK" in draft


def test_draft_without_key_uses_fallback():
    draft = draft_dispute(exception_record(), api_key="")
    assert "DETERMINISTIC FALLBACK" in draft
