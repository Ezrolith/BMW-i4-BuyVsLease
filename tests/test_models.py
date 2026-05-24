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
