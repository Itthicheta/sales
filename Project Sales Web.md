# Sales Web — Mama Pook sales-growth dashboard (SPEC, locked 2026-10-06)

**Status: BUILT 2026-10-06 — see BUILT block at the end.** This file is
self-contained: a fresh session can build from here alone.

## Repo layout / how it runs (moved to its own repo 2026-10-07)
Repo: **github.com/Itthicheta/sales**, cloned at `~/sales`. Until 2026-10-07 this
lived inside the data-backbone repo (`~/mamapook-data/sales_app/` + `tools/`);
that repo's git history still holds the earlier commits. Vault twin of this file:
`Second brain/Mama Pook/Projects/Sales Web/Project Sales Web.md` (keep identical),
plus a read-only nightly mirror of the whole repo at `Projects/Sales Web/repo/`
(rsync in mamapook-data `tools/backup.sh`).

```
sales/
  Project Sales Web.md        this file (handoff, self-contained)
  README.md
  tools/sales_tables.py       rebuild every computed sales_web.* table (30-min cycle)
  tools/grab_load.py          Grab CSV exports -> sales_web.grab_* (nightly)
  tools/weather_pull.py       Open-Meteo Bangkok rain -> sales_web.weather_daily (nightly)
  app/build/build.py          bake payload -> sales_web.app_cache id=1 + app/site/data/data.json
  app/edge/sales-data.ts      repo copy of the Supabase edge function (deployed via MCP)
  app/site/index.html         the SPA (Cloudflare Pages project mamapook-sales)
  app/deploy.sh               build + `wrangler pages deploy site --branch main`
  migrations/010_sales_web_schema.sql   schema (applied 2026-10-06)
  migrations/011_seats_2026-10-07.sql   seats per branch + holidays (idempotent upsert)
  docs/plan-2026-10-06-sales-web.md     original implementation plan
  docs/reports/2026-10-07-*.md          working reports (attach sources, tree categories, occupancy)
```

**Dependency on the backbone** (`~/mamapook-data`, github.com/Itthicheta/Mamapook-data):
every script puts `BACKBONE = Path.home() / "mamapook-data"` on `sys.path` and uses
`shared.env.load` (reads `~/mamapook-data/.env` — the ONLY place secrets live; never
copy them here) and `shared.backbone.Backbone`. Python = the backbone venv
`~/mamapook-data/venv`. build.py imports `sales_tables` from this repo's `tools/`
dir directly (not as package `tools`, which would collide with mamapook-data/tools).
The mp_clean transforms (incl. map_item_category / map_option_item in
`transforms/001_mappings.sql`, `002_items.sql`) are BACKBONE code and live there.

**Who runs it:** the backbone's launchd pipeline `~/mamapook-data/pipelines/pos_ers/run_local.sh`
(every 30 min, venv active, cwd = mamapook-data) calls, after transforms:
`python $HOME/sales/tools/sales_tables.py`, `python $HOME/sales/app/build/build.py`;
nightly also `weather_pull.py`, `grab_load.py "<vault>/Projects/Sales Web/Grab reports"`,
and `$HOME/sales/app/deploy.sh`. If `~/sales` is missing, those steps fail non-fatally
and the rest of the backbone pipeline is unaffected.

**Manual run:** `~/mamapook-data/venv/bin/python ~/sales/tools/sales_tables.py` (add
`--check` for row counts) → `~/mamapook-data/venv/bin/python ~/sales/app/build/build.py`
→ `~/sales/app/deploy.sh`. Live: https://mamapook-sales.pages.dev

## Why this exists
Point's directive: focus on the SALES side and find ways to increase sales.
The analytical framework is the **Mama Pook Sales Driver Tree** — see the
`sales-drivers` skill (~/.claude/skills/sales-drivers/), which is the locked
canonical reference (master identity: Net Sales = Σ Volume × Avg Ticket per
Daypart × Channel × Branch; mains = 100% anchor; attach × price everywhere;
billing vs analysis layers for off-premise). The new web = that tree made
LIVE + the growth-lever views around it. The tree's v1 Excel limitations
(one day/branch, hand-typed, no history) all disappear because every input
is POS-native and already mirrored in the backbone.

## The locked blueprint (9 sections)
| # | Section | Contents |
|---|---|---|
| 1 | Driver Tree (live) | volume × ticket per daypart/channel/branch with working filters (branch / day-type / date range) · pax-trust strip (see below) |
| 2 | Volume levers | hour × branch density heatmap with unit toggle (count / % of branch / % of hour) · **hourly seat occupancy** heatmap + turns per seat per hour (see "Section 2 — volume (as built 2026-10-07)") · dead-hour revenue curve (is the 16:00-20:00 promo working?) · baseline-vs-actual anomaly (branch's own history as the baseline) · dwell by hour and by daypart |
| 3 | Ticket levers | attach heatmap: category × channel × branch on per-MAIN-OCCASION denominator · beverage attach in 3 TIERS (water / paid / premium — Point: keep water in, the lever is tier CONVERSION, not exclusion) · PAID-OPTION attach (เกี๊ยวเพิ่ม/หมูเพิ่ม — is_paid_option; cheapest per-head lever, nobody measures it) · **OPPORTUNITY CALCULATOR: click any weak cell → "closing OCC bev gap to Silom's level = +฿X/month", ranked list = sales-meeting agenda** (centerpiece) · **TICKET & PARTY-SIZE DISTRIBUTIONS** (added 2026-10-06): histograms of bill total and pax, not just averages — solo / pair / group mix per branch × daypart, and spend-per-head by party-size bucket |
| 4 | Options & choices | choice share per modifier group per menu (เส้น split etc., branch deltas) · unpopular tail (<2% picks → menu simplification) · **topping ROUTE analysis: same topping as option vs standalone menu line — which route per branch, price parity check** · option-to-main lift |
| 5 | Repeat & members | member attach at POS (~1% today — headroom), repeat frequency per phone, days-since-last-visit win-back list |
| 6 | Menu & promo | **PER-MENU DRILL-DOWN** (click any menu → its sales trend, choice shares, attach, combos it sits in; a new item's launch ramp is read HERE, no separate launch scorecard) · **MENU ENGINEERING MATRIX** popularity × margin → stars / plowhorses / puzzles / dogs per branch (until the vendor cost fix lands, runs on popularity × price as an interim axis; switch to margin once cost data is trustworthy) · **COMBO/SET PERFORMANCE** (added 2026-10-06): set uptake vs à-la-carte for the same items, from SaleItemPackage + Package/PackageItems · 2-free-1 incremental read (derived promo tag exists) · combo simulator priced from basket data |
| 7 | Monthly Insights (models) | market-basket lift (co-occurrence, no ML) · attach logistic regressions (channel/daypart/group-size/main) · calendar regression: day-of-week, DAY-OF-MONTH (payday 1st/15th/end), WEEK-OF-MONTH, Thai holidays + bridge days, RAIN (pull Bangkok hourly rain, store daily) · models live HERE monthly, never in the daily tree (framework rule: no proxies in the daily tree) |
| 8 | Digest | weekly LINE push: top-3 baht-ranked opportunities (reuse watchdog push plumbing) |
| 9 | **Grab** (added 2026-10-06) | Grab-only insights — subsections 9.1–9.6, see "Grab — channel switch + section 9" below. Grab ALSO appears in sections 1-4 and 6-8 via the channel switch |

## Key design decisions (Point's calls, 2026-10-06)
- **No. of customers:** staff-keyed pax (orders.pax) AND 1-main≈1-customer
  proxy both exist. Metric **bowls ÷ keyed customers** serves double duty:
  per-branch trust check (sane ≈ 0.9-1.3; outside → tree falls back to main
  units for that branch and the branch appears on a keying-discipline strip,
  same pattern as count compliance) and, where honest, a real
  sharing-behavior metric.
- Water STAYS in beverage attach, split into tiers (water/paid/premium).
- **Tree categories (Point 2026-10-07):** main / **set** / side / beverage /
  topping / dessert. `set` = item codes `Set*` (Set สุดคุ้ม, Set อิ่มพอดี…; before
  this they were uncategorised). Item-level overrides live in
  `mp_clean.map_item_category` (transforms/001_mappings.sql): A2 ก๋วยเตี๋ยวหลอดโบราณ
  and A5 เล้งแซ่บ = main. **Main occasions = main units + set units** (a set is a
  meal): it is the pax-trust "bowls", the customer fallback, and the denominator
  of every attach % and of the opportunity calculator (`occ(s)` in index.html).
  The `topping` category is labelled **Sharing (จานกลาง)** in the UI (internal
  keys `top` / `topping` unchanged).
- **Paid options exclude set-mandatory picks (Point 2026-10-07; the paid-option lever was
  removed later the same day — see "Attach sources" below; `option_monthly.paid_*` still uses this):** modifier
  groups matching `(จานที่|แก้วที่|ชามที่|2ที่)` (the forced picks inside a set)
  don't count in tree/opportunity `paid_option_*`, `option_monthly.paid_*` or the
  Grab basket `paid_opt`; plain `picks` keep them. One constant:
  `SET_MANDATORY_RE` in tools/sales_tables.py.
- **Attach sources (Point 2026-10-07, supersedes the paid-option lever):**
  - **Free items excluded from every unit count** (attach, item units, pairs — not from
    money): A13 หมูกระจก ฟรีรีวิว, H4 น้ำแข็งฟรี, H2 แก้วเปล่า + any name containing
    'แลกฟรี'. One constant `FREE_ITEM_CODES` (+ name rule) in tools/sales_tables.py.
  - **Option → menu merge:** an option pick that is really a menu item counts AS that item
    (one row per item whatever the route). Map = `mp_clean.map_option_item(choice_key,
    item_code, qty_mult)` in transforms/001_mappings.sql: เกี๊ยวทอด→C6 · เต้าหู้ทอด→C5 ·
    เผือกทอด→C3 · เปาะเปี๊ยะ/ปอเปี๊ยะทอด→C4 · A9 ซุบบ๊วย→A9 · เพิ่มข้าว→A10 · เก๊กฮวย→E1 ·
    สไปรท์บ๊วย→E3 · ลำไยเนื้อทอง→E6 · ชาดำเย็น→E2 · ชามนม/ชานมเย็น→E7 · ชามะนาว→E5 ·
    กาแฟโบราณ→E4 · แดงโซดา→E8 · เขียวโซดา→**E9** (the POS item "E8 เขียวโซดา" carries
    item_code E9) · ไอศครีม 2ที่→F1 ×2. Groups covered (`OPTION_ITEM_GROUPS_RE`):
    เพิ่มความอร่อย, เพิ่มข้าว, ซุบบ๊วย, ของทอดไซส์ S (จานที่ n), เครื่องดื่ม แก้วที่ n,
    ไอศครีม 2ที่; unmapped choices there print a WARNING in sales_tables (never dropped silently).
  - **Source layers:** every unit has `source` = menu (order line) / option (paid upsell
    group: เพิ่มความอร่อย/เพิ่มข้าว/ซุบบ๊วย) / set (group matches SET_MANDATORY_RE).
    **รวม** = all three · **อัพเซล** = menu + option · **ในเซต** = set. Denominator stays
    main + set. Tree shows รวม with "(ในเซต x%)"; section 3 heatmap toggles รวม/อัพเซล/ในเซต;
    section 6 shows units per route + card "เซตช่วยเพิ่ม attach ไหม".
  - **Calculator uses อัพเซล** (units − set picks; price = that layer's ฿/unit) and **set is
    its own lever** (`set` = set units ÷ (main + set) × avg set price). The paid-option
    lever/column is removed (tree_daily.paid_option_* kept as deprecated zeros).
  - Option ฿ (paid_price × qty) also sits inside the parent line's gross — category ฿ is for
    unit prices only, never added to net.
  - Beverage tiers: water ≤ ฿20 inc VAT (was ≤ ฿15, which put H3 น้ำเปล่า ฿16.05 in "paid");
    paid ≤ ฿40; premium above.
  - Grab basket (9.6 / section 3 Grab) uses the same logic (free items out, option picks
    merged); `paid_opt` there is deprecated (0).
- **Section 2 — volume (as built 2026-10-07, Point):**
  - **(a) density heatmap** hour × branch (orders / customers / net, avg per trading day) has a unit
    toggle **จำนวน | % ของสาขา | % ของชั่วโมง**: % ของสาขา = each branch ROW sums to 100 (share of that
    branch's day by hour, colour scale per row); % ของชั่วโมง = each hour COLUMN sums to 100 across the
    selected branches (colour per column). State `S.heatU` ('n' / 'row' / 'col').
  - **Seat occupancy by hour (ที่นั่งถูกใช้ รายชั่วโมง)** replaces the old daypart "utilization" (pax × avg
    daypart dwell ÷ seats × daypart hours — unclear, Point: "lunch is always full with queues").
    Table `sales_web.occupancy_hourly(location_id, business_date, hour, seat_minutes, bills_open, turns,
    dwell_min_sum)` built by `build_occupancy` in tools/sales_tables.py (runs after pax_trust):
    dine-in, finalized, not voided, live branches, 120 days; one row per branch × trading day × hour,
    hours = sales_web.seats open_hour..close_hour clipped to 10–20 (Rama9 10–16).
    Per order: interval = [opened_at, closed_at] Bangkok (fallback table_sessions seated_at/left_at if
    either is missing); **split children** (`pos_sale_tabs.parentsaletabid > 0`, splittabname 'Split%')
    start at their MASTER tab's opened_at (they are opened at the moment of the split, ~1 min, and were
    ~40% of Silom's lunch bills — one bill per person paying); intervals < 3 or > 240 min dropped.
    pax_used = pax on trusted branch-days (sales_web.pax_trust) when 1–20, else main + set units (≥ 1).
    seat_minutes = Σ pax_used × overlap minutes with the hour; bills_open = orders overlapping the hour;
    turns = orders OPENED in the hour; dwell_min_sum = their dwell (avg dwell by hour = dwell_min_sum ÷ turns).
    Payload feed `occupancy` cols [loc, d, hour, seat_min, bills_open, turns, dwell_sum], 90 days.
    UI: occupancy % = Σ seat_min ÷ (seats × 60 × days in the filter); display capped at 100% (raw in the
    tooltip; > 100% = pax over-keying or table sharing); colours green ≥ 80 เต็ม / amber 50–79 / grey < 50 ว่าง;
    lunch 11–13 and dinner 17–20 column groups shaded like the tree table; second row per branch =
    **turns per seat per hour** (turns ÷ seats ÷ days); tooltip = raw %, avg guest-minutes/day, bills
    open/day, turns/day, seats, days. Seats: sales_web.seats (rama9 16, gaysorn 54, occ 66, all-seasons
    60, sathorn 64, silom 73). Grab leg of section 2 unchanged (demand curve, no seat cap).
  - **(d) dwell** shows avg dwell by the hour the bill opened (feed occupancy) above the existing daypart
    table (feed dwell).
  - Reality check (30 days to 2026-10-06, weekdays): 12:00 occupancy Silom 52%, Sathorn 44%, All Seasons
    63%, OCC 43%, Gaysorn 31%; 16:00 all ≤ 6%; dwell 33–38 min. POS bill time does NOT show the "full"
    Point sees: at the 5-minute peak (12:20–12:30) Silom averages ~45 guests in house (62% of 73 seats,
    best day 60 = 82%), Sathorn ~33 (52%). The hourly mean dilutes a ~30-minute rush, and seat % is not
    table %: solo diners at 2-seat tables (Silom 73 seats / 34 tables) make the room look full at ~60–70% seats. Rama9 can't be measured:
    ~90% of its dine-in bills are opened and closed within 3 min (counter-style keying) and are dropped.
    Report: docs/reports/2026-10-07-occupancy.md.
- Models = monthly insight layer, paired with weather + holidays + payday.
- Build order: section 1+3 (+ pax-trust check) first — calculator is the
  payoff; then 2 (needs seats per branch from Point); rest follow.
- Cut from v1: delivery funnel (no Grab exports), anything COGS (vendor cost
  data poisoned until ItemUnitConvert recalc), price elasticity (no price
  variation), customer clustering (only ~150 members).
- 2026-10-06 additions review (from the Aug analytics catalog): ADDED
  combo/set performance, ticket & party-size distributions, full menu
  engineering matrix. REJECTED: separate new-item launch scorecard (read it
  inside the per-menu drill-down instead), price-change response, realized
  price per item per channel (delivery markup vs commission).
- 2026-10-06 Grab: added as section 9 (Point). The "no Grab exports" cut
  above is superseded — Point exports Grab reports monthly.

## Grab data — rules and plumbing (section 9 + Grab legs of 1/2/3/4/6)
- **Grab money = Grab files ONLY. Never use POS prices for Grab** — Grab
  menu prices carry the platform markup, so POS line prices on Grab bills
  are wrong for revenue. ERS is used for Grab orders only for what it gets
  right: the REAL items, options, categories (and BOM → COGS).
- Source: monthly manual exports, one folder per month, in the vault at
  `Projects/Sales Web/Grab reports/` (Aug-26 present). Files are detected
  by HEADER SIGNATURE, not filename, by the existing engine
  `Grab reports/_analytics/grab_engine.py` (+ build_workbook.py /
  build_dashboard.py from an earlier one-off). Files: Sales (day×branch),
  Transaction_Store (one row per order: money split, gov-wallet
  adjustments, cancels+reason), Transfers_Store (payouts), Menu Sales
  (day×branch×item), Combo Sales, Peak Hour (day×branch×hour), Offers,
  Campaign / ads-hourly / Matched-Keywords (ads), MIWI ×3 (missing/wrong),
  Customer Review. Thai-header variants exist — match by header set.
- Load into a new schema (e.g. `mp_grab`) via a loader; Grab views refresh
  monthly, not every 30 min.
- Menu names come in 2 naming styles in Grab ("ก๋วยเตี๋ยว คอหมูย่างเตาถ่าน |
  Flat Egg…" vs "ก๋วยเตี๋ยวคอหมูย่าง Flat Egg…") → one-time name map to ERS
  item codes.
- **Order-level match Grab → ERS — by ORDER NUMBER ONLY (Point, 2026-10-06;
  re-checked against the DB).** Key = branch + Grab short order number
  (`GF-xxx` → xxx; Transaction_Store "รหัสคำสั่งซื้อสั้น"/"Short Order ID",
  Payment rows, dedup by booking ID) + SAME business day (Grab "วันที่สร้าง"
  date = POS tab start date). The day is required only because Grab numbers
  recycle: in Aug a number repeated up to 4×/month per branch but NEVER twice
  on the same day. No time-proximity matching.
  ERS side = `mp_raw.pos_sale_tabs` (join mp_clean.locations on branchid):
  number = `refdeliveryorder` matching `^(GF-?)?\d{3}$` (where staff type it
  at every branch: "213", "309", "GF322"), else `takehomename` same pattern
  ("GF309", "438"). NEVER use `tabname` — GF01/GB-1/Grab-1/TakeAway1 are
  queue slots recycled all day. Checked and empty: tab remark, sales
  custrefnum/notes/refnum, payment refnum/note, item remark.
  If one branch+number+day has several tabs (re-key / voided first try),
  take the finalized, non-voided bill; >1 valid bill = flag ambiguous
  (1 case in Aug). Matched bill id = `<branchid>-<saleid>` (e.g. 7-1898)
  → mp_metrics.bill_items / bill_item_options.
- Aug-26 coverage: 720 / 1,624 Grab orders (44%) — Gaysorn 63%, Silom 46%,
  Rama9 24% (Grab handled on FoodStory first, re-keyed later), OCC 10%
  (staff rarely type the number). ERS held only 740 numbered tabs, so the
  ceiling is staff typing the number, not the method. Show coverage per
  branch on the page; raising it is an ops ask (type the Grab number in
  the delivery-ref field on every order).
- What the match unlocks: per-order Grab baskets → real attach rates, options
  & topping choices, combos, and COGS per Grab order — on matched orders,
  labelled with coverage. By-product: keying lag (Grab created → POS keyed;
  Aug: Gaysorn/Silom median ≈2 min, tail >30 min; Rama9 ≈100 min).
- (superseded by the "channel switch" block below)

## Grab — channel switch + section 9 (locked with Point 2026-10-06)
**Channel switch หน้าร้าน / Grab / รวม on every section** (framework: delivery
is a channel inside the same tree) — side-by-side gaps ("bev attach 34% in
store vs 12% on Grab") are themselves opportunities. Grab legs:
| Section | Grab version | Source | Coverage |
|---|---|---|---|
| 1 Tree | orders × avg ticket per daypart; billing layer = discounts, commission, ads | Transaction_Store (per-order time + money) | 100% |
| 2 Volume | demand levers (no seat cap): hour curve, dead hours, baseline vs actual, ad/promo effect on volume | Transaction_Store, Peak Hour | 100% |
| 3 Ticket | attach = side/bev/dessert units ÷ main units; opportunity calculator at GRAB prices | Menu Sales (every item sold) — no matching needed | 100% |
| 4 Options | choice shares, toppings | ERS-matched orders | target 100% |
| 5 Members | NOT for Grab (no customer IDs) | – | – |
| 6 Menu & promo | Grab menu mix, menu matrix at Grab price, combos, campaign payback | Menu Sales, Combo Sales, Offers | 100% |
| 7 Insights | Grab daily series in calendar + RAIN model (rain→delivery likely strongest) | Grab daily | 100% |
| 8 Digest | Grab gaps compete in the weekly top-3 | – | – |

**Section 9 — Grab-only insights (each block = a decision):**
- **9.1 Profit per order** — gross → our discounts → commission → marketing
  fee → payout → minus food cost (BOM) → profit per Grab order, per branch
  and vs dine-in. Decision: Grab menu pricing, which branches to push.
  (Aug-26: ≈68% of gross reached us BEFORE food cost.) DECIDED (Point
  2026-10-06): food cost = REAL BOM of the ERS-matched order (items +
  options), never a standard %. Depends on the vendor BOM-cost fix
  (ItemUnitConvert recalc) — until then show the waterfall to payout and
  mark food cost "รอข้อมูลต้นทุน".
- **9.2 Ads** — branded vs generic keywords: spend, cost/order, orders per
  ฿100; funnel impression → menu visit → add-to-cart → order; ad spend by
  hour vs real order peaks. Decision: re-allocate budget. (Aug: Grab credits
  ads with 61% of Grab sales, mostly brand searches like "mama pook" —
  customers already looking for us.)
- **9.3 Promotions** — per campaign: spend, orders per ฿, campaign days vs
  non-campaign days. Decision: which Grab campaigns to keep joining. (Aug:
  "ราคาเดียว" ฿19k of ฿28k merchant spend, ≈฿40/order.)
- **9.4 Lost orders** — cancels by reason (sold out / waited too long),
  estimated ฿ lost, by hour/item. Decision: stock-out fixes at peak.
- **9.5 Ranking risk** — rating trend, unanswered reviews (Aug: 25 of 26),
  review themes, missing/wrong items as an ALERT only (Aug: 7 cases).
  Decision: reply discipline + quality fixes that protect Grab ranking.
- **9.6 Grab vs in-store comparison** — demand timing (Grab peaks 10-11h,
  earlier than dine-in lunch), basket shape, top items, attach gaps — the
  view that says what to change on the Grab menu.
- **Design assumption (Point 2026-10-06): Grab→ERS matching WILL reach 100%**
  — Point is fixing staff keying (type the Grab number in the delivery-ref
  field every order). Build every matched-order view (4 options, 9.1 food
  cost, basket/attach per order, 9.6) as full-coverage views, not samples.
  Until then a footer strip shows match coverage per branch so partial
  months aren't misread; Aug-26 baseline 44%.
- Deliberately OUT: payouts/transfers (bank recon → Finance/PEAK), video ad
  metrics, keyword long tail, ไทยช่วยไทย wallet adjustments.


## Data plumbing — everything already exists
All from Supabase `xnmzlqqudizckjhchhpn` (read creds: ~/mamapook-data/.env,
role mamapook_pipeline via pooler; see Project Data Backbone.md):
- `mp_metrics.bills` — channel, pax(guest_count), timestamps (UTC! +7h for
  display), discounts, payment, member phone (customer_id), split_bill
- `mp_metrics.bill_items` — qty, category (main_category = tree mapping:
  main/bev/side/soup/dessert…), prices, derived promo tag
- `mp_metrics.bill_item_options` — modifier_group, choice, choice_qty,
  paid_price_thb, is_paid_option
- `mp_clean.orders` / `order_lines` — opened_at/closed_at (dwell!), pax
- `mp_clean.table_sessions` — table turns
- `mp_metrics.daily_branch_sales` — revenue reconciliation anchor
- Reference inputs Point must supply once: SEATS per branch (+ opening hours
  per daypart; dwell comes from data, not assumption)
- External to add: Bangkok rain (free API, store daily), Thai holiday table

## Architecture — copy the proven dashboard_app pattern exactly
(see dashboard_app/dashboard.md for the battle-tested version of each piece)
1. SQL views/tables in a new `sales` schema (or dashboard schema) built by a
   `tools/sales_tables.py` run from run_local.sh each 30-min cycle
2. Payload baked into `dashboard.app_cache`-style row (id=2) by the build
   step — NEVER assemble big payloads inside the edge worker (546 lesson)
3. Edge function `sales-data` (copy dashboard-data v5: serve cached text
   verbatim; repo copy in app/edge/)
4. Static SPA on Cloudflare Pages — either same mamapook-dashboard project
   (new page behind same login: users in Pages env USERS_JSON, currently
   user mamapook) or separate project `mamapook-sales`; Point to choose
5. Deploy via app/deploy.sh (copy dashboard_app/deploy.sh; wrangler
   pages deploy --branch main)
6. Freshness stamp + offline-fallback warning from day one (red "ข้อมูลเก่า"
   badge — silent-fallback trap already bitten us twice)
7. Excel formulas reference: Mama_Pook_Sales_Driver_Tree_v2.xlsx (Point has
   it); the skill file documents every formula — tree math MUST follow it
   (weighted averages for rate totals, two layers off-premise, discounts in
   billing layer only)

## Gotchas a new session must know
- Timestamps in mp_raw/mp_clean are UTC — add 7h for any display
- Channel values: dine_in/take_away/delivery/pickup (bills view maps names)
- Rama9 closed Mondays; weekend ≈ half volume (office branches); branch
  may miss a stock-card day at HQ (All Seasons 2026-10-05) — sales data
  unaffected but joins to stock tables must tolerate gaps
- Split bills: use mp_metrics views (dedup already solved); never sum money
  from bill_item_options (already inside line totals)
- bowls = bill_items where main_category='main', sum(quantity)
- Language: UI Thai (staff-facing), talk to Point in English
- Update THIS file + plan-folder copy (the project's own folder in the vault/
  Project Sales Web.md) same session as any change; Project Data Backbone.md links here

## Open items for Point
- ~~Seats per branch~~ (done 2026-10-07, migrations/011) — Rama9 dine-in keying (bills opened+closed at once) blocks its occupancy
- Same-login vs separate site decision
- First milestone sign-off: sections 1+3 + pax-trust check

## BUILT (2026-10-06) — status
Live: https://mamapook-sales.pages.dev (no auth, v1). Data: schema sales_web (tools/sales_tables.py every 30 min;
tools/grab_load.py + tools/weather_pull.py nightly), payload sales_web.app_cache id=1 (app/build/build.py),
edge function sales-data (app/edge/sales-data.ts), SPA app/site/index.html, deploy app/deploy.sh
(Cloudflare Pages project mamapook-sales, --branch main). Reference inputs Point owns: sales_web.seats (filled 2026-10-07, see migrations/011),
sales_web.holidays. Open: BOM food cost for Grab 9.1 waits on vendor cost fix; LINE digest push not wired (copy-paste v1).
Known issues:
- A stray Worker `mamapook-sales.itthichet-a.workers.dev` was created by a wrangler bug (project create without --force); Point to delete it.
- `sales_web.holidays` contains 2026-01-02 (bank-only holiday) — Point to confirm or delete.
- Grab exports: export FULL months only (two files for the same branch-month overwrite each other).
- Win-back list shows hashed member keys only (v1 limit).

Final-review rulings (2026-10-06):
- In-store = dine_in + take_away. POS `delivery` tabs are Grab orders at POS markup prices: kept in the
  sales_web tables (ERS truth) but excluded from every in-store number in the UI and from the opportunity
  calculator; รวม = in-store + Grab, Grab money from grab.* feeds only (Grab files), never POS delivery.
- Grab 9.1 waterfall = ยอดขาย → ส่วนลดร้าน → ค่าคอมมิชชัน → ค่าการตลาด → โฆษณา → ปรับปรุงรายได้ → รับจริง
  (payout + ads + adjustment rows, feed grab.fees) → % ที่ได้รับ (Aug 2026 group: 68.3%). Rows with status
  เสร็จสมบูรณ์ are not paid out yet (fees incomplete) — flagged as a note.
- Grab 9.4 lost orders read feed grab.cancels (category 'other', status ยกเลิก); ฿ lost is an estimate =
  branch-month avg order value from grab_daily.
- Opportunity drink price is per tier (tree_daily.bev_paid_thb / bev_premium_thb); water baht excluded.
- party_size: pax > 20 on one order = keying junk -> bucket 'n/a'.
- grab_match only links finalized, non-voided POS tabs (no match rather than a voided one).
- Every Backbone session runs `set time zone 'Asia/Bangkok'` (shared/backbone.py), so current_date / now()
  are Bangkok. Watermarks (mp_ops.watermarks) are normalised to UTC in get/set_watermark so loader
  behaviour is unchanged.
- app/site/data/data.json is build output: gitignored, rebuilt and shipped by deploy.sh as the public
  fallback copy.
