"""Streamlit UI for the BMW i4 ownership-vs-lease model.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from models import (
    EV_BIK_SCHEDULE,
    ExitValue,
    LeaseComparator,
    LEASE_END_DATE,
    LIST_PRICE_GBP,
    MARGINAL_RELIEF,
    PurchaseInputs,
    RunningCosts,
    TaxParams,
    bik_rate_for_tax_year,
    breakeven_buyout,
    evaluate_preset,
    lease_year_costs,
    monthly_cost,
    ownership_year_costs,
    preset,
    suggest_exit_pct,
    total_cost,
)

st.set_page_config(page_title="BMW i4 — Buy vs Lease", layout="wide",
                   initial_sidebar_state="expanded")


# ---------------------------------------------------------------------------
# Research summary (shown at top, collapsed)
# ---------------------------------------------------------------------------

RESEARCH_MD = """
**Defaults below come from May 2026 research. Citations are in code comments next to each field.**

| Topic | Default | Source |
|---|---|---|
| VED standard rate (EV, reg before Apr 2025) | £200/yr | [Commons Library CBP-9690](https://commonslibrary.parliament.uk/research-briefings/cbp-9690/) |
| Expensive Car Supplement | £440/yr × 5 yrs from Apr 2025 (ends Apr 2030) | [Honest John 2026/27](https://www.honestjohn.co.uk/news/tax-insurance-and-warranties/2026-04/april-ved-tax-increases-2026-rates-explained/) |
| £50k threshold rise | Doesn't apply — only for EVs reg on/after 1 Apr 2025 | [gov.uk](https://www.gov.uk/government/publications/vehicle-excise-duty-for-expensive-car-supplement-threshold-increase-for-zero-emission-vehicles/increase-in-the-vehicle-excise-duty-expensive-car-supplement-threshold-for-zero-emission-cars) |
| Per-mile EV tax | **3p/mile from Apr 2028, confirmed** | [RAC Autumn Budget 2025](https://www.rac.co.uk/drive/news/motoring-news/autumn-budget-2025/) |
| Buy-out central | £22,000 (2023 i4 eDrive40 M Sport @ 65k miles ~£22,490 retail May 2026) | [Autotrader](https://www.autotrader.co.uk/cars/used/bmw/i4) |
| Buy-out range | £18k optimistic (wholesale) to £30k pessimistic (leasing co holds original residual) | Autotrader trade calc |
| Insurance central | £950/yr (age 40 + group 35-38 + Manchester loading) | [Finder UK Feb 2026, age 40: £756 for group 34](https://www.finder.com/uk/car-insurance/bmw/bmw-i4-insurance-group) |
| Insurance pessimistic | £1,500/yr (renewal-shock anecdote) | [i4talk forum thread](https://www.i4talk.com/threads/uk-insurance-huge-uplift.10768/) |
| Annual service | £300 (£250 indie / £350 dealer) | [bumper.co](https://www.bumper.co/blog/bmw-i4-repair-costs) |
| Tyre set (4 corners fitted) | £950 (premium EV-rated 19") | [pirelli.com](https://www.pirelli.com/tyres/en-ww/car/catalogue/car-brand/bmw/i4) |
| HV battery warranty | 8yr or 100k miles, ~70% capacity guarantee | [Recharged reliability](https://recharged.com/articles/bmw-i4-reliability-2026) |
| Lease at £700/mo | Does NOT typically include insurance — that's a £1k+/mo tier | [Carwow lease deals](https://www.carwow.co.uk/bmw/i4/lease) |

**ECS clock**: EV first reg Apr 2024 → first VED liable Apr 2025 → ECS active Apr 2025 – Apr 2030. All three years inside a 3-year hold from Apr 2027 are billed.

**Battery reserve**: At 20k miles/yr the car crosses 100k miles around year 3 of hold. The 8yr/100k HV-battery warranty expires whichever comes first. The reserve line lets you pre-fund that risk.
"""


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

def _init_state():
    """Seed st.session_state with the 'central' preset, filling in any missing keys.

    Uses setdefault so existing session values (e.g. user tweaks) are preserved
    across code updates that add new keys. Without this, an old session that
    predates a new field will AttributeError when the constructor reads it.
    """
    p = preset("central")
    defaults = {
        "buyout_price": p["purchase"]["buyout_price"],
        "hold_years": 3.0,
        "annual_miles": 20_000,
        "cost_of_capital_pct": p["purchase"]["cost_of_capital_pct"],
        "insurance_annual": p["running"]["insurance_annual"],
        "service_annual": p["running"]["service_annual"],
        "tyre_interval_miles": p["running"]["tyre_interval_miles"],
        "tyre_set_cost": p["running"]["tyre_set_cost"],
        "other_maintenance_annual": p["running"]["other_maintenance_annual"],
        "battery_reserve_annual": p["running"]["battery_reserve_annual"],
        "ved_standard": 200.0,
        "ecs_annual": 440.0,
        "per_mile_tax_enabled": True,
        "per_mile_tax_rate_pence": p["tax"]["per_mile_tax_rate_pence"],
        "per_mile_tax_start": date(2028, 4, 1),
        "exit_method": "pct_of_buyout",
        # Stored as a percentage 0-100; the UI shows "55" not "0.55".
        "pct_retained": p["exit"]["pct_retained"] * 100,
        "absolute_value": 14_000.0,
        "lease_monthly_cost": p["lease"]["monthly_cost"],
        "lease_mileage_allowance": 20_000,
        "lease_excess_pence": 10.0,
        "lease_includes_service": True,
        "lease_includes_ved": True,
        "lease_includes_insurance": p["lease"]["includes_insurance"],
        "lease_includes_eved": True,
        "lease_includes_tyres": True,
        "lease_insurance_annual": p["lease"]["insurance_annual"],
        # Default to salary sacrifice — matches typical "fully inclusive" work
        # leases that bundle insurance, service, VED and tyres. BiK applies
        # automatically using LIST_PRICE_GBP as the P11d value.
        "lease_type": "salary_sacrifice",
        "tax_band": "higher",
        "p11d_value": LIST_PRICE_GBP,
        "initialised": True,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _apply_preset(name: str):
    """Overlay a preset onto current state — user can tweak after."""
    p = preset(name)  # type: ignore[arg-type]
    st.session_state.buyout_price = p["purchase"]["buyout_price"]
    st.session_state.cost_of_capital_pct = p["purchase"]["cost_of_capital_pct"]
    st.session_state.insurance_annual = p["running"]["insurance_annual"]
    st.session_state.service_annual = p["running"]["service_annual"]
    st.session_state.tyre_interval_miles = p["running"]["tyre_interval_miles"]
    st.session_state.tyre_set_cost = p["running"]["tyre_set_cost"]
    st.session_state.other_maintenance_annual = p["running"]["other_maintenance_annual"]
    st.session_state.battery_reserve_annual = p["running"]["battery_reserve_annual"]
    st.session_state.per_mile_tax_rate_pence = p["tax"]["per_mile_tax_rate_pence"]
    st.session_state.per_mile_tax_enabled = p["tax"]["per_mile_tax_enabled"]
    st.session_state.pct_retained = p["exit"]["pct_retained"] * 100
    st.session_state.lease_monthly_cost = p["lease"]["monthly_cost"]
    st.session_state.lease_includes_insurance = p["lease"]["includes_insurance"]
    st.session_state.lease_insurance_annual = p["lease"]["insurance_annual"]
    # Reset lease tax treatment to salary-sacrifice on preset apply so BiK is on
    st.session_state.lease_type = "salary_sacrifice"
    st.session_state.p11d_value = LIST_PRICE_GBP


_init_state()

# Migration: pct_retained used to be a fraction (0-1); now stored as a percent (0-100).
# A stale session value < 1.5 is treated as the old format and rescaled once.
if st.session_state.get("pct_retained", 50) < 1.5:
    st.session_state.pct_retained = st.session_state.pct_retained * 100


# ---------------------------------------------------------------------------
# Soft-validation helper
# ---------------------------------------------------------------------------

def _typical(key: str, low: float, high: float, advice: str = "") -> None:
    """Show a small warning under an input if the value is outside the typical range.

    Doesn't block the input — purely advisory. `advice` is appended after the range.
    """
    v = st.session_state.get(key)
    if v is None:
        return
    if v < low or v > high:
        msg = f"Outside typical range £{low:,.0f}–£{high:,.0f}."
        if advice:
            msg += " " + advice
        st.caption(f":warning: {msg}")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("Assumptions")
st.sidebar.caption("All inputs accept any non-negative value. "
                   "Yellow notes flag anything outside the typical range.")

with st.sidebar.expander("Scenario presets", expanded=False):
    c1, c2, c3 = st.columns(3)
    if c1.button("Pessimistic"):
        _apply_preset("pessimistic"); st.rerun()
    if c2.button("Central"):
        _apply_preset("central"); st.rerun()
    if c3.button("Optimistic"):
        _apply_preset("optimistic"); st.rerun()

with st.sidebar.expander("Buy-out & holding", expanded=True):
    st.number_input("Buy-out price £", min_value=0.0, key="buyout_price", step=500.0)
    _typical("buyout_price", 15_000, 32_000,
             "May 2026 Autotrader: 2023 i4 eDrive40 M Sport @ 65k miles ~£22k retail. "
             "Wholesale ~£18k. Buyout could be £18k-£30k depending on leasing-co policy.")

    st.number_input("Hold period (years)", min_value=0.5, key="hold_years", step=0.5)
    _typical("hold_years", 1.5, 6.0,
             "Beyond 6 years the car would be 9+ yrs old at sale — extrapolation gets shaky.")

    st.number_input("Annual mileage", min_value=0, key="annual_miles", step=1_000)
    _typical("annual_miles", 8_000, 30_000,
             "Far outside this range, tyre/service assumptions probably need a second look.")

    st.number_input("Cost of capital %", min_value=0.0, key="cost_of_capital_pct", step=0.25,
                    help="Opportunity cost on £ tied up in the car (declining balance)")
    _typical("cost_of_capital_pct", 0.0, 10.0,
             "Most people use 3-7% (cash savings rate / mortgage offset / investment return).")

with st.sidebar.expander("Running costs", expanded=True):
    st.number_input("Insurance £/yr", min_value=0.0, key="insurance_annual", step=50.0)
    _typical("insurance_annual", 600, 1_800,
             "Age-40 finder.com Feb 2026: £756/yr for cheapest i4 (group 34). "
             "eDrive40 M Sport is group 35-38 → ~£900, +Manchester loading → £950 central. "
             "Pessimistic preset uses £1,500 for renewal-shock scenarios. "
             "Get a real quote — premiums vary wildly.")

    st.number_input("Service £/yr", min_value=0.0, key="service_annual", step=10.0)
    _typical("service_annual", 180, 450, "Dealer £288-£352, indie ~£250.")

    st.number_input("Tyre interval (miles)", min_value=1_000, key="tyre_interval_miles", step=1_000)
    _typical("tyre_interval_miles", 20_000, 50_000,
             "i4 owners typically report 30-40k miles per set.")

    st.number_input("Tyre set £ (4 corners fitted)", min_value=0.0, key="tyre_set_cost", step=50.0)
    _typical("tyre_set_cost", 600, 1_400,
             "~£235/corner × 4 for premium EV-rated 19\" (Michelin/Pirelli).")

    st.number_input("Other maintenance £/yr", min_value=0.0, key="other_maintenance_annual", step=50.0,
                    help="Wipers, MOT (yrs 3+), brake fluid, 12V battery, misc")
    _typical("other_maintenance_annual", 100, 700)

    # Battery-warranty risk: 8yr/100k from first reg. Exit miles = 41,500 + hold × annual
    exit_miles = 41_500 + st.session_state.hold_years * st.session_state.annual_miles
    car_age_at_exit = (LEASE_END_DATE.year - 2024) + st.session_state.hold_years
    past_warranty = exit_miles > 100_000 or car_age_at_exit > 8
    st.number_input("HV battery reserve £/yr", min_value=0.0,
                    key="battery_reserve_annual", step=100.0,
                    help="Self-insurance for HV-battery risk past the 8yr/100k warranty")
    if past_warranty and st.session_state.battery_reserve_annual == 0:
        st.caption(f":warning: At exit: ~{exit_miles:,.0f} mi, ~{car_age_at_exit:.1f} yrs — "
                   f"past the 8yr/100k battery warranty. Suggest £500-£1,000/yr reserve.")
    elif not past_warranty and st.session_state.battery_reserve_annual > 0:
        st.caption(f":information_source: Exit at ~{exit_miles:,.0f} mi, ~{car_age_at_exit:.1f} yrs — "
                   "still inside HV-battery warranty. Reserve may not be needed.")

with st.sidebar.expander("Tax", expanded=True):
    st.number_input("VED standard £/yr", min_value=0.0, key="ved_standard", step=10.0)
    _typical("ved_standard", 0, 300, "£200/yr is the EV standard rate (2026/27).")

    st.number_input("ECS £/yr", min_value=0.0, key="ecs_annual", step=10.0,
                    help="Expensive Car Supplement, 5 yrs from first VED liability (Apr 2025)")
    _typical("ecs_annual", 0, 600, "£440/yr is the 2026/27 ECS rate.")

    st.checkbox("Per-mile EV tax (Apr 2028)", key="per_mile_tax_enabled")
    st.number_input("Per-mile rate (pence)", min_value=0.0, key="per_mile_tax_rate_pence", step=0.25)
    _typical("per_mile_tax_rate_pence", 0.0, 8.0,
             "Confirmed 3p/mile for BEVs from Apr 2028.")
    st.date_input("Per-mile tax start date", key="per_mile_tax_start")

with st.sidebar.expander("Exit value at end of hold", expanded=True):
    st.radio("Method", ["pct_of_buyout", "absolute"], key="exit_method",
             format_func=lambda x: "% of buy-out retained" if x == "pct_of_buyout" else "Absolute £")
    suggested_pct = suggest_exit_pct(st.session_state.hold_years) * 100
    exit_miles_for_label = 41_500 + st.session_state.hold_years * st.session_state.annual_miles
    st.caption(f"Suggested for {st.session_state.hold_years:.1f} yrs "
               f"({exit_miles_for_label:,.0f} mi at exit): **{suggested_pct:.0f}%** of buy-out")
    if st.session_state.exit_method == "pct_of_buyout":
        st.number_input("% retained at sale", min_value=0.0, key="pct_retained", step=1.0,
                        format="%.0f", help="Enter as a percentage, e.g. 55 = 55% of buy-out")
        if st.button(f"Apply suggested ({suggested_pct:.0f}%)", key="apply_suggested_pct"):
            st.session_state.pct_retained = suggested_pct
            st.rerun()
        _typical("pct_retained", 15, 80,
                 "Above 80% implies almost no depreciation; below 15% implies a write-off.")
    else:
        st.number_input("Sale price £", min_value=0.0, key="absolute_value", step=500.0)
        _typical("absolute_value", 5_000, 30_000)

with st.sidebar.expander("Lease comparator", expanded=True):
    st.number_input("Monthly cost £", min_value=0.0, key="lease_monthly_cost", step=10.0)
    _typical("lease_monthly_cost", 400, 1_200, "Mainstream EV personal-lease band in 2026.")

    st.number_input("Mileage allowance/yr", min_value=0, key="lease_mileage_allowance", step=1_000)
    _typical("lease_mileage_allowance", 5_000, 30_000)

    st.number_input("Excess mileage pence/mile", min_value=0.0, key="lease_excess_pence", step=0.5)
    _typical("lease_excess_pence", 5.0, 30.0,
             "Most contracts charge 8-15p/mile over allowance.")

    st.checkbox("Includes service?", key="lease_includes_service")
    st.checkbox("Includes VED?", key="lease_includes_ved")
    st.checkbox("Includes eVED (per-mile)?", key="lease_includes_eved",
                help="Leases that bundle VED almost always bundle the per-mile EV tax too — "
                     "the leasing co holds the V5 and gets the bill")
    st.checkbox("Includes insurance?", key="lease_includes_insurance",
                help="Fully-inclusive personal leases bundle insurance — most £700/mo PCH deals don't")
    st.checkbox("Includes tyres?", key="lease_includes_tyres",
                help="Some inclusive / sal-sac leases bundle tyre replacement; most personal PCH deals don't")
    if not st.session_state.lease_includes_insurance:
        st.number_input("Insurance £/yr (lease)", min_value=0.0,
                        key="lease_insurance_annual", step=50.0)
        _typical("lease_insurance_annual", 800, 2_500)

with st.sidebar.expander("Lease tax treatment", expanded=True):
    st.radio("Lease type", ["personal", "salary_sacrifice", "company_car"],
             key="lease_type",
             format_func=lambda x: {
                 "personal": "Personal (no tax effect)",
                 "salary_sacrifice": "Salary sacrifice (gross + BiK)",
                 "company_car": "Company car (employer pays + BiK)",
             }[x],
             help="Personal = your money. Sal-sac = gross from salary, saves IT+NI, pay BiK. "
                  "Company car = employer pays, you only pay BiK.")
    st.radio("Income tax band", ["basic", "higher", "additional"],
             key="tax_band",
             format_func=lambda x: {
                 "basic": "Basic (20% IT + 8% NI = 28% relief)",
                 "higher": "Higher (40% IT + 2% NI = 42% relief)",
                 "additional": "Additional (45% IT + 2% NI = 47% relief)",
             }[x])
    st.number_input("P11d value of leased car £", min_value=0.0,
                    key="p11d_value", step=500.0,
                    help=f"Pre-populated with the car's list price (£{LIST_PRICE_GBP:,.0f}). "
                         "Override if the replacement lease car has a different list price. "
                         "Only used when lease type is Salary sacrifice or Company car.")
    # Show the BiK schedule the model will use
    rates_md = " · ".join(
        f"{y}/{str(y+1)[-2:]}: **{bik_rate_for_tax_year(y):.0f}%**"
        for y in (2027, 2028, 2029, 2030)
    )
    st.caption(f"BiK rates applied (gov.uk confirmed): {rates_md}")
    if st.session_state.lease_type == "personal":
        st.caption(":information_source: BiK shown above is not currently applied "
                   "because lease type is **Personal**. Switch to Salary sacrifice "
                   "or Company car for BiK to be added to the lease cost.")


# ---------------------------------------------------------------------------
# Build model objects from state
# ---------------------------------------------------------------------------

purchase = PurchaseInputs(
    buyout_price=st.session_state.buyout_price,
    hold_years=st.session_state.hold_years,
    annual_miles=st.session_state.annual_miles,
    cost_of_capital_pct=st.session_state.cost_of_capital_pct,
)
running = RunningCosts(
    insurance_annual=st.session_state.insurance_annual,
    service_annual=st.session_state.service_annual,
    tyre_interval_miles=st.session_state.tyre_interval_miles,
    tyre_set_cost=st.session_state.tyre_set_cost,
    other_maintenance_annual=st.session_state.other_maintenance_annual,
    battery_reserve_annual=st.session_state.battery_reserve_annual,
)
tax = TaxParams(
    ved_standard=st.session_state.ved_standard,
    ecs_annual=st.session_state.ecs_annual,
    per_mile_tax_enabled=st.session_state.per_mile_tax_enabled,
    per_mile_tax_rate_pence=st.session_state.per_mile_tax_rate_pence,
    per_mile_tax_start=st.session_state.per_mile_tax_start,
)
exit_val = ExitValue(
    method=st.session_state.exit_method,
    pct_retained=st.session_state.pct_retained / 100.0,  # UI stores as %, model wants fraction
    absolute_value=st.session_state.absolute_value,
)
lease = LeaseComparator(
    monthly_cost=st.session_state.lease_monthly_cost,
    mileage_allowance=st.session_state.lease_mileage_allowance,
    excess_pence_per_mile=st.session_state.lease_excess_pence,
    includes_service=st.session_state.lease_includes_service,
    includes_ved=st.session_state.lease_includes_ved,
    includes_insurance=st.session_state.lease_includes_insurance,
    includes_eved=st.session_state.lease_includes_eved,
    includes_tyres=st.session_state.lease_includes_tyres,
    insurance_annual=st.session_state.lease_insurance_annual,
    lease_type=st.session_state.lease_type,
    tax_band=st.session_state.tax_band,
    p11d_value=st.session_state.p11d_value,
)

own_years = ownership_year_costs(purchase, running, tax, exit_val)
lease_years = lease_year_costs(lease, running, tax,
                                annual_miles=purchase.annual_miles,
                                hold_years=purchase.hold_years)

own_total = total_cost(own_years)
lease_total = total_cost(lease_years)
own_mo = monthly_cost(own_years, purchase.hold_years)
lease_mo = monthly_cost(lease_years, purchase.hold_years)
delta_mo = own_mo - lease_mo
breakeven = breakeven_buyout(purchase, running, tax, exit_val, lease)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

st.title("BMW i4 eDrive40 M Sport — Buy out vs fresh lease")
st.caption(f"List price £{LIST_PRICE_GBP:,.0f} · First reg {LEASE_END_DATE.year - 3} · "
           f"Lease ends {LEASE_END_DATE.isoformat()} · Hold {purchase.hold_years:.1f} yrs at "
           f"{purchase.annual_miles:,}/yr")

with st.expander("Research summary & sources", expanded=False):
    st.markdown(RESEARCH_MD)

lease_label = {"personal": "Lease £/month", "salary_sacrifice": "Lease £/mo (net)",
               "company_car": "Lease £/mo (BiK only)"}[lease.lease_type]
# Gross-vs-net breakdown text for sal-sac/company-car
breakdown_help = f"Total over hold: £{lease_total:,.0f}"
if lease.lease_type == "salary_sacrifice":
    gross_total = sum(y.lease_payments for y in lease_years)
    saving_total = sum(y.tax_savings for y in lease_years)  # negative
    bik_total = sum(y.bik_tax for y in lease_years)
    breakdown_help = (f"Gross: £{gross_total:,.0f}  ·  "
                      f"Tax/NI saving: £{saving_total:,.0f}  ·  "
                      f"BiK: £{bik_total:,.0f}  ·  "
                      f"Net total: £{lease_total:,.0f}")
elif lease.lease_type == "company_car":
    bik_total = sum(y.bik_tax for y in lease_years)
    breakdown_help = f"BiK only: £{bik_total:,.0f} over hold (employer pays lease)"

k1, k2, k3, k4 = st.columns(4)
k1.metric("Ownership £/month", f"£{own_mo:,.0f}", help=f"Total over hold: £{own_total:,.0f}")
k2.metric(lease_label, f"£{lease_mo:,.0f}", help=breakdown_help)
k3.metric("Delta (own − lease)", f"£{delta_mo:+,.0f}/mo",
          delta=f"{delta_mo:+,.0f}", delta_color="inverse")
k4.metric("Breakeven buy-out", f"£{breakeven:,.0f}",
          help="Price at which ownership monthly cost = lease monthly cost")

# Lease-side line-item breakdown so the user can see what £X/mo is made of
def _per_mo(getter) -> float:
    return sum(getter(y) for y in lease_years) / (purchase.hold_years * 12)

lease_lines = []
if (gp := _per_mo(lambda y: y.lease_payments)) > 0:
    lease_lines.append(f"Lease gross £{gp:,.0f}")
if (ts := _per_mo(lambda y: y.tax_savings)) < 0:
    lease_lines.append(f"Tax/NI saving £{ts:,.0f}")
if (bk := _per_mo(lambda y: y.bik_tax)) > 0:
    lease_lines.append(f"BiK £{bk:,.0f}")
if (ins := _per_mo(lambda y: y.insurance)) > 0:
    lease_lines.append(f"Insurance £{ins:,.0f} (not bundled)")
if (srv := _per_mo(lambda y: y.service)) > 0:
    lease_lines.append(f"Service £{srv:,.0f} (not bundled)")
if (vd := _per_mo(lambda y: y.ved)) > 0:
    lease_lines.append(f"VED £{vd:,.0f} (not bundled)")
if (eved := _per_mo(lambda y: y.per_mile_tax)) > 0:
    lease_lines.append(f"Per-mile tax £{eved:,.0f} (not bundled)")
if (tyr := _per_mo(lambda y: y.tyres)) > 0:
    lease_lines.append(f"Tyres £{tyr:,.0f} (not bundled)")
if (xs := _per_mo(lambda y: y.excess_mileage)) > 0:
    lease_lines.append(f"Excess miles £{xs:,.0f}")

st.caption(f"**Lease side £{lease_mo:,.0f}/mo** = " + "  +  ".join(lease_lines))

if lease.lease_type == "salary_sacrifice":
    gross_mo = sum(y.lease_payments for y in lease_years) / (purchase.hold_years * 12)
    save_mo = sum(y.tax_savings for y in lease_years) / (purchase.hold_years * 12)
    bik_mo = sum(y.bik_tax for y in lease_years) / (purchase.hold_years * 12)
    net_lease_only = gross_mo + save_mo + bik_mo  # save_mo is already negative
    extras = lease_mo - net_lease_only
    st.caption(
        f"**Lease line** ({lease.tax_band.title()}-rate sal-sac): "
        f"Gross £{gross_mo:,.0f}/mo  →  saving £{save_mo:,.0f}/mo  +  "
        f"BiK £{bik_mo:,.0f}/mo  =  **net £{net_lease_only:,.0f}/mo** "
        f"(then + £{extras:,.0f}/mo for tyres / insurance / etc → "
        f"**total lease side £{lease_mo:,.0f}/mo**)"
    )
elif lease.lease_type == "company_car":
    bik_mo = sum(y.bik_tax for y in lease_years) / (purchase.hold_years * 12)
    extras = lease_mo - bik_mo
    st.caption(
        f"**Company car** ({lease.tax_band.title()}-rate): employer pays the lease; "
        f"you owe BiK of **£{bik_mo:,.0f}/mo** on £{lease.p11d_value:,.0f} P11d. "
        f"Plus £{extras:,.0f}/mo for tyres / insurance / etc → "
        f"**total out-of-pocket £{lease_mo:,.0f}/mo**."
    )

if delta_mo < 0:
    saving_total = -delta_mo * purchase.hold_years * 12
    st.markdown(
        f"### Verdict: **Buy out** ✓ — saves **£{-delta_mo:,.0f}/mo** "
        f"(£{saving_total:,.0f} over {purchase.hold_years:.1f} yrs). "
        f"Any buy-out price below **£{breakeven:,.0f}** beats this lease."
    )
elif delta_mo > 0:
    loss_total = delta_mo * purchase.hold_years * 12
    st.markdown(
        f"### Verdict: **Take the lease** ✓ — owning costs **£{delta_mo:,.0f}/mo more** "
        f"(£{loss_total:,.0f} over {purchase.hold_years:.1f} yrs). "
        f"Buy-out would need to drop below **£{breakeven:,.0f}** to beat the lease."
    )
else:
    st.markdown("### Verdict: **Tie** — both options cost the same per month.")

# Side-by-side scenario range
st.subheader("Scenario range")
st.caption(f"Run the same hold period ({purchase.hold_years:.1f} yrs at "
           f"{purchase.annual_miles:,}/yr) through each preset's assumptions.")

preset_rows = [
    evaluate_preset(name, hold_years=purchase.hold_years, annual_miles=purchase.annual_miles)
    for name in ("pessimistic", "central", "optimistic")
]
preset_df = pd.DataFrame([{
    "Scenario": r["scenario"].title(),
    "Buy-out £": f"£{r['buyout']:,.0f}",
    "Own £/mo": f"£{r['own_monthly']:,.0f}",
    "Lease £/mo": f"£{r['lease_monthly']:,.0f}",
    "Delta £/mo": f"£{r['delta_monthly']:+,.0f}",
    "Breakeven £": f"£{r['breakeven']:,.0f}",
    "Winner": "Own" if r["delta_monthly"] < 0 else "Lease",
} for r in preset_rows])
st.dataframe(preset_df, use_container_width=True, hide_index=True)

st.divider()


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

st.subheader("Annual cost breakdown")

CATEGORIES = [
    ("Depreciation", "depreciation"),
    ("Opportunity cost", "opportunity_cost"),
    ("Lease gross", "lease_payments"),
    ("Tax/NI saving", "tax_savings"),
    ("BiK tax", "bik_tax"),
    ("Excess mileage", "excess_mileage"),
    ("Insurance", "insurance"),
    ("Service", "service"),
    ("Tyres", "tyres"),
    ("Other maintenance", "other_maintenance"),
    ("Battery reserve", "battery_reserve"),
    ("VED", "ved"),
    ("ECS", "ecs"),
    ("Per-mile EV tax", "per_mile_tax"),
]


def _calendar_label(y, side: str) -> str:
    """Render '2027-28 (own)' style labels using actual year-bucket dates."""
    s, e = y.year_start.year, y.year_end.year
    span = f"{s}-{str(e)[-2:]}" if s != e else str(s)
    return f"{span} ({side})"


fig = go.Figure()
year_labels_own = [_calendar_label(y, "own") for y in own_years]
year_labels_lease = [_calendar_label(y, "lease") for y in lease_years]
all_labels = []
for o, l in zip(year_labels_own, year_labels_lease):
    all_labels.append(o)
    all_labels.append(l)

for label, attr in CATEGORIES:
    values = []
    for o, l in zip(own_years, lease_years):
        values.append(getattr(o, attr))
        values.append(getattr(l, attr))
    # Show any non-zero category (incl. negative tax savings)
    if any(abs(v) > 0.5 for v in values):
        fig.add_trace(go.Bar(name=label, x=all_labels, y=values))

fig.update_layout(barmode="stack", height=450, yaxis_title="£ per year",
                  legend=dict(orientation="h", y=-0.2))
st.plotly_chart(fig, use_container_width=True)


st.subheader("Cumulative cost")
own_cum, lease_cum, xlabels = [], [], []
o_running = l_running = 0.0
for o, l in zip(own_years, lease_years):
    o_running += o.total
    l_running += l.total
    own_cum.append(o_running)
    lease_cum.append(l_running)
    xlabels.append(f"End of {o.year_end.isoformat()}")

fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=xlabels, y=own_cum, mode="lines+markers", name="Ownership"))
fig2.add_trace(go.Scatter(x=xlabels, y=lease_cum, mode="lines+markers", name="Lease"))
fig2.update_layout(height=350, yaxis_title="Cumulative £")
st.plotly_chart(fig2, use_container_width=True)


# ---------------------------------------------------------------------------
# Sensitivity tornado
# ---------------------------------------------------------------------------

st.subheader("Sensitivity — ±20% on each input, impact on monthly delta")
st.caption("Positive bar = changing this input upward makes ownership *more* expensive vs lease.")

def _delta_for(overrides: dict) -> float:
    p = purchase.model_copy(update=overrides.get("purchase", {}))
    r = running.model_copy(update=overrides.get("running", {}))
    t = tax.model_copy(update=overrides.get("tax", {}))
    e = exit_val.model_copy(update=overrides.get("exit", {}))
    L = lease.model_copy(update=overrides.get("lease", {}))
    o_m = monthly_cost(ownership_year_costs(p, r, t, e), p.hold_years)
    l_m = monthly_cost(lease_year_costs(L, r, t, p.annual_miles, p.hold_years), p.hold_years)
    return o_m - l_m

SENSITIVITY_INPUTS = [
    ("Buy-out price", "purchase", "buyout_price", purchase.buyout_price),
    ("Annual mileage", "purchase", "annual_miles", purchase.annual_miles),
    ("Cost of capital %", "purchase", "cost_of_capital_pct", purchase.cost_of_capital_pct),
    ("Insurance (own)", "running", "insurance_annual", running.insurance_annual),
    ("Service", "running", "service_annual", running.service_annual),
    ("Tyre set cost", "running", "tyre_set_cost", running.tyre_set_cost),
    ("Tyre interval", "running", "tyre_interval_miles", running.tyre_interval_miles),
    ("Battery reserve", "running", "battery_reserve_annual", running.battery_reserve_annual),
    ("Exit % retained", "exit", "pct_retained", exit_val.pct_retained),
    ("Lease monthly", "lease", "monthly_cost", lease.monthly_cost),
    ("Per-mile tax rate", "tax", "per_mile_tax_rate_pence", tax.per_mile_tax_rate_pence),
]

base_delta = delta_mo
rows = []
for label, group, field, base_value in SENSITIVITY_INPUTS:
    if base_value == 0:
        continue
    up_val = base_value * 1.2
    down_val = base_value * 0.8
    # Some fields must be int — coerce where pydantic expects it.
    if isinstance(base_value, int):
        up_val = int(round(up_val)); down_val = int(round(down_val))
    try:
        up = _delta_for({group: {field: up_val}}) - base_delta
        down = _delta_for({group: {field: down_val}}) - base_delta
    except Exception:
        continue
    rows.append({"input": label, "down": down, "up": up,
                 "swing": abs(up) + abs(down)})

rows.sort(key=lambda r: r["swing"], reverse=True)
# Highlight top 3 inputs with bold colours; rest in muted grey.
def _bar_colour(idx: int, base: str) -> str:
    return base if idx < 3 else "#cccccc"

fig3 = go.Figure()
fig3.add_trace(go.Bar(name="-20%", y=[r["input"] for r in rows],
                     x=[r["down"] for r in rows], orientation="h",
                     marker_color=[_bar_colour(i, "#1f77b4") for i in range(len(rows))]))
fig3.add_trace(go.Bar(name="+20%", y=[r["input"] for r in rows],
                     x=[r["up"] for r in rows], orientation="h",
                     marker_color=[_bar_colour(i, "#ff7f0e") for i in range(len(rows))]))
fig3.update_layout(barmode="overlay", height=400, xaxis_title="Δ monthly delta (£)",
                   yaxis=dict(autorange="reversed"),
                   title="Top 3 inputs highlighted; rest muted")
st.plotly_chart(fig3, use_container_width=True)


# ---------------------------------------------------------------------------
# Show working
# ---------------------------------------------------------------------------

with st.expander("Show working — year-by-year tables", expanded=False):
    st.markdown("**Ownership**")
    own_df = pd.DataFrame([y.as_dict() for y in own_years])
    st.dataframe(own_df, use_container_width=True, hide_index=True)
    st.markdown(f"**Ownership total: £{own_total:,.0f} · £{own_mo:,.0f}/mo**")

    st.markdown("**Lease**")
    lease_df = pd.DataFrame([y.as_dict() for y in lease_years])
    st.dataframe(lease_df, use_container_width=True, hide_index=True)
    st.markdown(f"**Lease total: £{lease_total:,.0f} · £{lease_mo:,.0f}/mo**")

    st.markdown("### Formulas")
    st.markdown(r"""
- **Depreciation** = (buy-out − exit value) × (year fraction / hold years)
- **Opportunity cost** = avg book value this year × cost-of-capital %  × year fraction
- **Tyres** = annual miles × year fraction × (tyre set cost / tyre interval miles)
- **ECS this year** = ECS rate × (overlap of year with [first-VED, first-VED + 5 yrs]) / year length
- **Per-mile tax** = annual miles × year fraction × rate × (overlap of year with [start date, ∞))
- **Lease excess** = max(0, annual miles − allowance) × excess pence/mile × year fraction
- **Breakeven**: solved by bisection — the buy-out at which ownership £/mo = lease £/mo
""")


# ---------------------------------------------------------------------------
# Save / load scenario
# ---------------------------------------------------------------------------

st.subheader("Save / load scenario")
c1, c2 = st.columns(2)

with c1:
    EXPORT_KEYS = [
        "buyout_price", "hold_years", "annual_miles", "cost_of_capital_pct",
        "insurance_annual", "service_annual", "tyre_interval_miles", "tyre_set_cost",
        "other_maintenance_annual", "battery_reserve_annual",
        "ved_standard", "ecs_annual",
        "per_mile_tax_enabled", "per_mile_tax_rate_pence", "per_mile_tax_start",
        "exit_method", "pct_retained", "absolute_value",
        "lease_monthly_cost", "lease_mileage_allowance", "lease_excess_pence",
        "lease_includes_service", "lease_includes_ved", "lease_includes_insurance",
        "lease_includes_eved", "lease_includes_tyres", "lease_insurance_annual",
        "lease_type", "tax_band", "p11d_value",
    ]
    snapshot = {k: st.session_state[k] for k in EXPORT_KEYS}
    # Dates aren't JSON-serialisable by default — coerce.
    snapshot = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in snapshot.items()}
    st.download_button("Download scenario as JSON",
                       data=json.dumps(snapshot, indent=2),
                       file_name="bmw_i4_scenario.json",
                       mime="application/json")

with c2:
    uploaded = st.file_uploader("Upload scenario JSON", type=["json"])
    if uploaded is not None:
        try:
            data = json.loads(uploaded.read().decode("utf-8"))
            # Coerce date strings back
            for k in ("per_mile_tax_start",):
                if k in data and isinstance(data[k], str):
                    data[k] = date.fromisoformat(data[k])
            for k, v in data.items():
                if k in EXPORT_KEYS:
                    st.session_state[k] = v
            # Backwards-compat: older saves stored pct_retained as a fraction (0-1).
            if st.session_state.get("pct_retained", 50) < 1.5:
                st.session_state.pct_retained = st.session_state.pct_retained * 100
            st.success("Scenario loaded — rerun pending.")
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't load: {e}")
