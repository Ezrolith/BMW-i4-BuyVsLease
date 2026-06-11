"""Live LeaseLoco quote fetching for the market-deals table.

LeaseLoco's public API (the one its own frontend calls) exposes exact personal
PCH quotes per term/mileage/initial-payment combination. Two-step flow:

1. ``resolve_range_id`` — a model's numeric "range" id isn't published, but
   every range page (leaseloco.com/car-leasing/<make>/<model>) embeds it in
   its __NEXT_DATA__ JSON; the most frequent ``"range":<id>`` wins.
2. ``fetch_range_quotes`` — GET api.leaseloco.com/search/v2 with the range id
   and the user's terms; returns one best-priced result per vehicle.

CRITICAL: the API returns EX-VAT money fields (monthlyPayment, documentFee,
initialPaymentTotal) even for personal leases — LeaseLoco's frontend
multiplies by 1.2 for display, and so does this module (``VAT``). Discovered
11 Jun 2026 after a day of ~17% understated prices; verified against rendered
pages to the penny (e.g. Polestar 2 LR Prime 450.0833 ex -> GBP540.10 inc).

No Streamlit imports here — everything is unit-testable with injected
fetchers; the app wraps these in cached UI handlers.
"""
from __future__ import annotations

import collections
import re
from typing import Callable

VAT = 1.2  # UK VAT multiplier; API money fields are ex-VAT (see module doc)

API_BASE = "https://api.leaseloco.com"
SITE_BASE = "https://www.leaseloco.com"

# The API rejects arbitrary mileages — these are the values its own UI offers.
ALLOWED_MILEAGES = (5_000, 8_000, 10_000, 12_000, 15_000, 20_000, 25_000, 30_000)

# Browser-like headers: the API 403s anonymous/no-referer clients.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/148.0 Safari/537.36"),
    "Origin": SITE_BASE,
    "Referer": SITE_BASE + "/",
    "Accept": "application/json",
}


def nearest_allowed_mileage(annual_miles: int) -> int:
    """Snap the user's annual mileage to the closest value the API accepts."""
    return min(ALLOWED_MILEAGES, key=lambda m: abs(m - annual_miles))


def normalise_slug(slug_or_url: str) -> str:
    """Accept 'tesla/model-3' or any leaseloco.com/car-leasing/... URL."""
    s = slug_or_url.strip().strip("/")
    m = re.search(r"car-leasing/([^/]+/[^/?#]+)", s)
    if m:
        return m.group(1).lower()
    if re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", s.lower()):
        return s.lower()
    raise ValueError(
        f"'{slug_or_url}' doesn't look like a make/model slug or a LeaseLoco "
        "model page URL (expected e.g. 'tesla/model-3')")


def resolve_range_id(slug_or_url: str, get: Callable | None = None) -> int:
    """Extract the numeric range id from a model page's embedded JSON."""
    if get is None:  # pragma: no cover - exercised via the live smoke check
        import requests
        get = lambda url: requests.get(url, headers=HEADERS, timeout=30).text
    slug = normalise_slug(slug_or_url)
    html = get(f"{SITE_BASE}/car-leasing/{slug}")
    ids = collections.Counter(re.findall(r'"range":(\d+)', html))
    if not ids:
        raise ValueError(f"No LeaseLoco range id found on {slug} — page moved "
                         "or model not listed")
    return int(ids.most_common(1)[0][0])


def fetch_range_quotes(range_id: int, term: int = 36, mileage: int = 20_000,
                       initial_months: int = 1,
                       get_json: Callable | None = None) -> list[dict]:
    """Best-priced personal quote per vehicle in a range, raw API items."""
    if get_json is None:  # pragma: no cover - exercised via the live smoke check
        import requests

        def get_json(url, params):
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()

    data = get_json(f"{API_BASE}/search/v2", {
        "leaseTypes": 2,            # personal
        "terms": term,
        "mileages": mileage,
        "initialPaymentInMonths": initial_months,
        "sortBy": 2,                # price ascending
        "ranges": range_id,
    })
    # `or []`: an explicit "results": null must not leak None to callers —
    # st.cache_data would cache it as a success for the whole TTL.
    return data.get("results") or []


def notional_insurance(zero_to_62: float | None, baseline: float) -> float | None:
    """Notional annual premium for a fetched car, scaled off the user's own.

    The API doesn't expose ABI groups, so this uses 0-62 time as a proxy,
    calibrated against the groups verified on Parkers (11 Jun 2026):
    Model 3 RWD 5.8s/group 32 ~ x0.85 of the i4 baseline; LR AWD 4.2s/group
    40 ~ x1.10; Performance 2.9s & Seal AWD 3.8s/group 48 ~ x1.40.
    Returns None (= use the baseline untouched) when no 0-62 is published.
    Estimates only — get a real quote before deciding.
    """
    if not zero_to_62 or zero_to_62 <= 0:
        return None
    if zero_to_62 < 3.5:
        factor = 1.40
    elif zero_to_62 < 4.5:
        factor = 1.10
    elif zero_to_62 < 5.5:
        factor = 1.0
    else:
        factor = 0.85
    # int(x + 0.5): round half-up — Python's round() banker's-rounds 104.5 down
    return int(baseline * factor / 10 + 0.5) * 10.0


def quotes_to_deal_rows(results: list[dict], baseline_insurance: float,
                        fetched_on: str = "", max_rows: int = 12) -> list[dict]:
    """Map raw API items to market-deals table rows (app conventions).

    Money x1.2 to inc-VAT; Upfront = doc fee + (N-1) extra monthlies (the
    1+35-style convention used across the seed rows); Ins GBP/yr from the
    0-62 heuristic. Excess ppm isn't exposed by the API -> 10p default.

    The fetch date goes in Source, NOT Description: the app dedupes on
    (Description, Monthly), and a date inside the key would re-append every
    unchanged quote on a later-day refetch. A missing mileage maps to None
    (blank cell -> the app substitutes the user's own annual miles); 0 would
    bill every mile as phantom excess. One malformed vehicle is skipped, not
    allowed to abort the whole batch.
    """
    rows: list[dict] = []
    seen: set[tuple] = set()
    source = "LeaseLoco live" + (f" {fetched_on}" if fetched_on else "")
    for item in results:
        try:
            vp = item.get("vehiclePrice") or {}
            monthly_ex = float(vp.get("monthlyPayment") or 0)
            if monthly_ex <= 0:
                continue
            monthly = round(monthly_ex * VAT, 2)
            fee = round(float(vp.get("documentFee") or 0) * VAT, 2)
            initial_months = int(vp.get("initialPaymentInMonths") or 1)
            upfront = round(fee + (initial_months - 1) * monthly, 2)
            mileage = int(vp.get("mileage") or 0) or None
            term = int(vp.get("term") or 36)
            rng = vp.get("performanceElectricRange")
            try:
                s062 = float(vp.get("performance0to62") or 0) or None
            except (TypeError, ValueError):
                s062 = None
            name = " ".join(str(vp.get(k) or "").strip() for k in
                            ("manufacturerName", "rangeName", "trimName")).strip()
            perf_bits = [b for b in (f"{float(rng):.0f}mi" if rng else "",
                                     f"{s062}s 0-62" if s062 else "") if b]
            mk = f"{mileage // 1000}k" if mileage else "?k"
            basis = (f"LIVE {term}mo/{mk}/1+{term - 1}" if initial_months == 1
                     else f"LIVE {term}mo/{mk}/{initial_months}mo init")
            desc = (name + (f" ({', '.join(perf_bits)})" if perf_bits else "")
                    + f" — {basis} ({vp.get('stockStatusName', '?')}, £{fee:.0f} fee)")
            ins = notional_insurance(s062, baseline_insurance)
        except (TypeError, ValueError):
            continue  # one malformed vehicle must not kill the whole fetch
        key = (desc, monthly)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "Source": source,
            "Description": desc,
            "Monthly £": monthly,
            "Upfront £": upfront,
            "Miles/yr": mileage,
            "Term (mo)": term,
            "Excess p/mi": 10.0,
            "Ins £/yr": ins,
        })
        if len(rows) >= max_rows:
            break
    return rows
