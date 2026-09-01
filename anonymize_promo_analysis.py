#!/usr/bin/env python3
"""
Generate a portable, data-perturbed copy of the "Promo Consolidated
Analysis" Puppy Page for sharing without exposing exact proprietary
figures, while keeping the analysis internally consistent.

Strategy
--------
Every chart, table, KPI card, and "Recommendation Engine" verdict on this
page is computed AT RENDER TIME from ~16 embedded JS data blocks (arrays
of objects / a couple of nested objects). Rather than hand-editing
thousands of rendered numbers, we perturb the SOURCE data with row-level
random jitter (fixed seed => reproducible) while re-deriving every
tightly-coupled field (rates, totals, lifts, percentages) from the
perturbed base numbers. The page's own unmodified JS then recomputes
everything else automatically, so the story stays coherent.

A handful of fully-static narrative sentences (written as literal prose,
not interpolated from the data at render time) are patched separately
with proportionally-nudged figures so they don't contradict the new
numbers.

The original cached export is never modified; this reads it and writes a
brand new standalone file.
"""
import json
import math
import random
import re
from pathlib import Path

SRC = Path(
    "/Users/v0c003n/.code_puppy/puppy_share/"
    "p0s05rd__promo-consolidated-analysis__v4.html"
)
OUT_DIR = Path.home() / "promo-analysis-portable"
OUT = OUT_DIR / "promo-consolidated-analysis-portable.html"

# Bumped for a bigger, fresher cook-up further from the real figures.
SEED = 71828182


def jit(rng, lo=0.35, hi=2.8):
    """Log-uniform multiplicative jitter -- swings much further from the
    original value than a simple linear +/-15% would, while still staying
    positive and plausible-looking (geometric mean ~= 1)."""
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def num(v):
    if v is None or v == "":
        return 0.0
    return float(v)


def as_str_like(orig, value, decimals=None):
    """Format `value` to match whether the original field was a native
    JSON number (int/float) or a numeric-looking string."""
    if isinstance(orig, bool):
        return orig
    if isinstance(orig, int):
        return int(round(value))
    if isinstance(orig, float):
        return round(value, decimals if decimals is not None else 2)
    if decimals is None:
        decimals = 0 if "." not in str(orig) else 2
    if decimals == 0:
        return str(int(round(value)))
    return f"{value:.{decimals}f}"


# ---------------------------------------------------------------------------
# Generic extraction helpers (balanced-bracket scan, string-aware)
# ---------------------------------------------------------------------------


def find_balanced_end(text, start):
    open_ch = text[start]
    close_ch = {"{": "}", "[": "]"}[open_ch]
    depth = 0
    i = start
    in_str = False
    str_ch = ""
    escape = False
    while i < len(text):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == str_ch:
                in_str = False
        else:
            if c in ('"', "'"):
                in_str = True
                str_ch = c
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    raise ValueError(f"Unbalanced brackets starting at {start}")


def find_scalar_end(text, start):
    i = start
    while text[i] not in ";,\n":
        i += 1
    return i


def extract_literal(text, marker, unquoted_keys=False):
    idx = text.index(marker)
    start = idx + len(marker)
    end = find_balanced_end(text, start)
    raw = text[start:end]
    if unquoted_keys:
        raw = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', raw)
    obj = json.loads(raw)
    return obj, start, end


def replace_block(text, marker, transform, unquoted_keys=False):
    obj, start, end = extract_literal(text, marker, unquoted_keys)
    new_obj = transform(obj)
    new_text = json.dumps(new_obj, separators=(",", ":"))
    return text[:start] + new_text + text[end:]


def replace_scalar(text, marker, new_value):
    idx = text.index(marker)
    start = idx + len(marker)
    end = find_scalar_end(text, start)
    return text[:start] + json.dumps(new_value) + text[end:]


def get_scalar(text, name):
    marker = f"const {name} = "
    idx = text.index(marker)
    start = idx + len(marker)
    end = find_scalar_end(text, start)
    return json.loads(text[start:end])


# ---------------------------------------------------------------------------
# Field-shape aware perturbation functions
# ---------------------------------------------------------------------------


def perturb_issued_redeemed(row, rng, issued_k, redeemed_k, not_redeemed_k,
                             rate_k, total_amt_k, avg_amt_k,
                             redeemed_amt_k=None, unredeemed_amt_k=None):
    issued = num(row[issued_k])
    rate = num(row[rate_k])
    avg_amt = num(row[avg_amt_k])

    if issued <= 0:
        return dict(row)

    new_issued = max(1, round(issued * jit(rng)))
    new_rate = min(99.0, max(0.5, rate * jit(rng)))
    new_redeemed = round(new_issued * new_rate / 100)
    new_not_redeemed = new_issued - new_redeemed
    new_avg_amt = round(avg_amt * jit(rng), 2)
    new_total_amt = round(new_issued * new_avg_amt, 2)

    out = dict(row)
    out[issued_k] = as_str_like(row[issued_k], new_issued)
    out[redeemed_k] = as_str_like(row[redeemed_k], new_redeemed)
    out[not_redeemed_k] = as_str_like(row[not_redeemed_k], new_not_redeemed)
    out[rate_k] = as_str_like(row[rate_k], new_rate)
    out[total_amt_k] = as_str_like(row[total_amt_k], new_total_amt)
    out[avg_amt_k] = as_str_like(row[avg_amt_k], new_avg_amt)

    if redeemed_amt_k and redeemed_amt_k in row:
        new_redeemed_amt = round(new_redeemed * new_avg_amt, 2)
        out[redeemed_amt_k] = as_str_like(row[redeemed_amt_k], new_redeemed_amt)
        if unredeemed_amt_k and unredeemed_amt_k in row:
            new_unredeemed_amt = round(new_total_amt - new_redeemed_amt, 2)
            out[unredeemed_amt_k] = as_str_like(row[unredeemed_amt_k], new_unredeemed_amt)
    return out


def perturb_gmv_row(row, rng):
    """Handles GMV_DATA, GMV_CATEGORY, GMV_V3_DATA, GMV_V3_CATEGORY,
    WPLUS_GMV_DATA, and DR_DATA -- all variants of the same shape."""
    out = dict(row)

    new_cust = None
    if "unique_customers" in row:
        new_cust = max(1, round(num(row["unique_customers"]) * jit(rng)))
        out["unique_customers"] = as_str_like(row["unique_customers"], new_cust)

    pre_orders_k = next((k for k in row if k.startswith("avg_orders_pre")), None)
    post_orders_k = next((k for k in row if k.startswith("avg_orders_post")), None)
    if pre_orders_k:
        new_opre = round(num(row[pre_orders_k]) * jit(rng), 2)
        out[pre_orders_k] = as_str_like(row[pre_orders_k], new_opre)
        if "total_orders_pre" in row and new_cust is not None:
            out["total_orders_pre"] = as_str_like(row["total_orders_pre"], round(new_cust * new_opre))
    if post_orders_k:
        new_opost = round(num(row[post_orders_k]) * jit(rng), 2)
        out[post_orders_k] = as_str_like(row[post_orders_k], new_opost)
        if "total_orders_post" in row and new_cust is not None:
            out["total_orders_post"] = as_str_like(row["total_orders_post"], round(new_cust * new_opost))

    pre_k = next((k for k in row if k.startswith("avg_gmv_pre")), None)
    post_k = next((k for k in row if k.startswith("avg_gmv_post")), None)
    exp_k = next((k for k in row if k.startswith("avg_expected")), None)

    if pre_k and post_k:
        new_pre = round(num(row[pre_k]) * jit(rng), 2)
        new_post = round(num(row[post_k]) * jit(rng), 2)
        out[pre_k] = as_str_like(row[pre_k], new_pre)
        out[post_k] = as_str_like(row[post_k], new_post)

        if exp_k:
            new_exp = round(num(row[exp_k]) * jit(rng), 2)
            out[exp_k] = as_str_like(row[exp_k], new_exp)
            baseline = new_exp
        else:
            baseline = new_pre

        new_lift = round(new_post - baseline, 2)
        lift_k = "avg_gmv_lift" if "avg_gmv_lift" in row else ("avg_delta_gmv" if "avg_delta_gmv" in row else None)
        pct_k = "gmv_lift_pct" if "gmv_lift_pct" in row else ("delta_gmv_pct" if "delta_gmv_pct" in row else None)
        if lift_k:
            out[lift_k] = as_str_like(row[lift_k], new_lift)
        if pct_k:
            new_pct = round((new_lift / baseline) * 100, 2) if baseline else 0.0
            out[pct_k] = as_str_like(row[pct_k], new_pct)

        if new_cust is not None:
            total_pre_k = next((k for k in row if k.startswith("total_gmv_pre")), None)
            total_post_k = next((k for k in row if k.startswith("total_gmv_post")), None)
            total_exp_k = next((k for k in row if k.startswith("total_expected")), None)
            total_lift_k = "total_gmv_lift" if "total_gmv_lift" in row else ("total_delta_gmv" if "total_delta_gmv" in row else None)

            total_pre = round(new_cust * new_pre, 2)
            total_post = round(new_cust * new_post, 2)
            if total_pre_k:
                out[total_pre_k] = as_str_like(row[total_pre_k], total_pre)
            if total_post_k:
                out[total_post_k] = as_str_like(row[total_post_k], total_post)

            total_baseline = total_pre
            if total_exp_k:
                total_exp = round(new_cust * baseline, 2)
                out[total_exp_k] = as_str_like(row[total_exp_k], total_exp)
                total_baseline = total_exp
            if total_lift_k:
                out[total_lift_k] = as_str_like(row[total_lift_k], round(total_post - total_baseline, 2))

    for k in ("stddev_delta_gmv", "stderr_delta_gmv"):
        if k in row:
            out[k] = as_str_like(row[k], abs(round(num(row[k]) * jit(rng, 0.5, 2.0), 2)))

    return out


def perturb_aov_group(rows, rng):
    new_customers = [max(1, round(num(r["customers"]) * jit(rng))) for r in rows]
    total = sum(new_customers) or 1
    out = []
    for r, nc in zip(rows, new_customers):
        o = dict(r)
        o["customers"] = as_str_like(r["customers"], nc)
        if "pct_of_total" in r:
            o["pct_of_total"] = as_str_like(r["pct_of_total"], round(nc / total * 100, 2))
        if "avg_aov" in r:
            o["avg_aov"] = as_str_like(r["avg_aov"], round(num(r["avg_aov"]) * jit(rng), 2))
        if "avg_promo_value" in r:
            o["avg_promo_value"] = as_str_like(r["avg_promo_value"], round(num(r["avg_promo_value"]) * jit(rng), 2))
        if "avg_promo_pct_of_aov" in r:
            o["avg_promo_pct_of_aov"] = as_str_like(
                r["avg_promo_pct_of_aov"], round(num(r["avg_promo_pct_of_aov"]) * jit(rng), 2)
            )
        out.append(o)
    return out


def perturb_missing_order(row, rng):
    order_count = num(row["order_count"])
    if order_count <= 0:
        return dict(row)
    incident_count = num(row["incident_count"])
    ratio = incident_count / order_count if order_count else 1.0

    new_order_count = max(1, round(order_count * jit(rng)))
    new_incident_count = max(1, round(new_order_count * ratio * jit(rng, 0.7, 1.4)))
    new_avg_adj = round(num(row["avg_adj_amt"]) * jit(rng), 2)
    new_avg_order_val = round(num(row["avg_order_value"]) * jit(rng), 2)
    new_total_adj = round(new_order_count * new_avg_adj, 2)
    new_total_order = round(new_order_count * new_avg_order_val, 2)
    new_qty = round(num(row["avg_qty_adjusted"]) * jit(rng, 0.5, 2.0), 2)
    new_lines = round(num(row["avg_total_lines"]) * jit(rng, 0.5, 2.0), 2)
    new_survey = max(1, round(num(row["survey_responses"]) * jit(rng)))
    new_csat_pct = min(99.5, max(35.0, num(row["csat_pct"]) * jit(rng, 0.7, 1.4)))
    new_csat45 = round(new_survey * new_csat_pct / 100)
    promos_redeemed_raw = row.get("promos_redeemed", "0")
    promos_redeemed_val = num(promos_redeemed_raw) if promos_redeemed_raw else 0
    new_promos_redeemed = round(promos_redeemed_val * jit(rng)) if promos_redeemed_val else 0

    out = dict(row)
    out["order_count"] = as_str_like(row["order_count"], new_order_count)
    out["incident_count"] = as_str_like(row["incident_count"], new_incident_count)
    out["avg_adj_amt"] = as_str_like(row["avg_adj_amt"], new_avg_adj)
    out["avg_order_value"] = as_str_like(row["avg_order_value"], new_avg_order_val)
    out["total_adj_dollars"] = as_str_like(row["total_adj_dollars"], new_total_adj)
    out["total_order_dollars"] = as_str_like(row["total_order_dollars"], new_total_order)
    out["avg_qty_adjusted"] = as_str_like(row["avg_qty_adjusted"], new_qty)
    out["avg_total_lines"] = as_str_like(row["avg_total_lines"], new_lines)
    out["survey_responses"] = as_str_like(row["survey_responses"], new_survey)
    out["csat_4_5"] = as_str_like(row["csat_4_5"], new_csat45)
    out["csat_pct"] = as_str_like(row["csat_pct"], new_csat_pct)
    out["promos_redeemed"] = as_str_like(row["promos_redeemed"], new_promos_redeemed)
    return out


def perturb_denomination(row, rng):
    promo_amount = row["promo_amount"]  # fixed tier label, not perturbed
    issued = num(row["total_issued"])
    if issued <= 0:
        return dict(row)
    rate = num(row["redemption_rate_pct"])

    new_issued = max(1, round(issued * jit(rng)))
    new_rate = min(99.0, max(0.5, rate * jit(rng)))
    new_redeemed = round(new_issued * new_rate / 100)
    new_not_redeemed = new_issued - new_redeemed
    new_total_value = round(new_issued * promo_amount, 2)
    new_redeemed_value = round(new_redeemed * promo_amount, 2)
    new_unredeemed_value = round(new_total_value - new_redeemed_value, 2)

    out = dict(row)
    out["total_issued"] = int(new_issued)
    out["total_redeemed"] = int(new_redeemed)
    out["total_not_redeemed"] = int(new_not_redeemed)
    out["redemption_rate_pct"] = round(new_rate, 2)
    out["total_promo_value"] = round(new_total_value, 2)
    out["redeemed_promo_value"] = round(new_redeemed_value, 2)
    out["unredeemed_promo_value"] = round(new_unredeemed_value, 2)
    out["pct_value_redeemed"] = round(new_rate, 2)
    return out


def perturb_v3_promo_amt(row, rng):
    out = dict(row)
    if "total_promos_issued" in row:
        out["total_promos_issued"] = as_str_like(
            row["total_promos_issued"], round(num(row["total_promos_issued"]) * jit(rng))
        )
    if "total_promo_amount" in row:
        out["total_promo_amount"] = as_str_like(
            row["total_promo_amount"], round(num(row["total_promo_amount"]) * jit(rng), 2)
        )
    if "avg_promo_amount" in row:
        out["avg_promo_amount"] = as_str_like(
            row["avg_promo_amount"], round(num(row["avg_promo_amount"]) * jit(rng), 2)
        )
    return out


def perturb_wplus_issuance(row, rng):
    out = dict(row)
    out["total_issued"] = round(num(row["total_issued"]) * jit(rng))
    return out


def perturb_N(obj, rng):
    return {k: max(1, round(v * jit(rng))) for k, v in obj.items()}


def perturb_W(obj, rng):
    out = {}
    for cohort, d in obj.items():
        jp = jit(rng)
        jo = jit(rng)
        out[cohort] = {
            "pre": [round(v * jp, 2) for v in d["pre"]],
            "post": [round(v * jo, 2) for v in d["post"]],
        }
    return out


def perturb_scalar_totals(text, rng):
    total_issued = get_scalar(text, "TOTAL_ISSUED_COUNT")
    rate = get_scalar(text, "REDEMPTION_RATE")
    total_value = get_scalar(text, "TOTAL_PROMO_VALUE")
    missing_dollars = get_scalar(text, "MISSING_PROMO_DOLLARS")

    new_issued = max(1, round(total_issued * jit(rng)))
    new_rate = min(99.0, max(0.5, rate * jit(rng)))
    new_redeemed = round(new_issued * new_rate / 100)
    new_total_value = round(total_value * jit(rng), 1)
    new_redeemed_value = round(new_total_value * (new_redeemed / new_issued), 1)
    new_unredeemed_value = round(new_total_value - new_redeemed_value, 1)
    new_missing_dollars = round(missing_dollars * jit(rng))

    for name, val in [
        ("TOTAL_PROMO_VALUE", new_total_value),
        ("TOTAL_REDEEMED_VALUE", new_redeemed_value),
        ("TOTAL_UNREDEEMED_VALUE", new_unredeemed_value),
        ("TOTAL_ISSUED_COUNT", new_issued),
        ("TOTAL_REDEEMED_COUNT", new_redeemed),
        ("REDEMPTION_RATE", round(new_rate, 1)),
        ("MISSING_PROMO_DOLLARS", new_missing_dollars),
    ]:
        text = replace_scalar(text, f"const {name} = ", val)
    return text


# Fully-static narrative sentences (not computed at render time) that
# hardcode Walmart-specific figures. Generic industry-research thresholds
# ("10-15% of order value", "~20%") are left untouched -- they're not
# derived from this dataset.
NARRATIVE_FIXES = [
    ("A $25 promo on a $117 order is a 21% discount", "A $25 promo on a $95 order is a 26% discount"),
    ("At $10\\u2013$15 (8\\u201313% of AOV)", "At $10\\u2013$15 (11\\u201316% of AOV)"),
    ("This range delivers 23\\u201325pp of lift vs no promo.", "This range delivers 13\\u201315pp of lift vs no promo."),
    ("based on the 1.8pp advantage $15 holds over $25", "based on the 1.1pp advantage $15 holds over $25"),
    ("Control group (13.7M) dwarfs promo groups (170K\\u2013995K)", "Control group (21.9M) dwarfs promo groups (95K\\u20131.6M)"),
    ("single-digit declines (7\\u201312%)", "single-digit declines (4\\u20138%)"),
    ("much steeper declines (26\\u201337%)", "much steeper declines (41\\u201358%)"),
    ("as $25 (only ~4pp difference vs ~29pp for Non-W+)", "as $25 (only ~2pp difference vs ~46pp for Non-W+)"),
]


def main():
    text = SRC.read_text()
    rng = random.Random(SEED)

    text = replace_block(
        text,
        "const RAW_DATA = ",
        lambda rows: [
            perturb_issued_redeemed(
                r, rng, "total_issued", "total_redeemed", "total_not_redeemed",
                "redemption_rate_pct", "total_promo_amount", "avg_promo_amount",
                "redeemed_promo_amount", "unredeemed_promo_amount",
            )
            for r in rows
        ],
    )

    for name in ("GMV_DATA", "GMV_CATEGORY", "GMV_V3_DATA", "GMV_V3_CATEGORY", "DR_DATA"):
        text = replace_block(text, f"const {name} = ", lambda rows: [perturb_gmv_row(r, rng) for r in rows])

    text = replace_block(text, "const GMV_V3_PROMO_AMT = ", lambda rows: [perturb_v3_promo_amt(r, rng) for r in rows])
    text = replace_block(text, "const AOV_BY_L1 = ", lambda rows: perturb_aov_group(rows, rng))
    text = replace_block(text, "const AOV_BY_BUCKET = ", lambda rows: perturb_aov_group(rows, rng))
    text = replace_block(text, "const MISSING_ORDER_DATA = ", lambda rows: [perturb_missing_order(r, rng) for r in rows])
    text = replace_block(text, "const DENOMINATION_DATA = ", lambda rows: [perturb_denomination(r, rng) for r in rows])

    text = replace_block(text, "var WPLUS_GMV_DATA = ", lambda rows: [perturb_gmv_row(r, rng) for r in rows], unquoted_keys=True)
    text = replace_block(text, "var WPLUS_ISSUANCE = ", lambda rows: [perturb_wplus_issuance(r, rng) for r in rows], unquoted_keys=True)
    text = replace_block(text, "var N = ", lambda obj: perturb_N(obj, rng), unquoted_keys=True)
    text = replace_block(text, "var W = ", lambda obj: perturb_W(obj, rng), unquoted_keys=True)

    text = perturb_scalar_totals(text, rng)

    for old, new in NARRATIVE_FIXES:
        if old not in text:
            print(f"WARNING: narrative snippet not found (skipped): {old!r}")
        else:
            text = text.replace(old, new)

    footnote = (
        '<footer style="text-align:center;padding:16px;'
        'font-size:12px;color:#6b7280;border-top:1px solid #e5e7eb;'
        'margin-top:24px">For demonstration only &mdash; created by Victor Chowdhury</footer>\n'
    )
    text = text.replace("</body>", footnote + "</body>")

    OUT_DIR.mkdir(exist_ok=True)
    OUT.write_text(text)
    print(f"Wrote {OUT} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
