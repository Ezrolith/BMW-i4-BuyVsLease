# BMW i4 — Buy-out vs Fresh Lease Model

Streamlit app for comparing the total cost of buying out a leased BMW i4 eDrive40 M Sport at end of contract vs taking a new EV lease.

## The setup it models

- BMW i4 eDrive40 M Sport, 83.9 kWh, list £66,124 with options
- First registered April 2024, lease end April 2027 at 60,000 contractual miles
- Current mileage May 2026: 41,500
- Current car is on a **salary-sacrifice work scheme at £1,100/month gross** — its true
  net cost is ≈£836/mo (basic rate) or ≈£726/mo (higher rate) once IT+NI relief and BiK
  are applied, and that net figure (not the gross) is the "vs today" anchor for net-pay
  PCH comparisons (toggleable). Default comparator is a fresh EV salary-sacrifice lease
  at £1,000/month gross (relief applied, BiK added back)
- Driver: 41, full NCB, M-postcode (Salford)

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then visit http://localhost:8501.

## Run the tests

```bash
pip install -r requirements.txt
pytest tests/
```

## Model assumptions & sources

All defaults are editable. The top of the app shows a **Research summary & sources** panel
with the URLs used to anchor each default. Key choices:

- **VED** £200/yr standard rate (EV first reg before Apr 2025; 2026/27 rate)
- **Expensive Car Supplement: £0 — this car is exempt** (resolved Jun 2026). Gov.uk's
  2026/27 rate tables exempt zero-emission vehicles **first registered before 1 April
  2025** from the £440/yr supplement entirely; this Apr-2024 i4 never pays it despite its
  £66,124 list price. (Autumn Budget 2025 raised the ECS threshold to £50k for EVs
  registered on/after Apr 2025 — irrelevant here.) The `ecs_annual` input stays for
  modelling a *replacement* car registered after Apr 2025, which would pay it.
  Sources: gov.uk/vehicle-tax-rate-tables · gov.uk/guidance/vehicle-tax-for-electric-and-low-emissions-vehicles
- **Per-mile EV tax** 3p/mile from **April 2028**, confirmed in Autumn Budget 2025
- **Buy-out central** £22,000 (2023 i4 eDrive40 M Sport @ 65k miles ~£22,490 retail, May 2026;
  range £18k optimistic to £30k pessimistic)
- **Exit value** auto-suggested from a depreciation curve calibrated to the research:
  ~70% of buyout retained at 2yr hold, ~32% at 5yr hold
- **Insurance** £950/yr central (finder.com Feb 2026 base ~£756 for the cheapest i4 +
  group/Manchester loading). The £1,500/yr figure is the pessimistic renewal-shock preset
- **Service** £300/yr (range £180-£400, dealer end)
- **Tyres** £950 fitted set, 35k mile interval (premium EV-rated 19")
- **Cost of capital** 7% central (optimistic 5%, pessimistic 8.5%) applied to *declining*
  book value (avg book value × rate each year)

## What's deliberately excluded

- **Charging / electricity costs** — same for both alternatives if next lease is also EV
- **Fuel** — N/A on either side
- **BiK / company car tax** — this is personal ownership

## Architecture

- `models.py` — pure calculation functions + pydantic input schemas. No Streamlit imports.
  Anything here is unit-testable in isolation.
- `app.py` — Streamlit UI. Builds pydantic objects from `st.session_state`, hands them to
  `models.py`, renders the result.
- `leaseloco.py` — live LeaseLoco pricing-API client (range-id resolution, quote fetch,
  row mapping with the ex-VAT ×1.2 correction, notional-insurance heuristic). Pure
  functions with injectable fetchers; no Streamlit imports.
- `tests/` — pytest suite (66 tests across `test_models.py` + `test_leaseloco.py`).
  Covers each cost component, the ECS window, partial-year pro-rating, market-deal
  normalisation (term-aware amortisation, eVED flag, component identities), JSON-safe
  record coercion, the live-quote mapping/VAT/dedupe behaviour, and 3 end-to-end scenarios.

## Market lease deals — the EV field (LeaseLoco / Carwow / brokers)

An editable table of personal PCH quotes across the EV field — BMW i4 variants, the new
electric Mercedes CLA 250+, Tesla Model 3 (variants labelled exactly: Standard RWD vs
Long Range RWD), Polestar 2, Hyundai Ioniq 6, BYD Seal, Škoda Enyaq, Cupra Tavascan and
VW ID.7, each with WLTP range in the label and a per-row insurance estimate from its ABI
group — each normalised to an all-in **effective £/mo**
so deals are apples-to-apples with the buy-out and your own lease: upfront amortised over
the hold, excess mileage charged at your annual usage, insurance + maintenance added on
top (PCH deals don't bundle them).

**Live quotes:** the section's "Fetch live LeaseLoco quotes" expander pulls exact personal
prices for any model straight from LeaseLoco's pricing API at your term/mileage (their API
quotes ex-VAT — corrected ×1.2, a verified gotcha), attaches a notional insurance estimate
scaled off your own premium by each car's 0-62 time, and merges the rows into the table —
preserving any edits you've made. Estimates, not quotes; deals churn daily. Seed rows are **exact quotes (10 Jun 2026)** at the
target config — 36 months / 20k miles/yr / 1-month initial — captured from each site's
own pricing API or server-rendered listings, not "from" headlines. Upfront cells hold
one-off fees only (a 1+35 profile adds no extra months; an N-months-initial quote is
(N−1)×monthly + fees). Paste fresh configurator quotes in as prices move.

## Save & reload scenarios

The bottom of the app has download/upload buttons that round-trip the full scenario as JSON
— including the market-deals table as edited. Useful for sending a scenario to someone else
or comparing changes side-by-side.

## Sensitivity tornado

Each input is varied ±20% and the impact on monthly delta is plotted, ranked by swing.
Tells you which assumptions actually matter for the decision.
