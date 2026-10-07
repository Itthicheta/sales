# 2026-10-07 — Driver Tree menu categories (Point's review, 4 changes)

## What changed
1. **Item-level category overrides + `set` category** (transforms)
   - New `mp_clean.map_item_category(item_code, tree_category, note)` in
     `transforms/001_mappings.sql`, seeded A2 ก๋วยเตี๋ยวหลอดโบราณ → main,
     A5 เล้งแซ่บ → main (both were `side` via prefix A).
   - `transforms/002_items.sql`: `tree_category = coalesce(override, 'set' if itemcode ilike 'set%', prefix map)`.
     Set items (Set1 ×2 item ids, Set2, Setgf1–4) were **NULL / uncategorised** before, now `set`.
   - `mp_metrics.driver_tree_daily` gains `set_units` (appended last).
   - Side effects: `mp_metrics.bill_items.main_category` now yields `set` instead of `other`
     for set lines (1,104 lines all-time); `mp_metrics.category_sales` shows a `set` row instead of
     `UNMAPPED`; set items left `mp_metrics.unmapped_sold_items` (1 row remains: a ฿0
     "แลกฟรี" redemption item). Dashboard `usage_daily` / dashboard_tables use
     `coalesce(tree_category, vendor_category)` → set materials read `set` instead of `Set menu`.
2. **topping → "Sharing (จานกลาง)"** in every Sales Web label (tree header `Sharing%`, attach
   lever, opportunity lever, menu-matrix CATL, Grab attach, 9.6, route card
   "เส้นทาง Sharing — เป็นออปชัน vs เมนูเดี่ยว"). Internal keys `top` / `topping` unchanged.
3. **Paid options exclude set-mandatory groups** — `SET_MANDATORY_RE = (จานที่|แก้วที่|ชามที่|2ที่)`
   in `tools/sales_tables.py` (one constant; build.py imports it for the Grab basket `paid_opt`).
   Applied to tree/opportunity `paid_option_picks/thb` and `option_monthly.paid_picks/paid_thb`.
   Plain `picks` (option_monthly, option_by_dish) unchanged.
4. **เซต section** — `sales_web.tree_daily` + `set_units, set_thb` (appended); `tree` feed + `set, set_thb`
   (appended); `grab.basket` + `sets` (appended). UI: "เซต" column after "ชามหลัก", KPI tile "เซต",
   helper `occ(s) = main + set` used for the customer fallback, bowls-per-customer and the denominator
   of every attach %; note "ชามหลัก + เซต = จำนวนมื้อหลัก (ตัวหารของ attach)". pax_trust bowls and the
   opportunity calculator's `mu` = main + set. Menu matrix has a "เซต" category.

## Verification (2026-10-07, after transforms + sales_tables + build + deploy)
| Check | Result |
|---|---|
| items A2 / A5 / Set1 / Setgf1 | main / main / set / set |
| set units / ฿, last 30 d (tree_daily) | **672 / ฿162,447** (driver_tree_daily: 687 — tree_daily drops split-bill duplicate lines) |
| paid_option_picks last 30 d | **1,747 / ฿90,969** (was 3,048) |
| tree net 2026-09-15 | 187,619.98 (unchanged) |
| UI net, 2026-09-06 → 10-05 (the previous default range) | ฿3,679,644 (unchanged). The default range rolled forward a day (data_through 10-06) → ฿3,780,379 |
| main units last 30 d | 19,758 (was 18,868; +890 from A2/A5) |
| console | clean; all render* functions run without error |

Paid-option breakdown, last 30 d (live branches, finalized, split-dupes removed):

| group | counted? | picks | ฿ |
|---|---|---|---|
| เพิ่มความอร่อย | yes | 1,320 | 81,687 |
| เพิ่มข้าว | yes | 389 | 6,241 |
| ซุบบ๊วย | yes | 38 | 3,041 |
| เครื่องดื่ม แก้วที่ 1 | excluded | 665 | 45,068 |
| ของทอดไซส์ S (จานที่ 1) | excluded | 612 | 32,087 |
| ไอศครีม 2ที่ / จานที่ 2 / แก้วที่ 2 | excluded | 8 + 8 + 8 | 1,493 |

option_monthly 2026-09: every จานที่/แก้วที่/ชามที่/2ที่ group shows `paid_picks = 0`, `paid_thb = 0`
while `picks` keeps its counts (e.g. เครื่องดื่ม แก้วที่ 1: 639 picks).

## Worth knowing
- The set-mandatory picks carry a `paid_price` (≈ ฿78.6k / 30 d). That money is part of what a
  set customer pays but sits on option rows, so `set_thb` (line gross, ฿162k) understates the full set
  ticket; total net is unaffected.
- Section 6(ค) "เซต vs จานหลักแบบสั่งเดี่ยว" used to subtract set units from tree main — but sets were
  never in main (they were uncategorised), so it under-counted à-la-carte mains. Fixed: no subtraction.
- Not changed (scope): section 2 hourly customer fallback still uses main bowls only (the hourly feed
  has no set column); section 7 party "ชามหลัก/บิล" stays bowls-only.
