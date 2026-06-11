"""Unit tests for the LeaseLoco live-quote module (no network — injected fakes).

The sample item is a real API capture (11 Jun 2026, Polestar 2 range 485) whose
inc-VAT figures were verified against LeaseLoco's rendered pages to the penny.
"""
import pytest

from leaseloco import (
    VAT,
    fetch_range_quotes,
    nearest_allowed_mileage,
    normalise_slug,
    notional_insurance,
    quotes_to_deal_rows,
    resolve_range_id,
)

# Trimmed real capture: monthlyPayment/documentFee are EX-VAT.
POLESTAR_ITEM = {
    "vehiclePrice": {
        "vehicleId": 52832,
        "leaseType": 2,
        "term": 36,
        "mileage": 20000,
        "initialPaymentInMonths": 1,
        "stockStatusName": "In Stock",
        "documentFee": 224.9917,
        "monthlyPayment": 450.0833,
        "manufacturerName": "Polestar",
        "rangeName": "2",
        "trimName": "Long Range Prime",
        "performance0to62": 6.2,
        "performanceElectricRange": 409,
    }
}


def test_vat_correction_matches_verified_site_prices():
    """450.0833 ex VAT must become the £540.10 verified on the rendered page."""
    rows = quotes_to_deal_rows([POLESTAR_ITEM], baseline_insurance=950)
    assert rows[0]["Monthly £"] == pytest.approx(540.10)
    assert rows[0]["Upfront £"] == pytest.approx(269.99)  # doc fee × 1.2, 1+35


def test_row_mapping_fields_and_label():
    rows = quotes_to_deal_rows([POLESTAR_ITEM], baseline_insurance=950,
                               fetched_on="11 Jun")
    r = rows[0]
    # Fetch date lives in Source, NOT Description — the dedupe key is
    # (Description, Monthly), and a date inside it would re-append every
    # unchanged quote on a later-day refetch.
    assert r["Source"] == "LeaseLoco live 11 Jun"
    assert "11 Jun" not in r["Description"]
    assert "Polestar 2 Long Range Prime" in r["Description"]
    assert "409mi" in r["Description"] and "6.2s 0-62" in r["Description"]
    assert "In Stock" in r["Description"]
    assert r["Miles/yr"] == 20_000 and r["Term (mo)"] == 36
    assert r["Excess p/mi"] == 10.0  # API doesn't expose excess — default


def test_description_is_stable_across_fetch_days():
    """Same quote fetched on different days must produce an identical dedupe key."""
    day1 = quotes_to_deal_rows([POLESTAR_ITEM], 950, fetched_on="10 Jun")[0]
    day2 = quotes_to_deal_rows([POLESTAR_ITEM], 950, fetched_on="11 Jun")[0]
    assert day1["Description"] == day2["Description"]
    assert day1["Monthly £"] == day2["Monthly £"]


def test_missing_mileage_maps_to_blank_not_zero():
    """A null mileage must become None (app substitutes the user's own miles);
    0 would bill every driven mile as phantom excess."""
    item = {"vehiclePrice": dict(POLESTAR_ITEM["vehiclePrice"], mileage=None)}
    r = quotes_to_deal_rows([item], baseline_insurance=950)[0]
    assert r["Miles/yr"] is None
    assert "?k" in r["Description"]


def test_malformed_item_is_skipped_not_fatal():
    """One vehicle with string-typed numerics must not abort the whole batch."""
    bad = {"vehiclePrice": dict(POLESTAR_ITEM["vehiclePrice"],
                                performanceElectricRange="409",
                                performance0to62="oops",
                                monthlyPayment="not-a-number")}
    rows = quotes_to_deal_rows([bad, POLESTAR_ITEM], baseline_insurance=950)
    assert len(rows) == 1
    assert "Polestar 2" in rows[0]["Description"]


def test_null_results_returns_empty_list_not_none():
    """'results': null must come back as [] — None would be cached as success."""
    out = fetch_range_quotes(485, get_json=lambda url, params: {"results": None})
    assert out == []


def test_multi_month_initial_lands_in_upfront():
    item = {"vehiclePrice": dict(POLESTAR_ITEM["vehiclePrice"],
                                 initialPaymentInMonths=9)}
    r = quotes_to_deal_rows([item], baseline_insurance=950)[0]
    # (9-1) extra monthlies + fee, all inc VAT — the app's upfront convention.
    assert r["Upfront £"] == pytest.approx(8 * 540.10 + 269.99)


def test_dedupes_and_caps_rows():
    items = [POLESTAR_ITEM, POLESTAR_ITEM]
    assert len(quotes_to_deal_rows(items, baseline_insurance=950)) == 1
    many = [{"vehiclePrice": dict(POLESTAR_ITEM["vehiclePrice"],
                                  monthlyPayment=400 + i)} for i in range(20)]
    assert len(quotes_to_deal_rows(many, baseline_insurance=950, max_rows=12)) == 12


def test_notional_insurance_tiers():
    """Calibrated vs Parkers groups: 5.8s→×0.85, 4.2s→×1.10, ≤3.8s→×1.40."""
    assert notional_insurance(5.8, 950) == 810.0    # ~group 32
    assert notional_insurance(4.6, 950) == 950.0    # baseline band
    assert notional_insurance(4.2, 950) == 1_050.0  # ~group 40
    assert notional_insurance(2.9, 950) == 1_330.0  # ~group 48
    assert notional_insurance(None, 950) is None    # unknown → leave blank


def test_normalise_slug_accepts_slug_and_url():
    assert normalise_slug("tesla/model-3") == "tesla/model-3"
    assert normalise_slug("  BYD/Seal/ ") == "byd/seal"
    assert normalise_slug(
        "https://www.leaseloco.com/car-leasing/bmw/i4/extra?x=1") == "bmw/i4"
    with pytest.raises(ValueError):
        normalise_slug("just-words")


def test_resolve_range_id_picks_most_frequent():
    html = '{"range":485,"a":1}{"range":485}{"range":12}'
    assert resolve_range_id("polestar/2", get=lambda url: html) == 485
    with pytest.raises(ValueError):
        resolve_range_id("polestar/2", get=lambda url: "<html>nothing</html>")


def test_fetch_range_quotes_builds_expected_request():
    captured = {}

    def fake_get_json(url, params):
        captured["url"], captured["params"] = url, params
        return {"results": [POLESTAR_ITEM]}

    out = fetch_range_quotes(485, term=36, mileage=20_000, initial_months=1,
                             get_json=fake_get_json)
    assert out == [POLESTAR_ITEM]
    assert captured["url"].endswith("/search/v2")
    assert captured["params"]["ranges"] == 485
    assert captured["params"]["leaseTypes"] == 2  # personal — never business
    assert captured["params"]["mileages"] == 20_000


def test_nearest_allowed_mileage():
    assert nearest_allowed_mileage(20_000) == 20_000
    assert nearest_allowed_mileage(18_000) == 20_000
    assert nearest_allowed_mileage(6_000) == 5_000
    assert nearest_allowed_mileage(45_000) == 30_000
