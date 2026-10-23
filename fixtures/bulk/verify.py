#!/usr/bin/env python3
"""Re-read the golden CSVs with the csv module, clean the mess independently,
and check every count and answer against golden-data-ground-truth.json."""
import csv
import datetime as dt
import json
import os
import re

OUT = os.path.dirname(os.path.abspath(__file__))
NULLS = {"", "-", "n/a", "null", "tbd"}
MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(name)
    print(f"{'OK  ' if ok else 'FAIL'} {name}: got={got!r} want={want!r}"[:200])


def read(name):
    with open(os.path.join(OUT, name), newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def norm(s):
    s = re.sub(r"\s+", "", s.lower()).replace("and", "&")
    return "cancelled" if s == "canceled" else s


def cents(s):
    s = s.strip().lstrip("'")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.startswith("-"):
        neg, s = True, s[1:]
    s = s.lstrip("$")
    if re.fullmatch(r"\d{1,3}(\.\d{3})*,\d{2}", s):      # European
        s = s.replace(".", "").replace(",", ".")
    s = s.replace(",", "")
    whole, frac = s.split(".")
    v = int(whole) * 100 + int(frac)
    return -v if neg else v


def pdate(s):
    s = s.strip()
    if s.lower() in NULLS:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return dt.date.fromisoformat(s)
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", s):
        d, m, y = map(int, s.split("/"))
        return dt.date(y, m, d)
    if re.fullmatch(r"\d{5}", s):
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(s))
    d, m, y = s.split()
    return dt.date(int(y), MON[m], int(d))


truth = json.load(open(os.path.join(OUT, "golden-data-ground-truth.json")))

# ---- messy sales -----------------------------------------------------------
T = truth["messy-sales-200k.csv"]
L, A = T["layout"], T["answers"]
rows = read("messy-sales-200k.csv")
check("sales records", len(rows), L["records"])
check("sales all rows 20 wide", {len(r) for r in rows}, {20})
check("sales header row 5 col A", rows[4][0], "Order ID")
check("sales duplicate Date header", [i for i, h in enumerate(rows[4]) if h == "Date"], [1, 17])
data = [(i + 1, r) for i, r in enumerate(rows) if re.fullmatch(r"SO-\d+", r[0])]
check("sales data rows", len(data), A["data_rows"])
check("sales first data row", data[0][0], L["first_data_row"])
check("sales subtotal rows", [i + 1 for i, r in enumerate(rows) if r[0].startswith("Subtotal")],
      L["subtotal_rows"])
check("sales spacer rows", [i + 1 for i, r in enumerate(rows)
                            if i > 5 and not any(r) and i + 1 < L["grand_total_row"]], L["spacer_rows"])
check("sales grand total row", [i + 1 for i, r in enumerate(rows) if r[0] == "GRAND TOTAL"],
      [L["grand_total_row"]])
check("sales multiline rows", [n for n, r in data if "\n" in r[19]], L["multiline_rows"])
wt_text = [n for n, r in data if not re.fullmatch(r"\d+\.\d{2}", r[18])]
check("sales weight text rows", wt_text, L["weight_text_rows"])
check("sales weight text all after row 150000", min(wt_text) > 150000, True)

rev_total = 0
by_region, by_status, by_cat, months = {}, {}, {}, {}
qty_total, blank_dates, max_rev = 0, 0, None
for _n, r in data:
    v = cents(r[14])
    rev_total += v
    max_rev = v if max_rev is None else max(max_rev, v)
    by_region[norm(r[3])] = by_region.get(norm(r[3]), 0) + v
    by_status[norm(r[16])] = by_status.get(norm(r[16]), 0) + 1
    by_cat[norm(r[8])] = by_cat.get(norm(r[8]), 0) + v
    qty_total += int(float(r[11].strip().lstrip("'")))
    d = pdate(r[1])
    if d is None:
        blank_dates += 1
    else:
        months[d.strftime("%Y-%m")] = months.get(d.strftime("%Y-%m"), 0) + 1
check("revenue total", rev_total, A["revenue_total_cents"])
check("revenue by region", by_region, {norm(k): v for k, v in A["revenue_by_region_cents"].items()})
check("rows by status", by_status, {norm(k): v for k, v in A["rows_by_status"].items()})
check("revenue by category", by_cat, {norm(k): v for k, v in A["revenue_by_category_cents"].items()})
check("distinct regions", len(by_region), A["distinct_regions_normalised"])
check("distinct categories", len(by_cat), A["distinct_categories_normalised"])
check("distinct skus", len({r[10] for _, r in data}), A["distinct_skus"])
check("quantity total", qty_total, A["quantity_total"])
check("max order revenue", max_rev, A["max_single_order_revenue_cents"])
check("orders by month", dict(sorted(months.items())), A["orders_by_month_with_date"])
check("blank order dates", blank_dates, A["order_date_blank_rows"])
sub = {norm(r[3]): cents(r[14]) for r in rows if r[0].startswith("Subtotal")}
check("subtotals match region truth", sub, by_region)
check("grand total matches", cents(rows[L["grand_total_row"] - 1][14]), rev_total)
cells = len(rows) * 20
size = os.path.getsize(os.path.join(OUT, "messy-sales-200k.csv"))
check("cells < 5M", cells < 5_000_000, True)
check("size < 60MB", size < 60 * 1024 * 1024, True)

# ---- side by side ------------------------------------------------------------
T = truth["side-by-side-small.csv"]
A = T["answers"]
rows = read("side-by-side-small.csv")
check("sbs records", len(rows), T["layout"]["records"])
check("sbs header row 6", (rows[5][0], rows[5][8]), ("Item ID", "Return ID"))
check("sbs separator cols G-H blank", any(r[6] or r[7] for r in rows), False)
ta = [r[:6] for r in rows[6:] if r[0]]
tb = [r[8:] for r in rows[6:] if r[8]]
check("sbs table A rows", len(ta), A["table_a_inventory_rows"])
check("sbs on hand", sum(int(r[3]) for r in ta), A["table_a_on_hand_total"])
check("sbs below reorder", sum(1 for r in ta if int(r[3]) < int(r[4])), A["table_a_rows_below_reorder_point"])
wh = {}
for r in ta:
    wh[r[2]] = wh.get(r[2], 0) + 1
check("sbs by warehouse", wh, A["table_a_rows_by_warehouse"])
check("sbs table B rows", len(tb), A["table_b_returns_rows"])
check("sbs refund", sum(int(r[4]) for r in tb), A["table_b_refund_total_cents"])
rs = {}
for r in tb:
    rs[r[3]] = rs.get(r[3], 0) + 1
check("sbs by reason", rs, A["table_b_rows_by_reason"])

# ---- clean -------------------------------------------------------------------
T = truth["clean-wide-50k.csv"]
A = T["answers"]
rows = read("clean-wide-50k.csv")
check("clean records", len(rows), T["layout"]["records"])
check("clean width", {len(r) for r in rows}, {12})
d = rows[1:]
check("clean data rows", len(d), A["data_rows"])
check("clean revenue", sum(cents(r[8]) for r in d), A["revenue_total_cents"])
check("clean margin", sum(cents(r[10]) for r in d), A["margin_total_cents"])
check("clean qty", sum(int(r[5]) for r in d), A["quantity_total"])
reg = {}
st = {}
for r in d:
    reg[r[2]] = reg.get(r[2], 0) + cents(r[8])
    st[r[11]] = st.get(r[11], 0) + 1
check("clean revenue by region", reg, A["revenue_by_region_cents"])
check("clean by status", st, A["rows_by_status"])

print("\nFAILURES:", fails if fails else "none")
raise SystemExit(1 if fails else 0)
