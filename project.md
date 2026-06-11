# Project progress log — BMW i4 Buy-vs-Lease model

Rolling notes to pick up work between sessions. Newest entry first.
(For the stable project overview/assumptions, see `README.md`.)

---

## Session — 2026-06-11 (workplace scheme): Arval salary-sacrifice spreadsheet analysed

### Input
Peter's spreadsheet: `C:\Users\Peter\OneDrive\Desktop\Arval Salary Sacrifice - Car
Leasing Options June 2026.xlsx` (quotes run 11 Jun 2026). Flexi terms: fully insured
lease (third-party + own damage, postcode-rated), guaranteed maintenance, tyres,
breakdown, accident management — and **no early-termination penalty on leaving the job
(redundancy or by choice)**, which Peter values highly. Sheet maths verified exact
(net = gross × (1−28%/42%) + BiK), but uses a FLAT 4% BiK — understates the rising
5/7/9% schedule by ~£10-50/mo depending on band/start.

### His-usage rows (36mo / 60k total = 20k/yr), net recomputed with rising BiK (Sep-2026 start / Apr-2027 start)
| Car | Gross £/mo | Net basic | Net higher |
|---|---|---|---|
| Tesla M3 RWD (332mi, P11d £37,925) | 742.64 | £568 / £579 | **£498 / £519** |
| Mercedes CLA 250+ AMG Line Exec (480mi, P11d £47,785) | 928.53 | £711 / £724 | £623 / £650 |
| Tesla M3 LR RWD (466mi) | 945.19 | £720 / £733 | £628 / £653 |
| Tesla M3 LR AWD (444mi, 4.2s) | 1,005.61 | £768 / £782 | £672 / £700 |

### Verdict
**The work scheme dominates the PCH market at his usage.** Same-car comparison: M3 RWD
at work = £498-579 net all-in with REAL bundled insurance + no-penalty exit, vs the best
PCH £579 with estimated self-arranged insurance and a locked 36-month term. The CLA 250+
(480mi — the range car he originally wanted) costs £623-724 net, far under the PCH CLA
(~£1,038+). All four beat his current scheme's net cost (£726-836). His "I don't think
I'll get a better deal" is confirmed by data — recommended taking the scheme; choice is
M3 RWD (max saving) vs CLA 250+ (range, under his £1,000-gross budget) vs M3 LR AWD
(4.2s, at budget). Shortlist-tab cars (iX1, EX40, iX2, Seal Excellence) are quoted at
45k-total (15k/yr) only — ask Arval for 60k quotes before comparing those.

---

## Session — 2026-06-11 (fairness fix): "vs today" now uses the scheme's NET cost

### What was asked
Peter spotted a real modelling error: market PCH deals are paid from NET pay, but the
"vs today" comparisons used his £1,100 GROSS salary sacrifice — ignoring the IT+NI relief
(and BiK) of the current work scheme. "It feels like it's not a fair comparison."

### He was right — and it shifts every verdict
New `net_of_salary_sacrifice(gross, band, p11d, on)` in models.py: net = gross × (1 −
relief) + P11d × BiK-rate(tax year of `on`) × income-tax-rate / 12. For £1,100 gross,
2026/27 (4% BiK on £66,124): **≈£836/mo net at basic rate, ≈£726 at higher rate**.
Corrected "vs today" deltas (basic / higher):
- Used M3 LR AWD lease £476 → saves 360 / 250 (was "521")
- Tesla M3 RWD lease £579 → saves 257 / 147
- i4 buy-out £599 → saves 237 / 127
- BYD Seal Excellence £703 → saves 133 / **23** (marginal for a higher-rate payer!)
- Mercedes CLA ~£1,038 → **+202 / +312 WORSE than today**
App changes: `current_is_salsac` checkbox (default on, exported), net anchor used in the
top reference line, the market table's "vs today" column + caption, and the cheapest-deal
verdict; all show the gross→net derivation. +2 tests (band maths, Apr-6 tax-year
boundary), suite 68. Band is a UI toggle — he hinted he may be higher-rate (unconfirmed),
which materially tightens the case for switching.

---

## Session — 2026-06-11 (live API): in-app LeaseLoco quoting + review fixes

### What was asked
Peter: review the program against the "filtered deal hunt" use-case and upgrade it to
"API into LeaseLoco, add a notional insurance cost, come up with an effective price for
comparison" — i.e. live quotes in-app.

### What was built
- **`leaseloco.py`** (pure module, injectable fetchers, no Streamlit): `resolve_range_id`
  (model page → range id via `__NEXT_DATA__`, e.g. tesla/model-3 → 339), `fetch_range_quotes`
  (api.leaseloco.com/search/v2 at term/mileage/1-month initial, browser-ish headers),
  `quotes_to_deal_rows` (×1.2 ex-VAT correction baked in, upfront = fee + (N−1)×monthly,
  per-item error isolation), `notional_insurance` (0-62-time tiers calibrated to the
  Parkers groups verified this week: <3.5s ×1.40, <4.5 ×1.10, <5.5 ×1.0, else ×0.85 of
  the user's own premium — estimates, clearly labelled).
- **App**: "🔄 Fetch live LeaseLoco quotes (API)" expander above the deals editor — slug or
  URL in, term/mileage snapped to API-accepted values nearest the user's hold/usage,
  cached (`st.cache_data`, 15 min quotes / 24 h range-ids), rows merged into the table and
  ranked all-in like everything else. Live-verified: tesla/model-3 → 7 exact quotes.
- Suite 53 → **66 tests** (mapping/VAT/dedupe/edge cases), AppTest button-click E2E passes.

### Adversarial review (all confirmed by repro, all fixed + regression-tested)
1. **MAJOR — "append-only avoids a remount" was false.** Streamlit 1.41.1 hashes the
   data_editor's input into its element id, so ANY base-data change remounts it and wipes
   widget-state edits silently. Fix: each run snapshots the rendered table
   (`market_rendered`); the fetch folds that snapshot (= what the user sees, edits and
   all) into the base before appending, then remounts explicitly via the nonce. Scenario
   load clears the snapshot so pre-load rows can't resurrect.
2. Dedupe now keys off the rendered table (was: invisible base rows) — deleted rows can
   be re-fetched, hand-pasted quotes dedupe; "Added 0" shows as st.info.
3. Fetch date moved Description → Source: it was inside the dedupe key, so every
   later-day refetch would have duplicated all unchanged quotes.
4. Missing API mileage → None (user's own miles), not 0 (phantom £167/mo excess).
5. `"results": null` → `[]` (None would be cached as success for the 15-min TTL).
6. One malformed vehicle item is skipped, not allowed to abort the whole fetch.
(One finding rejected by the verifier: the range-id most-common heuristic is sound.)

### Validation verdict for Peter's use-case
The program now does the full loop he described: pick a model → live exact quotes at his
terms → notional insurance per car → all-in effective £/mo ranked against buy-out, his
lease, and today. Criteria filters (0-60, range) live in the row labels; the maths
(min(term,hold) amortisation, excess at his mileage, per-row insurance) was already
review-hardened earlier this week.

---

## Session — 2026-06-11 (fast variants): sub-4.5s Model 3 / Seal hunt

### What was asked
Peter: dig for Tesla Model 3 and BYD Seal **variants with 0-60 under 4.5s only** — best deal.

### Qualifying variants (0-60 verified per variant, Parkers/EV Database; Tesla quotes 0-60, BYD 0-62)
2026 lineup naming: Tesla renamed Long Range trims to "Premium" in Feb 2026; 85kWh pack
since late 2025. Qualifiers: **M3 Premium LR AWD 4.2s** (group 40), **M3 Performance
2.9s** (group 48), **BYD Seal Excellence AWD 390kW 3.8s 0-62** (group 48). Rejected for
speed: M3 RWD / Premium LR RWD (4.9s), Seal Design RWD (5.7s).

### Deals found (exact 36/20k/1+35, inc VAT — sweep re-audited the LeaseLoco ×1.2 rule, all clean)
| Deal | Headline | All-in |
|---|---|---|
| **USED 2021 M3 LR AWD, 35,699 mi, NVC (1 unit, LB71 UJV)** | £285.45 | **£476** |
| BYD Seal Excellence AWD, LeaseLoco/Autotrader (£0 fee, stock) | £522.30 | £703 |
| BYD Seal Excellence AWD pre-reg, NVC (21.6p excess!) | £521.87 | £713 |
| M3 Premium LR AWD new (444mi), LeaseLoco (£0 fee, factory order) | £809.65 | £965 |
| M3 Performance new (2.9s), FVL | £1,131.82 | £1,322 |

Caveats: group-48 insurance figures are estimates and Tesla/BYD real quotes often exceed
group-implied; used car's excess unpublished; M3 Performance tyre habit ~£900-1,000/yr at
20k mi/yr is NOT in the app's i4-calibrated maintenance add-on; Seal DC ceiling 150kW vs
Tesla 250kW + Supercharger access. 5 rows added to seeds (0-60 in labels), state v11.

---

## Session — 2026-06-11 (later): EV field expansion + critical VAT correction

### What was asked
Peter: don't limit to the i4/CLA — expand to other EVs, Teslas with **exact variant
labels** (RWD vs Long Range etc.), and "cheaper other electric cars", LeaseLoco
especially. Money-first, range matters (20k mi/yr).

### CRITICAL data correction (affects all prior LeaseLoco numbers)
**LeaseLoco's lease-profile API returns EX-VAT prices even for personal leases** — the
site's JS multiplies by 1.2 for display. The 10 Jun i4 capture missed this, so all
LeaseLoco i4 seed rows were ~17% understated. Corrected ×1.2 and cross-verified live
(eDrive35 Sport: 786.17 ex → **£943.40 inc**, exact match on a fresh 11 Jun quote; fee
£249 ex = £298.80 inc, matches VehicleFlex's page). **Consequence: there is no cheap new
i4 — the floor is ~£1,101/mo all-in, i.e. today's £1,100.** Any future API re-quote MUST
apply ×1.2 (Carwow Leasey's API is already inc-VAT; NVC/Select pages are inc-VAT).
Also: **the £574 used i4 M50 sold** — row removed.

### EV field sweep (5 agents, 112 raw rows, all exact quotes at 36/20k/1+35)
8 rows added (exact variant + WLTP range in every label, per-row insurance estimate from
ABI group, anchor i4-eDrive40-group-38 = £950):
| Car | Headline | All-in effective |
|---|---|---|
| Tesla Model 3 Standard RWD 62.5kWh, 332mi | £443.60, £0 fee | **£579** |
| Skoda Enyaq 85 SE L, 359mi | £478.55 | £622 |
| Hyundai Ioniq 6 Ultimate AWD, 323mi (12p excess verified) | £484.81 | £652 |
| Cupra Tavascan V1, 343mi (in stock) | £514.86 | £654 |
| BYD Seal Design, 354mi (in stock; group 46 ins!) | £484.00 | £658 |
| Polestar 2 LR SM, 409mi (in stock, run-out) | £540.10 | £705 |
| Tesla Model 3 Long Range RWD 85kWh, 466mi | £611.74 | £759 |
| VW ID.7 Match Pro S Plus, 434mi | £599.37 | £760 |

**Verdict flip: the Model 3 RWD (£579) now beats even buying out the i4 (£599 central)
and saves £521/mo vs today.** The M3 LR RWD offers 466mi (more than the CLA 250+) at
£759 all-in. The whole field undercuts every i4 option by £340–£520/mo.

### Mechanics / repo changes
- `SEED_MARKET_DEALS`: i4 LeaseLoco rows VAT-corrected (kept M Sport like-for-like +
  eDrive35 floor), used-M50 row removed (sold), 8 EV rows added → 16 rows total;
  `_STATE_VERSION` → 10; caption/README rewritten for the EV field.
- Suite 53 passing; AppTest 0 exceptions; ranking + verdict verified headlessly.
- Sweep agent tricks recorded: LeaseLoco search/v2 + lease-profile APIs (ex-VAT, needs
  browser-ish headers), Carwow Leasey bulk API (inc VAT), NVC/Select URL params, FVL
  honours ?term/mileage/initial_rental, Moneyshake locks 12mo initial. Leasing.com/
  hotukdeals/Reddit blocked. Ratebooks expire 30 Jun–10 Jul 2026.

### Possible next steps
- Insurance estimates are formula-scaled from ABI groups — get a real quote for the
  Model 3 RWD before deciding (group 32 → ~£777/yr est; BYD's group 46 → ~£1,241 est).
- Moneyshake's Ioniq 6 Premium AWD ~£474/mo effective requires a 12-month initial —
  add a row if Peter will tolerate heavy upfront.
- Re-quote everything when ratebooks roll (30 Jun–10 Jul); check NVC used stock again.

---

## Session — 2026-06-11: ECS resolved, deal details verified, insurance overrides, Mercedes CLA added

### What was asked
"Make further progress" on the open items, then mid-session: **bring in rival EVs — Peter
is not married to BMW, money-saving first** — starting with the new electric Mercedes CLA
(he likes its range vs the i4).

### Open questions closed (researched with live sources, 11 Jun 2026)
- **ECS: RESOLVED — the Apr-2024 i4 is fully exempt** (high confidence). Gov.uk 2026/27
  rate tables exempt zero-emission vehicles first registered before 1 Apr 2025 from the
  £440/yr supplement entirely. Autumn Budget 2025 raised the EV threshold to £50k but only
  for EVs registered on/after Apr 2025 — the carve-out is unchanged. README's contradictory
  £440/yr window text fixed; memory updated to resolved. (`ecs_annual` input stays — a
  replacement EV registered now with list > £50k would pay it.)
- **Used M50 verified on its live quote page** (MW23 EKN, vehicle id 7395, still listed):
  £574/mo at exactly 36/20k/1+35 confirmed; £357 processing fee confirmed; excess is
  6p +VAT = **7.2p/mi inc VAT** (10.8p beyond 5k excess). Caveats: 3-yr BMW warranty likely
  expired (battery to ~2031); optional used-maintenance package is steep (£209/mo). Seed row
  updated. Detail endpoint trick: /car-leasing/used/7395/vehicle needs header `nvc-ajax: true`.
- **Performance-variant insurance**: i4 M50 = ABI group 44, M60 = 45 vs eDrive40's 38
  (Parkers). For this profile (~3.4%/group): M50 ~£1,140/yr, M60 ~£1,200/yr vs the £950
  baseline. Estimates, not quotes.

### New: per-row insurance override
`MarketDeal.insurance_override` (£/yr, None = RunningCosts baseline) + "Ins £/yr" editor
column, wired through `evaluate_market_deal`, whitelisted in `_coerce_deal_rows`, +1 test.
M50/M60/CLA seed rows carry their estimates. Effect: used M50 now ~£749 all-in (was £733).

### New: Mercedes CLA expansion (user request)
- **CLA 250+ with EQ Technology: 484 mi WLTP** (vs ~365 i4 eDrive40), 85kWh, ABI group
  41-42, OTR from ~£43k. Range claim checks out.
- **No broker publishes 36/20k personal pricing yet** (car launched 2026; NVC "coming
  soon", LeaseLoco no EV-CLA inventory, Select/FVL/VehicleFlex quote-gated). Best published
  bases seeded: DriveElectric CLA 250+ Sport **£657.93/mo exact personal** at 48/5k/9mo
  initial; e-car lease business £515.27 ex-VAT → **£618.32 est** (+VAT) same basis. Both
  48mo → the term-cut warning fires (by design); 5k allowances get the excess penalty.
- **Normalised result: CLA ~£1,038–£1,087 all-in** — under today's £1,100 but above the
  LeaseLoco i4 rows (~£942–£982). Honest caveat: most of the CLA's penalty is excess
  mileage on a 5k base — a real 36/20k quote could land either side of the i4. Re-check
  when brokers list it.
- Section renamed "Market lease deals — i4 & rival EVs"; caption + README updated; state
  v9 reseed. Suite 53 tests passing; AppTest 0 exceptions; ranking + term warning verified.
- NOTE: the CLA sweep workflow itself died on the session limit (resets 04:20) — research
  was done inline instead; only ~8 fetches, narrower than the i4 sweep. A proper multi-agent
  CLA sweep (more brokers, stock deals, hotukdeals) is worth re-running later.

### Possible next steps
- Re-run the deal sweep when **Carwow's Arval ratebook expires 30 Jun 2026**, and re-check
  CLA 36/20k listings at the same time (LeaseLoco API / NVC / Select URL params are cheap).
- The used-M50 (£749 all-in) is still the standout — but verify the insurance estimate with
  a real quote before deciding; warranty expiry is the risk to price in.
- If more rivals join (Tesla Model 3 LR? Polestar 2?), consider a "Range mi" info column so
  the table carries the practicality trade-off, not just £/mo.

---

## Session — 2026-06-10 (evening): Real exact-config quotes + deals save/load round-trip

### What was asked
"Pick up project.md and continue the previous objective" — i.e. the open next steps from
the afternoon session: real 36mo/20k quotes for the seed rows, `market_deals` in the JSON
save/load, and the README test-count fix.

### Real quotes — the big finding
A 6-agent web sweep (5/6 sources succeeded) recovered **exact personal PCH quotes at the
exact target config (36 mo / 20,000 mi/yr / 1-month initial), 10 Jun 2026** — no more
"from"-price estimating:
- **LeaseLoco** exposes a public pricing API (`api.leaseloco.com/search/lease-profile`) +
  `__NEXT_DATA__` deal cards → eDrive35 Sport **£786.17**, eDrive35 M Sport £803.06,
  eDrive40 Sport **£816.18**, eDrive40 M Sport **£826.12**, M60 £995.19 (supplier Vehicle
  Flex, factory order, +£249 doc fee).
- **Carwow** main page is JS-only, but its white-label **Leasey** site has a live bulk
  pricing API → eDrive40 Sport £989.43, eDrive40 M Sport £1,001.34, M60 £1,162.60
  (+£295 admin fee; Arval ratebooks **expire 30 Jun 2026**).
- **Nationwide VC** honours URL params (`?term=36&mileage=20000&initial=1`) → new range is
  **M60-only** (£1,180.70–£1,256.24, excess 16.8–27.6p/mi, +£357 fee); plus a **used 2023
  M50 (55k mi) stock car at £574/mo** at the exact target terms.
- **Select Car Leasing** also honours URL params → M60 £1,207.86 (eDrive40 M Sport £1,532 —
  outlier, excluded).
- **Lineup change: the M50 is discontinued — the 2026 facelift range is
  eDrive35 / eDrive40 / M60.**
- Effective all-in (central running costs): used M50 **~£733/mo** (saves ~£367 vs today's
  £1,100; central buy-out still ~£134/mo cheaper); LeaseLoco eDrive40 M Sport like-for-like
  **~£982/mo** (~£118 under today). The £849 "target" from the afternoon entry is beaten
  by the used car but not by any new exact quote.
- The 6th sweep agent (deal communities) + the synthesis agent hit the session limit;
  synthesis was done by hand from the 60 raw rows (raw data in the workflow transcript).

### Upfront convention fix (worth remembering)
An N-months-initial PCH profile (e.g. 9+35) costs **(N−1) extra monthlies** over a flat
36-month profile — the initial rental replaces month 1. The afternoon seeds used N×monthly
(one month overcounted). New convention everywhere (seeds, column help, README): **Upfront £
= (N−1)×monthly + one-off fees**, so a 1+35 quote = fees only.

### Files changed
- **`app.py`**: `SEED_MARKET_DEALS` replaced with 8 exact-quote rows (sources/fees/dates in
  comments); `_STATE_VERSION` 6 → 7 (reseed); caption + Upfront help rewritten; market-deals
  table now round-trips through Save/load — base rows live in
  `st.session_state["market_deals"]`, the editor is remounted via a key nonce on load
  (`market_deals_editor_{nonce}`) because Streamlit forbids writing data-editor widget state
  and reusing a key overlays stale edits; export uses the editor's merged output
  (`market_records`); `market_add_insurance`/`market_add_maintenance` added to `EXPORT_KEYS`
  (checkboxes moved to the seeded-key idiom, no `value=`).
- **`models.py`**: new `jsonable_records()` — unwraps numpy scalars, maps NaN→None,
  stringifies exotics, so editor records survive `json.dumps`; `evaluate_market_deal` now
  term-aware + eVED flag (below).
- **`tests/test_models.py`**: +8 tests (3 jsonable coercion/round-trip, 5 from the review
  round below). **Suite now 52 tests, all passing.**
- **`README.md`**: test count 36 → 52; new "Market lease deals" section; save/load section
  mentions the deals table.

### Adversarial review round (multi-agent, every finding verified by repro)
A find→refute workflow over the diff confirmed 7 distinct issues, all fixed:
1. **Scenario upload was broken in the committed app all along** — writing widget keys
   after widgets are instantiated raises `StreamlitAPIException` (Streamlit 1.41.1), so
   every upload died on `buyout_price` with "Couldn't load". My deals-write landing first
   made it a *torn* half-load. Fix: the uploader now only **stages** the validated dict in
   `_pending_scenario` + `st.rerun()`; it's applied at the top of the script (before any
   widget exists). Success banner via `_scenario_loaded` flag.
2. **No processed-file guard** — `st.file_uploader` re-returns the file every rerun, which
   would re-bump the editor nonce (discarding post-load table edits) and loop. Fix: process
   only when `uploaded.file_id != st.session_state["_loaded_file_id"]`.
3. **Hand-edited JSON could brick the session** (uniformly wrong-typed column → editor
   raises above the uploader every rerun; negative/Infinity → MarketDeal `ge=0` crash loop).
   Fix: `_coerce_deal_rows()` whitelist-coerces rows at load; `_num` now requires
   `isfinite`; `max(0/1, …)` clamps at MarketDeal construction.
4. **`term_months` was decorative** — a 24mo deal's upfront amortised over 36mo and months
   it didn't cover were priced at its rate. Fix: deals are costed over **min(term, hold)**
   (`eval_years` in `evaluate_market_deal`); UI warns on term ≠ hold and the results table
   now shows Term.
5. **Blank Miles/yr → 0 allowance** billed all 20k miles as phantom excess (~£167/mo) on
   half-entered rows. Fix: blank allowance defaults to `purchase.annual_miles`.
6. **eVED hard-coded as included** contradicted `lease_year_costs`' docstring and tilted
   "vs buy" by ~£33/mo with no way to align. Fix: third checkbox ("Deals include per-mile
   EV tax?", default ticked), `include_eved` param, exported in the scenario JSON.
7. **Test blind spots** — upfront tests only used whole-year holds (a dropped `×fraction`
   survived the suite), wiring tests used values equal to field defaults, one assertion was
   tautological. Fix: fractional-hold, non-default-wiring, component-identity, term, and
   eVED tests added.

### Verified
`pytest` 52 passing; headless `AppTest` renders 0 exceptions; real upload path exercised
end-to-end with a patched `st.file_uploader`: atomic apply of widget keys + deals + nonce,
file_id guard stops reprocessing, poisoned file (wrong types / negatives / Infinity) is
coerced and can't crash, malformed file errors cleanly with state untouched; verdict line
renders ("Cheapest market deal: Nationwide VC — USED 2023 i4 M50 … effective £733/mo").

### Possible next steps
- Re-quote after **30 Jun 2026** (Carwow Arval ratebook expiry); LeaseLoco API + NVC/Select
  URL params make refreshing cheap.
- The used-M50 row is the standout — if interesting, verify its excess ppm + fee on the
  deal page (JS-only, needs a browser) before it sells.
- M60/M50 insurance will run above the £950 eDrive40 assumption — consider a per-row
  insurance override column if precision matters.
- Open question still pending: **ECS exemption** for this Apr-2024 EV (see memory).

---

## Session — 2026-06-10: Market lease deals (LeaseLoco / Carwow) comparison

### What was asked
Peter is starting to think **leasing might be the better option than buying out** the i4,
but wants to find a *cheaper* lease. He asked to expand the app to compare **personal** (not
business) lease deals from **LeaseLoco** (leaseloco.com) and **Carwow** (carwow.co.uk), plus
brokers, aligned to his terms — **36 months, 20k miles/yr, ideally £0 upfront** — and to add
**insurance + maintenance** on top (PCH deals don't bundle them), so he can see the most
efficient deal rather than comparing just the one lease.

### What was built
A new **"Market lease deals — LeaseLoco / Carwow"** section in the main panel (between the
Scenario range table and the charts). It takes an **editable table** of personal PCH quotes
and normalises every one to an **all-in effective £/mo** so they're apples-to-apples:

- **Upfront amortised** across the hold (initial rental ÷ months).
- **Excess mileage** charged automatically at his 20k/yr vs each deal's allowance.
- **Insurance + maintenance (service + tyres)** added from the existing running-cost inputs,
  with two checkboxes to toggle each add-on off.
- Road tax (VED) + per-mile EV tax treated as **included** (leasing co holds the V5) — not
  double-counted.
- Results **ranked cheapest-first**, with `vs your lease / vs buy / vs today (£1,100)` columns,
  a verdict line on the cheapest deal, and a horizontal bar chart against buy-out + your
  configured lease.
- Table is **fully editable** (`st.data_editor`, dynamic rows) — paste real configurator
  quotes straight in.

### Files changed
- **`models.py`**
  - `LeaseComparator`: added `initial_payment: float = Field(0, ge=0)` (£ upfront). Defaults
    to 0 so existing behaviour is unchanged.
  - `lease_year_costs()`: amortises `initial_payment / hold_years * fraction` into the lease
    gross (so it also flows through sal-sac relief / per-month maths correctly).
  - New `MarketDeal(BaseModel)` schema (source, label, monthly_cost, initial_payment,
    mileage_allowance, term_months, excess_pence_per_mile).
  - New `evaluate_market_deal(deal, running, tax, annual_miles, hold_years, add_insurance,
    add_maintenance, start)` → returns effective_monthly, total, and a per-month component
    breakdown (lease line, upfront, excess, insurance, service, tyres). It builds a
    `LeaseComparator(lease_type="personal", includes_ved=True, includes_eved=True,
    includes_insurance=not add_insurance, includes_service/tyres=not add_maintenance)` and
    reuses `lease_year_costs`.
- **`app.py`**
  - Imported `MarketDeal`, `evaluate_market_deal`.
  - Bumped `_STATE_VERSION` 5 → 6 (forces a one-time session reseed).
  - Added `SEED_MARKET_DEALS` constant (5 indicative rows, see below).
  - Added the whole Market lease deals UI block + `_num()` helper that coerces NaN/None
    data-editor cells safely.
- **`tests/test_models.py`**: +6 tests (upfront amortisation in two forms, market-deal
  insurance/maintenance/excess add-ons, upfront amortisation in the normaliser, and the
  toggle-off path). **Suite is now 44 tests (was 38), all passing.**
  - NOTE: `README.md` still says "36 tests" — slightly stale, worth a one-line fix next time.

### Data sourcing — important honesty notes
Live prices could **not** be scraped — LeaseLoco/Carwow are JS configurators that don't expose
deal data to a fetch, and prices change daily. So in-app live scraping is **deliberately not
done**. Instead the table is **seeded with real, dated (June 2026) headline figures** and is
editable. Which seed numbers are real vs estimated:
- **Real headline monthlies:** Carwow personal "from £574/mo"; Nationwide Vehicle Contracts
  eDrive40 M Sport "from £615", Sport+Tech "from £684".
- **Estimated (labelled "price est"):** LeaseLoco row — found the exact config (i4 GC eDrive35
  M Sport, 36mo / 10k miles / 12-month upfront) but the monthly sits behind their quote button,
  so £560 is a placeholder estimate.
- **Placeholder:** "(your quote)" row — a £0-upfront / 20k-allowance target at £700 headline,
  the shape Peter actually wants; replace with a real quote.
- Sources: leaseloco.com/car-leasing/bmw/i4 · carwow.co.uk/bmw/i4/lease ·
  nationwidevehiclecontracts.co.uk/car-leasing/bmw/i4

### Key finding (seeded numbers, central running costs, 3yr/20k hold)
Headline "from" prices are a trap at 20k/yr — they're quoted at 5k miles with a big upfront:

| Deal | Headline | All-in effective £/mo |
|---|---|---|
| No-upfront, true 20k/yr (target) | £700 | **£849** |
| Carwow eDrive40 M Sport | £574 | **£992** |
| Nationwide eDrive40 M Sport | £615 | **£1,043** |
| Nationwide Sport+Tech | £684 | **£1,129** |

So £574 "from" really costs **~£992/mo all-in** for a 20k driver (upfront ÷36 ≈ £144 + 15k
excess miles @10p ≈ £125 + insurance ≈ £79 + maintenance ≈ £70). Lesson the tool makes obvious:
**hunt a no-upfront deal with a real 20k/yr allowance** even at a higher headline — ~£849 all-in
beats today's £1,100 and most buy-out scenarios. Today's fully-inclusive lease (£1,100/mo) is
the benchmark to beat.

### Decisions taken this session (can revisit)
- 20,000 miles treated as **per-year** (matches his usage) → low-allowance deals get penalised
  via excess mileage. (Alt: treat 20k as whole-contract — would favour low-mileage deals.)
- "Maintenance" = **service + tyres** only (matches the existing single-lease comparator),
  keeping market deals directly comparable. "Other maintenance" sundries deliberately not added.

### How to run / verify
```bash
cd "C:/Users/Peter/OneDrive/Documents/Claude/Projects/BMW_i4_Model"
streamlit run app.py            # → http://localhost:8501
python -m pytest -q             # 44 passing
```
Headless render check (catches script-level runtime errors without a browser):
```bash
python -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('app.py',default_timeout=60).run(); print('exceptions:',len(at.exception))"
```

### Possible next steps
- Replace seed rows with **real 36mo/20k personal quotes** from the configurators once obtained.
- Consider adding `market_deals` to the JSON save/load (`EXPORT_KEYS`) so a deal set round-trips
  (currently not exported — the data-editor state reseeds each session).
- Update `README.md` test count 36 → 44.
- Open question still pending: **ECS exemption** for this Apr-2024 EV (see memory).

### Environment notes
- This repo is the **BMW i4 Streamlit model**. The global `C:\Users\Peter\CLAUDE.md` describes a
  different project (HannaPig snake game) — its "bump version / commit + Firebase deploy" workflow
  rules **do NOT apply here**. No `index.html`, no Firebase, no version footer in this project.
