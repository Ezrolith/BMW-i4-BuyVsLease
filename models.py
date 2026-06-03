"""Pure calculation functions and input schemas for the BMW i4 ownership-vs-lease model.

All money is in GBP. All distances in miles. Hold period is in years (allow half-years).

Sources for default values are cited inline next to the field. Defaults are deliberately
mid-range so the user can tighten them with their own data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, computed_field


# ---------------------------------------------------------------------------
# Vehicle / contract constants (locked facts about the user's actual car)
# ---------------------------------------------------------------------------

LIST_PRICE_GBP: float = 66_124.0
FIRST_REG_DATE: date = date(2024, 4, 1)
LEASE_END_DATE: date = date(2027, 4, 1)
CURRENT_MILEAGE: int = 41_500  # odometer reading at May 2026 (see README)
CURRENT_LEASE_MONTHLY: float = 1_100.0  # gross £/mo on the existing i4 lease today


# ---------------------------------------------------------------------------
# Input groups (validated with pydantic)
# ---------------------------------------------------------------------------

class PurchaseInputs(BaseModel):
    """How much you pay to take the car off the leasing company and for how long.

    Validation is intentionally loose — only checks values that would crash the maths
    (e.g. negative prices, zero hold period). The UI is responsible for warning when
    values look unusual.
    """
    buyout_price: float = Field(26_000, ge=0)
    hold_years: float = Field(3.0, gt=0)
    annual_miles: int = Field(20_000, ge=0)
    cost_of_capital_pct: float = Field(7.0, ge=0)


class RunningCosts(BaseModel):
    """Recurring annual costs that scale with the car, not the contract.

    Default values come from May 2026 research — see app for sources.
    """
    # Age-40 finder.com Feb 2026 figure: £756/yr for the cheapest i4 variant
    # (insurance group 34). The eDrive40 M Sport sits around group 35-38, so
    # add ~£100, plus ~10% Manchester postcode loading → £950 central.
    # Forum "+50% YoY renewal" anecdotes live in the pessimistic preset.
    insurance_annual: float = Field(950, ge=0)
    service_annual: float = Field(300, ge=0)
    tyre_interval_miles: int = Field(35_000, gt=0)
    tyre_set_cost: float = Field(950, ge=0)
    other_maintenance_annual: float = Field(300, ge=0)
    # Reserve for HV-battery risk past the 8yr/100k warranty. Suggested ~£600/yr
    # of cover when expected exit miles > 80k; £0 otherwise. UI populates the
    # default when the user crosses that threshold.
    battery_reserve_annual: float = Field(0, ge=0)


class TaxParams(BaseModel):
    """VED, Expensive Car Supplement and per-mile EV charge.

    Sources:
    - VED standard rate £200/yr for EVs first reg before Apr 2025 (2026/27 rate)
      https://commonslibrary.parliament.uk/research-briefings/cbp-9690/
    - ECS £440/yr for 5 years from first VED liability. For an Apr-2024 reg EV,
      first VED liability is Apr 2025, so ECS runs Apr 2025 — Apr 2030.
      https://www.bvrla.co.uk/home/support/guidance/ved-changes-expensive-car-supplement-for-evs
    - eVED 3p/mile for BEVs from Apr 2028, paid alongside VED (Autumn Budget 2025).
      https://www.rac.co.uk/drive/news/motoring-news/autumn-budget-2025/
    """
    ved_standard: float = Field(200, ge=0)
    ecs_annual: float = Field(440, ge=0)
    ecs_first_liable: date = Field(date(2025, 4, 1))
    ecs_years_total: int = Field(5, ge=0)
    per_mile_tax_enabled: bool = True
    per_mile_tax_rate_pence: float = Field(3.0, ge=0)
    per_mile_tax_start: date = Field(date(2028, 4, 1))


class ExitValue(BaseModel):
    """How the sale proceeds at end-of-hold are determined."""
    method: Literal["pct_of_buyout", "absolute"] = "pct_of_buyout"
    pct_retained: float = Field(0.50, ge=0)
    absolute_value: float = Field(13_000, ge=0)


TaxBand = Literal["basic", "higher", "additional"]
LeaseType = Literal["personal", "salary_sacrifice", "company_car"]


# Marginal relief (income tax + employee NI) on salary-sacrificed gross.
# Employee NI rates from April 2024: 8% on £12,570-£50,270, 2% above.
MARGINAL_RELIEF = {"basic": 0.28, "higher": 0.42, "additional": 0.47}

# Income-tax rate only (BiK is not employee-NIable).
INCOME_TAX_RATE = {"basic": 0.20, "higher": 0.40, "additional": 0.45}

# Published BiK schedule for pure EVs (Autumn 2025 Budget confirmed to 2029/30, capped 9%)
# Indexed by tax year starting April. https://www.gov.uk/government/publications/income-tax-increasing-the-appropriate-percentage-for-company-cars
EV_BIK_SCHEDULE = {
    2025: 3.0, 2026: 4.0, 2027: 5.0, 2028: 7.0, 2029: 9.0,
    2030: 9.0, 2031: 9.0, 2032: 9.0,  # assumed flat at the 9% cap
}


def bik_rate_for_tax_year(tax_year_start: int) -> float:
    """Return the BiK rate (%) for a given tax-year start (e.g. 2027 → 2027/28)."""
    if tax_year_start in EV_BIK_SCHEDULE:
        return EV_BIK_SCHEDULE[tax_year_start]
    # Conservative extrapolation: stay at the last known cap
    return max(EV_BIK_SCHEDULE.values())


class LeaseComparator(BaseModel):
    """The fresh EV lease you'd take instead of buying the car out.

    Supports three tax treatments:
      - personal: `monthly_cost` is the net out-of-pocket payment (no BiK, no saving)
      - salary_sacrifice: `monthly_cost` is the GROSS sacrificed from salary;
        marginal-relief is applied as a saving, BiK tax is added back
      - company_car: employer pays the lease; employee only sees BiK
    """
    monthly_cost: float = Field(1_000, ge=0)
    mileage_allowance: int = Field(20_000, ge=0)
    excess_pence_per_mile: float = Field(10.0, ge=0)
    includes_service: bool = True
    includes_ved: bool = True
    # Defaults to True per user preference — toggle off if your specific PCH
    # quote doesn't bundle insurance.
    includes_insurance: bool = True
    insurance_annual: float = Field(950, ge=0)
    includes_eved: bool = True
    # Some inclusive work / salary-sacrifice leases bundle tyres. Defaults True.
    includes_tyres: bool = True

    # Tax treatment
    lease_type: LeaseType = "salary_sacrifice"
    tax_band: TaxBand = "basic"
    # P11d value of the lease car (defaults to current i4 list — typically the
    # replacement car's P11d, which the user may override).
    p11d_value: float = Field(66_124, ge=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def suggest_exit_pct(hold_years: float) -> float:
    """Suggested fraction of buy-out retained at sale, given hold length.

    Calibrated from research: i4 loses ~50% of list at year 3 and continues
    shedding ~10-12% of its remaining value per year, faster at high miles.
    Returns a fraction of the BUY-OUT price (not list price).
    """
    # Empirical anchors derived from research findings:
    #   2yr → ~70%, 3yr → ~55%, 4yr → ~42%, 5yr → ~32%
    anchors = {2.0: 0.70, 2.5: 0.62, 3.0: 0.55, 3.5: 0.48,
               4.0: 0.42, 4.5: 0.37, 5.0: 0.32}
    # Snap to nearest 0.5 then look up.
    key = round(hold_years * 2) / 2
    return anchors.get(key, 0.50)


def ecs_years_active(tax: TaxParams, year_start: date, year_end: date) -> float:
    """Fraction of [year_start, year_end) during which ECS is payable.

    ECS applies from `ecs_first_liable` for `ecs_years_total` years. Returns a
    value in [0, 1] representing the share of this billing year that the
    surcharge is active — lets us pro-rate the final partial year.
    """
    ecs_end = date(tax.ecs_first_liable.year + tax.ecs_years_total,
                   tax.ecs_first_liable.month, tax.ecs_first_liable.day)
    overlap_start = max(year_start, tax.ecs_first_liable)
    overlap_end = min(year_end, ecs_end)
    if overlap_end <= overlap_start:
        return 0.0
    span_days = (year_end - year_start).days
    overlap_days = (overlap_end - overlap_start).days
    return overlap_days / span_days


def per_mile_tax_active_fraction(tax: TaxParams, year_start: date, year_end: date) -> float:
    """Fraction of [year_start, year_end) on or after the per-mile tax start date."""
    if not tax.per_mile_tax_enabled:
        return 0.0
    if year_end <= tax.per_mile_tax_start:
        return 0.0
    if year_start >= tax.per_mile_tax_start:
        return 1.0
    span_days = (year_end - year_start).days
    active_days = (year_end - tax.per_mile_tax_start).days
    return active_days / span_days


# ---------------------------------------------------------------------------
# Year-by-year cost breakdown
# ---------------------------------------------------------------------------

@dataclass
class YearCosts:
    """Cost components for one year of ownership or one year of leasing."""
    year_index: int           # 1-based: year 1 = first year of hold
    year_start: date
    year_end: date
    fraction: float           # 1.0 for full year, <1 for the trailing partial year
    depreciation: float = 0.0
    opportunity_cost: float = 0.0
    insurance: float = 0.0
    service: float = 0.0
    tyres: float = 0.0
    other_maintenance: float = 0.0
    battery_reserve: float = 0.0
    ved: float = 0.0
    ecs: float = 0.0
    per_mile_tax: float = 0.0
    lease_payments: float = 0.0       # GROSS (or net for personal lease)
    tax_savings: float = 0.0          # Negative — reduces effective cost on salary sacrifice
    bik_tax: float = 0.0              # Positive — BiK on P11d × rate × income tax
    excess_mileage: float = 0.0

    @property
    def total(self) -> float:
        return (self.depreciation + self.opportunity_cost + self.insurance
                + self.service + self.tyres + self.other_maintenance
                + self.battery_reserve
                + self.ved + self.ecs + self.per_mile_tax
                + self.lease_payments + self.tax_savings + self.bik_tax
                + self.excess_mileage)

    def as_dict(self) -> dict:
        return {
            "Year": self.year_index,
            "From": self.year_start.isoformat(),
            "To": self.year_end.isoformat(),
            "Fraction": round(self.fraction, 3),
            "Depreciation": round(self.depreciation, 0),
            "Opportunity cost": round(self.opportunity_cost, 0),
            "Insurance": round(self.insurance, 0),
            "Service": round(self.service, 0),
            "Tyres": round(self.tyres, 0),
            "Other maintenance": round(self.other_maintenance, 0),
            "Battery reserve": round(self.battery_reserve, 0),
            "VED": round(self.ved, 0),
            "ECS": round(self.ecs, 0),
            "Per-mile EV tax": round(self.per_mile_tax, 0),
            "Lease gross": round(self.lease_payments, 0),
            "Tax/NI saving": round(self.tax_savings, 0),
            "BiK tax": round(self.bik_tax, 0),
            "Excess mileage": round(self.excess_mileage, 0),
            "Total": round(self.total, 0),
        }


def _split_years(hold_years: float, start: date) -> list[tuple[int, date, date, float]]:
    """Split the hold period into year-long buckets, allowing a partial trailing year."""
    buckets: list[tuple[int, date, date, float]] = []
    whole = int(hold_years)
    remainder = hold_years - whole
    cursor = start
    for i in range(whole):
        next_cursor = date(cursor.year + 1, cursor.month, cursor.day)
        buckets.append((i + 1, cursor, next_cursor, 1.0))
        cursor = next_cursor
    if remainder > 0:
        # Partial year — fixed at remainder * 365 days for simplicity.
        days = round(remainder * 365)
        from datetime import timedelta
        next_cursor = cursor + timedelta(days=days)
        buckets.append((whole + 1, cursor, next_cursor, remainder))
    return buckets


def ownership_year_costs(
    purchase: PurchaseInputs,
    running: RunningCosts,
    tax: TaxParams,
    exit_value: ExitValue,
    start: date = LEASE_END_DATE,
) -> list[YearCosts]:
    """Year-by-year ownership cost breakdown.

    Logic:
      - Depreciation: straight-line from buy-out → exit value, pro-rated by fraction
      - Opportunity cost: cost_of_capital × average book value held during the year
      - Insurance, service, other_maintenance: flat per year, pro-rated
      - Tyres: annual_miles × fraction × (tyre_set_cost / tyre_interval_miles)
      - VED: flat per year, pro-rated
      - ECS: pro-rated by overlap of year with ECS active window
      - Per-mile tax: annual_miles × fraction × rate, pro-rated by start-date overlap
    """
    buckets = _split_years(purchase.hold_years, start)
    exit_val = (exit_value.pct_retained * purchase.buyout_price
                if exit_value.method == "pct_of_buyout"
                else exit_value.absolute_value)
    total_depreciation = purchase.buyout_price - exit_val

    results: list[YearCosts] = []
    book_value = purchase.buyout_price
    for year_idx, y_start, y_end, fraction in buckets:
        dep_this_year = total_depreciation * (fraction / purchase.hold_years)
        avg_book = book_value - dep_this_year / 2
        opp = avg_book * (purchase.cost_of_capital_pct / 100.0) * fraction

        tyre_cost = (purchase.annual_miles * fraction
                     * running.tyre_set_cost / running.tyre_interval_miles)

        ecs_share = ecs_years_active(tax, y_start, y_end)
        eved_share = per_mile_tax_active_fraction(tax, y_start, y_end)
        per_mile = (purchase.annual_miles * fraction
                    * (tax.per_mile_tax_rate_pence / 100.0) * eved_share)

        results.append(YearCosts(
            year_index=year_idx,
            year_start=y_start,
            year_end=y_end,
            fraction=fraction,
            depreciation=dep_this_year,
            opportunity_cost=opp,
            insurance=running.insurance_annual * fraction,
            service=running.service_annual * fraction,
            tyres=tyre_cost,
            other_maintenance=running.other_maintenance_annual * fraction,
            battery_reserve=running.battery_reserve_annual * fraction,
            ved=tax.ved_standard * fraction,
            ecs=tax.ecs_annual * ecs_share * fraction,
            per_mile_tax=per_mile,
        ))
        book_value -= dep_this_year
    return results


def lease_year_costs(
    lease: LeaseComparator,
    running: RunningCosts,
    tax: TaxParams,
    annual_miles: int,
    hold_years: float,
    start: date = LEASE_END_DATE,
) -> list[YearCosts]:
    """Year-by-year cost of running a fresh lease for the same period.

    Insurance + tyres always fall on the driver. Service & VED depend on the
    package. Per-mile EV tax falls on the driver regardless of who holds the V5.
    """
    buckets = _split_years(hold_years, start)
    results: list[YearCosts] = []
    for year_idx, y_start, y_end, fraction in buckets:
        excess_miles = max(0, annual_miles - lease.mileage_allowance)
        excess_cost = excess_miles * fraction * (lease.excess_pence_per_mile / 100.0)

        tyre_cost = (0.0 if lease.includes_tyres
                     else (annual_miles * fraction
                           * running.tyre_set_cost / running.tyre_interval_miles))
        ved_cost = 0.0 if lease.includes_ved else tax.ved_standard * fraction
        service_cost = 0.0 if lease.includes_service else running.service_annual * fraction
        insurance_cost = 0.0 if lease.includes_insurance else lease.insurance_annual * fraction

        eved_share = per_mile_tax_active_fraction(tax, y_start, y_end)
        per_mile = (0.0 if lease.includes_eved
                    else (annual_miles * fraction
                          * (tax.per_mile_tax_rate_pence / 100.0) * eved_share))

        # Tax treatment of the lease cost
        gross_annual = lease.monthly_cost * 12 * fraction

        if lease.lease_type == "salary_sacrifice":
            tax_savings = -gross_annual * MARGINAL_RELIEF[lease.tax_band]
            # BiK rate is dictated by the UK tax year (April–April). For a year
            # bucket that straddles 6 April, pick the rate at the bucket midpoint.
            mid = y_start + (y_end - y_start) / 2
            bik_year = mid.year - (1 if mid.month < 4 else 0)
            bik_rate = bik_rate_for_tax_year(bik_year)
            bik_tax = (lease.p11d_value * (bik_rate / 100.0)
                       * INCOME_TAX_RATE[lease.tax_band] * fraction)
        elif lease.lease_type == "company_car":
            # Employer pays the lease entirely; only BiK hits the employee.
            gross_annual = 0.0
            tax_savings = 0.0
            mid = y_start + (y_end - y_start) / 2
            bik_year = mid.year - (1 if mid.month < 4 else 0)
            bik_rate = bik_rate_for_tax_year(bik_year)
            bik_tax = (lease.p11d_value * (bik_rate / 100.0)
                       * INCOME_TAX_RATE[lease.tax_band] * fraction)
        else:  # personal — no tax interaction
            tax_savings = 0.0
            bik_tax = 0.0

        results.append(YearCosts(
            year_index=year_idx,
            year_start=y_start,
            year_end=y_end,
            fraction=fraction,
            insurance=insurance_cost,
            service=service_cost,
            tyres=tyre_cost,
            ved=ved_cost,
            per_mile_tax=per_mile,
            lease_payments=gross_annual,
            tax_savings=tax_savings,
            bik_tax=bik_tax,
            excess_mileage=excess_cost,
        ))
    return results


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

def total_cost(years: list[YearCosts]) -> float:
    return sum(y.total for y in years)


def monthly_cost(years: list[YearCosts], hold_years: float) -> float:
    return total_cost(years) / (hold_years * 12)


def breakeven_buyout(
    purchase: PurchaseInputs,
    running: RunningCosts,
    tax: TaxParams,
    exit_value: ExitValue,
    lease: LeaseComparator,
) -> float:
    """Buy-out price at which ownership monthly cost equals lease monthly cost.

    Bisection — robust against non-linearity from the ECS / per-mile tax
    thresholds. Returns the price in £, or NaN if no breakeven exists within
    the £1k–list-price bracket (i.e. ownership is dearer — or cheaper — than the
    lease at every price in range).
    """
    lease_years = lease_year_costs(
        lease=lease, running=running, tax=tax,
        annual_miles=purchase.annual_miles, hold_years=purchase.hold_years,
    )
    target = monthly_cost(lease_years, purchase.hold_years)

    def own_monthly(price: float) -> float:
        p2 = purchase.model_copy(update={"buyout_price": price})
        return monthly_cost(
            ownership_year_costs(p2, running, tax, exit_value),
            purchase.hold_years,
        )

    # Bisection only converges if the breakeven price actually lies in the
    # bracket. Check for a sign change first: if ownership is dearer than the
    # lease at every price in range (or cheaper at every price) there is no
    # crossing, so return NaN and let the caller say so rather than report a
    # clamped bracket bound as though it were a real breakeven.
    lo, hi = 1_000.0, LIST_PRICE_GBP
    if (own_monthly(lo) - target > 0) == (own_monthly(hi) - target > 0):
        return float("nan")
    for _ in range(60):
        mid = (lo + hi) / 2
        if own_monthly(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Scenario presets
# ---------------------------------------------------------------------------

def evaluate_preset(name: Literal["pessimistic", "central", "optimistic"],
                    hold_years: float = 3.0,
                    annual_miles: int = 20_000,
                    lease: "LeaseComparator | None" = None) -> dict:
    """Build full input objects from a preset and return summary KPIs.

    Lets the UI render a side-by-side preset comparison without the user having
    to click each scenario in turn. Presets vary ONLY the ownership side — the
    lease is a known fixed quote so it's held constant across scenarios using
    the caller's lease object (or LeaseComparator() defaults if not supplied).
    """
    p = preset(name)
    purchase = PurchaseInputs(
        buyout_price=p["purchase"]["buyout_price"],
        hold_years=hold_years,
        annual_miles=annual_miles,
        cost_of_capital_pct=p["purchase"]["cost_of_capital_pct"],
    )
    running = RunningCosts(**p["running"])
    tax = TaxParams(**p["tax"])
    exit_val = ExitValue(method="pct_of_buyout", pct_retained=p["exit"]["pct_retained"])
    if lease is None:
        lease = LeaseComparator()

    own_years = ownership_year_costs(purchase, running, tax, exit_val)
    lease_years = lease_year_costs(lease, running, tax, annual_miles, hold_years)
    own_mo = monthly_cost(own_years, hold_years)
    lease_mo = monthly_cost(lease_years, hold_years)
    return {
        "scenario": name,
        "buyout": purchase.buyout_price,
        "own_monthly": own_mo,
        "lease_monthly": lease_mo,
        "delta_monthly": own_mo - lease_mo,
        "breakeven": breakeven_buyout(purchase, running, tax, exit_val, lease),
    }


def preset(name: Literal["pessimistic", "central", "optimistic"]) -> dict:
    """Return a bundle of input overrides for a named scenario.

    Defaults rebased May 2026 against actual Autotrader listings + Finder national avg:
    - Buyout: 2023 i4 eDrive40 M Sport at 65k miles listed £22,490 retail
      (Autotrader, May 2026). Trade/wholesale ~15-20% below = £18-19k.
      Leasing-co buyout sits anywhere from market-following (£18k optimistic)
      up to original contract residual (£30k pessimistic).
    - Insurance: BMW i4 national average £1,430 (finder.com Feb 2026).
      Pessimistic = +50% renewal-shock anecdote from i4talk forum.
    """
    if name == "pessimistic":
        return {
            "purchase": {"buyout_price": 30_000, "cost_of_capital_pct": 8.5},
            "running": {"insurance_annual": 1_500, "service_annual": 400,
                        "tyre_interval_miles": 28_000, "tyre_set_cost": 1_150,
                        "other_maintenance_annual": 500,
                        "battery_reserve_annual": 700},
            "tax": {"per_mile_tax_rate_pence": 4.0, "per_mile_tax_enabled": True},
            "exit": {"pct_retained": 0.38},
        }
    if name == "optimistic":
        return {
            "purchase": {"buyout_price": 18_000, "cost_of_capital_pct": 5.0},
            "running": {"insurance_annual": 750, "service_annual": 230,
                        "tyre_interval_miles": 40_000, "tyre_set_cost": 800,
                        "other_maintenance_annual": 200,
                        "battery_reserve_annual": 0},
            "tax": {"per_mile_tax_rate_pence": 3.0, "per_mile_tax_enabled": True},
            "exit": {"pct_retained": 0.60},
        }
    # central
    return {
        "purchase": {"buyout_price": 22_000, "cost_of_capital_pct": 7.0},
        "running": {"insurance_annual": 950, "service_annual": 300,
                    "tyre_interval_miles": 35_000, "tyre_set_cost": 950,
                    "other_maintenance_annual": 300,
                    "battery_reserve_annual": 0},
        "tax": {"per_mile_tax_rate_pence": 3.0, "per_mile_tax_enabled": True},
        "exit": {"pct_retained": suggest_exit_pct(3.0)},
    }
