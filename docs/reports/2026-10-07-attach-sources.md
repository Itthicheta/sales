# 2026-10-07 — Attach units by source (Point's rework of the Driver Tree unit counts)

## What changed
1. **Free items out of every unit count** — `FREE_ITEM_CODES = ("A13","H4","H2")` + name
   `LIKE '%แลกฟรี%'` (tools/sales_tables.py). Money untouched (net = orders.total_thb).
2. **Option → menu merge** — new `mp_clean.map_option_item(choice_key pk, item_code, qty_mult)`
   in transforms/001_mappings.sql (18 keys). Choice key = choice_th with trailing `*`/`**`
   stripped. **E8 caveat:** "E8 เขียวโซดา" has item_code **E9** in the POS (only "E8 แดงโซดา"
   is E8), so เขียวโซดา → E9 explicitly. ไอศครีม 2ที่ → F1 × 2.
3. **Unified `t_units` temp table** (sales_tables.build_base) = order lines (split-dupe-free,
   free items excluded, source `menu`) ∪ mapped option picks (source `option` for
   เพิ่มความอร่อย/เพิ่มข้าว/ซุบบ๊วย, `set` for SET_MANDATORY_RE groups; qty = choice_qty × qty_mult,
   thb = paid_price × choice_qty, category/tier from the mapped item). t_ord now sums from it.
   Unmapped choices in covered groups print a WARNING line.
4. **tree_daily**: unit/thb columns = all sources; `paid_option_picks/thb` deprecated (0);
   appended `side_set_units, bev_paid_set_units, bev_premium_set_units, dessert_set_units,
   topping_set_units`. **item_daily**: `source` appended + grouped by. **opportunity**: built
   from t_units; levers side / bev_paid / bev_premium / dessert / topping on the อัพเซล layer
   (source ≠ set), `paid_option` dropped, NEW `set` lever (set units ÷ (main+set) × avg set
   price). A branch with zero units of a lever is priced at the channel average.
5. **build.py**: tree feed + `side_s, bev_p_s, bev_pr_s, des_s, top_s`; items feed + `src`;
   Grab basket rebuilt with the same source logic (free items out, option picks merged;
   `paid_opt` = 0). Payload 6.49 MB.
6. **index.html**: §1 ออปชันเพิ่ม% column removed, attach cells "21.3% (ในเซต 3%)", new KPI
   note; §3 toggle รวม / อัพเซล / ในเซต, opportunity lever "เซต" with its own explanation,
   ออปชันเพิ่มเงิน removed everywhere (also Grab §3 and 9.6); §6 drill-down "เส้นทางการขาย"
   (เมนู / ออปชัน / ในเซต) + card "เซตช่วยเพิ่ม attach ไหม" (branch × month, drink & side
   attach with vs without the set layer).
7. **Beverage water tier ceiling ฿15 → ฿20** (see Surprises).

## Verification — in-store (dine_in + take_away), 2026-09-07 → 2026-10-06 (current_date-30 … -1)
| Check | Expected (Point) | Now |
|---|---|---|
| side total | ≈ 6,724 | **7,070** |
| · menu | 4,325 | 4,723 |
| · option | ≈ 1,717 | 1,727 |
| · set | ≈ 636 | 620 |
| A10 ข้าวสวย | 809 | 797 (373 menu + 424 option) |
| A9 ซุปบ๊วย | 767 | 759 |
| C6 / C5 / C4 / C3 | 880 / 637 / 532 / 422 | 864 / 632 / 527 / 416 |
| E1 / E3 / E6 / E2 / E7 | 800 / 585 / 321 / 301 / 292 | 798 / 581 / 318 / 301 / 292 |
| E5 / E4 | 266 / 266 | 263 / 264 |
| E8 แดงโซดา / เขียวโซดา | 147 / 73 | 147 / 72 |
| drink set-layer picks | ≈ 635 | 620 |
| F1 (set) | 213 (16) | 213 (16) |
| water = H3 only | 3,702 | 3,702 (bev_water_units) |
| A13 / H4 / H2 | nothing | nothing (no item_daily rows) |
| net 2026-09-15 | 187,619.98 | 187,619.98 |
| unmapped choices | empty | empty ("no unmapped choices") |
| UI net, default 30 d | – | ฿3,780,379 (unchanged vs. yesterday) |

Per-item numbers are within ~1–2% (window rolled a day). The one real gap is **menu side
4,723 vs 4,325 (+398)** — every per-item figure matches, and the gap ≈ A16 ไข่ต้มยางมะตูม
(397 units), so Point's 4,325 most likely left out A16 (or a similar item); worth a 1-line
confirm.

Live site (https://mamapook-sales.pages.dev, live edge data): §1 header has no ออปชันเพิ่ม,
total row `21.3% (ในเซต 3%) · 38.7% (ในเซต 3%) · 2.1% · 1.7% (ในเซต 0.1%)`; §3 toggle Silom
side รวม 39.4% → อัพเซล 33.3%, premium 17.5% → 11.4%; opportunity shows "เซต" rows (e.g. All
Seasons dine-in 2.7% → 6.4% like Silom ≈ +฿29,142/month); all 9 render functions run, console clean.

## Surprises / worth knowing
- **Water tier was mis-set:** H3 น้ำเปล่า sells at ฿16.05 inc VAT, so the old ≤ ฿15 rule put H3
  in "paid" and only the ฿0 glass/ice in "water". With free items gone the water tier would
  have been empty; ceiling raised to ฿20 (H5 Coke ฿21.40 stays paid). Effect: paid-drink units
  4,562 → 860 (H1/H5/H6/H7 only), water 5,811 → 3,702. Grab basket uses the same constants.
- Set lever is the biggest single opportunity (dine-in ≈ ฿112k/month across branches) — it is a
  rough figure: it doesn't subtract the à-la-carte bowl a set replaces.
- Option ฿ also sits in the parent line's gross, so category ฿ in tree_daily overlaps main_thb;
  used only for unit prices.
- Rama 9 sells no sets (set layer 0).
