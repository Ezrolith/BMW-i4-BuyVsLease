# BMW i4 — Buy-out vs Fresh Lease Model

Streamlit app for comparing the total cost of buying out a leased BMW i4 eDrive40 M Sport at end of contract vs taking a new EV lease.

## The setup it models

- BMW i4 eDrive40 M Sport, 83.9 kWh, list £66,124 with options
- First registered April 2024, lease end April 2027 at 60,000 contractual miles
- Current mileage May 2026: 41,500
- Personal lease at ~£1,100/month today; default comparator is a fresh EV salary-sacrifice
  lease at £1,000/month gross (basic-rate IT+NI relief applied, BiK added back)
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

- **VED** £200/yr standard rate (EV first reg before Apr 2025)
- **Expensive Car Supplement** £440/yr for 5 years from first VED liability. For an Apr-2024
  reg EV, that window is **Apr 2025 — Apr 2030** (3 full years inside a 3-year hold from
  Apr 2027; pro-rated to zero in years that fall outside)
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
- `tests/test_models.py` — pytest suite (36 tests). Covers each cost component, the ECS
  window, partial-year pro-rating, and 3 end-to-end scenarios.

## Save & reload scenarios

The bottom of the app has download/upload buttons that round-trip the full scenario as JSON.
Useful for sending a scenario to someone else or comparing changes side-by-side.

## Sensitivity tornado

Each input is varied ±20% and the impact on monthly delta is plotted, ranked by swing.
Tells you which assumptions actually matter for the decision.
