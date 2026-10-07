# Sales Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Mama Pook Sales Web — the 9-section sales-growth dashboard specified in `sales_app/sales-web.md` — as live tables in a new Supabase schema `sales_web`, a baked JSON payload, a Supabase edge function `sales-data`, and a static Thai-UI SPA on Cloudflare Pages (`mamapook-sales`), no auth.

**Architecture:** Copy the proven `dashboard_app` pattern exactly: (1) `tools/sales_tables.py` materializes every section's aggregates into `sales_web.*` each 30-min pipeline cycle; (2) `tools/grab_load.py` loads Point's monthly Grab CSV exports (detected by header signature) into `sales_web.grab_*`; (3) `sales_app/build/build.py` bakes one JSON payload into `sales_web.app_cache` (id=1) + offline fallback `sales_app/site/data/data.json`; (4) edge function `sales-data` serves the cached text verbatim; (5) `sales_app/site/index.html` (vanilla JS, Thai UI) renders 9 sections with global filters + channel switch. Never assemble payloads inside the edge worker (HTTP 546 lesson).

**Tech Stack:** Python 3 (repo venv: psycopg only — **no pandas**, use stdlib `csv`), Postgres (Supabase project `xnmzlqqudizckjhchhpn`), Deno edge function, vanilla HTML/JS/CSS, Cloudflare Pages via `npx wrangler` (logged in as itthichet.a@gmail.com), Open-Meteo API (free, no key) for rain.

## Global Constraints

- Repo: `~/mamapook-data` (GitHub Itthicheta/Mamapook-data). Python entrypoints run as `cd ~/mamapook-data && ./venv/bin/python -m tools.<name>`; DB via `from shared.env import load; from shared.backbone import Backbone; bb = Backbone(load())` (psycopg connection at `bb.conn`, autocommit OFF — call `bb.conn.commit()`).
- New schema name: **`sales_web`** (Point asked for "sales-xxxx"; Postgres identifiers can't carry a hyphen unquoted). Owned by `postgres`, full grants to `mamapook_pipeline` (same pattern as `migrations/002_pipeline_role.sql`). `public` belongs to the planner tool — never touch.
- Read sources: `mp_clean.orders` (filter `is_finalized and not is_voided`), `mp_clean.order_lines` (filter `is_active and not is_special_line`), `mp_clean.sold_options`, `mp_clean.table_sessions`, `mp_clean.locations` (filter `is_confirmed_live and location_type='branch'`), `mp_clean.items`, `mp_clean.item_bom_cost`. Timestamps are UTC → `at time zone 'Asia/Bangkok'` for hours/dayparts.
- Channel values in mp_clean: `dine_in` / `take_away` / `delivery`. Tree categories (`order_lines.tree_category`): main / side / beverage / topping / dessert / other.
- Daypart (same cut as `mp_metrics.driver_tree_daily`): Bangkok hour of `closed_at` 11–13 = `lunch`, 17–20 = `dinner`, else `non_peak`.
- Beverage tiers by `unit_price_inc_vat_thb`: `water` ≤ 15 (น้ำเปล่า/น้ำแข็งฟรี/แก้วเปล่า), `paid` 16–40 (soft drinks), `premium` ≥ 41 (น้ำชง E-series).
- Window: tables hold `business_date >= current_date - 120`. Today's partial day is included but the payload carries `data_through` = yesterday and a `today_partial` flag.
- **Grab money comes ONLY from Grab files** (POS prices on Grab bills carry the platform markup). ERS is joined to Grab orders only for items/options/BOM. Match key: branch + Grab short order number (3 digits from `GF-xxx`) + SAME business day; ERS number from `mp_raw.pos_sale_tabs.refdeliveryorder` matching `^(GF-?)?\d{3}$`, else `takehomename` same pattern; **never `tabname`**. Several tabs same key → take finalized non-voided; >1 valid → `ambiguous=true`.
- Grab export folder (read-only, OneDrive): `/Users/point/Library/CloudStorage/OneDrive-Personal/Personal/Second brain/Mama Pook/Projects/Sales Web/Grab reports/` — any sub-folder or the root; files detected by header set, not filename.
- UI language Thai (staff-facing); code comments and commit messages English. No secrets in git (edge token lives in the edge function source + page, same as dashboard — acceptable for no-auth v1; DB URL stays in Supabase secrets).
- Cloudflare Pages project: **`mamapook-sales`**, production branch `main`, deploy with `--branch main`. Free tier 500 deploys/month → deploy on design change + nightly only.
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Docs: `sales_app/sales-web.md` (self-contained spec) gets a "BUILT" status block at the end (Task 8); twin copy in the vault `Projects/Sales Web/sales-web.md`.

---

## File map

| Path | Responsibility |
|---|---|
| `migrations/010_sales_web_schema.sql` | create schema `sales_web`, grants, reference tables (`seats`, `holidays`), `app_cache`, `app_status` |
| `tools/sales_tables.py` | rebuild every computed `sales_web.*` table from mp_clean (one engine) |
| `tools/grab_load.py` | load Grab CSV exports → `sales_web.grab_*` (idempotent, by header signature) |
| `tools/weather_pull.py` | Open-Meteo daily rain → `sales_web.weather_daily` |
| `sales_app/build/build.py` | bake payload → `sales_web.app_cache` + `site/data/data.json` |
| `sales_app/edge/sales-data.ts` | repo copy of the Supabase edge function (deployed via MCP) |
| `sales_app/site/index.html` | the SPA |
| `sales_app/deploy.sh` | build + wrangler pages deploy |
| `pipelines/pos_ers/run_local.sh` | add steps `sales_tables`, `sales_cache`, nightly `grab_load`+`weather`+deploy |
| `sales_app/sales-web.md` | spec + BUILT status |

Payload contract (what `build.py` emits and `index.html` consumes) is defined in Task 5 and is the interface between the data tasks and the UI task.

---

### Task 1: Schema, reference tables, migration

**Files:**
- Create: `migrations/010_sales_web_schema.sql`

**Interfaces:**
- Produces: schema `sales_web`; tables `sales_web.seats(location_id text pk, seats int, open_hour int, close_hour int)`, `sales_web.holidays(d date pk, name_th text)`, `sales_web.app_cache(id int pk, payload text, updated_at timestamptz)`, `sales_web.app_status(id int pk, last_build timestamptz, data_through date)`.

- [ ] **Step 1: Write the migration**

```sql
-- migrations/010_sales_web_schema.sql — Sales Web (sales_app/sales-web.md), 2026-10-06
create schema if not exists sales_web;
grant usage, create on schema sales_web to mamapook_pipeline;
alter default privileges in schema sales_web grant all on tables to mamapook_pipeline;
alter default privileges in schema sales_web grant all on sequences to mamapook_pipeline;

-- Reference input Point maintains by hand (seats per branch for utilization)
create table if not exists sales_web.seats (
  location_id text primary key,
  seats integer,            -- NULL until Point fills it; utilization shows "รอข้อมูลที่นั่ง"
  open_hour integer not null default 10,
  close_hour integer not null default 20
);
insert into sales_web.seats (location_id, seats, open_hour, close_hour)
select location_id, null, coalesce(open_hour,10), coalesce(close_hour,20)
from mp_clean.locations where is_confirmed_live and location_type='branch'
on conflict (location_id) do nothing;

-- Thai public holidays (Bank of Thailand list). Point/implementer verifies dates.
create table if not exists sales_web.holidays (d date primary key, name_th text not null);

create table if not exists sales_web.app_cache (
  id integer primary key, payload text, updated_at timestamptz default now());
create table if not exists sales_web.app_status (
  id integer primary key, last_build timestamptz, data_through date);
insert into sales_web.app_status (id) values (1) on conflict do nothing;

grant all on all tables in schema sales_web to mamapook_pipeline;
```

- [ ] **Step 2: Apply via Supabase MCP** — `apply_migration(project_id=xnmzlqqudizckjhchhpn, name="sales_web_schema", query=<file contents>)`. Then verify:

Run (MCP execute_sql): `select has_schema_privilege('mamapook_pipeline','sales_web','CREATE'), (select count(*) from sales_web.seats);`
Expected: `true`, `6`.

- [ ] **Step 3: Seed holidays** — WebSearch "วันหยุดราชการ 2569 ธนาคารแห่งประเทศไทย" and insert the 2026 list (New Year 1 Jan, Makha Bucha, Chakri 6 Apr, Songkran 13–15 Apr, Labour 1 May, Coronation 4 May, Visakha Bucha, Queen Suthida 3 Jun, Asanha Bucha, King's Birthday 28 Jul, Mother's Day 12 Aug, King Bhumibol Memorial 13 Oct, Chulalongkorn 23 Oct, Father's Day 5 Dec, Constitution 10 Dec, New Year's Eve 31 Dec, plus any substitution days). Insert with `insert into sales_web.holidays values ('2026-01-01','วันขึ้นปีใหม่'),... on conflict do nothing;`

Run: `select count(*) from sales_web.holidays where d between '2026-01-01' and '2026-12-31';`
Expected: ≥ 16.

- [ ] **Step 4: Commit**

```bash
cd ~/mamapook-data && git add migrations/010_sales_web_schema.sql && git commit -m "sales_web: schema, grants, seats/holidays/app_cache reference tables

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `tools/sales_tables.py` — the computed tables

**Files:**
- Create: `tools/sales_tables.py`

**Interfaces:**
- Consumes: `sales_web.seats`, `sales_web.holidays`, `sales_web.weather_daily` (Task 3, may be empty), `sales_web.grab_orders` (Task 4, may be empty).
- Produces (all `create table if not exists` + `truncate` + insert, in one transaction, committed at end): the tables below. Every table has `location_id text` (= mp_clean location_id) where branch-level.

```
tree_daily        (location_id, business_date, daypart, channel, orders, pax_keyed, main_units,
                   bev_units, bev_water_units, bev_paid_units, bev_premium_units, side_units,
                   topping_units, dessert_units, paid_option_picks, paid_option_thb,
                   gross_thb, discount_thb, net_thb, main_thb, bev_thb, side_thb, topping_thb, dessert_thb)
hourly            (location_id, business_date, hour, channel, orders, pax_keyed, net_thb, main_units)
dwell             (location_id, business_date, daypart, sessions, avg_dwell_min, avg_party)
pax_trust         (location_id, business_date, bowls, keyed_pax, ratio, trusted)
item_daily        (location_id, business_date, channel, item_code, item_name, main_category,
                   sub_category, bev_tier, units, thb, bills, avg_price)
option_monthly    (location_id, ym, channel, modifier_group, choice_th, picks, paid_picks, paid_thb)
option_by_dish    (location_id, ym, channel, dish_th, modifier_group, choice_th, picks)
topping_route     (location_id, ym, channel, topping, as_option_units, as_option_thb,
                   as_menu_units, as_menu_thb)
pair_lift         (location_id, item_a, item_b, bills_ab, bills_a, bills_b, total_bills, lift)
party_size        (location_id, ym, channel, daypart, pax_bucket, bills, net_thb, main_units)
ticket_hist       (location_id, ym, channel, thb_bucket, bills)
members_monthly   (location_id, ym, bills, member_bills, distinct_members)
member_visits     (member_key, last_location_id, visits, first_date, last_date, days_since, avg_thb)
promo_daily       (location_id, business_date, channel, promo, bills, main_units, net_thb)
set_monthly       (location_id, ym, channel, set_name, units, thb)
calendar_daily    (location_id, business_date, channel, orders, net_thb, dow, dom, wom,
                   is_holiday, rain_mm, expected_thb, gap_pct)
opportunity       (location_id, channel, lever, current_rate, best_location_id, best_rate,
                   main_units_30d, unit_price_thb, uplift_thb_month)
grab_match        (booking_id, location_id, business_date, gf_no, bill_id, ambiguous)
```

- [ ] **Step 1: Write the module skeleton + helpers**

```python
"""Materialize every Sales Web table into schema sales_web each pipeline cycle.
Spec: sales_app/sales-web.md. One engine — build.py only READS these tables.
Run: ./venv/bin/python -m tools.sales_tables   (optional --check prints row counts)
"""
import datetime as dt, sys
from shared.env import load
from shared.backbone import Backbone

WINDOW_DAYS = 120
bb = Backbone(load())
BKK = "at time zone 'Asia/Bangkok'"

# Reused CTE: finalized orders of live branches inside the window, with Bangkok
# hour/daypart/ym already derived. Every section query starts from this.
ORDERS_CTE = f"""
with o as (
  select o.order_id, o.location_id, o.business_date, o.channel, o.pax,
         o.total_thb, o.discount_thb, o.net_ex_vat_thb, o.customerid,
         extract(hour from o.closed_at {BKK})::int as hr,
         case when extract(hour from o.closed_at {BKK}) between 11 and 13 then 'lunch'
              when extract(hour from o.closed_at {BKK}) between 17 and 20 then 'dinner'
              else 'non_peak' end as daypart,
         to_char(o.business_date,'YYYY-MM') as ym
  from mp_clean.orders o
  join mp_clean.locations l on l.location_id = o.location_id
  where o.is_finalized and not o.is_voided
    and l.is_confirmed_live and l.location_type = 'branch'
    and o.business_date >= current_date - {WINDOW_DAYS}
),
li as (
  select li.order_id, li.line_id, li.itemid, li.name_th, li.tree_category,
         li.qty, li.gross_inc_vat_thb, li.unit_price_inc_vat_thb, li.discount_thb,
         case when li.tree_category <> 'beverage' then null
              when li.unit_price_inc_vat_thb <= 15 then 'water'
              when li.unit_price_inc_vat_thb <= 40 then 'paid' else 'premium' end as bev_tier
  from mp_clean.order_lines li
  join o on o.order_id = li.order_id
  where li.is_active and not li.is_special_line
)
"""

def rebuild(cur, table: str, ddl_cols: str, select_sql: str):
    cur.execute(f"create table if not exists sales_web.{table} ({ddl_cols})")
    cur.execute(f"truncate sales_web.{table}")
    cur.execute(f"insert into sales_web.{table} {select_sql}")
    cur.execute(f"select count(*) from sales_web.{table}")
    n = cur.fetchone()[0]
    print(f"  sales_web.{table}: {n} rows")
    return n
```

- [ ] **Step 2: tree_daily, hourly, dwell, pax_trust**

```python
def build_tree(cur):
    rebuild(cur, "tree_daily",
        """location_id text, business_date date, daypart text, channel text, orders int,
           pax_keyed numeric, main_units numeric, bev_units numeric, bev_water_units numeric,
           bev_paid_units numeric, bev_premium_units numeric, side_units numeric,
           topping_units numeric, dessert_units numeric, paid_option_picks int,
           paid_option_thb numeric, gross_thb numeric, discount_thb numeric, net_thb numeric,
           main_thb numeric, bev_thb numeric, side_thb numeric, topping_thb numeric, dessert_thb numeric""",
        ORDERS_CTE + """
        , opt as (
          select so.order_id, count(*) filter (where so.is_paid_option) picks,
                 coalesce(sum(so.paid_price_thb * so.choice_qty) filter (where so.is_paid_option),0) thb
          from mp_clean.sold_options so join o on o.order_id = so.order_id group by 1)
        select o.location_id, o.business_date, o.daypart, o.channel,
               count(distinct o.order_id),
               sum(distinct_pax.pax),
               coalesce(sum(li.qty) filter (where li.tree_category='main'),0),
               coalesce(sum(li.qty) filter (where li.tree_category='beverage'),0),
               coalesce(sum(li.qty) filter (where li.bev_tier='water'),0),
               coalesce(sum(li.qty) filter (where li.bev_tier='paid'),0),
               coalesce(sum(li.qty) filter (where li.bev_tier='premium'),0),
               coalesce(sum(li.qty) filter (where li.tree_category='side'),0),
               coalesce(sum(li.qty) filter (where li.tree_category='topping'),0),
               coalesce(sum(li.qty) filter (where li.tree_category='dessert'),0),
               coalesce(sum(distinct_pax.picks),0), coalesce(sum(distinct_pax.opt_thb),0),
               sum(distinct_pax.total_thb), sum(distinct_pax.discount_thb),
               sum(distinct_pax.total_thb) - sum(distinct_pax.discount_thb),
               coalesce(sum(li.gross_inc_vat_thb) filter (where li.tree_category='main'),0),
               coalesce(sum(li.gross_inc_vat_thb) filter (where li.tree_category='beverage'),0),
               coalesce(sum(li.gross_inc_vat_thb) filter (where li.tree_category='side'),0),
               coalesce(sum(li.gross_inc_vat_thb) filter (where li.tree_category='topping'),0),
               coalesce(sum(li.gross_inc_vat_thb) filter (where li.tree_category='dessert'),0)
        from o
        left join li on li.order_id = o.order_id
        -- per-order scalars must not be multiplied by line count: pre-aggregate per order
        left join lateral (
          select o.pax, o.total_thb, o.discount_thb, opt.picks, opt.thb as opt_thb
          from opt where opt.order_id = o.order_id
          union all select o.pax, o.total_thb, o.discount_thb, 0, 0 where not exists (select 1 from opt where opt.order_id=o.order_id)
        ) distinct_pax on true
        group by 1,2,3,4""")
```

> Implementer note: the lateral above still multiplies per-order scalars by the number of lines. Replace with the two-step pattern: build a temp table `ord_scalar(order_id, pax, total_thb, discount_thb, picks, opt_thb)` first, aggregate line sums into `line_agg(order_id, …)`, then join the two per order and `group by location_id, business_date, daypart, channel`. Verify: `select sum(net_thb) from sales_web.tree_daily where business_date='2026-09-15'` must equal `select sum(total_thb-discount_thb) from mp_clean.orders where business_date='2026-09-15' and is_finalized and not is_voided and location_id in (live branches)`.

```python
def build_hourly(cur):
    rebuild(cur, "hourly",
        "location_id text, business_date date, hour int, channel text, orders int, pax_keyed numeric, net_thb numeric, main_units numeric",
        ORDERS_CTE + """
        select o.location_id, o.business_date, o.hr, o.channel, count(*), sum(o.pax),
               sum(o.total_thb - o.discount_thb),
               coalesce((select sum(li.qty) from li where li.order_id in (select order_id from o o2 where o2.location_id=o.location_id and o2.business_date=o.business_date and o2.hr=o.hr and o2.channel=o.channel) and li.tree_category='main'),0)
        from o group by 1,2,3,4""")

def build_dwell(cur):
    rebuild(cur, "dwell",
        "location_id text, business_date date, daypart text, sessions int, avg_dwell_min numeric, avg_party numeric",
        f"""select ts.location_id, o.business_date, o.daypart, count(*),
                   round(avg(ts.dwell_minutes),1), round(avg(ts.pax),2)
            from mp_clean.table_sessions ts
            join (select order_id, business_date,
                         case when extract(hour from closed_at {BKK}) between 11 and 13 then 'lunch'
                              when extract(hour from closed_at {BKK}) between 17 and 20 then 'dinner'
                              else 'non_peak' end daypart
                  from mp_clean.orders where is_finalized and not is_voided
                    and business_date >= current_date - {WINDOW_DAYS}) o on o.order_id = ts.order_id
            where not ts.is_cancelled and ts.dwell_minutes between 3 and 240
            group by 1,2,3""")

def build_pax_trust(cur):
    rebuild(cur, "pax_trust",
        "location_id text, business_date date, bowls numeric, keyed_pax numeric, ratio numeric, trusted boolean",
        ORDERS_CTE + """
        select o.location_id, o.business_date,
               coalesce(sum(li.qty) filter (where li.tree_category='main'),0) bowls,
               (select sum(pax) from o o2 where o2.location_id=o.location_id and o2.business_date=o.business_date and o2.channel='dine_in') keyed,
               null::numeric, null::boolean
        from o left join li on li.order_id=o.order_id
        where o.channel='dine_in' group by 1,2""")
    cur.execute("""update sales_web.pax_trust set ratio = case when keyed_pax>0 then round(bowls/keyed_pax,2) end,
                   trusted = case when keyed_pax>0 and bowls/keyed_pax between 0.9 and 1.3 then true else false end""")
```

- [ ] **Step 3: item_daily, option tables, topping_route, pair_lift**

```python
def build_items(cur):
    rebuild(cur, "item_daily",
        """location_id text, business_date date, channel text, item_code bigint, item_name text,
           main_category text, sub_category text, bev_tier text, units numeric, thb numeric, bills int, avg_price numeric""",
        ORDERS_CTE + """
        select o.location_id, o.business_date, o.channel, li.itemid, li.name_th,
               coalesce(li.tree_category,'other'), it.vendor_category, li.bev_tier,
               sum(li.qty), sum(li.gross_inc_vat_thb), count(distinct li.order_id),
               round(sum(li.gross_inc_vat_thb)/nullif(sum(li.qty),0),2)
        from li join o on o.order_id=li.order_id
        left join mp_clean.items it on it.itemid = li.itemid
        group by 1,2,3,4,5,6,7,8""")

def build_options(cur):
    rebuild(cur, "option_monthly",
        "location_id text, ym text, channel text, modifier_group text, choice_th text, picks int, paid_picks int, paid_thb numeric",
        ORDERS_CTE + """
        select o.location_id, o.ym, o.channel, so.modifier_group, so.choice_th,
               count(*), count(*) filter (where so.is_paid_option),
               coalesce(sum(so.paid_price_thb*so.choice_qty) filter (where so.is_paid_option),0)
        from mp_clean.sold_options so join o on o.order_id = so.order_id
        group by 1,2,3,4,5""")
    rebuild(cur, "option_by_dish",
        "location_id text, ym text, channel text, dish_th text, modifier_group text, choice_th text, picks int",
        ORDERS_CTE + """
        select o.location_id, o.ym, o.channel, so.dish_th, so.modifier_group, so.choice_th, count(*)
        from mp_clean.sold_options so join o on o.order_id = so.order_id
        group by 1,2,3,4,5,6""")
    # Topping route: same topping sold as a PAID OPTION vs as a STANDALONE menu line.
    # Normalise names by stripping the menu prefix (B1/B2…) and whitespace so
    # "คอหมูย่าง" (option) and "B2 คอหมูย่าง" (menu) collapse to one key.
    rebuild(cur, "topping_route",
        "location_id text, ym text, channel text, topping text, as_option_units numeric, as_option_thb numeric, as_menu_units numeric, as_menu_thb numeric",
        ORDERS_CTE + r"""
        , opt as (
          select o.location_id, o.ym, o.channel,
                 regexp_replace(regexp_replace(so.choice_th,'^[A-Z]\d+\s*',''),'\s+','','g') k,
                 sum(so.choice_qty) u, sum(so.paid_price_thb*so.choice_qty) t
          from mp_clean.sold_options so join o on o.order_id=so.order_id
          where so.is_paid_option group by 1,2,3,4),
        menu as (
          select o.location_id, o.ym, o.channel,
                 regexp_replace(regexp_replace(li.name_th,'^[A-Z]\d+\s*',''),'\s+','','g') k,
                 sum(li.qty) u, sum(li.gross_inc_vat_thb) t
          from li join o on o.order_id=li.order_id where li.tree_category='topping' group by 1,2,3,4)
        select coalesce(opt.location_id,menu.location_id), coalesce(opt.ym,menu.ym),
               coalesce(opt.channel,menu.channel), coalesce(opt.k,menu.k),
               coalesce(opt.u,0), coalesce(opt.t,0), coalesce(menu.u,0), coalesce(menu.t,0)
        from opt full join menu on menu.location_id=opt.location_id and menu.ym=opt.ym
                               and menu.channel=opt.channel and menu.k=opt.k
        where coalesce(opt.u,0)+coalesce(menu.u,0) > 0""")

def build_pairs(cur):
    # Market-basket lift over the last 90 days, per branch, items with >= 30 bills.
    rebuild(cur, "pair_lift",
        "location_id text, item_a text, item_b text, bills_ab int, bills_a int, bills_b int, total_bills int, lift numeric",
        ORDERS_CTE + """
        , bi as (select distinct o.location_id, o.order_id, li.name_th item
                 from li join o on o.order_id=li.order_id
                 where o.business_date >= current_date - 90 and li.tree_category in ('main','side','beverage','topping','dessert')),
        tot as (select location_id, count(distinct order_id) n from bi group by 1),
        cnt as (select location_id, item, count(*) n from bi group by 1,2 having count(*) >= 30)
        select a.location_id, a.item, b.item, count(*), ca.n, cb.n, t.n,
               round((count(*)::numeric * t.n) / (ca.n * cb.n), 2)
        from bi a join bi b on b.location_id=a.location_id and b.order_id=a.order_id and a.item < b.item
        join cnt ca on ca.location_id=a.location_id and ca.item=a.item
        join cnt cb on cb.location_id=b.location_id and cb.item=b.item
        join tot t on t.location_id=a.location_id
        group by a.location_id, a.item, b.item, ca.n, cb.n, t.n
        having count(*) >= 10""")
```

- [ ] **Step 4: party_size, ticket_hist, members, promo, sets**

```python
def build_distributions(cur):
    rebuild(cur, "party_size",
        "location_id text, ym text, channel text, daypart text, pax_bucket text, bills int, net_thb numeric, main_units numeric",
        ORDERS_CTE + """
        , mu as (select order_id, sum(qty) u from li where tree_category='main' group by 1)
        select o.location_id, o.ym, o.channel, o.daypart,
               case when o.channel<>'dine_in' then 'n/a' when o.pax<=1 then '1' when o.pax=2 then '2'
                    when o.pax<=4 then '3-4' else '5+' end,
               count(*), sum(o.total_thb-o.discount_thb), coalesce(sum(mu.u),0)
        from o left join mu on mu.order_id=o.order_id group by 1,2,3,4,5""")
    rebuild(cur, "ticket_hist",
        "location_id text, ym text, channel text, thb_bucket text, bills int",
        ORDERS_CTE + """
        select o.location_id, o.ym, o.channel,
               case when o.total_thb<150 then '<150' when o.total_thb<250 then '150-249'
                    when o.total_thb<400 then '250-399' when o.total_thb<600 then '400-599' else '600+' end,
               count(*) from o group by 1,2,3,4""")

def build_members(cur):
    rebuild(cur, "members_monthly",
        "location_id text, ym text, bills int, member_bills int, distinct_members int",
        ORDERS_CTE + """
        select location_id, ym, count(*), count(*) filter (where customerid is not null and customerid<>0),
               count(distinct customerid) filter (where customerid is not null and customerid<>0)
        from o group by 1,2""")
    # member_key = md5 of the vendor customer id — no phone/name leaves the DB
    rebuild(cur, "member_visits",
        "member_key text, last_location_id text, visits int, first_date date, last_date date, days_since int, avg_thb numeric",
        ORDERS_CTE + """
        select md5(customerid::text), (array_agg(location_id order by business_date desc))[1],
               count(*), min(business_date), max(business_date),
               current_date - max(business_date), round(avg(total_thb))
        from o where customerid is not null and customerid<>0 group by 1""")

def build_promo(cur):
    # promo tag = order-level discount reason/promotion text when present, else line-level item_promotion
    rebuild(cur, "promo_daily",
        "location_id text, business_date date, channel text, promo text, bills int, main_units numeric, net_thb numeric",
        ORDERS_CTE + """
        , tag as (select b.bill_id order_id,
                         coalesce(nullif(b.bill_promotion,''), (select max(nullif(bi.item_promotion,'')) from mp_metrics.bill_items bi where bi.bill_id=b.bill_id), 'ไม่มีโปร') promo
                  from mp_metrics.bills b),
        mu as (select order_id, sum(qty) u from li where tree_category='main' group by 1)
        select o.location_id, o.business_date, o.channel, coalesce(tag.promo,'ไม่มีโปร'),
               count(*), coalesce(sum(mu.u),0), sum(o.total_thb-o.discount_thb)
        from o left join tag on tag.order_id=o.order_id left join mu on mu.order_id=o.order_id
        group by 1,2,3,4""")
    rebuild(cur, "set_monthly",
        "location_id text, ym text, channel text, set_name text, units numeric, thb numeric",
        ORDERS_CTE + """
        select o.location_id, o.ym, o.channel, li.name_th, sum(li.qty), sum(li.gross_inc_vat_thb)
        from li join o on o.order_id=li.order_id
        where li.name_th ilike 'set%' or li.name_th ilike 'เซต%' group by 1,2,3,4""")
```

> Implementer: `mp_metrics.bills.bill_id` format is `<branchid>-<saleid>` and `mp_clean.orders.order_id` is the same string — verify with `select count(*) from mp_metrics.bills b join mp_clean.orders o on o.order_id=b.bill_id` (must be > 20,000) before relying on the join.

- [ ] **Step 5: calendar_daily (baseline + holidays + rain) and opportunity**

```python
def build_calendar(cur):
    cur.execute("create table if not exists sales_web.weather_daily (d date primary key, rain_mm numeric, tmax_c numeric)")
    rebuild(cur, "calendar_daily",
        """location_id text, business_date date, channel text, orders int, net_thb numeric, dow int, dom int, wom int,
           is_holiday boolean, rain_mm numeric, expected_thb numeric, gap_pct numeric""",
        ORDERS_CTE + """
        , d as (select location_id, business_date, channel, count(*) orders, sum(total_thb-discount_thb) net
                from o group by 1,2,3)
        select d.location_id, d.business_date, d.channel, d.orders, d.net,
               extract(isodow from d.business_date)::int, extract(day from d.business_date)::int,
               ((extract(day from d.business_date)::int - 1) / 7) + 1,
               exists (select 1 from sales_web.holidays h where h.d=d.business_date),
               w.rain_mm, null::numeric, null::numeric
        from d left join sales_web.weather_daily w on w.d = d.business_date""")
    # expected = mean of the same weekday, same branch+channel, over the previous 8 weeks (excluding holidays)
    cur.execute("""
      update sales_web.calendar_daily c set expected_thb = e.exp,
             gap_pct = case when e.exp > 0 then round(100*(c.net_thb - e.exp)/e.exp,1) end
      from (select c1.location_id, c1.business_date, c1.channel, round(avg(c2.net_thb)) exp
            from sales_web.calendar_daily c1
            join sales_web.calendar_daily c2 on c2.location_id=c1.location_id and c2.channel=c1.channel
                 and c2.dow=c1.dow and not c2.is_holiday
                 and c2.business_date between c1.business_date-56 and c1.business_date-7
            group by 1,2,3 having count(*) >= 3) e
      where e.location_id=c.location_id and e.business_date=c.business_date and e.channel=c.channel""")

def build_opportunity(cur):
    """Opportunity calculator, last 30 full days, per branch x channel x lever.
    rate = lever units / main units; best = highest branch rate for that channel;
    uplift ฿/month = (best - current) x main_units_30d x avg unit price of the lever."""
    rebuild(cur, "opportunity",
        """location_id text, channel text, lever text, current_rate numeric, best_location_id text,
           best_rate numeric, main_units_30d numeric, unit_price_thb numeric, uplift_thb_month numeric""",
        """with t as (
             select location_id, channel,
                    sum(main_units) mu, sum(side_units) side, sum(bev_paid_units) bev_paid,
                    sum(bev_premium_units) bev_prem, sum(dessert_units) dessert,
                    sum(topping_units) topping, sum(paid_option_picks) paid_opt,
                    sum(side_thb)/nullif(sum(side_units),0) p_side,
                    sum(bev_thb)/nullif(sum(bev_paid_units)+sum(bev_premium_units),0) p_bev,
                    sum(dessert_thb)/nullif(sum(dessert_units),0) p_dessert,
                    sum(topping_thb)/nullif(sum(topping_units),0) p_topping,
                    sum(paid_option_thb)/nullif(sum(paid_option_picks),0) p_opt
             from sales_web.tree_daily
             where business_date between current_date-30 and current_date-1 group by 1,2),
           r as (
             select location_id, channel, lever, rate, mu, price from t,
             lateral (values ('side', side/nullif(mu,0), p_side), ('bev_paid', bev_paid/nullif(mu,0), p_bev),
                             ('bev_premium', bev_prem/nullif(mu,0), p_bev), ('dessert', dessert/nullif(mu,0), p_dessert),
                             ('topping', topping/nullif(mu,0), p_topping), ('paid_option', paid_opt/nullif(mu,0), p_opt)
                     ) v(lever, rate, price)
             where mu >= 100),
           best as (select distinct on (channel, lever) channel, lever, location_id, rate
                    from r where rate is not null order by channel, lever, rate desc)
           select r.location_id, r.channel, r.lever, round(r.rate,3), b.location_id, round(b.rate,3),
                  r.mu, round(coalesce(r.price,0)),
                  round(greatest(b.rate - r.rate, 0) * r.mu * coalesce(r.price,0))
           from r join best b on b.channel=r.channel and b.lever=r.lever""")
```

- [ ] **Step 6: grab_match (Grab → ERS by order number + same day)**

```python
def build_grab_match(cur):
    cur.execute("select to_regclass('sales_web.grab_orders')")
    if not cur.fetchone()[0]:
        print("  grab_orders missing - skip grab_match"); return
    rebuild(cur, "grab_match",
        "booking_id text, location_id text, business_date date, gf_no text, bill_id text, ambiguous boolean",
        r"""with tabs as (
              select l.location_id, st.branchid||'-'||st.saleid bill_id, (st.starttime at time zone 'Asia/Bangkok')::date d,
                     lpad(coalesce(substring(st.refdeliveryorder from '^\s*(?:[Gg][Ff]\s*-?\s*)?(\d{3})\s*$'),
                                   substring(st.takehomename   from '^\s*(?:[Gg][Ff]\s*-?\s*)?(\d{3})\s*$')),3,'0') num,
                     coalesce(o.is_finalized and not o.is_voided, false) ok
              from mp_raw.pos_sale_tabs st
              join mp_clean.locations l on l.branchid = st.branchid
              left join mp_clean.orders o on o.order_id = st.branchid||'-'||st.saleid
              where st.starttime >= (current_date - %s)::timestamp),
            cand as (select g.booking_id, g.location_id, g.business_date, g.gf_no, t.bill_id, t.ok
                     from sales_web.grab_orders g
                     join tabs t on t.location_id=g.location_id and t.d=g.business_date and t.num=g.gf_no
                     where g.category='payment'),
            ranked as (select *, count(*) filter (where ok) over (partition by booking_id) n_ok,
                              row_number() over (partition by booking_id order by ok desc, bill_id) rn from cand)
            select booking_id, location_id, business_date, gf_no, bill_id, n_ok > 1 from ranked where rn = 1"""
        .replace("%s", str(WINDOW_DAYS + 10)))
```

> Note: `pos_sale_tabs.starttime` is stored as naive/UTC — confirm with `select starttime, starttime at time zone 'Asia/Bangkok' from mp_raw.pos_sale_tabs limit 1`; if the column is `timestamp without time zone` holding Bangkok wall time (the 2026-09-08 analysis treated it as naive Bangkok), use `st.starttime::date` instead.

- [ ] **Step 7: main() + heartbeat + --check**

```python
def main():
    t0 = dt.datetime.now()
    with bb.conn.cursor() as cur:
        for fn in (build_tree, build_hourly, build_dwell, build_pax_trust, build_items, build_options,
                   build_pairs, build_distributions, build_members, build_promo, build_calendar,
                   build_opportunity, build_grab_match):
            print(fn.__name__); fn(cur)
        cur.execute("""insert into sales_web.app_status (id, last_build, data_through) values (1, now(), current_date-1)
                       on conflict (id) do update set last_build=excluded.last_build, data_through=excluded.data_through""")
    bb.conn.commit()
    print(f"sales_tables ok in {(dt.datetime.now()-t0).seconds}s")

if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run and reconcile**

Run: `cd ~/mamapook-data && ./venv/bin/python -m tools.sales_tables`
Expected: every table prints a non-zero row count (grab_match may skip), finishes < 300 s.

Reconcile (MCP execute_sql), all three must agree to the baht:
```sql
select (select round(sum(net_thb)) from sales_web.tree_daily where business_date='2026-09-15') tree,
       (select round(sum(net_thb)) from sales_web.hourly where business_date='2026-09-15') hourly,
       (select round(sum(total_thb-discount_thb)) from mp_clean.orders o join mp_clean.locations l using (location_id)
         where business_date='2026-09-15' and is_finalized and not is_voided and l.is_confirmed_live and l.location_type='branch') orders;
```
And sanity: `select location_id, round(avg(ratio),2), bool_and(trusted) from sales_web.pax_trust where business_date>=current_date-14 group by 1` — ratios land roughly 0.5–1.5 (Gaysorn/Silom avg pax ≈1.5, so bowls/pax ≈ 1.0–1.3).

- [ ] **Step 9: Commit**

```bash
git add tools/sales_tables.py && git commit -m "sales_web: materialize all Sales Web section tables (tools/sales_tables.py)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `tools/weather_pull.py` — Bangkok rain

**Files:**
- Create: `tools/weather_pull.py`

**Interfaces:**
- Produces: `sales_web.weather_daily(d date pk, rain_mm numeric, tmax_c numeric)` from 2026-07-01 to today.

- [ ] **Step 1: Write it (stdlib only)**

```python
"""Daily Bangkok rain/temperature from Open-Meteo (free, no key) -> sales_web.weather_daily.
Archive API lags ~5 days; the forecast API's past_days covers the gap. Run nightly."""
import json, datetime as dt, urllib.request
from shared.env import load
from shared.backbone import Backbone

LAT, LON = 13.7563, 100.5018   # Bangkok (Silom/Sathorn cluster)
START = "2026-07-01"

def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)

def main():
    today = dt.date.today()
    rows = {}
    arc = fetch(f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}"
                f"&start_date={START}&end_date={today}&daily=precipitation_sum,temperature_2m_max&timezone=Asia/Bangkok")
    for d, p, t in zip(arc["daily"]["time"], arc["daily"]["precipitation_sum"], arc["daily"]["temperature_2m_max"]):
        if p is not None: rows[d] = (p, t)
    fc = fetch(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
               f"&past_days=10&forecast_days=1&daily=precipitation_sum,temperature_2m_max&timezone=Asia/Bangkok")
    for d, p, t in zip(fc["daily"]["time"], fc["daily"]["precipitation_sum"], fc["daily"]["temperature_2m_max"]):
        if p is not None and d not in rows and d <= str(today): rows[d] = (p, t)
    bb = Backbone(load())
    with bb.conn.cursor() as cur:
        cur.execute("create table if not exists sales_web.weather_daily (d date primary key, rain_mm numeric, tmax_c numeric)")
        for d, (p, t) in rows.items():
            cur.execute("""insert into sales_web.weather_daily values (%s,%s,%s)
                           on conflict (d) do update set rain_mm=excluded.rain_mm, tmax_c=excluded.tmax_c""", (d, p, t))
    bb.conn.commit()
    print(f"weather_daily: {len(rows)} days through {max(rows)}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run** `./venv/bin/python -m tools.weather_pull` → expected `weather_daily: ~95 days through <yesterday or today>`. Verify `select count(*), max(d) from sales_web.weather_daily`.

- [ ] **Step 3: Commit** `git add tools/weather_pull.py && git commit -m "sales_web: Bangkok daily rain from Open-Meteo" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 4: `tools/grab_load.py` — Grab exports loader

**Files:**
- Create: `tools/grab_load.py`

**Interfaces:**
- Consumes: CSV files under the Grab reports folder (any depth). Detection by header signature (sets below). Branch normalisation: text after the last `" - "` in the store name → `location_id` via `{"พระราม 9":"rama9","One City Centre":"occ","Park Silom":"silom","Gaysorn Tower":"gaysorn","sathorn square":"sathorn-square","All Season":"all-seasons"}` (case-insensitive contains).
- Produces (idempotent: each table truncated per (location_id, month) present in the loaded files, then inserted — so re-dropping August replaces August only):

```
grab_orders      (booking_id text pk, location_id, business_date, created_at timestamp, gf_no text(3 digits),
                  category text('payment'|'adjustment'|'ads'|'other'), status text, order_type text,
                  gross_thb, merchant_discount_thb, net_thb, commission_thb, marketing_fee_thb, payout_thb,
                  cancel_reason text, cancelled_by text)
grab_daily       (location_id, business_date, gross_thb, net_thb, orders, avg_order_thb, rating)   pk(location_id,business_date)
grab_menu_daily  (location_id, business_date, item, units, thb)                                   pk(location_id,business_date,item)
grab_offers      (location_id, business_date, campaign, gross_thb, net_thb, orders, spend_thb)     pk(location_id,business_date,campaign)
grab_peak        (location_id, business_date, hour int, orders)                                   pk(location_id,business_date,hour)
grab_ads_hourly  (d date, hour int, campaign text, spend_thb, impressions, clicks, menu_visits, add_to_cart, orders, sales_thb) pk(d,hour,campaign)
grab_keywords    (ym text, keyword text, is_brand bool, impressions, clicks, spend_thb, orders, sales_thb)   pk(ym,keyword)
grab_miwi_daily  (location_id, business_date, wrong, missing, total_reported, completed_orders)   pk(location_id,business_date)
grab_reviews     (review_key text pk (md5 of store+date+text), location_id, review_date date, rating int, review text, replied bool, service_type text)
grab_combos      (location_id, as_of date, combo text, n int)                                     pk(location_id,as_of,combo)
```

Header signatures (all columns must be present; Thai headers as exported):
```python
SIG = {
 "sales":     {"วันที่","ร้าน","ยอดขายรวม (฿)","จำนวนรายการชำระเงิน","เรตติ้งเฉลี่ย"},
 "menu":      {"วันที่","ร้าน","รายการ","จำนวนที่ขายได้"},
 "combo":     {"ร้าน","คอมโบ"},
 "peak":      {"วันที่","ร้าน","01","13","00"},
 "offers":    {"วันที่","ร้าน","โปรโมชัน","เงินที่ใช้ (฿)"},
 "ads_campaign": {"Campaigns Name","Ad Spend","Impressions","Hourly"},
 "keywords":  {"Matched Keywords","Ad Spend","Impressions"},
 "transactions": {"หมวดหมู่","ยอดขายสุทธิ","ทั้งหมด","Transaction ID"},
 "miwi_store": {"ร้าน","Missing or Wrong Item Rate (%)"},
 "reviews":   {"ร้าน","รีวิว","เรตติ้ง","ลูกค้า"},
}
# ignored on purpose: Transfers_Store (bank recon = Finance), MIWI item/heatmap (alert only via miwi_store), ads hourly account-level (subset of campaign file)
```
Transaction_Store column positions (header has duplicate names, so use INDEX): 2 store, 5 created ("31 Aug 2026 6:47 PM" → `%d %b %Y %I:%M %p`), 7 category (ชำระเงิน→payment, การปรับรายได้→adjustment, โฆษณา→ads, else other), 9 status, 15 short order id (`GF-199`), 16 booking id, 18 order type, 29 gross (ยอด), 35 merchant discount (negative), 40 net, 44 marketing fee, 46 platform commission, 47 order commission (OCC/Sathorn use this instead of 46 — sum both), 52 payout (ทั้งหมด), 59 cancel reason, 60 cancelled by. Numbers: strip everything except digits, `.`, `-`.
Dates in Sales/Menu/Offers/Peak are `dd/mm/yyyy`; reviews `7 Sep 2026 17:25 GMT+07` → `%d %b %Y`. Keyword `is_brand` = keyword contains any of `mama`, `หม่าม้า`, `มาม่าปุก`, `pook`.

- [ ] **Step 1: Write the loader** — functions `detect(header:set)->str|None`, `branch(store:str)->str|None`, `num(s)->float`, one `load_<kind>(rows, cur)` per kind, `main(folder)` that walks `*.csv` (utf-8-sig), detects, loads, prints `kind: file -> n rows`, commits. Unknown files print `skip: <name>`. For each table collect the set of `(location_id, ym)` touched and `delete from <table> where location_id=%s and to_char(business_date,'YYYY-MM')=%s` before insert (ads/keywords keyed by `d`/`ym` only).

- [ ] **Step 2: Run** `./venv/bin/python -m tools.grab_load "/Users/point/Library/CloudStorage/OneDrive-Personal/Personal/Second brain/Mama Pook/Projects/Sales Web/Grab reports"`
Expected prints: transactions → 1624 payment rows (+584 adjustment, 154 ads), sales 115, menu 1228, offers 167, peak 117, ads_campaign 745, keywords 1093 (dedup by keyword: fewer), miwi 115, reviews 26, combo 63.

Verify (MCP): `select location_id, count(*) filter (where category='payment') orders, round(sum(gross_thb) filter (where category='payment')) gross, round(sum(payout_thb)) payout from sales_web.grab_orders group by 1` → Silom 700 / 204,309 / ~137,132; Gaysorn 545 / 158,508 / ~110,718; OCC 248; Rama9 129.

- [ ] **Step 3: Re-run the same command** → identical counts (idempotent, no duplicates).

- [ ] **Step 4: Run `./venv/bin/python -m tools.sales_tables`** again → `grab_match` now prints ≈ 720 rows; `select location_id, count(*) from sales_web.grab_match group by 1` → gaysorn ≈341, silom ≈321, rama9 ≈31, occ ≈26.

- [ ] **Step 5: Commit** `git add tools/grab_load.py && git commit -m "sales_web: Grab exports loader (header-signature detection, idempotent per branch-month)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 5: `sales_app/build/build.py` — payload + fallback

**Files:**
- Create: `sales_app/build/build.py`, `sales_app/site/data/.gitkeep` (data.json is committed too, like dashboard_app)

**Interfaces:**
- Consumes: all `sales_web.*` tables.
- Produces: JSON written to `sales_web.app_cache` id=1 and `sales_app/site/data/data.json`. **Contract (the UI depends on these exact keys):**

```jsonc
{
 "generated_at": "2026-10-06 21:30", "tz": "Asia/Bangkok", "data_through": "2026-10-05",
 "branches": [{"id":"gaysorn","th":"เกษร","seats":null,"open":10,"close":20}, ...],   // live branches; th = short Thai name map below
 "holidays": ["2026-10-13", ...],
 "tree":   [[loc,d,daypart,channel,orders,pax,main,bev,bev_w,bev_p,bev_pr,side,top,des,opt_n,opt_thb,gross,disc,net,main_thb,bev_thb,side_thb,top_thb,des_thb], ...],  // arrays, 90 days
 "hourly": [[loc,d,hour,channel,orders,pax,net,main], ...],                           // 90 days
 "dwell":  [[loc,d,daypart,sessions,avg_min,avg_party], ...],
 "pax_trust": [[loc,d,bowls,keyed,ratio,trusted], ...],
 "items":  [[loc,d,channel,item,cat,sub,tier,units,thb,bills,price], ...],           // 90 days, item_code dropped
 "options": [[loc,ym,channel,group,choice,picks,paid_picks,paid_thb], ...],
 "option_by_dish": [[loc,ym,channel,dish,group,choice,picks], ...],
 "topping_route": [[loc,ym,channel,topping,opt_u,opt_thb,menu_u,menu_thb], ...],
 "pairs":  [[loc,a,b,ab,na,nb,n,lift], ...],                                          // lift >= 1.2 only, top 300 per loc
 "party":  [[loc,ym,channel,daypart,bucket,bills,net,main], ...],
 "ticket": [[loc,ym,channel,bucket,bills], ...],
 "members": {"monthly": [[loc,ym,bills,member_bills,members], ...],
             "winback": [[key,loc,visits,last_date,days_since,avg_thb], ...]},        // visits>=2 and days_since>=21, top 200
 "promo":  [[loc,d,channel,promo,bills,main,net], ...],
 "sets":   [[loc,ym,channel,name,units,thb], ...],
 "calendar": [[loc,d,channel,orders,net,dow,dom,wom,hol,rain,expected,gap], ...],
 "opportunity": [[loc,channel,lever,rate,best_loc,best_rate,main30,price,uplift], ...],
 "grab": {"daily": [[loc,d,gross,net,orders,avg,rating]], "orders": [[booking,loc,d,gf,hr,gross,disc,net,comm,mkt,payout,status,cancel_reason]],
          "menu": [[loc,d,item,units,thb]], "offers": [[loc,d,campaign,gross,net,orders,spend]],
          "peak": [[loc,d,hour,orders]], "ads": [[d,hour,campaign,spend,impr,clicks,visits,cart,orders,sales]],
          "keywords": [[ym,kw,brand,impr,clicks,spend,orders,sales]], "miwi": [[loc,d,wrong,missing,reported,completed]],
          "reviews": [[loc,d,rating,text,replied,service]], "combos": [[loc,as_of,combo,n]],
          "match": [[booking,loc,d,gf,bill,ambiguous]],
          "match_cov": [[loc,ym,orders,matched]],
          "basket": [[loc,ym,mains,sides,bev_paid,bev_prem,dessert,topping,paid_opt,orders]],  // from grab_match x order_lines/sold_options
          "months": ["2026-08"]}
}
```
Short Thai names: gaysorn เกษร · silom สีลม · occ OCC · sathorn-square สาทร · all-seasons ออลซีซั่นส์ · rama9 พระราม 9.
Column-array (not object) rows keep the payload small; `build.py` includes a `"cols"` object mapping each key → its column list so the UI self-documents.

- [ ] **Step 1: Write build.py** — same skeleton as `dashboard_app/build/build.py` (`q(sql)`, `js()`), but each feed is `[list(r.values()) for r in rows]`. Include the `grab.basket` query:

```sql
select m.location_id, to_char(m.business_date,'YYYY-MM'),
       sum(li.qty) filter (where li.tree_category='main'),
       sum(li.qty) filter (where li.tree_category='side'),
       sum(li.qty) filter (where li.tree_category='beverage' and li.unit_price_inc_vat_thb between 16 and 40),
       sum(li.qty) filter (where li.tree_category='beverage' and li.unit_price_inc_vat_thb > 40),
       sum(li.qty) filter (where li.tree_category='dessert'),
       sum(li.qty) filter (where li.tree_category='topping'),
       (select count(*) from mp_clean.sold_options so where so.order_id=any(array_agg(distinct m.bill_id)) and so.is_paid_option),
       count(distinct m.bill_id)
from sales_web.grab_match m
join mp_clean.order_lines li on li.order_id=m.bill_id and li.is_active and not li.is_special_line
group by 1,2
```
and `grab.match_cov`: `select location_id, to_char(business_date,'YYYY-MM'), count(*), count(*) filter (where exists (select 1 from sales_web.grab_match gm where gm.booking_id=g.booking_id)) from sales_web.grab_orders g where category='payment' group by 1,2`.

Write cache: `insert into sales_web.app_cache (id,payload,updated_at) values (1,%s,now()) on conflict (id) do update set payload=excluded.payload, updated_at=now()` then commit; also write `site/data/data.json`.

- [ ] **Step 2: Run** `./venv/bin/python sales_app/build/build.py` → prints payload size. Expected < 6 MB raw. If larger, cut `items` to 60 days first, then `hourly` to 60 days.

Verify: `select length(payload), updated_at from sales_web.app_cache where id=1` and `python3 -c "import json;d=json.load(open('sales_app/site/data/data.json'));print(list(d), len(d['tree']), d['data_through'])"`.

- [ ] **Step 3: Commit** `git add sales_app/build/build.py sales_app/site/data/data.json && git commit -m "sales_app: payload builder -> sales_web.app_cache + offline fallback" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 6: Edge function `sales-data`

**Files:**
- Create: `sales_app/edge/sales-data.ts`

- [ ] **Step 1: Write it** — copy `dashboard_app/edge/dashboard-data.ts` verbatim with three changes: `TOKEN` = new 48-hex random (`python3 -c "import secrets;print(secrets.token_hex(24))"`), `ORIGIN = "https://mamapook-sales.pages.dev"`, query `select payload from sales_web.app_cache where id = 1`. Header comment: `// Sales Web live data door — v1 (2026-10-06). Deployed on Supabase as "sales-data"; repo copy.`

- [ ] **Step 2: Deploy via MCP** `deploy_edge_function(project_id=xnmzlqqudizckjhchhpn, name="sales-data", verify_jwt=false, files=[{name:"index.ts", content:<file>}])`. `SUPABASE_DB_URL` secret already exists project-wide (used by dashboard-data).

- [ ] **Step 3: Verify** `curl -s -H "x-dash-token: <TOKEN>" https://xnmzlqqudizckjhchhpn.supabase.co/functions/v1/sales-data | head -c 300` → starts with `{"generated_at"`. Without the header → `{"error":"unauthorized"}`.

- [ ] **Step 4: Commit** `git add sales_app/edge/sales-data.ts && git commit -m "sales_app: edge function sales-data (serves sales_web.app_cache verbatim)" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 7: The SPA `sales_app/site/index.html`

**Files:**
- Create: `sales_app/site/index.html`
- Create: `sales_app/deploy.sh`

**Interfaces:**
- Consumes: the Task 5 payload (live via edge function with `x-dash-token`, fallback `data/data.json`).

**Shared frame (copy from dashboard_app/site/index.html: CSS variables, `.wrap`, sticky `nav`, `.card`, table styles, light/dark):**
- Header: `Mama Pook · Sales Web` + freshness pill `ข้อมูล ณ <generated_at>` (solid red if older than 90 min or if fallback used → text `(สำเนาล่าสุด)`), `ข้อมูลถึง <data_through>`.
- **Global filters** (sticky under nav): branch multi-tick (default all), channel switch **หน้าร้าน / Grab / รวม** (หน้าร้าน = dine_in+take_away+delivery from POS; Grab = `grab.*` feeds; รวม = both side by side where a section supports it), date range presets (7 / 30 / 90 วัน / เดือนนี้ / เดือนก่อน / กำหนดเอง, default 30 วัน ending data_through), day type (ทุกวัน / วันธรรมดา / เสาร์-อาทิตย์ / วันหยุด).
- State object `F = {branches:Set, channel:'store'|'grab'|'both', from, to, daytype}`; every section exposes `render<Section>()` called on any filter change; helper `rows(feed)` returns filtered arrays by loc/date; `fmt(n)` thousands separators, `pct(x)`.
- Nav tabs (ids): 1 ต้นไม้ยอดขาย · 2 ปริมาณ · 3 ตั๋วเฉลี่ย · 4 ตัวเลือก · 5 สมาชิก · 6 เมนู&โปร · 7 Insights · 8 สรุปรายสัปดาห์ · 9 Grab.

**Per-section content (each = cards with tables; simple inline bar/heat cells via `<div class=bar style="width:%">`, no chart library):**

1. **ต้นไม้ยอดขาย** — top KPIs (ยอดขายสุทธิ, บิล, ลูกค้า, ตั๋วเฉลี่ย/บิล, ตั๋วเฉลี่ย/หัว, ชามต่อลูกค้า); tree table rows = branch → expandable daypart → channel with columns ออเดอร์ · ลูกค้า · ชามหลัก · ตั๋ว/ออเดอร์ · attach% (เครื่องดื่มจ่ายเงิน / เครื่องเคียง / ท็อปปิ้ง / ของหวาน / ออปชันเพิ่ม) · ส่วนลด% · ยอดสุทธิ. Volume source per branch-day = keyed pax if `pax_trust.trusted`, else bowls (show a ⚠ tag). **Pax-trust strip**: per branch, % trusted days + avg ratio, red when < 70% days trusted ("ต้องคีย์จำนวนลูกค้าให้ถูก"). Grab channel: uses `grab.daily` (orders, avg order) + a billing-layer row (ส่วนลดร้าน / ค่าคอมมิชชัน / ค่าการตลาด / รับจริง) from `grab.orders`.
2. **ปริมาณ** — (a) heatmap ชั่วโมง × สาขา of orders (and a toggle to pax or net); utilization % = pax-hours ÷ (seats × hours) when `seats` set, else cell note "รอข้อมูลที่นั่ง"; tag per branch-daypart: ว่าง (<50%) / เต็ม (>80%). (b) ชั่วโมงเงียบ: revenue by hour line-table with the 16–20 band highlighted and % of day. (c) ปกติ vs จริง: `calendar` rows with gap% colored (red < −15%, green > +15%), holiday/rain icons. (d) dwell table by branch × daypart. Grab: `grab.peak` hour curve next to in-store hours.
3. **ตั๋วเฉลี่ย** — (a) attach heatmap: rows = branch, cols = เครื่องดื่ม(น้ำเปล่า/จ่ายเงิน/พรีเมียม) · เครื่องเคียง · ท็อปปิ้ง · ของหวาน · ออปชันเพิ่มเงิน, per channel tabs; cell color vs best branch. (b) **โอกาสเพิ่มยอด (฿/เดือน)**: table from `opportunity` sorted by uplift desc: สาขา · ช่องทาง · ตัวขับ · ตอนนี้ · ดีที่สุด (สาขา) · ชามหลัก/30วัน · ราคาเฉลี่ย · **+฿/เดือน**; click a row → explanation sentence "ถ้า <สาขา> ขาย<ตัวขับ>ได้เท่า<best> (<rate>) จะเพิ่ม ≈ ฿X/เดือน". (c) party-size mix bars and ticket histogram per branch. Grab: attach from `grab.basket` (labelled with match coverage) and aggregate cross-check from `grab.menu`.
4. **ตัวเลือก** — (a) choice share per modifier group (bars, per branch columns), group picker; (b) หางที่ไม่มีคนเลือก: choices < 2% of their group's picks; (c) เส้นทางท็อปปิ้ง: table topping × branch: ออปชัน (units, ฿/unit) vs เมนูเดี่ยว (units, ฿/unit), highlight price mismatch > ฿5; (d) option-by-dish drill: pick a dish → its groups' shares. Grab: same from matched orders only, header shows coverage %.
5. **สมาชิก** — member attach % per branch per month (bars), distinct members, win-back list table (รหัส · สาขาล่าสุด · ครั้ง · ล่าสุด · หายไป (วัน) · เฉลี่ย ฿). Grab tab: "ไม่มีข้อมูลลูกค้าจาก Grab".
6. **เมนู&โปร** — (a) menu matrix: scatter-as-table: item · units · ฿ · price · popularity quartile → class ดาว/ม้างาน/ปริศนา/หมา using popularity × price (margin axis "รอข้อมูลต้นทุน"); (b) per-menu drill: click item → daily trend (90-day mini bars), channel split, its option shares (from option_by_dish), pairs it appears in; (c) เซต: set units vs à-la-carte mains, per month; (d) โปร: promo rows: bills, mains/bill vs ไม่มีโปร, net; (e) combo simulator: pick 2–3 items → shows pair lift + bills_ab from `pairs`, expected uptake = bills_ab/bills_a. Grab: menu mix from `grab.menu`, combos from `grab.combos`, campaign payback from `grab.offers`.
7. **Insights** — (a) pairs table (top lifts per branch); (b) calendar effects: avg net by day-of-week, by day-of-month band (1–7 / 8–14 / 15–21 / 22–end), holiday vs not, rain (≥1 mm) vs dry — each as % vs branch mean, per channel; (c) attach by context: attach rates split by daypart × party bucket (from `party` + `tree`). Text note: "โมเดลรายเดือน — ไม่ใช้ในต้นไม้รายวัน".
8. **สรุปรายสัปดาห์** — top-3 opportunities (from `opportunity`) + top-3 gaps (calendar gap% worst days last 7 days) rendered as the LINE message text in a `<pre>` with a copy button (push plumbing is a later task; v1 = copy-paste).
9. **Grab** — 9.1 กำไรต่อออเดอร์: waterfall table per branch (ยอดขาย → ส่วนลดร้าน → ค่าคอม → ค่าการตลาด → รับจริง → ต้นทุนอาหาร "รอข้อมูลต้นทุน" → กำไร/ออเดอร์), % kept; 9.2 โฆษณา: brand vs generic (spend, orders, cost/order, orders per ฿100), funnel impressions→visits→cart→orders, spend by hour vs orders by hour (two columns); 9.3 โปรโมชัน: per campaign spend, orders, ฿/order, campaign days vs non-campaign avg orders; 9.4 ออเดอร์ที่เสียไป: cancels by reason, by hour, ฿; 9.5 ความเสี่ยงอันดับ: rating trend by month, unanswered reviews list (date, branch, rating, text), MIWI rate alert when > 1%; 9.6 Grab vs หน้าร้าน: orders-by-hour side by side, top items side by side, attach gaps; footer: match coverage per branch per month.

- [ ] **Step 1: Build the frame + filters + section 1** with the fallback json; open `sales_app/site/index.html` via `python3 -m http.server` in `sales_app/site` and check in the built-in browser: filters change numbers, tree totals equal the KPI total.
- [ ] **Step 2: Sections 2–4.** Check: opportunity table top row uplift matches `select * from sales_web.opportunity order by uplift_thb_month desc limit 1`.
- [ ] **Step 3: Sections 5–8.**
- [ ] **Step 4: Section 9** with the Grab feeds; Grab channel switch wired into 1,2,3,4,6,7.
- [ ] **Step 5: Live fetch**: on load `fetch(EDGE_URL, {headers:{'x-dash-token':TOKEN}})` → on failure load `data/data.json` and mark fallback; every 10 min re-check `generated_at` and show a reload button.
- [ ] **Step 6: deploy.sh**

```bash
#!/bin/zsh
# Build payload + deploy Sales Web to Cloudflare Pages (project mamapook-sales). No auth.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"
source "$HOME/mamapook-data/venv/bin/activate"
python build/build.py
npx wrangler pages deploy site --project-name mamapook-sales --branch main --commit-dirty=true 2>&1 | tail -2
```
First time: `npx wrangler pages project create mamapook-sales --production-branch main`. Then `chmod +x sales_app/deploy.sh && sales_app/deploy.sh` → URL https://mamapook-sales.pages.dev. Open it in the built-in browser; the freshness pill must be green and show the edge `generated_at` (not สำเนาล่าสุด).

- [ ] **Step 7: Commit** `git add sales_app/site/index.html sales_app/deploy.sh && git commit -m "sales_app: Sales Web SPA (9 sections, channel switch) + Cloudflare deploy" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"`

---

### Task 8: Pipeline wiring + docs

**Files:**
- Modify: `pipelines/pos_ers/run_local.sh` (after the `app_cache` step, ~line 74)
- Modify: `sales_app/sales-web.md` (append BUILT block), vault twin, `Project Data Backbone.md` (one line in the data-flow section pointing to sales_app), `/Users/point/Claude automation/TOOLBOX.md` (new rows: Open-Meteo, Cloudflare Pages no-auth project, Grab header-signature loader).

- [ ] **Step 1: run_local.sh**

```bash
  run_step sales_tables 600 python -m tools.sales_tables || echo "sales_tables failed/timed out (rc=$?)"
  run_step sales_cache 300 python sales_app/build/build.py || echo "sales_cache failed (rc=$?)"
  if [ "$MODE" = "nightly" ]; then
    run_step weather 120 python -m tools.weather_pull || echo "weather failed (non-fatal)"
    run_step grab_load 300 python -m tools.grab_load "$HOME/Library/CloudStorage/OneDrive-Personal/Personal/Second brain/Mama Pook/Projects/Sales Web/Grab reports" || echo "grab_load failed (non-fatal)"
    [ -x "$HOME/mamapook-data/sales_app/deploy.sh" ] && "$HOME/mamapook-data/sales_app/deploy.sh" || echo "sales deploy failed (non-fatal)"
  fi
```
Dry-run the two 30-min steps by hand (`python -m tools.sales_tables && python sales_app/build/build.py`) and confirm `select updated_at from sales_web.app_cache` advanced.

- [ ] **Step 2: Docs** — append to `sales_app/sales-web.md`:
```
## BUILT (2026-10-06) — status
Live: https://mamapook-sales.pages.dev (no auth, v1). Data: schema sales_web (tools/sales_tables.py every 30 min;
tools/grab_load.py + tools/weather_pull.py nightly), payload sales_web.app_cache id=1 (sales_app/build/build.py),
edge function sales-data (sales_app/edge/sales-data.ts), SPA sales_app/site/index.html, deploy sales_app/deploy.sh
(Cloudflare Pages project mamapook-sales, --branch main). Reference inputs Point owns: sales_web.seats (NULL until filled),
sales_web.holidays. Open: BOM food cost for Grab 9.1 waits on vendor cost fix; LINE digest push not wired (copy-paste v1).
```
Copy the file to the vault twin; add the Data Backbone line; TOOLBOX rows.

- [ ] **Step 3: Commit + push** `git add -A && git commit -m "sales_app: pipeline wiring (30-min tables+cache, nightly grab/weather/deploy) + docs" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" && git push`

---

## Self-review

- Spec coverage: §1 tree + pax-trust (T2 tree_daily/pax_trust, T7 s1) · §2 utilization/dead hours/baseline/dwell (T2 hourly/dwell/calendar + seats, T7 s2) · §3 attach tiers/paid options/calculator/party-ticket distributions (T2 tree_daily/opportunity/party_size/ticket_hist, T7 s3) · §4 choices/tail/topping route/by dish (T2 option_*, topping_route) · §5 members/win-back (T2 members_*) · §6 per-menu/matrix/sets/promo/combo sim (T2 item_daily/set_monthly/promo_daily/pair_lift, T7 s6) · §7 pairs/calendar+holiday+rain (T2 pair_lift/calendar_daily, T3) · §8 digest text (T7 s8; LINE push deferred, documented) · §9 Grab 9.1–9.6 + channel switch + match coverage (T4, T2 grab_match, T5 grab feeds, T7 s9).
- Deferred/open on purpose: BOM food cost (vendor fix), attach logistic regressions (v1 shows attach by context tables instead), LINE push.
- Type consistency: table/column names in T2 match the T5 contract arrays; `location_id` everywhere; Grab `gf_no` is a 3-char zero-padded string in both grab_load (T4) and grab_match (T2 `lpad(...,3,'0')`).
