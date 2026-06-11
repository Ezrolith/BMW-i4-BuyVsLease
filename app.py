"""Streamlit UI for the BMW i4 ownership-vs-lease model.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import json
import math
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from models import (
    CURRENT_LEASE_MONTHLY,
    CURRENT_MILEAGE,
    EV_BIK_SCHEDULE,
    ExitValue,
    FIRST_REG_DATE,
    LeaseComparator,
    LEASE_END_DATE,
    LIST_PRICE_GBP,
    MARGINAL_RELIEF,
    MarketDeal,
    jsonable_records,
    net_of_salary_sacrifice,
    PurchaseInputs,
    RunningCosts,
    TaxParams,
    bik_rate_for_tax_year,
    breakeven_buyout,
    evaluate_market_deal,
    evaluate_preset,
    lease_year_costs,
    monthly_cost,
    ownership_year_costs,
    preset,
    suggest_exit_pct,
    total_cost,
)
from leaseloco import (
    fetch_range_quotes,
    nearest_allowed_mileage,
    quotes_to_deal_rows,
    resolve_range_id,
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
| Expensive Car Supplement | £0/yr (Exempt - registered before Apr 2025) | [Gov.uk VED guidelines](https://www.gov.uk/vehicle-tax) |
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

**ECS exemption**: EVs registered before 1 April 2025 are completely exempt from the Expensive Car Supplement. Since this i4 was registered in April 2024, it is exempt from the £440/yr surcharge.

**Battery reserve**: At 20k miles/yr the car crosses 100k miles around year 3 of hold. The 8yr/100k HV-battery warranty expires whichever comes first. The reserve line lets you pre-fund that risk.
"""


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

# Bump when default values change in a way that should overwrite existing sessions.
# Old sessions with a lower version (or none) get a one-time wipe-and-reseed.
_STATE_VERSION = 11


# EXACT personal PCH quotes captured 10 June 2026 at the target config —
# 36 months / 20,000 miles/yr / 1-month initial — via each site's own pricing
# API or server-rendered listing (not "from" headlines). Notes:
# - Upfront convention: an N-month-initial profile costs (N-1) extra monthlies
#   on top of a flat monthly term, so a 1+35 profile adds NOTHING upfront —
#   the "Upfront £" cell holds only one-off admin/doc fees. For a 9-months-
#   initial quote, enter 8 × monthly + fees.
# - The 2026 i4 range is eDrive35 / eDrive40 / M60; the M50 is discontinued
#   (M60 replaced it). The £574 M50 row is a USED 2023 stock car (55k miles).
# - Excess p/mi is rarely published; 10p is the default guess, and it's inert
#   while the allowance matches the 20k/yr usage anyway. Nationwide publishes
#   16.8p on the M60 pack variants.
# - Carwow prices come from Arval ratebooks expiring 30 Jun 2026 — re-quote
#   after that. Every row is editable; paste fresh configurator quotes in.
SEED_MARKET_DEALS = [
    # LeaseLoco (supplier Vehicle Flex/Dreamlease, factory order). VAT
    # CORRECTION 11 Jun 2026: LeaseLoco's lease-profile API returns EX-VAT
    # prices even for personal leases (the site's JS ×1.2s them for display) —
    # the 10 Jun capture missed that, so these are the same quotes ×1.2.
    # Cross-check: eDrive35 re-quoted live on 11 Jun at exactly £943.40 inc
    # VAT. Fee £249 ex VAT = £298.80 inc. Any future API re-quote MUST ×1.2.
    {"Source": "LeaseLoco", "Description": "i4 eDrive40 M Sport new — exact 36mo/20k/1+35 (£299 fee)",
     "Monthly £": 991.34, "Upfront £": 298.80, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": None},
    {"Source": "LeaseLoco", "Description": "i4 eDrive35 Sport new — exact 36mo/20k/1+35 (£299 fee) — i4 floor",
     "Monthly £": 943.40, "Upfront £": 298.80, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": None},
    # The NVC used 2023 i4 M50 stock car (£574/mo, the 10-11 Jun standout) was
    # SOLD by the 11 Jun evening sweep — row removed. Used-lease stock moves
    # fast; worth re-checking nationwidevehiclecontracts.co.uk/car-leasing/used
    # periodically for one-off bargains.
    # Carwow Leasey API (funder Arval, +£295 admin fee).
    {"Source": "Carwow", "Description": "i4 eDrive40 Sport new — exact 36mo/20k/1+35 (£295 fee)",
     "Monthly £": 989.43, "Upfront £": 295.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": None},
    {"Source": "Carwow", "Description": "i4 eDrive40 M Sport new — exact 36mo/20k/1+35 (£295 fee)",
     "Monthly £": 1001.34, "Upfront £": 295.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": None},
    # New-shape M60 rows for completeness. ABI group 45 (vs eDrive40's 38) →
    # insurance override ~£1,200/yr for this profile (estimate; thin claims
    # history on the 2026 M60 means wide quote dispersion).
    {"Source": "Nationwide VC", "Description": "i4 M60 Tech/Pro new — exact 36mo/20k/1+35 (£357 fee)",
     "Monthly £": 1180.70, "Upfront £": 357.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 16.8, "Ins £/yr": 1_200.0},
    {"Source": "Select", "Description": "i4 M60 new — exact 36mo/20k/1+35 (fee not shown)",
     "Monthly £": 1207.86, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 1_200.0},
    # --- Rival EVs (user is money-first, not BMW-loyal) -------------------
    # New electric Mercedes CLA "with EQ Technology" (MMA platform, 2026):
    # CLA 250+ 85kWh does 484 mi WLTP (vs ~365 for the i4 eDrive40), OTR from
    # ~£43k. So new (11 Jun 2026) that 36mo/20k personal pricing isn't
    # published anywhere yet — NVC says "coming soon", LeaseLoco has no EV CLA
    # inventory, Select/FVL quote-gate it. Seeds below are the best PUBLISHED
    # bases; both are 48mo/5k, so the term-cut warning and excess-mileage
    # normalisation apply. Paste a real 36/20k quote when brokers list one.
    # Insurance: ABI group 41-42 (Parkers) vs i4 eDrive40's 38 → ~£1,050/yr
    # estimate for this profile. DriveElectric initial was 9 months → upfront
    # = 8 × monthly (extra over flat profile); arrangement fee not published.
    {"Source": "DriveElectric", "Description": "Mercedes CLA 250+ Sport EQ (484mi) — exact 48mo/5k/9mo init",
     "Monthly £": 657.93, "Upfront £": 5_263.44, "Miles/yr": 5_000,
     "Term (mo)": 48, "Excess p/mi": 10.0, "Ins £/yr": 1_050.0},
    # e-car lease publishes business ex-VAT only; personal est = ×1.2 VAT.
    {"Source": "e-car lease", "Description": "Mercedes CLA 250+ Sport EQ — 48mo/5k/9mo init (est: bus.+VAT)",
     "Monthly £": 618.32, "Upfront £": 4_946.59, "Miles/yr": 5_000,
     "Term (mo)": 48, "Excess p/mi": 10.0, "Ins £/yr": 1_050.0},
    # --- The wider EV field (swept 11 Jun 2026, all ≥300mi WLTP) ----------
    # All exact personal quotes inc VAT at 36mo/20k/1+35, cross-checked on at
    # least one second source where noted. Variant + WLTP range in each label
    # (user asked for exact variants — "Model 3" alone is meaningless).
    # "Ins £/yr" = profile-scaled estimate from the variant's ABI group
    # (anchor: i4 eDrive40 group 38 = £950, ~3.4%/group). Excess p/mi mostly
    # unpublished → 10p default; Ioniq 6 verified 12.0p; BYD Seal AWD showed
    # 21.6p at NVC, so confirm the Design RWD's rate before signing.
    # Ratebooks behind these expire 30 Jun–10 Jul 2026.
    {"Source": "LeaseLoco", "Description": "Tesla Model 3 Standard RWD 62.5kWh (332mi) — exact 36mo/20k/1+35 (£0 fee)",
     "Monthly £": 443.60, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 777.0},
    {"Source": "LeaseLoco", "Description": "Tesla Model 3 Long Range RWD 85kWh (466mi) — exact 36mo/20k/1+35 (£0 fee)",
     "Monthly £": 611.74, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 919.0},
    {"Source": "LeaseLoco", "Description": "Skoda Enyaq 85 SE L RWD 82kWh (359mi) — exact 36mo/20k/1+35 (£295 fee)",
     "Monthly £": 478.55, "Upfront £": 295.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 777.0},
    {"Source": "LeaseLoco", "Description": "BYD Seal Design RWD 83kWh (354mi) — exact 36mo/20k/1+35 (£0 fee, in stock)",
     "Monthly £": 484.00, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 1_241.0},
    {"Source": "Nationwide VC", "Description": "Hyundai Ioniq 6 Ultimate 77kWh AWD (323mi) — exact 36mo/20k/1+35 (£357 fee)",
     "Monthly £": 484.81, "Upfront £": 357.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 12.0, "Ins £/yr": 1_050.0},
    {"Source": "LeaseLoco", "Description": "Cupra Tavascan V1 RWD 77kWh (343mi) — exact 36mo/20k/1+35 (£0 fee, in stock)",
     "Monthly £": 514.86, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 831.0},
    {"Source": "LeaseLoco", "Description": "Polestar 2 LR Single Motor 82kWh (409mi) — exact 36mo/20k/1+35 (£270 fee, in stock, run-out)",
     "Monthly £": 540.10, "Upfront £": 269.99, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 1_050.0},
    {"Source": "Carwow", "Description": "VW ID.7 Match Pro S Plus 86kWh (434mi) — exact 36mo/20k/1+35 (£295 fee)",
     "Monthly £": 599.37, "Upfront £": 295.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 982.0},
    # --- Fast variants: 0-60 under 4.5s only (swept 11 Jun 2026) ----------
    # User asked for sub-4.5s Model 3 / Seal options. Qualifying 2026-lineup
    # cars (0-60 verified per variant on Parkers): M3 Premium LR AWD 4.2s
    # (group 40), M3 Performance 2.9s (group 48), Seal Excellence AWD 390kW
    # 3.8s 0-62 (group 48). The RWD/Design cars do NOT qualify (4.9-5.7s).
    # Ins £/yr are group-implied estimates; Tesla/BYD real quotes often run
    # higher (repair-cost loadings). Not modelled: M3 Performance tyre habit
    # at 20k mi/yr (~£900-1,000/yr, vs the i4-calibrated maintenance add-on).
    # The used 2021 LR AWD is ONE unit (LB71 UJV, 35,699 mi, pre-facelift,
    # road tax + breakdown incl.); its excess rate is unpublished.
    {"Source": "Nationwide VC", "Description": "USED 2021 Tesla Model 3 LR AWD, 36k mi (360mi, 4.2s) — exact 36mo/20k/1+35 (£357 fee, 1 unit)",
     "Monthly £": 285.45, "Upfront £": 357.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 1_327.0},
    {"Source": "LeaseLoco", "Description": "BYD Seal Excellence AWD 390kW (323mi, 3.8s) — exact 36mo/20k/1+35 (£0 fee, in stock)",
     "Monthly £": 522.30, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 1_327.0},
    {"Source": "Nationwide VC", "Description": "BYD Seal Excellence AWD 390kW pre-reg (323mi, 3.8s) — exact 36mo/20k/1+35 (£357 fee, 21.6p excess)",
     "Monthly £": 521.87, "Upfront £": 357.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 21.6, "Ins £/yr": 1_327.0},
    {"Source": "LeaseLoco", "Description": "Tesla Model 3 Premium LR AWD 85kWh (444mi, 4.2s) — exact 36mo/20k/1+35 (£0 fee)",
     "Monthly £": 809.65, "Upfront £": 0.0, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 10.0, "Ins £/yr": 1_016.0},
    {"Source": "FVL", "Description": "Tesla Model 3 Performance 85kWh (354mi, 2.9s) — exact 36mo/20k/1+35 (£350 fee)",
     "Monthly £": 1131.82, "Upfront £": 349.99, "Miles/yr": 20_000,
     "Term (mo)": 36, "Excess p/mi": 15.84, "Ins £/yr": 1_327.0},
]


# Session-state keys that round-trip through the scenario JSON download/upload.
# Module-level because two places need it: the download snapshot and the
# staged-scenario apply step that runs before any widget is instantiated.
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
    "lease_type", "tax_band", "p11d_value", "current_is_salsac",
    "market_add_insurance", "market_add_maintenance", "market_includes_eved",
]


def _init_state():
    """Seed st.session_state with the 'central' preset, filling in any missing keys.

    Uses setdefault so existing session values (e.g. user tweaks) are preserved
    across code updates that add new keys — *unless* the state version has been
    bumped, in which case we wipe and reseed from current defaults.
    """
    if st.session_state.get("_state_version") != _STATE_VERSION:
        # Clear everything except internal Streamlit-managed keys
        for k in list(st.session_state.keys()):
            if not k.startswith("_"):
                del st.session_state[k]
        st.session_state["_state_version"] = _STATE_VERSION

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
        "ecs_annual": 0.0,
        "per_mile_tax_enabled": True,
        "per_mile_tax_rate_pence": p["tax"]["per_mile_tax_rate_pence"],
        "per_mile_tax_start": date(2028, 4, 1),
        "exit_method": "pct_of_buyout",
        # Stored as a percentage 0-100; the UI shows "55" not "0.55".
        "pct_retained": p["exit"]["pct_retained"] * 100,
        "absolute_value": 14_000.0,
        # Lease defaults are NOT pulled from presets — your lease quote is a
        # known fixed number, not a scenario variable. Presets only vary
        # ownership-side assumptions (buyout, residual, insurance, etc.).
        "lease_monthly_cost": 1_000,
        "lease_mileage_allowance": 20_000,
        "lease_excess_pence": 10.0,
        "lease_includes_service": True,
        "lease_includes_ved": True,
        "lease_includes_insurance": True,
        "lease_includes_eved": True,
        "lease_includes_tyres": True,
        "lease_insurance_annual": 950,
        # Default to salary sacrifice — matches typical "fully inclusive" work
        # leases that bundle insurance, service, VED and tyres. BiK applies
        # automatically using LIST_PRICE_GBP as the P11d value.
        "lease_type": "salary_sacrifice",
        "tax_band": "basic",
        "p11d_value": LIST_PRICE_GBP,
        # Market-deals table base rows (list of dicts, JSON-safe). The data
        # editor renders these and keeps its own edits in widget state; this
        # key only changes when a scenario JSON is loaded, at which point
        # market_deals_nonce is bumped to remount the editor (Streamlit
        # forbids writing data-editor widget state directly).
        "market_deals": [dict(r) for r in SEED_MARKET_DEALS],
        "market_deals_nonce": 0,
        "market_add_insurance": True,
        "market_add_maintenance": True,
        "market_includes_eved": True,
        # Today's £1,100 i4 scheme is salary-sacrificed from GROSS pay — its
        # true net cost (gross less IT/NI relief, plus BiK) is what any
        # net-pay PCH deal must beat. Untick if comparing against a lease
        # paid from net pay.
        "current_is_salsac": True,
        "initialised": True,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _apply_preset(name: str):
    """Overlay a preset onto current state — user can tweak after."""
    p = preset(name, hold_years=st.session_state.hold_years)  # type: ignore[arg-type]
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
    # Lease + tax treatment are deliberately NOT overwritten — they reflect the
    # user's actual quote and circumstances, not a scenario.


_init_state()

# Apply a scenario staged by the uploader on the previous run. This must run
# before ANY widget is instantiated: Streamlit raises if a widget's session key
# is written after the widget exists, so the uploader (rendered at the bottom
# of the page) only stages the parsed dict and reruns. The dict was validated
# and coerced at stage time — this step just writes it.
_pending = st.session_state.pop("_pending_scenario", None)
if _pending is not None:
    md = _pending.pop("market_deals", None)
    if md is not None:
        st.session_state["market_deals"] = md
        # Remount the data editor with a fresh key so stale widget-state edits
        # don't overlay the loaded rows (editor state can't be written directly),
        # and drop the rendered snapshot — folding pre-load rows into a
        # post-load fetch would resurrect them.
        st.session_state["market_deals_nonce"] += 1
        st.session_state.pop("market_rendered", None)
    for k, v in _pending.items():
        if k in EXPORT_KEYS:
            st.session_state[k] = v
    # Backwards-compat: older saves stored pct_retained as a fraction (0-1).
    # Only rescale a genuine fraction (0 < v <= 1); 1 (=1%) is left untouched.
    pr = st.session_state.get("pct_retained", 50)
    if 0 < pr <= 1:
        st.session_state.pct_retained = pr * 100
    st.session_state["_scenario_loaded"] = True

# Migration: pct_retained used to be a fraction (0-1); now stored as a percent (0-100).
# A stale session value < 1.5 is treated as the old format and rescaled — but only
# once per session, gated by a flag, so a user legitimately entering a tiny percent
# (e.g. 1 = 1%) on a later rerun isn't silently rewritten to 100.
if not st.session_state.get("_pct_migrated"):
    if st.session_state.get("pct_retained", 50) < 1.5:
        st.session_state.pct_retained = st.session_state.pct_retained * 100
    st.session_state["_pct_migrated"] = True


# ---------------------------------------------------------------------------
# Soft-validation helper
# ---------------------------------------------------------------------------

def _typical(key: str, low: float, high: float, advice: str = "",
             unit: str = "£") -> None:
    """Show a small warning under an input if the value is outside the typical range.

    Doesn't block the input — purely advisory. `advice` is appended after the range.
    `unit` controls how the range reads: "£" prefixes pounds (the default); any other
    value (e.g. "%", "p", " mi", " yrs") is suffixed instead, so non-money inputs
    don't show a misleading pound sign.
    """
    v = st.session_state.get(key)
    if v is None:
        return
    if v < low or v > high:
        if unit == "£":
            rng = f"£{low:,.0f}–£{high:,.0f}"
        else:
            rng = f"{low:,g}–{high:,g}{unit}"
        msg = f"Outside typical range {rng}."
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
             "Beyond 6 years the car would be 9+ yrs old at sale — extrapolation gets shaky.",
             unit=" yrs")

    st.number_input("Annual mileage", min_value=0, key="annual_miles", step=1_000)
    _typical("annual_miles", 8_000, 30_000,
             "Far outside this range, tyre/service assumptions probably need a second look.",
             unit=" mi")

    st.number_input("Cost of capital %", min_value=0.0, key="cost_of_capital_pct", step=0.25,
                    help="Opportunity cost on £ tied up in the car (declining balance)")
    _typical("cost_of_capital_pct", 0.0, 10.0,
             "Most people use 3-7% (cash savings rate / mortgage offset / investment return).",
             unit="%")

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
             "i4 owners typically report 30-40k miles per set.",
             unit=" mi")

    st.number_input("Tyre set £ (4 corners fitted)", min_value=0.0, key="tyre_set_cost", step=50.0)
    _typical("tyre_set_cost", 600, 1_400,
             "~£235/corner × 4 for premium EV-rated 19\" (Michelin/Pirelli).")

    st.number_input("Other maintenance £/yr", min_value=0.0, key="other_maintenance_annual", step=50.0,
                    help="Wipers, MOT (yrs 3+), brake fluid, 12V battery, misc")
    _typical("other_maintenance_annual", 100, 700)

    # Battery-warranty risk: 8yr/100k from first reg. Exit miles = current odo + hold × annual
    exit_miles = CURRENT_MILEAGE + st.session_state.hold_years * st.session_state.annual_miles
    car_age_at_exit = (LEASE_END_DATE.year - FIRST_REG_DATE.year) + st.session_state.hold_years
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
    if FIRST_REG_DATE < date(2025, 4, 1):
        st.caption(":information_source: **Exempt:** EVs registered before 1 April 2025 "
                   "are exempt from the Expensive Car Supplement. Defaulted to £0.")
    else:
        _typical("ecs_annual", 0, 600, "£440/yr is the 2026/27 ECS rate.")

    st.checkbox("Per-mile EV tax (Apr 2028)", key="per_mile_tax_enabled")
    st.number_input("Per-mile rate (pence)", min_value=0.0, key="per_mile_tax_rate_pence", step=0.25)
    _typical("per_mile_tax_rate_pence", 0.0, 8.0,
             "Confirmed 3p/mile for BEVs from Apr 2028.",
             unit="p")
    st.date_input("Per-mile tax start date", key="per_mile_tax_start")

with st.sidebar.expander("Exit value at end of hold", expanded=True):
    st.radio("Method", ["pct_of_buyout", "absolute"], key="exit_method",
             format_func=lambda x: "% of buy-out retained" if x == "pct_of_buyout" else "Absolute £")
    suggested_pct = suggest_exit_pct(st.session_state.hold_years) * 100
    exit_miles_for_label = 41_500 + st.session_state.hold_years * st.session_state.annual_miles
    st.caption(f"Suggested for {st.session_state.hold_years:.1f} yrs "
               f"({exit_miles_for_label:,.0f} mi at exit): **{suggested_pct:.0f}%** of buy-out")
    if st.session_state.exit_method == "pct_of_buyout":
        # Apply-suggested must run BEFORE the number_input is instantiated this
        # rerun — Streamlit forbids writes to a widget-bound key after the
        # widget exists. Use on_click so the write happens before the next run.
        def _apply_suggested_pct():
            st.session_state.pct_retained = (
                suggest_exit_pct(st.session_state.hold_years) * 100
            )

        st.number_input("% retained at sale", min_value=0.0, key="pct_retained", step=1.0,
                        format="%.0f", help="Enter as a percentage, e.g. 55 = 55% of buy-out")
        st.button(f"Apply suggested ({suggested_pct:.0f}%)",
                  key="apply_suggested_pct", on_click=_apply_suggested_pct)
        _typical("pct_retained", 15, 80,
                 "Above 80% implies almost no depreciation; below 15% implies a write-off.",
                 unit="%")
    else:
        st.number_input("Sale price £", min_value=0.0, key="absolute_value", step=500.0)
        _typical("absolute_value", 5_000, 30_000)

with st.sidebar.expander("Lease comparator", expanded=True):
    st.number_input("Monthly cost £", min_value=0.0, key="lease_monthly_cost", step=10.0)
    _typical("lease_monthly_cost", 400, 1_200, "Mainstream EV personal-lease band in 2026.")

    st.number_input("Mileage allowance/yr", min_value=0, key="lease_mileage_allowance", step=1_000)
    _typical("lease_mileage_allowance", 5_000, 30_000, unit=" mi")

    st.number_input("Excess mileage pence/mile", min_value=0.0, key="lease_excess_pence", step=0.5)
    _typical("lease_excess_pence", 5.0, 30.0,
             "Most contracts charge 8-15p/mile over allowance.",
             unit="p")

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
    st.checkbox("Current £1,100 lease is salary sacrifice",
                key="current_is_salsac",
                help="Today's scheme comes out of GROSS pay (saves IT + NI, "
                     "adds BiK). 'vs today' comparisons then use its true "
                     "net cost rather than the £1,100 gross sacrifice — "
                     "otherwise net-pay PCH deals look unfairly good.")
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
breakeven_known = not math.isnan(breakeven)
breakeven_txt = f"£{breakeven:,.0f}" if breakeven_known else "—"


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

st.title("BMW i4 eDrive40 M Sport — Buy out vs fresh lease")
st.caption(f"List price £{LIST_PRICE_GBP:,.0f} · First reg {FIRST_REG_DATE.year} · "
           f"Lease ends {LEASE_END_DATE.isoformat()} · Hold {purchase.hold_years:.1f} yrs at "
           f"{purchase.annual_miles:,}/yr")

with st.expander("Research summary & sources", expanded=False):
    st.markdown(RESEARCH_MD)

lease_label = {"personal": "Lease £/month", "salary_sacrifice": "Lease £/mo (net)",
               "company_car": "Lease £/mo (out-of-pocket)"}[lease.lease_type]
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
k4.metric("Breakeven buy-out", breakeven_txt,
          help="Price at which ownership monthly cost = lease monthly cost"
               + ("" if breakeven_known
                  else f" — no crossing within £1k–£{LIST_PRICE_GBP:,.0f}"))

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
    tail = (f"Any buy-out price below **£{breakeven:,.0f}** beats this lease."
            if breakeven_known else
            "Ownership beats this lease across the whole modelled buy-out range.")
    st.markdown(
        f"### Verdict: **Buy out** ✓ — saves **£{-delta_mo:,.0f}/mo** "
        f"(£{saving_total:,.0f} over {purchase.hold_years:.1f} yrs). " + tail
    )
elif delta_mo > 0:
    loss_total = delta_mo * purchase.hold_years * 12
    tail = (f"Buy-out would need to drop below **£{breakeven:,.0f}** to beat the lease."
            if breakeven_known else
            "No buy-out price in the modelled range beats this lease.")
    st.markdown(
        f"### Verdict: **Take the lease** ✓ — owning costs **£{delta_mo:,.0f}/mo more** "
        f"(£{loss_total:,.0f} over {purchase.hold_years:.1f} yrs). " + tail
    )
else:
    st.markdown("### Verdict: **Tie** — both options cost the same per month.")

# Reference: what you pay today, so both options are anchored against the
# status quo. The current scheme is salary-sacrificed from GROSS pay, so the
# fair anchor is its NET cost: gross less IT/NI relief, plus BiK on the i4's
# P11d at this tax year's rate — not the £1,100 gross sacrifice.
if st.session_state.current_is_salsac:
    today_net = net_of_salary_sacrifice(
        CURRENT_LEASE_MONTHLY, st.session_state.tax_band,
        p11d_value=LIST_PRICE_GBP, on=date.today())
    today_label = (f"≈£{today_net:,.0f}/mo net (£{CURRENT_LEASE_MONTHLY:,.0f} "
                   f"gross sacrifice, {st.session_state.tax_band}-rate relief, "
                   "BiK added back)")
else:
    today_net = CURRENT_LEASE_MONTHLY
    today_label = f"£{today_net:,.0f}/mo"
cheapest = min(own_mo, lease_mo)
vs_today = today_net - cheapest
st.caption(
    f"For reference, the current lease truly costs you **{today_label}**. "
    f"Cheapest modelled option (£{cheapest:,.0f}/mo) is "
    f"**£{abs(vs_today):,.0f}/mo {'less' if vs_today >= 0 else 'more'}** than today — "
    f"£{abs(vs_today) * purchase.hold_years * 12:,.0f} over {purchase.hold_years:.1f} yrs. "
    f"(Today's car is fully inclusive; check the lease toggles match for a fair compare.)"
)

# Side-by-side scenario range. Only the OWNERSHIP side varies across presets —
# the lease is your fixed quote — so we show the lease once in the caption and
# keep the table focused on what actually changes.
st.subheader("Scenario range")
st.caption(f"Same hold period ({purchase.hold_years:.1f} yrs at "
           f"{purchase.annual_miles:,}/yr) and the same lease "
           f"(**£{lease_mo:,.0f}/mo**) through each preset's *ownership* assumptions.")

preset_rows = [
    evaluate_preset(name, hold_years=purchase.hold_years,
                    annual_miles=purchase.annual_miles, lease=lease)
    for name in ("pessimistic", "central", "optimistic")
]
preset_df = pd.DataFrame([{
    "Scenario": r["scenario"].title(),
    "Buy-out £": f"£{r['buyout']:,.0f}",
    "Own £/mo": f"£{r['own_monthly']:,.0f}",
    "Delta £/mo": f"£{r['delta_monthly']:+,.0f}",
    "Breakeven £": (f"£{r['breakeven']:,.0f}"
                    if not math.isnan(r["breakeven"]) else "—"),
    "Winner": "Own" if r["delta_monthly"] < 0 else "Lease",
} for r in preset_rows])
st.dataframe(preset_df, use_container_width=True, hide_index=True)

st.divider()


# ---------------------------------------------------------------------------
# Market lease deals (LeaseLoco / Carwow / brokers)
# ---------------------------------------------------------------------------

st.subheader("Market lease deals — the EV field (LeaseLoco / Carwow / brokers)")
st.caption(
    "Personal PCH quotes across the EV field — BMW i4, Mercedes CLA EQ, Tesla, "
    "Polestar, Hyundai, BYD, Škoda, Cupra, VW — normalised so they're "
    "apples-to-apples: any **upfront is amortised** across min(term, hold), "
    "**insurance + maintenance are added** (PCH deals don't bundle them; 'Ins £/yr' "
    "overrides your insurance per row from each variant's ABI group), and your "
    f"**{purchase.annual_miles:,}/yr** usage charges excess mileage on lower "
    "allowances. Rows are **exact quotes (10–11 Jun 2026) at 36 mo / 20k miles / "
    "1-month initial** with the exact variant + WLTP range in each label, except "
    "the CLA rows (too new — best published 48mo/5k bases, penalised accordingly). "
    "i4 LeaseLoco rows were **VAT-corrected on 11 Jun** (their API quotes ex-VAT; "
    "×1.2 applied and re-verified). Upfront cells = extra over a flat profile + "
    "one-off fees ((N−1)×monthly for N-months-initial; 1+35 = fees only). Most "
    "ratebooks expire **30 Jun–10 Jul 2026**. Edit any cell, or use ＋ to add "
    "quotes. Sources: "
    "[LeaseLoco](https://www.leaseloco.com/car-leasing/electric) · "
    "[Carwow](https://www.carwow.co.uk/leasey/cars) · "
    "[Nationwide VC](https://www.nationwidevehiclecontracts.co.uk/car-leasing) · "
    "[Select](https://www.selectcarleasing.co.uk/car-leasing) · "
    "[DriveElectric](https://www.drive-electric.co.uk/electric-car-leasing/mercedes-benz/cla-electric/) · "
    "[e-car lease](https://www.electriccarlease.co.uk/electric-car-leasing/mercedes-benz/cla)."
)

mc1, mc2, mc3 = st.columns(3)
add_market_insurance = mc1.checkbox(
    "Add insurance to each deal", key="market_add_insurance",
    help=f"Adds your insurance assumption (£{running.insurance_annual:,.0f}/yr) — "
         "PCH deals don't include it")
add_market_maint = mc2.checkbox(
    "Add maintenance (service + tyres)", key="market_add_maintenance",
    help="Adds service + tyre wear at your running-cost assumptions — PCH deals "
         "rarely bundle a maintenance package")
market_includes_eved = mc3.checkbox(
    "Deals include per-mile EV tax?", key="market_includes_eved",
    help="Whether the Apr-2028 per-mile EV tax lands on the leasing co (ticked) "
         "or on you on top of each deal (unticked). Mirror your own-lease "
         "'Includes eVED?' setting to keep the comparison symmetric")

@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _ll_range_id(slug: str) -> int:
    return resolve_range_id(slug)


@st.cache_data(ttl=900, show_spinner=False)
def _ll_quotes(range_id: int, term: int, mileage: int, initial: int) -> list[dict]:
    return fetch_range_quotes(range_id, term, mileage, initial)


with st.expander("🔄 Fetch live LeaseLoco quotes (API)"):
    st.caption(
        "Pulls exact personal quotes **at your terms** straight from LeaseLoco's "
        "pricing API (its prices are ex-VAT — corrected ×1.2 here), adds a "
        "**notional insurance** estimate scaled off your own premium by each "
        "car's 0-62 time, and drops the rows into the table below so they rank "
        "as all-in effective £/mo like everything else. Estimates, not quotes — "
        "verify before signing."
    )
    ll_c1, ll_c2 = st.columns([3, 1])
    ll_slug = ll_c1.text_input(
        "LeaseLoco model page or make/model slug", value="tesla/model-3",
        key="ll_slug",
        help="e.g. tesla/model-3 · byd/seal · bmw/i4 — or paste the full "
             "leaseloco.com model page URL")
    # Snap to the API's accepted values, keeping term aligned with the hold so
    # the term-mismatch warning stays quiet for live rows.
    _ll_term = min((24, 36, 48), key=lambda t: abs(t - purchase.hold_years * 12))
    _ll_miles = nearest_allowed_mileage(int(purchase.annual_miles))
    if ll_c2.button(f"Fetch at {_ll_term}mo/{_ll_miles // 1000}k",
                    use_container_width=True):
        try:
            with st.spinner("Quoting LeaseLoco…"):
                results = _ll_quotes(_ll_range_id(ll_slug), _ll_term, _ll_miles, 1)
            new_rows = quotes_to_deal_rows(
                results, baseline_insurance=running.insurance_annual,
                fetched_on=date.today().strftime("%d %b"))
            if not new_rows:
                st.warning(f"No personal quotes at {_ll_term} mo / {_ll_miles:,} "
                           "mi / 1-month initial for that model.")
            else:
                # Changing the editor's input data remounts it whatever the key
                # (Streamlit hashes the data into the element id), which would
                # silently discard widget-state edits. So: fold the table AS
                # THE USER SEES IT (last run's rendered snapshot — base rows +
                # their edits/additions/deletions) into the base first, append
                # the genuinely new quotes, and remount explicitly.
                base = (st.session_state.get("market_rendered")
                        or st.session_state["market_deals"])
                existing = {(r.get("Description"), r.get("Monthly £"))
                            for r in base}
                fresh = [r for r in new_rows
                         if (r["Description"], r["Monthly £"]) not in existing]
                st.session_state["market_deals"] = list(base) + fresh
                st.session_state["market_deals_nonce"] += 1
                dupes = len(new_rows) - len(fresh)
                msg = (f"Added {len(fresh)} live quote(s)"
                       + (f" ({dupes} already in the table)." if dupes else "."))
                (st.success if fresh else st.info)(msg)
        except Exception as e:
            st.error(f"LeaseLoco fetch failed: {e}")

# Base rows come from session state (seed, a loaded scenario, or live-fetched
# LeaseLoco quotes). An empty list still needs the column headers, else the
# editor renders with no columns.
_deal_rows = st.session_state["market_deals"]
deals_df = st.data_editor(
    pd.DataFrame(_deal_rows) if _deal_rows else pd.DataFrame(SEED_MARKET_DEALS).iloc[0:0],
    num_rows="dynamic",
    key=f"market_deals_editor_{st.session_state['market_deals_nonce']}",
    use_container_width=True,
    hide_index=True,
    column_config={
        "Source": st.column_config.TextColumn("Source"),
        "Description": st.column_config.TextColumn("Description", width="large"),
        "Monthly £": st.column_config.NumberColumn("Monthly £", min_value=0.0,
                                                   step=10.0, format="£%.0f"),
        "Upfront £": st.column_config.NumberColumn("Upfront £", min_value=0.0,
                                                   step=100.0, format="£%.0f",
                                                   help="Extra paid upfront beyond a flat monthly profile, "
                                                        "plus one-off fees. An N-months-initial quote = "
                                                        "(N−1) × monthly + fees; a 1+35 profile = fees only"),
        "Miles/yr": st.column_config.NumberColumn("Miles/yr", min_value=0, step=1_000),
        "Term (mo)": st.column_config.NumberColumn("Term (mo)", min_value=1, step=6),
        "Excess p/mi": st.column_config.NumberColumn("Excess p/mi", min_value=0.0,
                                                     step=0.5, format="%.1f"),
        "Ins £/yr": st.column_config.NumberColumn(
            "Ins £/yr", min_value=0.0, step=50.0, format="£%.0f",
            help="Optional per-deal insurance override (£/yr). Blank = your "
                 f"running-cost assumption (£{running.insurance_annual:,.0f}). "
                 "Use for M50/M60, which insure above the eDrive40 baseline"),
    },
)


def _num(v, default: float = 0.0) -> float:
    """Coerce a possibly-NaN/None data-editor cell to a float, else the default.

    Non-finite values (inf from a hand-edited JSON — json.loads accepts the
    Infinity token) also fall back to the default: downstream int() would raise.
    """
    try:
        if v is None:
            return default
        f = float(v)
        return default if not math.isfinite(f) else f
    except (TypeError, ValueError):
        return default


def _coerce_deal_rows(rows: list[dict]) -> list[dict]:
    """Whitelist-coerce deal rows loaded from a scenario JSON.

    A hand-edited file can otherwise brick the session: a uniformly wrong-typed
    column (e.g. numeric "Source") makes st.data_editor raise on every rerun,
    and negative / Infinity numbers crash MarketDeal's ge=0 validation below
    the editor — in both cases above the uploader, with no in-app recovery.
    Blank cells stay None so the editor shows them empty and the evaluation
    defaults apply.
    """
    def _cell(v, lo: float = 0.0):
        if v is None:
            return None
        f = _num(v, float("nan"))
        return None if math.isnan(f) else max(lo, f)

    return [{
        "Source": None if r.get("Source") is None else str(r["Source"]),
        "Description": None if r.get("Description") is None else str(r["Description"]),
        "Monthly £": _cell(r.get("Monthly £")),
        "Upfront £": _cell(r.get("Upfront £")),
        "Miles/yr": _cell(r.get("Miles/yr")),
        "Term (mo)": _cell(r.get("Term (mo)"), lo=1.0),
        "Excess p/mi": _cell(r.get("Excess p/mi")),
        "Ins £/yr": _cell(r.get("Ins £/yr")),
    } for r in rows]


# JSON-safe snapshot of the table as edited (base rows + widget-state edits).
# The Save/load section exports this, so a deal set round-trips with the
# scenario — and the live-fetch handler folds it into the base on the next run
# so a remount can't lose the user's edits.
market_records = jsonable_records(deals_df.to_dict("records"))
st.session_state["market_rendered"] = market_records

market_results = []
for row in market_records:
    monthly = _num(row.get("Monthly £"), 0.0)
    if monthly <= 0:
        continue  # skip blank / placeholder rows with no price
    deal = MarketDeal(
        source=str(row.get("Source") or "?"),
        label=str(row.get("Description") or ""),
        monthly_cost=monthly,
        # max() guards: MarketDeal validates ge=0, and loaded base rows bypass
        # the editor's min_value (UI-only). A blank allowance defaults to the
        # user's own usage — 0 would bill every mile as phantom excess.
        initial_payment=max(0.0, _num(row.get("Upfront £"), 0.0)),
        mileage_allowance=max(0, int(_num(row.get("Miles/yr"), purchase.annual_miles))),
        term_months=max(1, int(_num(row.get("Term (mo)"), 36))),
        excess_pence_per_mile=max(0.0, _num(row.get("Excess p/mi"), 10.0)),
        insurance_override=(None if math.isnan(ins_cell := _num(row.get("Ins £/yr"), float("nan")))
                            else max(0.0, ins_cell)),
    )
    market_results.append(evaluate_market_deal(
        deal, running, tax,
        annual_miles=purchase.annual_miles, hold_years=purchase.hold_years,
        add_insurance=add_market_insurance, add_maintenance=add_market_maint,
        include_eved=market_includes_eved,
    ))

if not market_results:
    st.info("Add at least one deal with a monthly price above £0 to see the comparison.")
else:
    market_results.sort(key=lambda r: r["effective_monthly"])
    table_rows = [{
        "Source": r["source"],
        "Deal": r["label"],
        "Headline £/mo": f"£{r['headline_monthly']:,.0f}",
        "Upfront £": f"£{r['initial_payment']:,.0f}",
        "Term (mo)": f"{r['term_months']}",
        "Miles/yr": f"{r['mileage_allowance']:,}",
        "+Upfront/mo": f"£{r['upfront_mo']:,.0f}",
        "+Excess/mo": f"£{r['excess_mo']:,.0f}",
        "+Ins/mo": f"£{r['insurance_mo']:,.0f}",
        "+Maint/mo": f"£{r['service_mo'] + r['tyres_mo']:,.0f}",
        "Effective £/mo": f"£{r['effective_monthly']:,.0f}",
        "vs your lease": f"£{r['effective_monthly'] - lease_mo:+,.0f}",
        "vs buy": f"£{r['effective_monthly'] - own_mo:+,.0f}",
        "vs today": f"£{r['effective_monthly'] - today_net:+,.0f}",
    } for r in market_results]
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
    st.caption("Effective £/mo = headline + amortised upfront + excess mileage + "
               "insurance + maintenance, costed over min(term, hold). **vs** columns: "
               "negative = the deal is cheaper than that option. These deals are "
               "paid from NET pay, so 'today' = the current scheme's true net cost: "
               f"**£{today_net:,.0f}/mo**"
               + (f" (£{CURRENT_LEASE_MONTHLY:,.0f} gross salary sacrifice less "
                  f"{st.session_state.tax_band}-rate IT/NI relief, plus BiK)."
                  if st.session_state.current_is_salsac else "."))

    hold_months = round(purchase.hold_years * 12)
    mismatched = [r for r in market_results if r["term_months"] != hold_months]
    if mismatched:
        st.warning(
            f"{len(mismatched)} deal(s) have a term ≠ your {hold_months}-month hold. "
            "Shorter deals are priced over their own term — what replaces them "
            "afterwards is re-quote risk. Longer deals are cut at the hold end with "
            "the full upfront counted; early-termination charges aren't modelled."
        )

    # Cheapest-deal verdict, anchored to buying and to today's true net cost.
    best = market_results[0]
    eff = best["effective_monthly"]
    vs_buy = eff - own_mo
    vs_today = today_net - eff
    msg = (f"**Cheapest market deal: {best['source']} — {best['label']} at an "
           f"effective £{eff:,.0f}/mo** (£{best['headline_monthly']:,.0f} headline, "
           f"all-in once upfront, excess miles, insurance and maintenance are added). ")
    if vs_buy < 0:
        msg += f"That **beats buying the car out** by £{-vs_buy:,.0f}/mo. "
    else:
        msg += f"Buying out is still £{vs_buy:,.0f}/mo cheaper than even this deal. "
    msg += (f"It's £{abs(vs_today):,.0f}/mo {'less' if vs_today >= 0 else 'more'} "
            f"than the £{today_net:,.0f}/mo your current scheme truly costs"
            + (f" net (£{CURRENT_LEASE_MONTHLY:,.0f} gross sacrificed)."
               if st.session_state.current_is_salsac else " today."))
    (st.success if vs_buy < 0 else st.info)(msg)

    # Bar chart: every deal's all-in effective monthly vs buy and your own lease.
    bar_names = [f"{r['source']}: {r['label'][:24]}" for r in market_results]
    bar_vals = [r["effective_monthly"] for r in market_results]
    bar_colours = ["#1f77b4"] * len(market_results)
    bar_names += ["▶ Buy out", "▶ Your configured lease"]
    bar_vals += [own_mo, lease_mo]
    bar_colours += ["#2ca02c", "#ff7f0e"]
    fig_m = go.Figure(go.Bar(
        x=bar_vals, y=bar_names, orientation="h", marker_color=bar_colours,
        text=[f"£{v:,.0f}" for v in bar_vals], textposition="auto",
    ))
    fig_m.update_layout(height=max(300, 55 * len(bar_names)),
                        xaxis_title="Effective £/mo (all-in)",
                        yaxis=dict(autorange="reversed"),
                        title="All-in effective monthly — cheapest at top")
    st.plotly_chart(fig_m, use_container_width=True)

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
    snapshot = {k: st.session_state[k] for k in EXPORT_KEYS}
    # Dates aren't JSON-serialisable by default — coerce.
    snapshot = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in snapshot.items()}
    # The deals table is exported as edited (not the widget key, which Streamlit
    # owns) — market_records was sanitised right after the data editor above.
    snapshot["market_deals"] = market_records
    st.download_button("Download scenario as JSON",
                       data=json.dumps(snapshot, indent=2),
                       file_name="bmw_i4_scenario.json",
                       mime="application/json")

with c2:
    uploaded = st.file_uploader("Upload scenario JSON", type=["json"])
    if st.session_state.pop("_scenario_loaded", False):
        st.success("Scenario loaded.")
    # Guard on file_id: the uploader keeps returning the file on every rerun
    # until the user removes it, and reprocessing each run would re-bump the
    # editor nonce (discarding post-load table edits) and rerun forever.
    if uploaded is not None and st.session_state.get("_loaded_file_id") != uploaded.file_id:
        try:
            data = json.loads(uploaded.read().decode("utf-8"))
            # Coerce date strings back
            for k in ("per_mile_tax_start",):
                if k in data and isinstance(data[k], str):
                    data[k] = date.fromisoformat(data[k])
            md = data.get("market_deals")
            if md is not None:
                if not (isinstance(md, list) and all(isinstance(r, dict) for r in md)):
                    raise ValueError("market_deals must be a list of row objects")
                data["market_deals"] = _coerce_deal_rows(md)
            # Widget session keys can't be written here — the widgets already
            # exist this run. Stage the validated dict; it's applied at the top
            # of the next run, before any widget is instantiated.
            st.session_state["_pending_scenario"] = data
            st.session_state["_loaded_file_id"] = uploaded.file_id
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't load: {e}")
