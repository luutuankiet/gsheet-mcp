#!/usr/bin/env python3
"""Golden test data generator for gsheet-mcp.

Standard library only, fixed seed: reruns are byte-identical.
Writes, next to this script:
  messy-sales-200k.csv, side-by-side-small.csv, clean-wide-50k.csv,
  golden-data-ground-truth.json, golden-data-manifest.md

Row numbers everywhere are 1-based sheet rows (CSV record index + 1); a cell
with embedded newlines still occupies one sheet row.
Ground truth is computed from the clean values BEFORE any formatting mess.
"""
import csv
import datetime as dt
import json
import os
import random

SEED = 20261023
OUT = os.path.dirname(os.path.abspath(__file__))
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EXCEL_EPOCH = dt.date(1899, 12, 30)


def money(cents):
    """Exact cents -> '1234.50' string."""
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100}.{c % 100:02d}"


def thousands(cents, sep=",", dec="."):
    c = abs(cents)
    whole = f"{c // 100:,}".replace(",", sep)
    return f"{whole}{dec}{c % 100:02d}"


# ---------------------------------------------------------------------------
# 1. messy-sales-200k.csv
# ---------------------------------------------------------------------------
REGIONS = ["North", "South", "East", "West", "Central"]
REGION_WEIGHTS = [24, 18, 22, 21, 15]
REGION_DRIFT = {
    "North": ["north", "NORTH ", "Nort h", " North"],
    "South": ["south", "SOUTH ", "Sout h"],
    "East": ["east", "EAST ", "Eas t"],
    "West": ["west", "WEST ", "Wes t"],
    "Central": ["central", "CENTRAL ", "Cent ral"],
}
COUNTRIES = {
    "North": ["Norway", "Sweden", "Finland"],
    "South": ["Spain", "Italy", "Portugal"],
    "East": ["Poland", "Romania", "Hungary"],
    "West": ["Ireland", "France", "Belgium"],
    "Central": ["Germany", "Austria", "Switzerland"],
}
FIRST = ["Anna", "Ben", "Chloe", "David", "Elena", "Farid", "Grace", "Hugo",
         "Ines", "Jonas", "Kira", "Liam", "Maya", "Nils", "Olga", "Pedro"]
LAST = ["Berg", "Costa", "Dubois", "Eriksen", "Fischer", "Garcia", "Horvat",
        "Ivanov", "Jensen", "Kowalski", "Lind", "Moreau", "Novak", "Rossi"]
CATEGORIES = {
    "Electronics": [("Wireless Mouse", 2499), ("USB-C Hub", 4999),
                    ("Noise Cancelling Headphones", 19999), ("4K Monitor", 34999),
                    ("Mechanical Keyboard", 8999), ("Webcam HD", 5999)],
    "Home & Kitchen": [("Chef Knife", 3999), ("Espresso Machine", 24999),
                       ("Cast Iron Pan", 4499), ("Blender", 7999),
                       ("Kettle", 2999), ("Cutlery Set", 5499)],
    "Office Supplies": [("Stapler", 899), ("Printer Paper 500", 599),
                        ("Desk Lamp", 2999), ("Gel Pens 12pk", 1199),
                        ("Whiteboard", 6999), ("Ergonomic Chair", 18999)],
    "Apparel": [("Rain Jacket", 8999), ("Wool Socks", 1499), ("Running Shoes", 11999),
                ("Denim Jeans", 6999), ("Baseball Cap", 1999), ("Hoodie", 4999)],
    "Toys": [("Puzzle 1000pc", 1999), ("Building Blocks", 4999), ("Board Game", 3499),
             ("RC Car", 5999), ("Plush Bear", 1599), ("Kite", 2499)],
}
CATEGORY_DRIFT = {
    "Electronics": ["electronics", "ELECTRONICS", "Electronic s", "Electronics "],
    "Home & Kitchen": ["Home and Kitchen", "home & kitchen", "Home &Kitchen"],
    "Office Supplies": ["office supplies", "OFFICE SUPPLIES", "Office  Supplies"],
    "Apparel": ["apparel", "APPAREL", "Appare l"],
    "Toys": ["toys", "TOYS", "Toy s"],
}
STATUSES = ["Delivered", "Shipped", "Pending", "Cancelled", "Returned"]
STATUS_WEIGHTS = [55, 20, 10, 8, 7]
STATUS_DRIFT = {
    "Delivered": ["delivered", "DELIVERED", "Delivered "],
    "Shipped": ["shipped", "SHIPPED "],
    "Pending": ["pending", " Pending"],
    "Cancelled": ["Canceled", "cancelled", "CANCELLED"],
    "Returned": ["returned", "RETURNED"],
}
CURRENCY_VARIANTS = ["USD"] * 90 + ["usd"] * 4 + ["US$"] * 3 + ["$"] * 2 + ["USD "] * 1
NULLS = ["", "-", "N/A", "n/a", "null", "TBD"]
CHANNELS = ["Online", "Retail", "Wholesale"]
SHORT_NOTES = ["Gift wrap", "Invoice requested", "Repeat customer", "Promo Q3",
               "Partial delivery", "Address corrected", "Priority"]
MULTILINE_NOTES = [
    'Customer called, said "rush, please"\nLeft voicemail, will retry Monday',
    'Box damaged in transit\nReplacement sent, ref "RMA-77, B"',
    'Line 1: deliver to back door\nLine 2: ring twice, "no dogs"\nLine 3: thanks',
    'Discount approved by "Regional Mgr", see email\nApplied manually, 3 units',
]
WEIGHT_TEXT = ["approx 2.5", "2.5 kg", "see notes", "TBC", "heavy", "~1.2kg"]

N_SALES = 200_000
SPACER_EVERY = 4_000
WEIGHT_TEXT_AFTER = 150_000   # data-row index after which text may appear
N_WEIGHT_TEXT = 200


def fmt_date(rng, d, mess, row, prefix):
    """Return a messy rendering of date d and log the format used."""
    r = rng.random()
    if r < 0.60:
        s, k = d.isoformat(), "iso"
    elif r < 0.75:
        s, k = f"{d.day:02d}/{d.month:02d}/{d.year}", "dmy_slash"
    elif r < 0.87:
        s, k = f"{d.day} {MONTHS[d.month - 1]} {d.year}", "d_mon_yyyy"
    else:
        s, k = str((d - EXCEL_EPOCH).days), "excel_serial"
    mess[f"{prefix}_{k}"].append(row)
    return s


def build_sales(rng):
    # --- clean records -----------------------------------------------------
    reps = {}
    for reg in REGIONS:
        reps[reg] = [f"{rng.choice(FIRST)} {rng.choice(LAST)}" for _ in range(12)]
    stores = {reg: [f"Store {reg[0]}-{n:03d}" for n in rng.sample(range(1, 400), 25)]
              for reg in REGIONS}
    q3_start = dt.date(2026, 7, 1)
    clean = []
    for i in range(N_SALES):
        reg = rng.choices(REGIONS, REGION_WEIGHTS)[0]
        cat = rng.choice(sorted(CATEGORIES))
        prod, price = rng.choice(CATEGORIES[cat])
        sku = f"SKU-{cat[:3].upper()}-{CATEGORIES[cat].index((prod, price)) + 1:03d}"
        price_cents = price + rng.choice([0, 0, 0, 100, -100, 50])
        qty = min(50, int(rng.expovariate(1 / 6)) + 1)
        disc = rng.choice([0, 0, 0, 5, 10, 12, 15, 20, 25])
        status = rng.choices(STATUSES, STATUS_WEIGHTS)[0]
        gross = qty * price_cents * (100 - disc)
        rev = (gross + 50) // 100           # exact half-up rounding to cents
        if status == "Cancelled":
            rev = 0
        elif status == "Returned":
            rev = -rev
        od = q3_start + dt.timedelta(days=rng.randrange(92))
        sd = od + dt.timedelta(days=rng.randrange(1, 11))
        clean.append({
            "seq": i, "id": f"SO-{1000001 + i}", "date": od, "channel": rng.choice(CHANNELS),
            "region": reg, "country": rng.choice(COUNTRIES[reg]),
            "store": rng.choice(stores[reg]), "rep": rng.choice(reps[reg]),
            "cust": f"C-{rng.randrange(10000, 99999)}", "cat": cat, "prod": prod,
            "sku": sku, "qty": qty, "price": price_cents, "disc": disc, "rev": rev,
            "status": status, "ship": sd if status in ("Delivered", "Shipped", "Returned") else None,
            "weight": round(rng.uniform(0.1, 25.0), 2),
        })
    # region blocks (stable: ids ascend inside a block)
    order = {r: k for k, r in enumerate(REGIONS)}
    clean.sort(key=lambda c: (order[c["region"]], c["seq"]))

    # --- ground truth from clean values -------------------------------------
    gt = {"data_rows": N_SALES}
    gt["revenue_total_cents"] = sum(c["rev"] for c in clean)
    gt["revenue_by_region_cents"] = {r: sum(c["rev"] for c in clean if c["region"] == r)
                                     for r in REGIONS}
    gt["rows_by_region"] = {r: sum(1 for c in clean if c["region"] == r) for r in REGIONS}
    gt["rows_by_status"] = {s: sum(1 for c in clean if c["status"] == s) for s in STATUSES}
    gt["revenue_by_category_cents"] = {k: sum(c["rev"] for c in clean if c["cat"] == k)
                                       for k in sorted(CATEGORIES)}
    gt["quantity_total"] = sum(c["qty"] for c in clean)
    gt["distinct_regions_normalised"] = len({c["region"] for c in clean})
    gt["distinct_categories_normalised"] = len({c["cat"] for c in clean})
    gt["distinct_skus"] = len({c["sku"] for c in clean})
    gt["max_single_order_revenue_cents"] = max(c["rev"] for c in clean)

    # --- messify -------------------------------------------------------------
    from collections import defaultdict
    mess = defaultdict(list)
    rows = []

    def add(rec, kind=None):
        rows.append(rec)
        if kind:
            mess[kind].append(len(rows))
        return len(rows)

    width = 20
    add(["Q3 sales export"] + [""] * (width - 1), "banner_title")
    add(["Generated by ERP Reports v4.2 (scheduled job 'sales_q3'), all amounts USD"]
        + [""] * (width - 1), "banner_generated_by")
    add([""] * width, "banner_blank")
    group = ["Order", "", "", "Location", "", "", "People", "", "Product", "", "",
             "Money", "", "", "", "", "Fulfilment", "", "", ""]
    header = ["Order ID", "Date", "Channel", "Region", "Country", "Store", "Sales Rep",
              "", "Category", "Product", "SKU", "Qty", "Unit Price", "Discount",
              "Revenue", "Currency", "Status", "Date", "Weight (kg)", "Notes"]
    assert len(group) == len(header) == width
    add(group, "header_group_row")
    add(header, "header_column_row")

    month_counts = defaultdict(int)
    blank_dates = 0
    weight_text_idx = set(rng.sample(range(WEIGHT_TEXT_AFTER, N_SALES), N_WEIGHT_TEXT))
    region_sub = defaultdict(int)
    region_qty = defaultdict(int)

    for n, c in enumerate(clean):
        row = len(rows) + 1
        # order date: 1% blank, rest mixed formats
        if rng.random() < 0.01:
            od = ""
            blank_dates += 1
            mess["order_date_blank"].append(row)
        else:
            od = fmt_date(rng, c["date"], mess, row, "order_date")
            month_counts[c["date"].strftime("%Y-%m")] += 1
        # region drift ~3%
        reg = c["region"]
        if rng.random() < 0.03:
            reg = rng.choice(REGION_DRIFT[reg])
            mess["region_drift"].append(row)
        cat = c["cat"]
        if rng.random() < 0.03:
            cat = rng.choice(CATEGORY_DRIFT[cat])
            mess["category_drift"].append(row)
        status = c["status"]
        if rng.random() < 0.04:
            status = rng.choice(STATUS_DRIFT[status])
            mess["status_drift"].append(row)
        rep = c["rep"]
        if rng.random() < 0.02:
            rep = rng.choice(NULLS)
            mess["sales_rep_null"].append(row)
        cust = c["cust"]
        if rng.random() < 0.015:
            cust = rng.choice(NULLS)
            mess["customer_null"].append(row)
        channel = c["channel"]
        if rng.random() < 0.01:
            channel = rng.choice(NULLS)
            mess["channel_null"].append(row)
        # quantity
        r = rng.random()
        q = str(c["qty"])
        if r < 0.04:
            q = f" {q} "
            mess["qty_padded_spaces"].append(row)
        elif r < 0.06:
            q = "'" + q
            mess["qty_leading_apostrophe"].append(row)
        elif r < 0.08:
            q = q + ".0"
            mess["qty_trailing_dot_zero"].append(row)
        # unit price
        r = rng.random()
        p = c["price"]
        if r < 0.08:
            up = "$" + thousands(p)
            mess["price_dollar_thousands"].append(row)
        elif r < 0.12:
            up = thousands(p, ".", ",")
            mess["price_european"].append(row)
        elif r < 0.15:
            up = f" {money(p)} "
            mess["price_padded_spaces"].append(row)
        else:
            up = money(p)
        # discount
        r = rng.random()
        if r < 0.40:
            dsc = f"{c['disc']}%"
            mess["discount_percent_sign"].append(row)
        elif r < 0.45:
            dsc = f" {c['disc']}% "
            mess["discount_percent_padded"].append(row)
        else:
            dsc = f"{c['disc'] / 100:.2f}"
        # revenue
        rv = c["rev"]
        r = rng.random()
        if rv < 0:
            if r < 0.40:
                rev = f"({money(-rv)})"
                mess["revenue_paren_negative"].append(row)
            elif r < 0.55:
                rev = "-$" + thousands(rv)
                mess["revenue_dollar_thousands"].append(row)
            elif r < 0.65:
                rev = "-" + thousands(rv, ".", ",")
                mess["revenue_european"].append(row)
            else:
                rev = money(rv)
        else:
            if r < 0.09:
                rev = "$" + thousands(rv)
                mess["revenue_dollar_thousands"].append(row)
            elif r < 0.14:
                rev = thousands(rv, ".", ",")
                mess["revenue_european"].append(row)
            elif r < 0.20:
                rev = f" {money(rv)} "
                mess["revenue_padded_spaces"].append(row)
            elif r < 0.25:
                rev = "'" + money(rv)
                mess["revenue_leading_apostrophe"].append(row)
            else:
                rev = money(rv)
        cur = rng.choice(CURRENCY_VARIANTS)
        if cur != "USD":
            mess["currency_spelling_drift"].append(row)
        # ship date
        if c["ship"] is None:
            sd = rng.choice(NULLS)
            mess["ship_date_null_unshipped"].append(row)
        else:
            sd = fmt_date(rng, c["ship"], mess, row, "ship_date")
        # weight: numeric, text only after data row 150,000
        if n in weight_text_idx:
            wt = rng.choice(WEIGHT_TEXT)
            mess["weight_text_after_150k"].append(row)
        else:
            wt = f"{c['weight']:.2f}"
        # notes
        r = rng.random()
        if r < 0.001:
            note = rng.choice(MULTILINE_NOTES)
            mess["notes_multiline_quoted"].append(row)
        elif r < 0.05:
            note = rng.choice(SHORT_NOTES)
        elif r < 0.10:
            note = rng.choice(NULLS[1:])
            mess["notes_null_spelling"].append(row)
        else:
            note = ""
        add([c["id"], od, channel, reg, c["country"], c["store"], rep, cust, cat,
             c["prod"], c["sku"], q, up, dsc, rev, cur, status, sd, wt, note])
        region_sub[c["region"]] += c["rev"]
        region_qty[c["region"]] += c["qty"]

        if (n + 1) % SPACER_EVERY == 0 and n + 1 < N_SALES:
            add([""] * width, "blank_spacer_row")
        last_in_block = n + 1 == N_SALES or clean[n + 1]["region"] != c["region"]
        if last_in_block:
            sub = [""] * width
            sub[0] = f"Subtotal {c['region']}"
            sub[3] = c["region"]
            sub[11] = str(region_qty[c["region"]])
            sub[14] = "$" + thousands(region_sub[c["region"]])
            add(sub, "subtotal_row")

    gt["orders_by_month_with_date"] = dict(sorted(month_counts.items()))
    gt["order_date_blank_rows"] = blank_dates
    gt["weight_text_rows"] = N_WEIGHT_TEXT

    tot = [""] * width
    tot[0] = "GRAND TOTAL"
    tot[11] = str(gt["quantity_total"])
    tot[14] = "$" + thousands(gt["revenue_total_cents"])
    add(tot, "grand_total_row")
    add([""] * width, "footnote_blank_separator")
    for line in ["* Revenue is net of discount; returns are shown as negative amounts.",
                 "* Subtotal rows are per region; do not sum them with order rows.",
                 "Source: ERP Reports v4.2. Confidential, internal use only."]:
        add([line] + [""] * (width - 1), "footnote_line")
    return rows, dict(mess), gt


# ---------------------------------------------------------------------------
# 2. side-by-side-small.csv
# ---------------------------------------------------------------------------
def build_side_by_side(rng):
    WAREHOUSES = ["AMS-1", "BER-2", "MAD-3", "WAW-4"]
    REASONS = ["Damaged", "Wrong item", "Changed mind", "Late delivery", "Defective"]
    n_a, n_b = 2000, 1200
    a_rows, b_rows = [], []
    for i in range(n_a):
        a_rows.append([f"INV-{i + 1:05d}", f"SKU-{rng.randrange(1, 600):04d}",
                       rng.choice(WAREHOUSES), rng.randrange(0, 900),
                       rng.randrange(10, 120), rng.randrange(150, 25000)])
    for i in range(n_b):
        d = dt.date(2026, 7, 1) + dt.timedelta(days=rng.randrange(92))
        b_rows.append([f"RT-{i + 1:05d}", f"SO-{rng.randrange(1000001, 1200001)}",
                       d.isoformat(), rng.choice(REASONS), rng.randrange(500, 60000)])
    gt = {
        "table_a_inventory_rows": n_a,
        "table_a_on_hand_total": sum(r[3] for r in a_rows),
        "table_a_rows_by_warehouse": {w: sum(1 for r in a_rows if r[2] == w) for w in WAREHOUSES},
        "table_a_rows_below_reorder_point": sum(1 for r in a_rows if r[3] < r[4]),
        "table_b_returns_rows": n_b,
        "table_b_refund_total_cents": sum(r[4] for r in b_rows),
        "table_b_rows_by_reason": {k: sum(1 for r in b_rows if r[3] == k) for k in REASONS},
    }
    # layout: A-F table A, G-H blank, I-M table B
    W = 13
    rows = []
    kv = [("Owner:", "Jane Doe (Ops Analytics)"), ("Last refreshed:", "2026-10-20 08:15 UTC"),
          ("Source:", "WMS nightly extract")]
    for k, v in kv:
        rows.append([k, v] + [""] * (W - 2))
    rows.append([""] * W)
    rows.append(["Inventory", "", "", "", "", "", "", "", "Returns", "", "", "", ""])
    rows.append(["Item ID", "SKU", "Warehouse", "On Hand", "Reorder Point", "Unit Cost (cents)",
                 "", "", "Return ID", "Order ID", "Return Date", "Reason", "Refund (cents)"])
    for i in range(max(n_a, n_b)):
        a = [str(x) for x in a_rows[i]] if i < n_a else [""] * 6
        b = [str(x) for x in b_rows[i]] if i < n_b else [""] * 5
        rows.append(a + ["", ""] + b)
    layout = {
        "key_value_block_rows": "1-3 (key in column A, value in column B)",
        "blank_row": 4,
        "table_title_row": 5,
        "header_row": 6,
        "table_a": {"title": "Inventory", "columns": "A-F", "header_row": 6,
                    "data_rows": f"7-{6 + n_a}"},
        "blank_separator_columns": "G-H",
        "table_b": {"title": "Returns", "columns": "I-M", "header_row": 6,
                    "data_rows": f"7-{6 + n_b}"},
    }
    return rows, gt, layout


# ---------------------------------------------------------------------------
# 3. clean-wide-50k.csv
# ---------------------------------------------------------------------------
def build_clean(rng):
    n = 50_000
    header = ["order_id", "order_date", "region", "category", "product_id", "quantity",
              "unit_price", "discount_pct", "revenue", "cost", "margin", "status"]
    regions = ["North", "South", "East", "West", "Central"]
    cats = sorted(CATEGORIES)
    statuses = ["Delivered", "Shipped", "Pending", "Cancelled"]
    rows = [header]
    rev_by_region = {r: 0 for r in regions}
    by_status = {s: 0 for s in statuses}
    total_rev = total_margin = total_qty = 0
    for i in range(n):
        reg = rng.choice(regions)
        cat = rng.choice(cats)
        qty = rng.randrange(1, 40)
        price = rng.randrange(199, 50000)
        disc = rng.choice([0, 5, 10, 15, 20])
        rev = (qty * price * (100 - disc) + 50) // 100
        cost = (rev * rng.randrange(40, 80) + 50) // 100
        margin = rev - cost
        st = rng.choices(statuses, [60, 20, 12, 8])[0]
        d = dt.date(2026, 1, 1) + dt.timedelta(days=rng.randrange(273))
        rows.append([f"CW-{i + 1:06d}", d.isoformat(), reg, cat, f"P-{rng.randrange(1, 300):04d}",
                     str(qty), money(price), str(disc), money(rev), money(cost), money(margin), st])
        rev_by_region[reg] += rev
        by_status[st] += 1
        total_rev += rev
        total_margin += margin
        total_qty += qty
    gt = {"data_rows": n, "revenue_total_cents": total_rev, "margin_total_cents": total_margin,
          "quantity_total": total_qty, "revenue_by_region_cents": rev_by_region,
          "rows_by_status": by_status}
    return rows, gt


# ---------------------------------------------------------------------------
def write_csv(name, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerows(rows)
    return path


def rowlist(rs, limit=None):
    if limit and len(rs) > limit:
        return ", ".join(map(str, rs[:limit])) + f", ... ({len(rs) - limit} more)"
    return ", ".join(map(str, rs))


def usd(cents):
    return ("-" if cents < 0 else "") + "$" + thousands(cents)


MESS_DESC = {
    "banner_title": "Title banner line `Q3 sales export` above the header",
    "banner_generated_by": "`Generated by ...` banner line",
    "banner_blank": "Blank line between banner and header",
    "header_group_row": "Group-label header row (`Order`, `Location`, `People`, `Product`, `Money`, `Fulfilment`; merged-style, other cells blank)",
    "header_column_row": "Column-name header row; `Date` appears twice (B = order date, R = ship date) and H (customer id) is blank",
    "order_date_iso": "Order date `2026-07-04` (ISO)",
    "order_date_dmy_slash": "Order date `04/07/2026` (DD/MM/YYYY; day first)",
    "order_date_d_mon_yyyy": "Order date `4 Jul 2026`",
    "order_date_excel_serial": "Order date as Excel serial number, e.g. `46207` (epoch 1899-12-30)",
    "order_date_blank": "Order date blank",
    "ship_date_iso": "Ship date ISO", "ship_date_dmy_slash": "Ship date DD/MM/YYYY",
    "ship_date_d_mon_yyyy": "Ship date `D Mon YYYY`", "ship_date_excel_serial": "Ship date Excel serial",
    "ship_date_null_unshipped": "Ship date is a null spelling (`\"\"`, `-`, `N/A`, `n/a`, `null`, `TBD`) on Pending/Cancelled orders",
    "region_drift": "Region case/typo drift (`north`, `NORTH `, `Nort h`, ` North`, ...)",
    "category_drift": "Category drift (`electronics`, `Electronic s`, `Home and Kitchen`, `Home &Kitchen`, `Office  Supplies`, ...)",
    "status_drift": "Status drift (`delivered`, `DELIVERED`, `Canceled`, ` Pending`, ...)",
    "sales_rep_null": "Sales rep is a null spelling",
    "customer_null": "Customer id (blank-header column H) is a null spelling",
    "channel_null": "Channel is a null spelling",
    "qty_padded_spaces": "Qty with surrounding spaces `\" 42 \"`",
    "qty_leading_apostrophe": "Qty with leading apostrophe `'42`",
    "qty_trailing_dot_zero": "Qty as `42.0`",
    "price_dollar_thousands": "Unit price `$1,234.50`",
    "price_european": "Unit price European `1.234,50`",
    "price_padded_spaces": "Unit price with surrounding spaces",
    "discount_percent_sign": "Discount `12%` (otherwise fraction `0.12`)",
    "discount_percent_padded": "Discount `\" 12% \"`",
    "revenue_dollar_thousands": "Revenue `$1,234.50` / `-$1,234.50`",
    "revenue_european": "Revenue European `1.234,50` / `-1.234,50`",
    "revenue_paren_negative": "Revenue negative in accounting parentheses `(120.00)`",
    "revenue_padded_spaces": "Revenue with surrounding spaces",
    "revenue_leading_apostrophe": "Revenue with leading apostrophe `'1234.50`",
    "currency_spelling_drift": "Currency drift (`usd`, `US$`, `$`, `USD `); all amounts are USD",
    "weight_text_after_150k": "Weight (kg) column holds text (`approx 2.5`, `2.5 kg`, `see notes`, `TBC`, `heavy`, `~1.2kg`); numeric everywhere before",
    "notes_multiline_quoted": "Multi-line Notes cell with embedded newlines, double quotes and commas (valid CSV quoting)",
    "notes_null_spelling": "Notes is a null spelling",
    "blank_spacer_row": "Fully blank spacer row (after every 4,000 data rows)",
    "subtotal_row": "Region subtotal row (`Subtotal <Region>` in A, region in D, qty sum in L, revenue sum in O)",
    "grand_total_row": "Grand total row (`GRAND TOTAL` in A, qty in L, revenue in O)",
    "footnote_blank_separator": "Blank line between grand total and footnotes",
    "footnote_line": "Footnote text lines under the grand total",
}
FULL_LIST = {"banner_title", "banner_generated_by", "banner_blank", "header_group_row",
             "header_column_row", "weight_text_after_150k", "notes_multiline_quoted",
             "blank_spacer_row", "subtotal_row", "grand_total_row", "footnote_blank_separator",
             "footnote_line"}


def main():
    rng = random.Random(SEED)
    sales_rows, mess, sales_gt = build_sales(rng)
    sbs_rows, sbs_gt, sbs_layout = build_side_by_side(random.Random(SEED + 1))
    clean_rows, clean_gt = build_clean(random.Random(SEED + 2))

    sales_path = write_csv("messy-sales-200k.csv", sales_rows)
    write_csv("side-by-side-small.csv", sbs_rows)
    write_csv("clean-wide-50k.csv", clean_rows)

    first_data, last_row = 6, len(sales_rows)
    sales_meta = {
        "records": len(sales_rows), "columns": 20, "header_row": 5, "group_header_row": 4,
        "first_data_row": first_data, "last_data_row": mess["subtotal_row"][-1] - 1,
        "grand_total_row": mess["grand_total_row"][0],
        "subtotal_rows": mess["subtotal_row"], "spacer_rows": mess["blank_spacer_row"],
        "weight_text_rows": mess["weight_text_after_150k"],
        "multiline_rows": mess["notes_multiline_quoted"],
    }
    truth = {
        "messy-sales-200k.csv": {"layout": sales_meta, "answers": sales_gt},
        "side-by-side-small.csv": {"layout": {"records": len(sbs_rows), "columns": 13, **sbs_layout},
                                   "answers": sbs_gt},
        "clean-wide-50k.csv": {"layout": {"records": len(clean_rows), "columns": 12,
                                          "header_row": 1, "data_rows": f"2-{len(clean_rows)}"},
                               "answers": clean_gt},
    }
    with open(os.path.join(OUT, "golden-data-ground-truth.json"), "w") as f:
        json.dump(truth, f, indent=1, sort_keys=True)
        f.write("\n")

    # ---------------- manifest ----------------
    g = sales_gt
    L = []
    L.append("# Golden data manifest\n")
    L.append("Generated by `generate.py` (Python stdlib, seed `20261023`; reruns are byte-identical). "
             "Row numbers are 1-based sheet rows, i.e. CSV record index + 1; a cell with embedded "
             "newlines still counts as one row. Machine-readable copy of every answer: "
             "`golden-data-ground-truth.json`. Money in the JSON is integer cents.\n")
    L.append("Upload note: import each CSV into its own tab. With Google Sheets' "
             "\"Convert text to numbers, dates, and formulas\" ON, Sheets will itself coerce some "
             "cells (e.g. `$1,234.50`, `12%`, `'42` loses the apostrophe and becomes text, "
             "`04/07/2026` is parsed by the spreadsheet locale). Turn it OFF to keep the raw mess "
             "exactly as listed here.\n")

    L.append("## 1. `messy-sales-200k.csv`\n")
    L.append("| fact | value |\n|---|---|")
    for k, v in [("CSV records (sheet rows)", len(sales_rows)), ("Columns", "20 (A-T)"),
                 ("Order data rows", f"{N_SALES:,}"),
                 ("Banner rows", "1-3"), ("Group header row", 4),
                 ("**Header row (column names)**", "**5**"), ("First data row", first_data),
                 ("Grand total row", mess["grand_total_row"][0]),
                 ("Last row", last_row), ("File size (bytes)", os.path.getsize(sales_path)),
                 ("Cells (records x 20)", f"{len(sales_rows) * 20:,}")]:
        L.append(f"| {k} | {v} |")
    L.append("\nColumns (row 5): A `Order ID`, B `Date` (order date), C `Channel`, D `Region`, "
             "E `Country`, F `Store`, G `Sales Rep`, H *(blank; customer id)*, I `Category`, "
             "J `Product`, K `SKU`, L `Qty`, M `Unit Price`, N `Discount`, O `Revenue`, "
             "P `Currency`, Q `Status`, R `Date` (ship date, duplicate name), S `Weight (kg)`, "
             "T `Notes`.\n")
    L.append("Data is grouped in region blocks in this order: North, South, East, West, Central; "
             "each block ends with a subtotal row. Revenue = round_half_up(qty x unit_price x "
             "(1 - discount)); Cancelled = 0; Returned = negative.\n")
    L.append("Normalisation rules that make the answers reproducible: region/category/status: "
             "lowercase, drop all whitespace; `&`/`and` equivalent; `canceled` = `cancelled`. "
             "DD/MM/YYYY is day-first. Excel serials count days from 1899-12-30. Treat "
             "`\"\"`, `-`, `N/A`, `n/a`, `null`, `TBD` as null.\n")
    L.append("### Injected mess\n")
    L.append("| kind | count | rows |\n|---|---|---|")
    for k in MESS_DESC:
        rs = mess.get(k, [])
        rows_txt = rowlist(rs) if k in FULL_LIST else (
            f"{rs[0]}-{rs[-1]} scattered; first: {rowlist(rs[:8])}" if rs else "-")
        L.append(f"| {MESS_DESC[k]} | {len(rs):,} | {rows_txt} |")
    L.append("\n### Ground-truth answers (order rows only; exclude subtotal, grand total, "
             "spacer, banner, header, footnote rows)\n")
    L.append("| question | answer |\n|---|---|")
    L.append(f"| Order data rows | {g['data_rows']:,} |")
    L.append(f"| Total revenue | {usd(g['revenue_total_cents'])} |")
    for r in REGIONS:
        L.append(f"| Revenue, region {r} | {usd(g['revenue_by_region_cents'][r])} "
                 f"({g['rows_by_region'][r]:,} rows) |")
    for s in STATUSES:
        L.append(f"| Rows with status {s} | {g['rows_by_status'][s]:,} |")
    for k, v in g["revenue_by_category_cents"].items():
        L.append(f"| Revenue, category {k} | {usd(v)} |")
    L.append(f"| Total quantity | {g['quantity_total']:,} |")
    L.append(f"| Distinct normalised regions | {g['distinct_regions_normalised']} |")
    L.append(f"| Distinct normalised categories | {g['distinct_categories_normalised']} |")
    L.append(f"| Distinct SKUs | {g['distinct_skus']} |")
    L.append(f"| Largest single-order revenue | {usd(g['max_single_order_revenue_cents'])} |")
    for m, v in g["orders_by_month_with_date"].items():
        L.append(f"| Orders in {m} (non-blank order date) | {v:,} |")
    L.append(f"| Rows with blank order date | {g['order_date_blank_rows']:,} |")
    L.append(f"| Rows whose Weight (kg) is text | {g['weight_text_rows']} |")
    L.append("\nCheck: the subtotal rows equal the per-region revenue above, and the grand total "
             "row equals total revenue, so naively summing column O including them gives "
             f"{usd(3 * g['revenue_total_cents'])} (3x).\n")

    t = sbs_gt
    L.append("## 2. `side-by-side-small.csv`\n")
    L.append("| fact | value |\n|---|---|")
    for k, v in [("CSV records (sheet rows)", len(sbs_rows)), ("Columns", "13 (A-M)"),
                 ("Key-value block", "rows 1-3, key in A, value in B (`Owner:`, `Last refreshed:`, `Source:`)"),
                 ("Blank row", 4), ("Table title row", "5 (`Inventory` in A, `Returns` in I)"),
                 ("**Header row (both tables)**", "**6**"),
                 ("Table A `Inventory`", f"columns A-F, data rows 7-{6 + 2000} (2,000 rows)"),
                 ("Blank separator columns", "G-H (2 columns)"),
                 ("Table B `Returns`", f"columns I-M, data rows 7-{6 + 1200} (1,200 rows); "
                                       f"rows {7 + 1200}-{6 + 2000} blank in I-M"),
                 ("File size (bytes)", os.path.getsize(os.path.join(OUT, "side-by-side-small.csv")))]:
        L.append(f"| {k} | {v} |")
    L.append("\n### Ground-truth answers\n")
    L.append("| question | answer |\n|---|---|")
    L.append(f"| Inventory rows | {t['table_a_inventory_rows']:,} |")
    L.append(f"| Sum of On Hand | {t['table_a_on_hand_total']:,} |")
    for w, v in t["table_a_rows_by_warehouse"].items():
        L.append(f"| Inventory rows in warehouse {w} | {v} |")
    L.append(f"| Inventory rows with On Hand < Reorder Point | {t['table_a_rows_below_reorder_point']} |")
    L.append(f"| Returns rows | {t['table_b_returns_rows']:,} |")
    L.append(f"| Sum of Refund (cents) | {t['table_b_refund_total_cents']:,} |")
    for k, v in t["table_b_rows_by_reason"].items():
        L.append(f"| Returns with reason {k} | {v} |")

    c = clean_gt
    L.append("\n## 3. `clean-wide-50k.csv`\n")
    L.append("| fact | value |\n|---|---|")
    for k, v in [("CSV records (sheet rows)", len(clean_rows)), ("Columns", "12 (A-L)"),
                 ("**Header row**", "**1**"), ("Data rows", f"2-{len(clean_rows)} (50,000)"),
                 ("Mess injected", "none (control file); ISO dates, plain decimals, consistent labels"),
                 ("File size (bytes)", os.path.getsize(os.path.join(OUT, "clean-wide-50k.csv")))]:
        L.append(f"| {k} | {v} |")
    L.append("\nColumns: `order_id, order_date, region, category, product_id, quantity, "
             "unit_price, discount_pct, revenue, cost, margin, status`.\n")
    L.append("### Ground-truth answers\n")
    L.append("| question | answer |\n|---|---|")
    L.append(f"| Data rows | {c['data_rows']:,} |")
    L.append(f"| Total revenue | {usd(c['revenue_total_cents'])} |")
    L.append(f"| Total margin | {usd(c['margin_total_cents'])} |")
    L.append(f"| Total quantity | {c['quantity_total']:,} |")
    for r, v in c["revenue_by_region_cents"].items():
        L.append(f"| Revenue, region {r} | {usd(v)} |")
    for s, v in c["rows_by_status"].items():
        L.append(f"| Rows with status {s} | {v:,} |")
    with open(os.path.join(OUT, "golden-data-manifest.md"), "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
