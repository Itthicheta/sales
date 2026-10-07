"""Load Point's monthly GrabFood CSV exports into sales_web.grab_* tables.
Run: ~/mamapook-data/venv/bin/python ~/sales/tools/grab_load.py "<Grab reports folder>"

Files are detected by HEADER SIGNATURE (filenames don't matter), searched at
any depth (a leading-underscore folder such as _analytics/ is skipped).
Idempotent: for each table, the rows for every (location_id, year-month) present
in the file being loaded are deleted before insert (ads_hourly: by date,
keywords: by ym, combos: by (location_id, as_of)) — so re-dropping August
replaces August only. One transaction, committed at the end.

Ignored on purpose: Transfers_Store (bank recon = Finance), MIWI item/heatmap
(alert only via miwi_store), account-level ads hourly (subset of campaign file).
"""
import csv
import datetime as dt
import hashlib
import os
import re
import sys
from collections import defaultdict

from pathlib import Path
BACKBONE = Path.home() / "mamapook-data"  # backbone repo = dependency (shared.*, .env)
sys.path.insert(0, str(BACKBONE))
from shared.env import load  # noqa: E402
from shared.backbone import Backbone  # noqa: E402

SIG = {
    "sales":        {"วันที่", "ร้าน", "ยอดขายรวม (฿)", "จำนวนรายการชำระเงิน", "เรตติ้งเฉลี่ย"},
    "menu":         {"วันที่", "ร้าน", "รายการ", "จำนวนที่ขายได้"},
    "combo":        {"ร้าน", "คอมโบ"},
    "peak":         {"วันที่", "ร้าน", "01", "13", "00"},
    "offers":       {"วันที่", "ร้าน", "โปรโมชัน", "เงินที่ใช้ (฿)"},
    "ads_campaign": {"Campaigns Name", "Ad Spend", "Impressions", "Hourly"},
    "keywords":     {"Matched Keywords", "Ad Spend", "Impressions"},
    "transactions": {"หมวดหมู่", "ยอดขายสุทธิ", "ทั้งหมด", "Transaction ID"},
    "miwi_store":   {"ร้าน", "Missing or Wrong Item Rate (%)"},
    "reviews":      {"ร้าน", "รีวิว", "เรตติ้ง", "ลูกค้า"},
}
# most specific first where signatures could overlap
ORDER = ["transactions", "ads_campaign", "keywords", "miwi_store", "menu", "offers",
         "peak", "sales", "reviews", "combo"]

BRANCHES = {"พระราม 9": "rama9", "One City Centre": "occ", "Park Silom": "silom",
            "Gaysorn Tower": "gaysorn", "sathorn square": "sathorn-square",
            "All Season": "all-seasons"}

DDL = {
    "grab_orders": """row_id text primary key, booking_id text, location_id text, business_date date,
        created_at timestamp, gf_no text, category text, status text, order_type text,
        gross_thb numeric, merchant_discount_thb numeric, net_thb numeric, commission_thb numeric,
        marketing_fee_thb numeric, payout_thb numeric, cancel_reason text, cancelled_by text""",
    "grab_daily": """location_id text, business_date date, gross_thb numeric, net_thb numeric,
        orders integer, avg_order_thb numeric, rating numeric, primary key (location_id, business_date)""",
    "grab_menu_daily": """location_id text, business_date date, item text, units numeric, thb numeric,
        primary key (location_id, business_date, item)""",
    "grab_offers": """location_id text, business_date date, campaign text, gross_thb numeric,
        net_thb numeric, orders integer, spend_thb numeric, primary key (location_id, business_date, campaign)""",
    "grab_peak": """location_id text, business_date date, hour integer, orders integer,
        primary key (location_id, business_date, hour)""",
    "grab_ads_hourly": """d date, hour integer, campaign text, spend_thb numeric, impressions integer,
        clicks integer, menu_visits integer, add_to_cart integer, orders integer, sales_thb numeric,
        primary key (d, hour, campaign)""",
    "grab_keywords": """ym text, keyword text, is_brand boolean, impressions integer, clicks integer,
        spend_thb numeric, orders integer, sales_thb numeric, primary key (ym, keyword)""",
    "grab_miwi_daily": """location_id text, business_date date, wrong integer, missing integer,
        total_reported integer, completed_orders integer, primary key (location_id, business_date)""",
    "grab_reviews": """review_key text primary key, location_id text, review_date date, rating integer,
        review text, replied boolean, service_type text""",
    "grab_combos": """location_id text, as_of date, combo text, n integer,
        primary key (location_id, as_of, combo)""",
}

CATEGORY = {"ชำระเงิน": "payment", "การปรับรายได้": "adjustment", "โฆษณา": "ads"}
BRAND_WORDS = ("mama", "หม่าม้า", "มาม่าปุก", "pook")


# ---------- helpers ----------

def detect(header: set):
    for kind in ORDER:
        if SIG[kind] <= header:
            return kind
    return None


def branch(store: str):
    tail = (store or "").rsplit(" - ", 1)[-1].strip().lower()
    for key, loc in BRANCHES.items():
        if key.lower() in tail:
            return loc
    return None


def num(s) -> float:
    s = re.sub(r"[^0-9.\-]", "", str(s or ""))
    try:
        return float(s) if s not in ("", "-", ".", "-.") else 0.0
    except ValueError:
        return 0.0


def dmy(s):
    return dt.datetime.strptime(s.strip(), "%d/%m/%Y").date()


def gf_no(s):
    # full digit run of the first id: "GF-050" -> "050", "GF-5265" -> "5265" (never truncated),
    # letter suffix dropped ("GF-851T") -> "851"; runs of <=3 digits zero-padded to 3
    m = re.search(r"(\d+)", s or "")
    return m.group(1).zfill(3) if m else None


def replace(cur, table, rows, cols, del_sql, del_keys):
    """Delete the slices this file covers, then insert. Returns rows inserted."""
    cur.execute(f"create table if not exists sales_web.{table} ({DDL[table]})")
    for k in sorted(del_keys):
        cur.execute(f"delete from sales_web.{table} where {del_sql}", k)
    if rows:
        ph = ",".join(["%s"] * len(cols))
        cur.executemany(f"insert into sales_web.{table} ({','.join(cols)}) values ({ph})", rows)
    return len(rows)


LOC_YM = "location_id=%s and to_char(business_date,'YYYY-MM')=%s"


def by_store(rows, store_key, fn, unknown):
    """Map rows via fn(row, loc) for rows whose store resolves to a branch."""
    out = []
    for r in rows:
        loc = branch(r[store_key])
        if loc is None:
            unknown.add(r[store_key])
            continue
        out.append(fn(r, loc))
    return out


# ---------- loaders: each takes (rows, cur, path) and returns rows inserted ----------

def load_transactions(rows, cur, path, unknown):
    # header has duplicate names (ชื่อร้าน twice) -> positional
    out = {}
    for r in rows[1:]:
        if len(r) < 61:
            continue
        loc = branch(r[2])
        if loc is None:
            unknown.add(r[2])
            continue
        created = dt.datetime.strptime(r[5].strip(), "%d %b %Y %I:%M %p")
        txn, booking = r[10].strip(), r[16].strip()
        row_id = txn or f"{booking}|{r[7]}|{r[5]}"
        out[row_id] = (row_id, booking or None, loc, created.date(), created, gf_no(r[15]),
                       CATEGORY.get(r[7].strip(), "other"), r[9].strip(), r[18].strip(),
                       num(r[29]), num(r[35]), num(r[40]), num(r[46]) + num(r[47]), num(r[44]),
                       num(r[52]), r[59].strip() or None, r[60].strip() or None)
    vals = list(out.values())
    return replace(cur, "grab_orders", vals,
                   ["row_id", "booking_id", "location_id", "business_date", "created_at", "gf_no",
                    "category", "status", "order_type", "gross_thb", "merchant_discount_thb",
                    "net_thb", "commission_thb", "marketing_fee_thb", "payout_thb",
                    "cancel_reason", "cancelled_by"],
                   LOC_YM, {(v[2], v[3].strftime("%Y-%m")) for v in vals})


def load_sales(rows, cur, path, unknown):
    vals = by_store(rows, "ร้าน", lambda r, loc: (
        loc, dmy(r["วันที่"]), num(r["ยอดขายรวม (฿)"]), num(r["ยอดขายสุทธิ (฿)"]),
        int(num(r["จำนวนรายการชำระเงิน"])), num(r.get("ยอดชำระเงินเฉลี่ย (฿)")),
        num(r["เรตติ้งเฉลี่ย"]) or None), unknown)
    vals = list({(v[0], v[1]): v for v in vals}.values())
    return replace(cur, "grab_daily", vals,
                   ["location_id", "business_date", "gross_thb", "net_thb", "orders",
                    "avg_order_thb", "rating"], LOC_YM, {(v[0], v[1].strftime("%Y-%m")) for v in vals})


def load_menu(rows, cur, path, unknown):
    agg = defaultdict(lambda: [0.0, 0.0])
    for loc, d, item, u, t in by_store(rows, "ร้าน", lambda r, loc: (
            loc, dmy(r["วันที่"]), r["รายการ"].strip(), num(r["จำนวนที่ขายได้"]),
            num(r.get("ยอดขายที่ทำได้ (฿)"))), unknown):
        agg[(loc, d, item)][0] += u
        agg[(loc, d, item)][1] += t
    vals = [(*k, u, t) for k, (u, t) in agg.items()]
    return replace(cur, "grab_menu_daily", vals, ["location_id", "business_date", "item", "units", "thb"],
                   LOC_YM, {(v[0], v[1].strftime("%Y-%m")) for v in vals})


def load_offers(rows, cur, path, unknown):
    agg = defaultdict(lambda: [0.0, 0.0, 0, 0.0])
    for loc, d, c, g, n, o, s in by_store(rows, "ร้าน", lambda r, loc: (
            loc, dmy(r["วันที่"]), r["โปรโมชัน"].strip(), num(r["ยอดขายรวม (฿)"]),
            num(r["ยอดขายสุทธิ (฿)"]), int(num(r["จำนวนรายการชำระเงิน"])),
            num(r["เงินที่ใช้ (฿)"])), unknown):
        a = agg[(loc, d, c)]
        a[0] += g; a[1] += n; a[2] += o; a[3] += s
    vals = [(*k, *a) for k, a in agg.items()]
    return replace(cur, "grab_offers", vals,
                   ["location_id", "business_date", "campaign", "gross_thb", "net_thb", "orders",
                    "spend_thb"], LOC_YM, {(v[0], v[1].strftime("%Y-%m")) for v in vals})


def load_peak(rows, cur, path, unknown):
    # a branch-day can appear on two rows (seen 26/08 Gaysorn/Silom) -> sum
    agg = defaultdict(int)
    for loc, d, r in by_store(rows, "ร้าน", lambda r, loc: (loc, dmy(r["วันที่"]), r), unknown):
        for h in range(24):
            agg[(loc, d, h)] += int(num(r.get(f"{h:02d}")))
    vals = [(*k, n) for k, n in agg.items()]
    return replace(cur, "grab_peak", vals, ["location_id", "business_date", "hour", "orders"],
                   LOC_YM, {(v[0], v[1].strftime("%Y-%m")) for v in vals})


def load_ads_campaign(rows, cur, path, unknown):
    agg = defaultdict(lambda: [0.0, 0, 0, 0, 0, 0, 0.0])
    for r in rows:
        d = dt.date(int(num(r["Yearly"])), int(num(r["Monthly"])), int(num(r["Daily"])))
        a = agg[(d, int(num(r["Hourly"])), r["Campaigns Name"].strip())]
        for i, (c, f) in enumerate([("Local Ad Spend", num), ("Impressions", int), ("Clicks", int),
                                    ("MenuVisit_", int), ("AddToCart_", int),
                                    ("Ad Generated Orders", int), ("Ad Generated Sales", num)]):
            a[i] += f(num(r.get(c)))
    vals = [(*k, *a) for k, a in agg.items()]
    return replace(cur, "grab_ads_hourly", vals,
                   ["d", "hour", "campaign", "spend_thb", "impressions", "clicks", "menu_visits",
                    "add_to_cart", "orders", "sales_thb"], "d=%s", {(v[0],) for v in vals})


def load_keywords(rows, cur, path, unknown):
    agg = defaultdict(lambda: [0, 0, 0.0, 0, 0.0])
    for r in rows:
        kw = (r["Matched Keywords"] or "").strip()
        if not kw:
            continue
        ym = f"{int(num(r['Yearly'])):04d}-{int(num(r['Monthly'])):02d}"
        a = agg[(ym, kw)]
        a[0] += int(num(r.get("Impressions"))); a[1] += int(num(r.get("Clicks")))
        a[2] += num(r.get("Local Ad Spend")); a[3] += int(num(r.get("Ad Generated Orders")))
        a[4] += num(r.get("Ad Generated Sales"))
    vals = [(ym, kw, any(w in kw.lower() for w in BRAND_WORDS), *a) for (ym, kw), a in agg.items()]
    return replace(cur, "grab_keywords", vals,
                   ["ym", "keyword", "is_brand", "impressions", "clicks", "spend_thb", "orders",
                    "sales_thb"], "ym=%s", {(v[0],) for v in vals})


def load_miwi_store(rows, cur, path, unknown):
    vals = by_store(rows, "ร้าน", lambda r, loc: (
        loc, dt.date.fromisoformat(r["วันที่"].strip()), int(num(r["Wrong Reported"])),
        int(num(r["Missing Reported"])), int(num(r["Total Reported"])),
        int(num(r["Total Completed Orders"]))), unknown)
    vals = list({(v[0], v[1]): v for v in vals}.values())
    return replace(cur, "grab_miwi_daily", vals,
                   ["location_id", "business_date", "wrong", "missing", "total_reported",
                    "completed_orders"], LOC_YM, {(v[0], v[1].strftime("%Y-%m")) for v in vals})


def load_reviews(rows, cur, path, unknown):
    def mk(r, loc):
        raw = r["วันที่"].strip()
        d = dt.datetime.strptime(" ".join(raw.split()[:3]), "%d %b %Y").date()
        text = r["รีวิว"] or ""
        key = hashlib.md5(f"{r['ร้าน']}|{raw}|{text}".encode()).hexdigest()
        return (key, loc, d, int(num(r["เรตติ้ง"])), text, bool((r.get("ตอบกลับ") or "").strip()),
                (r.get("ประเภทบริการ") or "").strip() or None)
    vals = list({v[0]: v for v in by_store(rows, "ร้าน", mk, unknown)}.values())
    return replace(cur, "grab_reviews", vals,
                   ["review_key", "location_id", "review_date", "rating", "review", "replied",
                    "service_type"], "location_id=%s and to_char(review_date,'YYYY-MM')=%s",
                   {(v[1], v[2].strftime("%Y-%m")) for v in vals})


def load_combo(rows, cur, path, unknown):
    # the export carries no date: as_of = last dd_mm_yy in the filename, else file mtime
    m = re.findall(r"(\d{2})_(\d{2})_(\d{2})", os.path.basename(path))
    as_of = (dt.date(2000 + int(m[-1][2]), int(m[-1][1]), int(m[-1][0])) if m
             else dt.date.fromtimestamp(os.path.getmtime(path)))
    agg = defaultdict(int)
    for loc, c in by_store(rows, "ร้าน", lambda r, loc: (loc, (r["คอมโบ"] or "").strip()), unknown):
        if c:
            agg[(loc, c)] += 1
    vals = [(loc, as_of, c, n) for (loc, c), n in agg.items()]
    return replace(cur, "grab_combos", vals, ["location_id", "as_of", "combo", "n"],
                   "location_id=%s and as_of=%s", {(v[0], v[1]) for v in vals})


LOADERS = {k: globals()[f"load_{k}"] for k in SIG}


# ---------- main ----------

def csv_files(folder):
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        for f in sorted(files):
            if f.lower().endswith(".csv"):
                yield os.path.join(root, f)


def main(folder):
    bb = Backbone(load())
    unknown = set()
    with bb.conn.cursor() as cur:
        for path in csv_files(folder):
            name = os.path.relpath(path, folder)
            with open(path, encoding="utf-8-sig", newline="") as fh:
                raw = list(csv.reader(fh))
            if not raw:
                print(f"skip: {name} (empty)")
                continue
            header = [h.strip().lstrip("﻿") for h in raw[0]]
            kind = detect(set(header))
            if kind is None:
                print(f"skip: {name}")
                continue
            if kind == "transactions":
                data = raw
            else:
                data = [dict(zip(header, r)) for r in raw[1:] if any(c.strip() for c in r)]
            n = LOADERS[kind](data, cur, path, unknown)
            print(f"{kind}: {name} -> {n} rows")
            if kind == "transactions":
                cur.execute("""select category, count(*) from sales_web.grab_orders
                               group by 1 order by 2 desc""")
                print("  grab_orders by category: " + ", ".join(f"{c} {k}" for c, k in cur.fetchall()))
    bb.conn.commit()
    if unknown:
        print("WARN unmapped store names (rows skipped): " + "; ".join(sorted(unknown)))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
