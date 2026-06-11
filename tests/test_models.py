"""Tests for models.py — component-level checks plus end-to-end scenarios."""
from __future__ import annotations

from datetime import date

import pytest

from models import (
    ExitValue,
    LeaseComparator,
    LEASE_END_DATE,
    PurchaseInputs,
    RunningCosts,
    TaxParams,
    breakeven_buyout,
    ecs_years_active,
    lease_year_costs,
    monthly_cost,
    ownership_year_costs,
    per_mile_tax_active_fraction,
    suggest_exit_pct,
    total_cost,
)


# ---------------------------------------------------------------------------
# Component-level
# ---------------------------------------------------------------------------

def test_ecs_active_during_hold_central():
    """Hold Apr 2027 → Apr 2030 — ECS active full 3 yrs (ECS window Apr25-Apr30)."""
    tax = TaxParams()
    full = ecs_years_active(tax, date(2027, 4, 1), date(2028, 4, 1))
    assert full == pytest.approx(1.0)


def test_ecs_inactive_after_window_ends():
    """ECS ends Apr 2030 — a year starting Apr 2030 should have 0 share."""
    tax = TaxParams()
    share = ecs_years_active(tax, date(2030, 4, 1), date(2031, 4, 1))
    assert share == 0.0


def test_ecs_partial_overlap_at_window_end():
    """Year Apr 2029 → Apr 2030: ECS active throughout (last full year)."""
    tax = TaxParams()
    share = ecs_years_active(tax, date(2029, 4, 1), date(2030, 4, 1))
    assert share == pytest.approx(1.0)


def test_per_mile_tax_inactive_before_start():
    tax = TaxParams()  # default start Apr 2028
    share = per_mile_tax_active_fraction(tax, date(2027, 4, 1), date(2028, 4, 1))
    assert share == 0.0


def test_per_mile_tax_partial_in_start_year():
    tax = TaxParams()
    share = per_mile_tax_active_fraction(tax, date(2027, 10, 1), date(2028, 10, 1))
    # ~half the year is on/after Apr 2028
    assert 0.45 < share < 0.55


def test_per_mile_tax_fully_active_later():
    tax = TaxParams()
    share = per_mile_tax_active_fraction(tax, date(2029, 4, 1), date(2030, 4, 1))
    assert share == pytest.approx(1.0)


def test_per_mile_tax_disabled_returns_zero():
    tax = TaxParams(per_mile_tax_enabled=False)
    share = per_mile_tax_active_fraction(tax, date(2029, 4, 1), date(2030, 4, 1))
    assert share == 0.0


def test_depreciation_straight_line_sums_to_total():
    purchase = PurchaseInputs(buyout_price=26_000, hold_years=3.0, annual_miles=20_000)
    exit_val = ExitValue(method="pct_of_buyout", pct_retained=0.5)
    years = ownership_year_costs(purchase, RunningCosts(), TaxParams(), exit_val)
    expected_total_dep = 26_000 - 13_000
    assert sum(y.depreciation for y in years) == pytest.approx(expected_total_dep)


def test_tyre_cost_scales_with_miles():
    running = RunningCosts(tyre_interval_miles=40_000, tyre_set_cost=1_000)
    purchase = PurchaseInputs(annual_miles=20_000, hold_years=2.0)
    years = ownership_year_costs(purchase, running, TaxParams(), ExitValue())
    # 20k miles / 40k interval = 0.5 sets per year × £1000 = £500/year
    assert years[0].tyres == pytest.approx(500)


def test_ved_present_every_year():
    years = ownership_year_costs(PurchaseInputs(), RunningCosts(), TaxParams(), ExitValue())
    assert all(y.ved == pytest.approx(200) for y in years)


def test_suggest_exit_pct_monotonic():
    """Longer hold → lower retained value (sanity check)."""
    assert suggest_exit_pct(2.0) > suggest_exit_pct(3.0) > suggest_exit_pct(5.0)


# ---------------------------------------------------------------------------
# End-to-end scenarios with hand-checked totals
# ---------------------------------------------------------------------------

def test_scenario_central_3yr():
    """Central case: £26k buyout, 3yr hold, 20k/yr, ends at £13k.

    Year-by-year ownership rolls up the documented categories. We assert the
    annual TOTAL is in a plausible band rather than to-the-penny, because
    several pro-rated taxes shift between years.
    """
    purchase = PurchaseInputs(buyout_price=26_000, hold_years=3.0,
                              annual_miles=20_000, cost_of_capital_pct=5.0)
    running = RunningCosts()
    tax = TaxParams()
    exit_val = ExitValue(method="pct_of_buyout", pct_retained=0.5)

    years = ownership_year_costs(purchase, running, tax, exit_val)
    grand = total_cost(years)
    per_month = monthly_cost(years, 3.0)
    # Sanity bounds for a £26k buy-out + 3 years of running costs + 60k tyre wear
    assert 22_000 < grand < 32_000
    assert 600 < per_month < 900
    # Pin the deterministic, decision-independent components so a whole category
    # silently dropping out (e.g. opportunity cost) is caught despite the wide bands.
    assert sum(y.depreciation for y in years) == pytest.approx(13_000)
    assert sum(y.opportunity_cost for y in years) == pytest.approx(2_925, rel=1e-3)


def test_scenario_lease_comparator_central():
    """Fully inclusive personal lease — monthly equals the headline lease cost."""
    lease = LeaseComparator(monthly_cost=700, mileage_allowance=20_000,
                            includes_service=True, includes_ved=True,
                            includes_insurance=True, includes_eved=True,
                            includes_tyres=True, lease_type="personal")
    years = lease_year_costs(lease, RunningCosts(), TaxParams(),
                             annual_miles=20_000, hold_years=3.0)
    monthly = monthly_cost(years, 3.0)
    assert monthly == pytest.approx(700)


def test_lease_includes_tyres_zeroes_tyre_cost():
    """When the lease bundles tyres, lease side shouldn't carry a tyre line."""
    base = dict(monthly_cost=700, mileage_allowance=20_000)
    incl = LeaseComparator(includes_tyres=True, **base)
    excl = LeaseComparator(includes_tyres=False, **base)
    years_incl = lease_year_costs(incl, RunningCosts(), TaxParams(), 20_000, 3.0)
    years_excl = lease_year_costs(excl, RunningCosts(), TaxParams(), 20_000, 3.0)
    assert all(y.tyres == 0 for y in years_incl)
    assert all(y.tyres > 400 for y in years_excl)


def test_lease_includes_eved_zeroes_per_mile_tax():
    """Per-mile tax shouldn't be billed twice when the lease bundles it."""
    base = dict(monthly_cost=700, mileage_allowance=20_000, includes_insurance=True)
    incl = LeaseComparator(includes_eved=True, **base)
    excl = LeaseComparator(includes_eved=False, **base)
    years_incl = lease_year_costs(incl, RunningCosts(), TaxParams(), 20_000, 3.0)
    years_excl = lease_year_costs(excl, RunningCosts(), TaxParams(), 20_000, 3.0)
    assert all(y.per_mile_tax == 0 for y in years_incl)
    # When excluded, per-mile tax kicks in for years 2 and 3 (Apr 2028 start)
    assert sum(y.per_mile_tax for y in years_excl) > 1_000


def test_battery_reserve_flows_through():
    """battery_reserve_annual adds to each ownership year at the chosen rate."""
    running = RunningCosts(battery_reserve_annual=700)
    years = ownership_year_costs(PurchaseInputs(), running, TaxParams(), ExitValue())
    assert all(y.battery_reserve == pytest.approx(700) for y in years)


def test_battery_reserve_default_zero():
    """No reserve unless the user opts in."""
    years = ownership_year_costs(PurchaseInputs(), RunningCosts(), TaxParams(), ExitValue())
    assert all(y.battery_reserve == 0 for y in years)


def test_salary_sacrifice_lease_applies_tax_savings_and_bik():
    """Sal-sac at higher rate: gross saves 42%, BiK adds back at 40% × p11d × rate."""
    lease = LeaseComparator(
        monthly_cost=1_000, lease_type="salary_sacrifice", tax_band="higher",
        p11d_value=66_124, includes_insurance=True, mileage_allowance=20_000,
    )
    years = lease_year_costs(lease, RunningCosts(), TaxParams(),
                             annual_miles=20_000, hold_years=3.0)
    # Year 1 (Apr 2027-Apr 2028): BiK 5%; year 2: 7%; year 3: 9%
    assert years[0].lease_payments == pytest.approx(12_000)
    assert years[0].tax_savings == pytest.approx(-12_000 * 0.42)
    # BiK Y1: 66124 × 5% × 40% = 1,322.48
    assert years[0].bik_tax == pytest.approx(66_124 * 0.05 * 0.40, rel=1e-3)
    # BiK Y2: 7%
    assert years[1].bik_tax == pytest.approx(66_124 * 0.07 * 0.40, rel=1e-3)
    # BiK Y3: 9%
    assert years[2].bik_tax == pytest.approx(66_124 * 0.09 * 0.40, rel=1e-3)


def test_salary_sacrifice_net_cost_lower_than_personal_for_higher_rate():
    """Salary sacrifice should beat a personal lease at the same gross for a higher-rate payer."""
    base = dict(monthly_cost=1_000, mileage_allowance=20_000,
                includes_insurance=True, p11d_value=66_124)
    personal = LeaseComparator(lease_type="personal", **base)
    salsac = LeaseComparator(lease_type="salary_sacrifice", tax_band="higher", **base)
    p_years = lease_year_costs(personal, RunningCosts(), TaxParams(), 20_000, 3.0)
    s_years = lease_year_costs(salsac, RunningCosts(), TaxParams(), 20_000, 3.0)
    assert total_cost(s_years) < total_cost(p_years)


def test_company_car_zero_lease_payment_but_bik():
    """Company-car: employer pays lease, employee owes BiK only."""
    lease = LeaseComparator(monthly_cost=1_000, lease_type="company_car",
                            tax_band="higher", p11d_value=66_124)
    years = lease_year_costs(lease, RunningCosts(), TaxParams(),
                             annual_miles=20_000, hold_years=3.0)
    assert all(y.lease_payments == 0 for y in years)
    assert all(y.tax_savings == 0 for y in years)
    assert all(y.bik_tax > 0 for y in years)


def test_personal_lease_zero_tax_fields():
    """Personal lease keeps the existing behaviour: no tax adjustments."""
    lease = LeaseComparator(monthly_cost=700, lease_type="personal")
    years = lease_year_costs(lease, RunningCosts(), TaxParams(),
                             annual_miles=20_000, hold_years=3.0)
    assert all(y.tax_savings == 0 for y in years)
    assert all(y.bik_tax == 0 for y in years)


def test_bik_rate_for_tax_year():
    """Spot-check published rates."""
    from models import bik_rate_for_tax_year
    assert bik_rate_for_tax_year(2027) == 5.0
    assert bik_rate_for_tax_year(2028) == 7.0
    assert bik_rate_for_tax_year(2029) == 9.0
    assert bik_rate_for_tax_year(2035) == 9.0  # extrapolated cap


def test_evaluate_preset_returns_kpis():
    """evaluate_preset gives back monthly KPIs the UI can render side-by-side."""
    from models import evaluate_preset
    result = evaluate_preset("central", hold_years=3.0, annual_miles=20_000)
    assert "own_monthly" in result and "lease_monthly" in result
    assert "breakeven" in result and "delta_monthly" in result
    # Sanity: central scenario monthly should be in a plausible band
    assert 500 < result["own_monthly"] < 1_200
    assert 500 < result["lease_monthly"] < 1_200


def test_lease_includes_insurance_zeroes_it_out():
    """When includes_insurance=True, insurance is excluded from lease cost."""
    base = dict(monthly_cost=700, mileage_allowance=20_000, insurance_annual=1_500)
    incl = LeaseComparator(includes_insurance=True, **base)
    excl = LeaseComparator(includes_insurance=False, **base)
    years_incl = lease_year_costs(incl, RunningCosts(), TaxParams(), 20_000, 3.0)
    years_excl = lease_year_costs(excl, RunningCosts(), TaxParams(), 20_000, 3.0)
    assert all(y.insurance == 0 for y in years_incl)
    assert all(y.insurance == pytest.approx(1_500) for y in years_excl)


def test_scenario_lease_with_excess_mileage():
    lease = LeaseComparator(monthly_cost=700, mileage_allowance=15_000,
                            excess_pence_per_mile=10.0)
    years = lease_year_costs(lease, RunningCosts(), TaxParams(),
                             annual_miles=25_000, hold_years=3.0)
    # 25k - 15k = 10k excess miles × 10p = £1,000/year
    for y in years:
        assert y.excess_mileage == pytest.approx(1_000)


def test_breakeven_makes_ownership_match_lease():
    """The breakeven price should produce equal monthly costs (within £1)."""
    purchase = PurchaseInputs()
    running = RunningCosts()
    tax = TaxParams()
    exit_val = ExitValue(method="pct_of_buyout", pct_retained=0.5)
    lease = LeaseComparator()

    price = breakeven_buyout(purchase, running, tax, exit_val, lease)
    p2 = purchase.model_copy(update={"buyout_price": price})
    own_m = monthly_cost(ownership_year_costs(p2, running, tax, exit_val), purchase.hold_years)
    lease_m = monthly_cost(
        lease_year_costs(lease, running, tax, purchase.annual_miles, purchase.hold_years),
        purchase.hold_years,
    )
    assert abs(own_m - lease_m) < 1.0


def test_opportunity_cost_zero_when_rate_zero():
    purchase = PurchaseInputs(cost_of_capital_pct=0.0)
    years = ownership_year_costs(purchase, RunningCosts(), TaxParams(), ExitValue())
    assert all(y.opportunity_cost == 0 for y in years)


def test_partial_trailing_year_pro_rates_costs():
    """A 2.5-year hold should produce 3 buckets, the last at fraction 0.5."""
    purchase = PurchaseInputs(hold_years=2.5)
    years = ownership_year_costs(purchase, RunningCosts(), TaxParams(), ExitValue())
    assert len(years) == 3
    assert years[-1].fraction == pytest.approx(0.5)
    # Insurance in the partial year should be half the annual
    assert years[-1].insurance == pytest.approx(years[0].insurance * 0.5)


# ---------------------------------------------------------------------------
# Additional coverage — branches and edges the suite above left untested
# ---------------------------------------------------------------------------

def test_ecs_partial_overlap_straddling_window_start():
    """A billing year straddling ECS start (Apr 2025) is ~half active (183/365)."""
    tax = TaxParams()
    share = ecs_years_active(tax, date(2024, 10, 1), date(2025, 10, 1))
    assert share == pytest.approx(0.5, abs=0.01)


def test_ecs_partial_overlap_straddling_window_end():
    """A billing year straddling ECS end (Apr 2030) is ~half active (182/365)."""
    tax = TaxParams()
    share = ecs_years_active(tax, date(2029, 10, 1), date(2030, 10, 1))
    assert share == pytest.approx(0.5, abs=0.01)


def test_depreciation_absolute_method():
    """The 'absolute' exit method depreciates the buy-out down to the absolute sale price."""
    purchase = PurchaseInputs(buyout_price=26_000, hold_years=3.0)
    exit_val = ExitValue(method="absolute", absolute_value=10_000)
    years = ownership_year_costs(purchase, RunningCosts(), TaxParams(), exit_val)
    assert sum(y.depreciation for y in years) == pytest.approx(16_000)


def test_partial_trailing_year_depreciation_pro_rates():
    """Partial-year depreciation uses fraction/hold_years, and the total stays exact."""
    purchase = PurchaseInputs(buyout_price=26_000, hold_years=2.5)
    exit_val = ExitValue(method="pct_of_buyout", pct_retained=0.5)
    years = ownership_year_costs(purchase, RunningCosts(), TaxParams(), exit_val)
    assert sum(y.depreciation for y in years) == pytest.approx(13_000)
    assert years[-1].depreciation == pytest.approx(2_600)  # 13000 * (0.5/2.5)


def test_suggest_exit_pct_out_of_range_fallback():
    """Holds that snap to a key outside the anchor table fall back to 0.50."""
    assert suggest_exit_pct(1.0) == pytest.approx(0.50)
    assert suggest_exit_pct(6.0) == pytest.approx(0.50)


def test_evaluate_preset_scenarios_ordered():
    """Pessimistic costs more to own than central, which costs more than optimistic.

    Exercises the pessimistic/optimistic branches of preset() (otherwise only
    'central' is ever built) and pins the cheaper→pricier ownership ordering.
    """
    from models import evaluate_preset
    pess = evaluate_preset("pessimistic", hold_years=3.0, annual_miles=20_000)
    cen = evaluate_preset("central", hold_years=3.0, annual_miles=20_000)
    opt = evaluate_preset("optimistic", hold_years=3.0, annual_miles=20_000)
    assert pess["own_monthly"] > cen["own_monthly"] > opt["own_monthly"]


def test_company_car_driver_borne_lines_still_apply():
    """For a company car, unbundled insurance/tyres/per-mile still fall on the driver."""
    lease = LeaseComparator(monthly_cost=1_000, lease_type="company_car",
                            tax_band="higher", p11d_value=66_124,
                            includes_eved=False, includes_tyres=False,
                            includes_insurance=False)
    years = lease_year_costs(lease, RunningCosts(), TaxParams(), 20_000, 3.0)
    assert all(y.lease_payments == 0 for y in years)
    assert all(y.bik_tax > 0 for y in years)
    assert all(y.insurance > 0 for y in years)
    assert all(y.tyres > 0 for y in years)
    assert sum(y.per_mile_tax for y in years) > 0  # year 1 is 0 (tax starts Apr 2028)


def test_breakeven_returns_nan_when_no_crossing():
    """No breakeven when ownership is dearer (or cheaper) than the lease at every price."""
    import math
    purchase = PurchaseInputs()
    running = RunningCosts()
    tax = TaxParams()
    exit_val = ExitValue(method="pct_of_buyout", pct_retained=0.5)
    cheap = LeaseComparator(monthly_cost=50, lease_type="personal")
    assert math.isnan(breakeven_buyout(purchase, running, tax, exit_val, cheap))
    dear = LeaseComparator(monthly_cost=5_000, lease_type="personal")
    assert math.isnan(breakeven_buyout(purchase, running, tax, exit_val, dear))


def test_add_years_leap_years():
    """Verify add_years handles leap years and normal years correctly."""
    from models import add_years
    # 2024-02-29 is a leap year. Adding 1 year should result in 2025-02-28.
    assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    # Adding 4 years should result in 2028-02-29 (another leap year).
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)
    # Normal date adding years.
    assert add_years(date(2023, 5, 15), 2) == date(2025, 5, 15)


def test_bik_tax_year_boundary():
    """Verify BiK tax year boundary uses precise April 6 start."""
    from models import LeaseComparator, lease_year_costs, RunningCosts, TaxParams
    lease = LeaseComparator(
        monthly_cost=1000, lease_type="salary_sacrifice", tax_band="higher",
        p11d_value=100000, includes_insurance=True, mileage_allowance=20000
    )
    # Year starts 2027-10-04, ends 2028-10-04. Midpoint is 2028-04-04.
    # mid < April 6 -> tax year 2027 -> BiK 5%.
    # BiK tax = 100,000 * 5% * 40% = 2,000.
    years_2027 = lease_year_costs(lease, RunningCosts(), TaxParams(), 20000, 1.0, start=date(2027, 10, 4))
    assert years_2027[0].bik_tax == pytest.approx(100000 * 0.05 * 0.40)

    # Year starts 2027-10-08, ends 2028-10-08. Midpoint is 2028-04-08.
    # mid >= April 6 -> tax year 2028 -> BiK 7%.
    # BiK tax = 100,000 * 7% * 40% = 2,800.
    years_2028 = lease_year_costs(lease, RunningCosts(), TaxParams(), 20000, 1.0, start=date(2027, 10, 8))
    assert years_2028[0].bik_tax == pytest.approx(100000 * 0.07 * 0.40)


def test_presets_scale_with_hold_years():
    """Verify presets scale exit percentages dynamically based on hold_years."""
    from models import preset, suggest_exit_pct
    # For a 3-year hold:
    p_3 = preset("central", hold_years=3.0)
    assert p_3["exit"]["pct_retained"] == pytest.approx(suggest_exit_pct(3.0)) # 0.55
    
    opt_3 = preset("optimistic", hold_years=3.0)
    assert opt_3["exit"]["pct_retained"] == pytest.approx(0.60) # 0.55 + 0.05
    
    pess_3 = preset("pessimistic", hold_years=3.0)
    assert pess_3["exit"]["pct_retained"] == pytest.approx(0.38) # 0.55 - 0.17

    # For a 5-year hold:
    p_5 = preset("central", hold_years=5.0)
    assert p_5["exit"]["pct_retained"] == pytest.approx(suggest_exit_pct(5.0)) # 0.32
    
    opt_5 = preset("optimistic", hold_years=5.0)
    assert opt_5["exit"]["pct_retained"] == pytest.approx(0.32 + 0.05) # 0.37
    
    pess_5 = preset("pessimistic", hold_years=5.0)
    assert pess_5["exit"]["pct_retained"] == pytest.approx(0.32 - 0.17) # 0.15


# ---------------------------------------------------------------------------
# Market lease deals — upfront amortisation + normalisation
# ---------------------------------------------------------------------------

def test_initial_payment_amortises_into_lease_gross():
    """A £9k upfront over a 3-yr hold adds £3k/yr to the lease gross, evenly."""
    base = dict(monthly_cost=600, mileage_allowance=20_000, includes_insurance=True,
                includes_service=True, includes_tyres=True, includes_eved=True,
                lease_type="personal")
    no_upfront = LeaseComparator(initial_payment=0, **base)
    with_upfront = LeaseComparator(initial_payment=9_000, **base)
    y0 = lease_year_costs(no_upfront, RunningCosts(), TaxParams(), 20_000, 3.0)
    y1 = lease_year_costs(with_upfront, RunningCosts(), TaxParams(), 20_000, 3.0)
    # Each full year carries 9000/3 = 3000 more gross than the no-upfront case.
    for a, b in zip(y0, y1):
        assert b.lease_payments - a.lease_payments == pytest.approx(3_000)
    # Effective monthly rises by exactly 9000 / 36 = £250/mo.
    assert (monthly_cost(y1, 3.0) - monthly_cost(y0, 3.0)) == pytest.approx(9_000 / 36)


def test_initial_payment_defaults_zero_preserves_behaviour():
    """Existing leases (no upfront field set) are unchanged."""
    lease = LeaseComparator(monthly_cost=700, mileage_allowance=20_000,
                            includes_insurance=True, includes_service=True,
                            includes_ved=True, includes_eved=True,
                            includes_tyres=True, lease_type="personal")
    years = lease_year_costs(lease, RunningCosts(), TaxParams(), 20_000, 3.0)
    assert monthly_cost(years, 3.0) == pytest.approx(700)


def test_evaluate_market_deal_adds_insurance_maintenance_and_excess():
    """A 5k-allowance PCH deal at 20k/yr picks up insurance, service, tyres and excess."""
    from models import MarketDeal, evaluate_market_deal
    running = RunningCosts(insurance_annual=950, service_annual=300,
                           tyre_interval_miles=35_000, tyre_set_cost=950)
    deal = MarketDeal(source="Carwow", monthly_cost=574, initial_payment=0,
                      mileage_allowance=5_000, excess_pence_per_mile=10.0)
    res = evaluate_market_deal(deal, running, TaxParams(),
                               annual_miles=20_000, hold_years=3.0)
    # Headline is the floor; all-in effective monthly must be higher.
    assert res["effective_monthly"] > 574
    # Insurance ≈ £950/yr → ~£79/mo.
    assert res["insurance_mo"] == pytest.approx(950 / 12, rel=1e-3)
    # Excess: 15k over allowance × 10p = £1,500/yr → £125/mo.
    assert res["excess_mo"] == pytest.approx(1_500 / 12, rel=1e-3)
    # Maintenance present (service + tyre wear at 20k/yr).
    assert res["service_mo"] > 0 and res["tyres_mo"] > 0


def test_evaluate_market_deal_amortises_upfront():
    """Upfront flows into the effective monthly at exactly upfront / months."""
    from models import MarketDeal, evaluate_market_deal
    running = RunningCosts()
    common = dict(source="X", monthly_cost=600, mileage_allowance=20_000)
    no_up = evaluate_market_deal(MarketDeal(initial_payment=0, **common),
                                 running, TaxParams(), 20_000, 3.0)
    up = evaluate_market_deal(MarketDeal(initial_payment=5_400, **common),
                              running, TaxParams(), 20_000, 3.0)
    assert up["upfront_mo"] == pytest.approx(5_400 / 36)
    assert (up["effective_monthly"] - no_up["effective_monthly"]) == pytest.approx(5_400 / 36)


def test_evaluate_market_deal_toggles_off_extras():
    """With insurance + maintenance off, only lease + upfront + excess remain."""
    from models import MarketDeal, evaluate_market_deal
    deal = MarketDeal(source="X", monthly_cost=600, mileage_allowance=20_000)
    res = evaluate_market_deal(deal, RunningCosts(), TaxParams(), 20_000, 3.0,
                               add_insurance=False, add_maintenance=False)
    assert res["insurance_mo"] == 0
    assert res["service_mo"] == 0 and res["tyres_mo"] == 0
    # No upfront, no excess (allowance == usage) → effective equals headline.
    assert res["effective_monthly"] == pytest.approx(600)


def test_market_deal_shorter_term_amortises_over_its_own_term():
    """A 24-month deal held 3 years is priced over its 24 months, not 36 —
    upfront divides by 24 and no months are fabricated at the expired rate."""
    from models import MarketDeal, evaluate_market_deal
    common = dict(source="X", monthly_cost=500, mileage_allowance=20_000,
                  term_months=24)
    no_up = evaluate_market_deal(MarketDeal(initial_payment=0, **common),
                                 RunningCosts(), TaxParams(), 20_000, 3.0,
                                 add_insurance=False, add_maintenance=False)
    up = evaluate_market_deal(MarketDeal(initial_payment=4_800, **common),
                              RunningCosts(), TaxParams(), 20_000, 3.0,
                              add_insurance=False, add_maintenance=False)
    assert up["eval_months"] == pytest.approx(24)
    assert up["upfront_mo"] == pytest.approx(4_800 / 24)
    assert (up["effective_monthly"] - no_up["effective_monthly"]) == pytest.approx(4_800 / 24)
    # Total covers only the deal's own life: 24 × £500 + upfront.
    assert up["total"] == pytest.approx(24 * 500 + 4_800)


def test_market_deal_upfront_amortises_exactly_on_fractional_hold():
    """A 2.5-year hold must spread the upfront to exactly the upfront — the
    partial year takes a pro-rated share (catches a dropped ×fraction)."""
    from models import MarketDeal, evaluate_market_deal
    common = dict(source="X", monthly_cost=600, mileage_allowance=20_000,
                  term_months=36)
    no_up = evaluate_market_deal(MarketDeal(initial_payment=0, **common),
                                 RunningCosts(), TaxParams(), 20_000, 2.5,
                                 add_insurance=False, add_maintenance=False)
    up = evaluate_market_deal(MarketDeal(initial_payment=3_000, **common),
                              RunningCosts(), TaxParams(), 20_000, 2.5,
                              add_insurance=False, add_maintenance=False)
    assert up["eval_months"] == pytest.approx(30)
    assert (up["total"] - no_up["total"]) == pytest.approx(3_000)
    assert (up["effective_monthly"] - no_up["effective_monthly"]) == pytest.approx(3_000 / 30)


def test_market_deal_wiring_uses_non_default_excess_and_insurance():
    """Deal excess rate and the user's insurance must actually be wired through —
    values deliberately differ from the LeaseComparator field defaults."""
    from models import MarketDeal, evaluate_market_deal
    running = RunningCosts(insurance_annual=1_200)
    deal = MarketDeal(source="NVC", monthly_cost=1_180.70,
                      mileage_allowance=10_000, excess_pence_per_mile=16.8)
    res = evaluate_market_deal(deal, running, TaxParams(),
                               annual_miles=20_000, hold_years=3.0,
                               add_insurance=True, add_maintenance=False)
    # 10k over allowance × 16.8p = £1,680/yr → £140/mo (not £83.33 at 10p).
    assert res["excess_mo"] == pytest.approx(1_680 / 12)
    assert res["insurance_mo"] == pytest.approx(1_200 / 12)


def test_market_deal_components_sum_to_effective_monthly():
    """The UI columns must always sum: lease line (incl. upfront) + excess +
    insurance + service + tyres == effective monthly."""
    from models import MarketDeal, evaluate_market_deal
    deal = MarketDeal(source="X", monthly_cost=826.12, initial_payment=249,
                      mileage_allowance=15_000, excess_pence_per_mile=12.0)
    res = evaluate_market_deal(deal, RunningCosts(), TaxParams(),
                               annual_miles=20_000, hold_years=3.0)
    assert res["lease_line_mo"] == pytest.approx(res["headline_monthly"] + res["upfront_mo"])
    assert res["effective_monthly"] == pytest.approx(
        res["lease_line_mo"] + res["excess_mo"] + res["insurance_mo"]
        + res["service_mo"] + res["tyres_mo"])


def test_market_deal_eved_flag_adds_per_mile_tax():
    """include_eved=False bills the per-mile EV tax on top of the deal: 2 of the
    3 hold years (Apr 2027–Apr 2030) fall after the Apr-2028 start at 3p/mile."""
    from models import MarketDeal, evaluate_market_deal
    deal = MarketDeal(source="X", monthly_cost=700, mileage_allowance=20_000)
    kwargs = dict(annual_miles=20_000, hold_years=3.0,
                  add_insurance=False, add_maintenance=False)
    inc = evaluate_market_deal(deal, RunningCosts(), TaxParams(), **kwargs)
    exc = evaluate_market_deal(deal, RunningCosts(), TaxParams(),
                               include_eved=False, **kwargs)
    expected = 20_000 * 0.03 * 2 / 36  # £1,200 over the hold → £33.33/mo
    assert (exc["effective_monthly"] - inc["effective_monthly"]) == pytest.approx(expected)


def test_market_deal_insurance_override_replaces_running_assumption():
    """A per-deal insurance figure (M50/M60 insure higher) replaces the
    RunningCosts baseline; None keeps the baseline."""
    from models import MarketDeal, evaluate_market_deal
    running = RunningCosts(insurance_annual=950)
    kwargs = dict(annual_miles=20_000, hold_years=3.0)
    base = evaluate_market_deal(
        MarketDeal(source="X", monthly_cost=574, mileage_allowance=20_000),
        running, TaxParams(), **kwargs)
    overridden = evaluate_market_deal(
        MarketDeal(source="X", monthly_cost=574, mileage_allowance=20_000,
                   insurance_override=1_450),
        running, TaxParams(), **kwargs)
    assert base["insurance_mo"] == pytest.approx(950 / 12)
    assert overridden["insurance_mo"] == pytest.approx(1_450 / 12)
    assert (overridden["effective_monthly"] - base["effective_monthly"]) \
        == pytest.approx((1_450 - 950) / 12)


def test_net_of_salary_sacrifice_basic_and_higher():
    """£1,100 gross sacrificed in 2026/27 (4% BiK on £66,124 P11d):
    basic = 72% of gross + 20% BiK tax; higher = 58% of gross + 40% BiK tax."""
    from models import net_of_salary_sacrifice
    on = date(2026, 6, 11)
    basic = net_of_salary_sacrifice(1_100, "basic", 66_124, on)
    higher = net_of_salary_sacrifice(1_100, "higher", 66_124, on)
    assert basic == pytest.approx(1_100 * 0.72 + 66_124 * 0.04 * 0.20 / 12)
    assert higher == pytest.approx(1_100 * 0.58 + 66_124 * 0.04 * 0.40 / 12)
    assert basic == pytest.approx(836.08, abs=0.01)
    assert higher == pytest.approx(726.41, abs=0.5)


def test_net_of_salary_sacrifice_tax_year_boundary():
    """5 Apr 2027 is still 2026/27 (4% BiK); 6 Apr 2027 rolls to 2027/28 (5%)."""
    from models import net_of_salary_sacrifice
    before = net_of_salary_sacrifice(0, "basic", 66_124, date(2027, 4, 5))
    after = net_of_salary_sacrifice(0, "basic", 66_124, date(2027, 4, 6))
    assert before == pytest.approx(66_124 * 0.04 * 0.20 / 12)
    assert after == pytest.approx(66_124 * 0.05 * 0.20 / 12)


def test_jsonable_records_unwraps_numpy_and_nan():
    """DataFrame records (numpy scalars, NaN blanks) become plain JSON-safe Python."""
    import numpy as np
    from models import jsonable_records
    rows = jsonable_records([{
        "Source": "Carwow", "Monthly £": np.float64(574.0),
        "Miles/yr": np.int64(20_000), "Upfront £": float("nan"),
        "Description": None,
    }])
    assert rows[0]["Monthly £"] == 574.0 and type(rows[0]["Monthly £"]) is float
    assert rows[0]["Miles/yr"] == 20_000 and type(rows[0]["Miles/yr"]) is int
    assert rows[0]["Upfront £"] is None        # NaN blanks must not reach json.dumps
    assert rows[0]["Description"] is None


def test_jsonable_records_survives_json_round_trip():
    """The cleaned rows must round-trip json.dumps → json.loads unchanged."""
    import json
    import numpy as np
    from models import jsonable_records
    clean = jsonable_records([{
        "Source": "Nationwide VC", "Monthly £": np.float64(615),
        "Term (mo)": np.int64(36), "Excess p/mi": np.float64(10.0),
        "Upfront £": np.float64("nan"),
    }])
    assert json.loads(json.dumps(clean)) == clean


def test_jsonable_records_passthrough_and_exotic_fallback():
    """Plain Python passes through untouched; exotic cell types fall back to str."""
    from models import jsonable_records
    plain = [{"Source": "(your quote)", "Monthly £": 700.0, "ok": True}]
    assert jsonable_records(plain) == plain
    assert jsonable_records([{"d": date(2026, 6, 10)}]) == [{"d": "2026-06-10"}]
