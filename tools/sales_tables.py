"""Materialize every Sales Web table into schema sales_web each pipeline cycle.
Spec: Project Sales Web.md (repo Itthicheta/sales). One engine — build.py only READS these tables.
Run: ~/mamapook-data/venv/bin/python ~/sales/tools/sales_tables.py   (optional --check prints row counts)

Design: the shared base (window orders, their lines, their options, and a
per-order roll-up) is materialized ONCE into temp tables, then every section
table is a cheap aggregate over them. Per-order scalars (pax, money, option
picks) live on one row per order in t_ord, so they are never multiplied by
line count.

Money definitions (spec-owner ruling 2026-10-06): mp_clean.orders.total_thb is the
amount PAID, already after discount (inc VAT); discount_thb sits on top of it.
So everywhere here:  net_thb = total_thb;  gross_thb = total_thb + discount_thb;
discount_thb = orders.discount_thb.

Writes only to schema sales_web; reads mp_clean / mp_metrics / mp_raw.
Everything runs in one transaction, committed at the end (readers never see a
half-built set).
"""
import datetime as dt
import sys

from pathlib import Path
BACKBONE = Path.home() / "mamapook-data"  # backbone repo = dependency (shared.*, .env)
sys.path.insert(0, str(BACKBONE))
from shared.env import load  # noqa: E402
from shared.backbone import Backbone  # noqa: E402

WINDOW_DAYS = 120
PAIR_DAYS = 90
BKK = "at time zone 'Asia/Bangkok'"
NO_PROMO = "ไม่มีโปร"
# Set-mandatory modifier groups. Point 2026-10-07: the forced picks inside set menus
# ("ของทอดไซส์ S (จานที่ 1)", "เครื่องดื่ม แก้วที่ 1/2", "ไอศครีม 2ที่", "… ชามที่ 1/2") are
# part of the set, not an upsell — they must NOT count as paid options in the tree /
# opportunity / option_monthly.paid_*. Plain `picks` counts keep them (what customers choose).
SET_MANDATORY_RE = r"(จานที่|แก้วที่|ชามที่|2ที่)"
PAID_OPT = f"(so.is_paid_option and so.modifier_group !~ '{SET_MANDATORY_RE}')"
# Free items (Point 2026-10-07): excluded from EVERY unit count (attach, item units, pairs);
# their money is ~฿0 and net comes from orders.total_thb, so money is untouched.
#   A13 หมูกระจก ฟรีรีวิว (review give-away), H4 น้ำแข็งฟรี (free ice), H2 แก้วเปล่า (empty
#   glass) + any item whose name contains 'แลกฟรี' (redemption, e.g. PRO1).
FREE_ITEM_CODES = ("A13", "H4", "H2")
FREE_NAME_LIKE = "%แลกฟรี%"


def free_item_sql(code_col: str, name_col: str) -> str:
    """SQL predicate: TRUE when the row is a free item (see FREE_ITEM_CODES)."""
    codes = ",".join(f"'{c}'" for c in FREE_ITEM_CODES)
    return f"(coalesce({code_col},'') in ({codes}) or coalesce({name_col},'') like '{FREE_NAME_LIKE}')"


# Option picks that are really menu items -> merged into that item via
# mp_clean.map_option_item (transforms/001_mappings.sql). Groups covered:
#   paid upsell options  -> source 'option': เพิ่มความอร่อย, เพิ่มข้าว, ซุบบ๊วย
#   set-mandatory picks  -> source 'set'   : ของทอดไซส์ S (จานที่ n), เครื่องดื่ม แก้วที่ n, ไอศครีม 2ที่
# (source = 'set' when the group matches SET_MANDATORY_RE, else 'option'; order lines = 'menu').
OPTION_ITEM_GROUPS_RE = r"^(เพิ่มความอร่อย|เพิ่มข้าว|ซุบบ๊วย|ของทอดไซส์ S|เครื่องดื่ม แก้วที่|ไอศครีม 2ที่)"


def choice_key_sql(col: str) -> str:
    """sold_options.choice_th -> map_option_item.choice_key (trailing * / ** stripped)."""
    return rf"btrim(regexp_replace({col}, '\*+\s*$', ''))"


# Beverage price tiers (unit price inc VAT). H3 น้ำเปล่า sells at ฿16.05 inc VAT, so the
# water ceiling is ฿20 (the old ฿15 put H3 in "paid" and only ฿0 glasses/ice in water).
WATER_MAX, PAID_BEV_MAX = 20, 40


def bev_tier_sql(price_col: str) -> str:
    return (f"case when {price_col} <= {WATER_MAX} then 'water' "
            f"when {price_col} <= {PAID_BEV_MAX} then 'paid' else 'premium' end")
TREE_CATS = "('main','side','beverage','topping','dessert','set')"
DAYPART = f"""case when extract(hour from o.closed_at {BKK}) between 11 and 13 then 'lunch'
                   when extract(hour from o.closed_at {BKK}) between 17 and 20 then 'dinner'
                   else 'non_peak' end"""

TABLES = ["tree_daily", "hourly", "dwell", "pax_trust", "item_daily", "option_monthly",
          "option_by_dish", "topping_route", "pair_lift", "party_size", "ticket_hist",
          "members_monthly", "member_visits", "promo_daily", "set_monthly", "calendar_daily",
          "opportunity", "grab_match", "occupancy_hourly", "set_incremental", "pair_attach", "dead_hours"]


def rebuild(cur, table: str, ddl_cols: str, select_sql: str):
    cur.execute(f"create table if not exists sales_web.{table} ({ddl_cols})")
    cur.execute(f"truncate sales_web.{table}")
    cur.execute(f"insert into sales_web.{table} {select_sql}")
    cur.execute(f"select count(*) from sales_web.{table}")
    n = cur.fetchone()[0]
    print(f"  sales_web.{table}: {n} rows")
    return n


def build_base(cur):
    """Temp tables shared by every section (dropped at commit).
    t_o     finalized, non-voided orders of live branches in the window, with
            Bangkok hour / daypart / ym derived from closed_at.
    t_li    their active, non-special lines MINUS split-bill duplicate copies
            (mp_metrics.split_dupe_lines — same rule as mp_metrics.bill_items, so
            a dish on a split table counts once). is_free flags FREE_ITEM_CODES.
    t_so    sold options hanging off t_li lines.
    t_units EVERY unit sold, one row per source (Point 2026-10-07):
            'menu'   = an order line (free items excluded),
            'option' = a paid upsell option pick that is really a menu item,
            'set'    = a set-mandatory pick (SET_MANDATORY_RE) that is a menu item;
            option picks are merged into their item via mp_clean.map_option_item
            (qty = choice_qty x qty_mult, thb = paid_price x choice_qty, category /
            tier from the mapped item). Attach รวม = all three; อัพเซล = menu + option;
            ในเซต = set. NB option thb also sits inside the parent line's gross, so
            category thb is for unit prices only — never add it to net.
    t_ord   one row per order: t_o scalars + unit/thb sums by category from t_units."""
    cur.execute(f"""
      create temp table t_o on commit drop as
      select o.order_id, o.location_id, o.business_date, o.channel, o.pax,
             o.total_thb, o.discount_thb, o.net_ex_vat_thb, o.customerid,
             extract(hour from o.closed_at {BKK})::int as hr,
             {DAYPART} as daypart,
             to_char(o.business_date, 'YYYY-MM') as ym
      from mp_clean.orders o
      join mp_clean.locations l on l.location_id = o.location_id
      where o.is_finalized and not o.is_voided
        and l.is_confirmed_live and l.location_type = 'branch'
        and o.business_date >= current_date - {WINDOW_DAYS}""")
    cur.execute("create index on t_o (order_id)")
    cur.execute("create temp table t_dupe on commit drop as select line_id from mp_metrics.split_dupe_lines")
    cur.execute("create index on t_dupe (line_id)")
    cur.execute(f"""
      create temp table t_li on commit drop as
      select li.order_id, li.line_id, li.itemid, it.item_code, li.name_th, li.tree_category,
             li.qty, li.gross_inc_vat_thb, li.unit_price_inc_vat_thb, li.base_price_thb,
             li.discount_thb,
             case when li.tree_category = 'beverage' then {bev_tier_sql('li.unit_price_inc_vat_thb')} end as bev_tier,
             {free_item_sql('it.item_code', 'li.name_th')} as is_free
      from mp_clean.order_lines li
      join t_o o on o.order_id = li.order_id
      left join mp_clean.items it on it.itemid = li.itemid
      where li.is_active and not li.is_special_line
        and not exists (select 1 from t_dupe d where d.line_id = li.line_id)""")
    cur.execute("create index on t_li (order_id)")
    cur.execute("create index on t_li (line_id)")
    cur.execute(f"""
      create temp table t_so on commit drop as
      select so.order_id, so.line_id, so.dish_th, so.modifier_group, so.choice_th,
             so.choice_qty, so.paid_price_thb, so.is_paid_option,
             {PAID_OPT} as is_paid_upsell   -- paid AND not a set-mandatory pick
      from mp_clean.sold_options so
      join t_li li on li.line_id = so.line_id""")
    cur.execute(f"""
      create temp table t_units on commit drop as
      select li.order_id, o.location_id, o.business_date, o.daypart, o.channel,
             li.itemid, li.item_code, li.name_th item_name, li.tree_category, li.bev_tier,
             'menu'::text source, li.qty, li.gross_inc_vat_thb thb
      from t_li li join t_o o on o.order_id = li.order_id
      where not li.is_free
      union all
      select so.order_id, o.location_id, o.business_date, o.daypart, o.channel,
             it.itemid, it.item_code, it.name_th, it.tree_category,
             case when it.tree_category = 'beverage' then {bev_tier_sql('it.retail_price_thb')} end,
             case when so.modifier_group ~ '{SET_MANDATORY_RE}' then 'set' else 'option' end,
             so.choice_qty * m.qty_mult, coalesce(so.paid_price_thb, 0) * so.choice_qty
      from t_so so
      join t_o o on o.order_id = so.order_id
      join mp_clean.map_option_item m on m.choice_key = {choice_key_sql('so.choice_th')}
      join mp_clean.items it on it.item_code = m.item_code
      where so.modifier_group ~ '{OPTION_ITEM_GROUPS_RE}'""")
    cur.execute("create index on t_units (order_id)")
    # unmapped option choices in the covered groups: report, never drop silently
    cur.execute(f"""
      select so.modifier_group, so.choice_th, count(*)
      from t_so so
      left join mp_clean.map_option_item m on m.choice_key = {choice_key_sql('so.choice_th')}
      left join mp_clean.items it on it.item_code = m.item_code
      where so.modifier_group ~ '{OPTION_ITEM_GROUPS_RE}' and it.itemid is null
      group by 1, 2 order by 3 desc""")
    um = cur.fetchall()
    if um:
        print("  WARNING unmapped option choices (not counted as units): " +
              "; ".join(f"{g} / {c} x{n}" for g, c, n in um))
    else:
        print("  option->item map: no unmapped choices")
    # promo tag: ERS records no bill-level promo names (mp_metrics.bills.bill_promotion
    # is hard-null), so the tag is the line-level derived "buy 2 get 1" rule — the same
    # predicate mp_metrics.bill_items.item_promotion uses — else NO_PROMO.
    cats = [("main", "tree_category='main'"), ("bev", "tree_category='beverage'"),
            ("bev_water", "bev_tier='water'"), ("bev_paid", "bev_tier='paid'"),
            ("bev_premium", "bev_tier='premium'"), ("side", "tree_category='side'"),
            ("topping", "tree_category='topping'"), ("dessert", "tree_category='dessert'"),
            ("set", "tree_category='set'")]
    usum = ",\n               ".join(
        [f"sum(qty) filter (where {w}) {k}_units" for k, w in cats] +
        [f"sum(thb) filter (where {w}) {k}_thb" for k, w in cats if k not in ("bev_water",)] +
        # the ในเซต layer: picks forced inside a set menu
        [f"sum(qty) filter (where source='set' and {w}) {k}_set_units" for k, w in cats
         if k in ("side", "bev_paid", "bev_premium", "dessert", "topping")] +
        [f"sum(thb) filter (where source='set' and {w}) {k}_set_thb" for k, w in cats
         if k in ("side", "bev_paid", "bev_premium", "dessert", "topping")])
    ucols = [f"{k}_units" for k, _ in cats] + [f"{k}_thb" for k, _ in cats if k != "bev_water"] + \
            [f"{k}_set_units" for k in ("side", "bev_paid", "bev_premium", "dessert", "topping")] + \
            [f"{k}_set_thb" for k in ("side", "bev_paid", "bev_premium", "dessert", "topping")]
    cur.execute(f"""
      create temp table t_ord on commit drop as
      with ua as (
        select order_id,
               {usum}
        from t_units group by 1),
      b2 as (
        select order_id,
               bool_or(base_price_thb >= 99 and gross_inc_vat_thb <= 0.01
                       and coalesce(discount_thb,0) <= 0.01) is_b2g1
        from t_li group by 1)
      select o.*,
             {", ".join(f"coalesce(ua.{c},0) {c}" for c in ucols)},
             case when b2.is_b2g1 then 'ซื้อ 2 แถม 1 (derived)' else '{NO_PROMO}' end promo
      from t_o o
      left join ua on ua.order_id = o.order_id
      left join b2 on b2.order_id = o.order_id""")
    cur.execute("analyze t_o; analyze t_li; analyze t_so; analyze t_units; analyze t_ord")
    cur.execute("select (select count(*) from t_o), (select count(*) from t_li), (select count(*) from t_so), (select count(*) from t_units)")
    print("  base: %s orders, %s lines, %s options, %s unit rows" % cur.fetchone())


# ---------- Step 2: tree, hourly, dwell, pax trust ----------

def build_tree(cur):
    # net_thb = total_thb (paid, post-discount); gross_thb = total_thb + discount_thb.
    # Unit columns = ALL sources (menu + option + set picks, free items excluded — t_units).
    # paid_option_picks / paid_option_thb are DEPRECATED (2026-10-07: option picks now live
    # in side/bev/dessert by item) — kept, filled 0, so the column order / build.py indices
    # never shift. The ในเซต layer (*_set_units) is appended last.
    rebuild(cur, "tree_daily",
        """location_id text, business_date date, daypart text, channel text, orders int,
           pax_keyed numeric, main_units numeric, bev_units numeric, bev_water_units numeric,
           bev_paid_units numeric, bev_premium_units numeric, side_units numeric,
           topping_units numeric, dessert_units numeric,
           paid_option_picks int,      -- DEPRECATED 2026-10-07, always 0
           paid_option_thb numeric,    -- DEPRECATED 2026-10-07, always 0
           gross_thb numeric, discount_thb numeric, net_thb numeric,
           main_thb numeric, bev_thb numeric, side_thb numeric, topping_thb numeric, dessert_thb numeric,
           bev_paid_thb numeric, bev_premium_thb numeric,
           set_units numeric, set_thb numeric,
           side_set_units numeric, bev_paid_set_units numeric, bev_premium_set_units numeric,
           dessert_set_units numeric, topping_set_units numeric""",
        """select location_id, business_date, daypart, channel, count(*), sum(pax),
                  sum(main_units), sum(bev_units), sum(bev_water_units), sum(bev_paid_units),
                  sum(bev_premium_units), sum(side_units), sum(topping_units), sum(dessert_units),
                  0, 0,
                  sum(total_thb + discount_thb), sum(discount_thb), sum(total_thb),
                  sum(main_thb), sum(bev_thb), sum(side_thb), sum(topping_thb), sum(dessert_thb),
                  sum(bev_paid_thb), sum(bev_premium_thb),
                  sum(set_units), sum(set_thb),
                  sum(side_set_units), sum(bev_paid_set_units), sum(bev_premium_set_units),
                  sum(dessert_set_units), sum(topping_set_units)   -- appended last (column order = contract)
           from t_ord group by 1,2,3,4""")


def build_hourly(cur):
    rebuild(cur, "hourly",
        "location_id text, business_date date, hour int, channel text, orders int, pax_keyed numeric, net_thb numeric, main_units numeric",
        """select location_id, business_date, hr, channel, count(*), sum(pax),
                  sum(total_thb), sum(main_units)
           from t_ord group by 1,2,3,4""")


def build_dwell(cur):
    rebuild(cur, "dwell",
        "location_id text, business_date date, daypart text, sessions int, avg_dwell_min numeric, avg_party numeric",
        """select o.location_id, o.business_date, o.daypart, count(*),
                  round(avg(ts.dwell_minutes),1), round(avg(ts.pax),2)
           from mp_clean.table_sessions ts
           join t_o o on o.order_id = ts.order_id
           -- is_cancelled is NULL on most rows (vendor leaves IsCancel unset): treat NULL as
           -- not cancelled. Dwell is a dine-in metric — take-away tabs open/close in seconds.
           where not coalesce(ts.is_cancelled, false) and o.channel = 'dine_in'
             and ts.dwell_minutes between 3 and 240
           group by 1,2,3""")


def build_pax_trust(cur):
    rebuild(cur, "pax_trust",
        "location_id text, business_date date, bowls numeric, keyed_pax numeric, ratio numeric, trusted boolean",
        """select location_id, business_date, b, k,
                  case when k > 0 then round(b / k, 2) end,
                  case when k > 0 then b / k between 0.9 and 1.3 else false end
           -- bowls = main occasions = main + set units (a set is a meal, Point 2026-10-07)
           from (select location_id, business_date, sum(main_units + set_units) b, sum(pax)::numeric k
                 from t_ord where channel = 'dine_in' group by 1,2) x""")


# ---------- Step 3: items, options, topping route, pairs ----------

def build_items(cur):
    # one row per item per source (menu / option / set) — an item sold as a menu line,
    # a paid option and a set pick shares its item_code / name; source appended last.
    rebuild(cur, "item_daily",
        """location_id text, business_date date, channel text, item_code bigint, item_name text,
           main_category text, sub_category text, bev_tier text, units numeric, thb numeric, bills int,
           avg_price numeric, source text""",
        """select u.location_id, u.business_date, u.channel, u.itemid, u.item_name,
                  coalesce(u.tree_category,'other'), it.vendor_category, u.bev_tier,
                  sum(u.qty), sum(u.thb), count(distinct u.order_id),
                  round(sum(u.thb)/nullif(sum(u.qty),0),2), u.source
           from t_units u
           left join mp_clean.items it on it.itemid = u.itemid
           group by 1,2,3,4,5,6,7,8,13""")


def build_options(cur):
    rebuild(cur, "option_monthly",
        "location_id text, ym text, channel text, modifier_group text, choice_th text, picks int, paid_picks int, paid_thb numeric",
        """select o.location_id, o.ym, o.channel, so.modifier_group, so.choice_th,
                  count(*), count(*) filter (where so.is_paid_upsell),
                  coalesce(sum(so.paid_price_thb*so.choice_qty) filter (where so.is_paid_upsell),0)
           from t_so so join t_o o on o.order_id = so.order_id
           group by 1,2,3,4,5""")
    rebuild(cur, "option_by_dish",
        "location_id text, ym text, channel text, dish_th text, modifier_group text, choice_th text, picks int",
        """select o.location_id, o.ym, o.channel, so.dish_th, so.modifier_group, so.choice_th, count(*)
           from t_so so join t_o o on o.order_id = so.order_id
           group by 1,2,3,4,5,6""")
    # Topping route: the same topping added to a bowl as an OPTION vs sold as a
    # STANDALONE menu line (tree_category='topping', "B2 คอหมูย่าง").
    # Option side = the add-topping modifier group only ('เพิ่ม toppping' today:
    # "เพิ่ม คอหมู", "เพิ่ม สันคอ", "เพิ่ม หมูกรอบ" ...), counted regardless of
    # is_paid_option — those picks are priced at 0 on the option (the charge, if
    # any, sits on the dish line), so as_option_thb = paid_price_thb x qty is
    # usually 0. The "เครื่อง ..." groups are a dish's INCLUDED meat choice, and
    # "เพิ่มความอร่อย"/"เพิ่มข้าว" are paid sides/rice — not toppings.
    # Key = name with menu prefix (B1/B2…), the word เพิ่ม, parenthesised text,
    # gram/number suffix, trailing * and all whitespace removed; then the short
    # option names are aliased to the menu names (คอหมู→คอหมูย่าง, สันคอ→สันคอหมูย่าง).
    rebuild(cur, "topping_route",
        "location_id text, ym text, channel text, topping text, as_option_units numeric, as_option_thb numeric, as_menu_units numeric, as_menu_thb numeric",
        f"""with opt as (
              select o.location_id, o.ym, o.channel, {_topping_key('so.choice_th')} k,
                     sum(so.choice_qty) u, coalesce(sum(so.paid_price_thb*so.choice_qty),0) t
              from t_so so join t_o o on o.order_id = so.order_id
              where so.modifier_group ilike '%topp%' or so.modifier_group like '%ท็อปปิ้ง%'
                 or so.modifier_group like '%ทอปปิ้ง%'
              group by 1,2,3,4),
            menu as (
              select o.location_id, o.ym, o.channel, {_topping_key('li.name_th')} k,
                     sum(li.qty) u, sum(li.gross_inc_vat_thb) t
              from t_li li join t_o o on o.order_id = li.order_id
              where li.tree_category = 'topping' group by 1,2,3,4)
            select coalesce(opt.location_id,menu.location_id), coalesce(opt.ym,menu.ym),
                   coalesce(opt.channel,menu.channel), coalesce(opt.k,menu.k),
                   coalesce(opt.u,0), coalesce(opt.t,0), coalesce(menu.u,0), coalesce(menu.t,0)
            from opt full join menu on menu.location_id=opt.location_id and menu.ym=opt.ym
                                   and menu.channel=opt.channel and menu.k=opt.k
            where coalesce(opt.u,0)+coalesce(menu.u,0) > 0""")


def _topping_key(col: str) -> str:
    """SQL expression normalising a topping name (option choice or menu line) to one key."""
    k = col
    k = rf"regexp_replace({k}, '^\s*[A-Z]\d+\s*', '')"          # menu prefix B1/B2…
    k = rf"regexp_replace({k}, '^\s*เพิ่ม\s*', '')"                # leading 'เพิ่ม'
    k = rf"regexp_replace({k}, '\([^)]*\)', '', 'g')"              # (…) text
    k = rf"regexp_replace({k}, '\*+', '', 'g')"                     # * markers
    k = rf"regexp_replace({k}, '\s*\d+\s*(g|gr|กรัม)?\s*$', '')" # gram / number suffix
    k = rf"regexp_replace({k}, '\s+', '', 'g')"                     # all whitespace
    return f"(case {k} when 'คอหมู' then 'คอหมูย่าง' when 'สันคอ' then 'สันคอหมูย่าง' else {k} end)"


def build_pairs(cur):
    # Market-basket lift over the last PAIR_DAYS, per branch, items with >= 30 bills,
    # pairs with >= 10 co-occurring bills.
    rebuild(cur, "pair_lift",
        "location_id text, item_a text, item_b text, bills_ab int, bills_a int, bills_b int, total_bills int, lift numeric",
        f"""with bi as (select distinct o.location_id, o.order_id, li.name_th item
                        from t_li li join t_o o on o.order_id = li.order_id
                        where o.business_date >= current_date - {PAIR_DAYS}
                          and li.tree_category in {TREE_CATS} and not li.is_free),
            tot as (select location_id, count(distinct order_id) n from bi group by 1),
            cnt as (select location_id, item, count(*) n from bi group by 1,2 having count(*) >= 30),
            bk as (select bi.* from bi join cnt using (location_id, item))
            select a.location_id, a.item, b.item, count(*), ca.n, cb.n, t.n,
                   round((count(*)::numeric * t.n) / (ca.n * cb.n), 2)
            from bk a join bk b on b.location_id=a.location_id and b.order_id=a.order_id and a.item < b.item
            join cnt ca on ca.location_id=a.location_id and ca.item=a.item
            join cnt cb on cb.location_id=b.location_id and cb.item=b.item
            join tot t on t.location_id=a.location_id
            group by a.location_id, a.item, b.item, ca.n, cb.n, t.n
            having count(*) >= 10""")


# ---------- Step 4: distributions, members, promo, sets ----------

def build_distributions(cur):
    rebuild(cur, "party_size",
        "location_id text, ym text, channel text, daypart text, pax_bucket text, bills int, net_thb numeric, main_units numeric",
        """select location_id, ym, channel, daypart,
                  case when channel<>'dine_in' or pax > 20 then 'n/a' when pax<=1 then '1' when pax=2 then '2'
                       when pax<=4 then '3-4' else '5+' end,
                  count(*), sum(total_thb), sum(main_units)
           from t_ord group by 1,2,3,4,5""")
    rebuild(cur, "ticket_hist",
        "location_id text, ym text, channel text, thb_bucket text, bills int",
        """select location_id, ym, channel,
                  case when total_thb<150 then '<150' when total_thb<250 then '150-249'
                       when total_thb<400 then '250-399' when total_thb<600 then '400-599' else '600+' end,
                  count(*) from t_o group by 1,2,3,4""")


def build_members(cur):
    rebuild(cur, "members_monthly",
        "location_id text, ym text, bills int, member_bills int, distinct_members int",
        """select location_id, ym, count(*), count(*) filter (where customerid is not null and customerid<>0),
                  count(distinct customerid) filter (where customerid is not null and customerid<>0)
           from t_o group by 1,2""")
    # member_key = md5 of the vendor customer id — no phone/name leaves the DB
    rebuild(cur, "member_visits",
        "member_key text, last_location_id text, visits int, first_date date, last_date date, days_since int, avg_thb numeric",
        """select md5(customerid::text), (array_agg(location_id order by business_date desc, order_id desc))[1],
                  count(*), min(business_date), max(business_date),
                  current_date - max(business_date), round(avg(total_thb))
           from t_o where customerid is not null and customerid<>0 group by customerid""")


def build_promo(cur):
    rebuild(cur, "promo_daily",
        "location_id text, business_date date, channel text, promo text, bills int, main_units numeric, net_thb numeric",
        """select location_id, business_date, channel, promo, count(*), sum(main_units),
                  sum(total_thb)
           from t_ord group by 1,2,3,4""")
    rebuild(cur, "set_monthly",
        "location_id text, ym text, channel text, set_name text, units numeric, thb numeric",
        """select o.location_id, o.ym, o.channel, li.name_th, sum(li.qty), sum(li.gross_inc_vat_thb)
           from t_li li join t_o o on o.order_id = li.order_id
           where li.name_th ilike 'set%' or li.name_th ilike 'เซต%' group by 1,2,3,4""")


# ---------- Step 5: calendar + opportunity ----------

def build_calendar(cur):
    cur.execute("create table if not exists sales_web.weather_daily (d date primary key, rain_mm numeric, tmax_c numeric)")
    rebuild(cur, "calendar_daily",
        """location_id text, business_date date, channel text, orders int, net_thb numeric, dow int, dom int, wom int,
           is_holiday boolean, rain_mm numeric, expected_thb numeric, gap_pct numeric""",
        """with d as (select location_id, business_date, channel, count(*) orders, sum(total_thb) net
                      from t_o group by 1,2,3)
           select d.location_id, d.business_date, d.channel, d.orders, d.net,
                  extract(isodow from d.business_date)::int, extract(day from d.business_date)::int,
                  ((extract(day from d.business_date)::int - 1) / 7) + 1,
                  exists (select 1 from sales_web.holidays h where h.d = d.business_date),
                  w.rain_mm, null::numeric, null::numeric
           from d left join sales_web.weather_daily w on w.d = d.business_date""")
    # expected = mean of the same weekday, same branch+channel, over the previous 8 weeks
    # (excluding holidays), needs >= 3 comparable days
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


OPP_DAYS = 30            # calculator window = last 30 full days (current_date-30 .. current_date-1)
OWN_BEST_DAYS = 90       # own-best search range: 28-day windows stepping 7 days inside the last 90 days
SET_INC_DAYS = 90        # set_incremental measurement window (sets are few: ~100-230/branch/month)
PEER_MIN_MEALS = 1000    # peer qualifier: office branch with >= 1,000 meals (main+set) in the window
OPP_MIN_MEALS = 100      # a branch x channel row needs >= 100 meals (also: own-best window minimum)
PEERLESS = ("rama9",)    # peer group `office` = every live branch except these; they get peer_target NULL
DEAD_HOURS = (10, 11, 14, 15, 16, 20)   # off-peak hours benchmarked (lunch 12-13 / dinner 17-19 excluded)
DEAD_PEER_MIN_DAYS = 20  # dead-hours peer needs >= 20 open weekdays in the window
DEAD_OWN_MIN_DAYS = 15   # an own-best 4-week window needs >= 15 open weekdays (max 20)
DEAD_SKIP = ("rama9",)   # counter-style keying (~100% bills < 3 min): occupancy is only an ESTIMATE
                         # (late-keyed imputation, 2026-10-08) — too soft to price a lever on
VARIANT_RE = r"\s(ธรรมดา|เครื่องใน|ทรงเครื่อง)\s*$"
OPP_WIN = "business_date between current_date-%d and current_date-1" % OPP_DAYS
INSTORE = "('dine_in','take_away')"


def family_sql(name_col: str) -> str:
    """Main-dish family = item name without its menu code and trailing variant word
    (N1 'ก๋วยเตี๋ยว ต้มยำแห้ง ธรรมดา' -> 'ก๋วยเตี๋ยว ต้มยำแห้ง'). Items without a variant word keep
    their code-less name (they are a family of one; not in scope for the trade-up lever)."""
    base = rf"regexp_replace({name_col}, '^\s*[A-Za-z]+\d+\s*', '')"
    return rf"btrim(regexp_replace({base}, '{VARIANT_RE}', ''))"


def variant_sql(name_col: str) -> str:
    return rf"substring({name_col} from '{VARIANT_RE}')"


def build_set_incremental(cur):
    """Measured incremental ฿ per set (Point 2026-10-08). Per branch, in-store bills (dine-in +
    take-away) of the last SET_INC_DAYS full days, persons = main + set units (>= 1 kept, 0 dropped),
    bucketed 1 / 2 / 3-4 / 5+. In each bucket: net ฿ per meal on bills WITH a set minus on bills
    WITHOUT; the bucket diffs are averaged weighted by set-bill count. incremental per set-bill =
    max(0, diff) x avg persons per set-bill; per SET = that / avg sets per set-bill (= the same
    number when a set-bill holds one set, which is the norm)."""
    rebuild(cur, "set_incremental",
        """location_id text, set_bills int, nonset_bills int, incremental_thb_per_set numeric, method text,
           diff_per_meal_thb numeric, persons_per_set_bill numeric, sets_per_set_bill numeric""",
        f"""with b as (
              select location_id, total_thb net, main_units + set_units p, set_units s,
                     case when main_units + set_units <= 1 then '1' when main_units + set_units = 2 then '2'
                          when main_units + set_units <= 4 then '3-4' else '5+' end bk
              from t_ord
              where business_date between current_date-{SET_INC_DAYS} and current_date-1
                and channel in {INSTORE} and main_units + set_units > 0),
            k as (
              select location_id, bk,
                     count(*) filter (where s > 0) n_s, count(*) filter (where s = 0) n_n,
                     sum(net) filter (where s > 0) / nullif(sum(p) filter (where s > 0), 0)
                       - sum(net) filter (where s = 0) / nullif(sum(p) filter (where s = 0), 0) d
              from b group by 1, 2),
            w as (
              select location_id, sum(n_s * d) / nullif(sum(n_s) filter (where d is not null), 0) d
              from k where d is not null group by 1),
            t as (
              select location_id, count(*) filter (where s > 0) sb, count(*) filter (where s = 0) nb,
                     avg(p) filter (where s > 0) pp, avg(s) filter (where s > 0) sp
              from b group by 1)
            select t.location_id, t.sb, t.nb,
                   round(greatest(coalesce(w.d, 0), 0) * coalesce(t.pp, 0) / nullif(t.sp, 0), 2),
                   'in-store bills, last {SET_INC_DAYS} full days; per persons bucket (1/2/3-4/5+, persons = bowls+sets): '
                   || 'net per meal WITH set - WITHOUT set, weighted by set-bill count; '
                   || 'x avg persons per set-bill / avg sets per set-bill; floored at 0',
                   round(w.d, 2), round(t.pp, 2), round(t.sp, 2)
            from t left join w using (location_id)""")


def build_pair_attach(cur):
    """Menu-pair scripts (Point 2026-10-08): for each main family x side / paid-or-premium drink /
    dessert item (upsell layer = menu lines + merged paid option picks; set picks and water out),
    per branch x in-store channel over the last OPP_DAYS full days:
    rate = bills with both / bills with the main family. item_price_thb = that item's avg ฿/unit at
    the branch (upsell layer) — the UI's ฿ for 'if branch X matched branch Y'."""
    rebuild(cur, "pair_attach",
        """location_id text, channel text, main_family text, item text, bills_main int, bills_both int,
           rate numeric, item_price_thb numeric""",
        f"""with u as (
              select u.order_id, u.location_id, u.channel, u.itemid, u.tree_category, u.bev_tier,
                     u.qty, u.thb, it.name_th
              from t_units u join mp_clean.items it on it.itemid = u.itemid
              where u.{OPP_WIN} and u.channel in {INSTORE} and u.source <> 'set'),
            mf as (select distinct order_id, location_id, channel, {family_sql('name_th')} fam
                   from u where tree_category = 'main'),
            itm as (select distinct order_id, {family_sql('name_th')} item
                   from u where tree_category in ('side','dessert')
                             or (tree_category = 'beverage' and bev_tier in ('paid','premium'))),
            pr as (select location_id, {family_sql('name_th')} item, sum(thb) / nullif(sum(qty), 0) p
                   from u where tree_category in ('side','dessert','beverage') group by 1, 2),
            bm as (select location_id, channel, fam, count(*) n from mf group by 1, 2, 3)
            select mf.location_id, mf.channel, mf.fam, it.item, bm.n, count(*),
                   round(count(*)::numeric / bm.n, 4), round(pr.p, 2)
            from mf join itm it on it.order_id = mf.order_id
            join bm on bm.location_id = mf.location_id and bm.channel = mf.channel and bm.fam = mf.fam
            left join pr on pr.location_id = mf.location_id and pr.item = it.item
            group by mf.location_id, mf.channel, mf.fam, it.item, bm.n, pr.p""")


def build_opportunity(cur):
    """Opportunity calculator v2 (Point 2026-10-08). Window: last OPP_DAYS full days, in-store
    channels dine_in and take_away SEPARATELY (POS delivery = Grab at POS markup -> out).
    Rows: branch x channel x lever with >= OPP_MIN_MEALS meals (main + set).

    rate (current) = upsell-layer units / meals (side, dessert, topping=Sharing, bev_paid,
    bev_premium, set), except tradeup = ทรงเครื่อง units / all variant units in families that HAVE a
    ทรงเครื่อง item (for tradeup meals_30d holds that denominator, not meals).

    Two targets:
      peer_target = office peers (all live branches except PEERLESS): 2nd-highest rate among office
        branches with >= PEER_MIN_MEALS meals in that channel; if < 3 qualify -> the highest OTHER
        office branch (>= OPP_MIN_MEALS). PEERLESS branches (Rama 9) get NULL.
      own_best = the branch's best 28-day window (stepping 7 days, last OWN_BEST_DAYS days,
        window needs >= OPP_MIN_MEALS of denominator); own_best_window = that window's start.
    target_used = the SMALLER of the targets that are above current (conservative); none above ->
      uplift 0, note 'already at target'.
    value_per_unit_thb:
      side / dessert / topping = branch avg ฿/unit of the category (upsell layer, in-store, window)
      bev_paid / bev_premium   = tier difference: branch avg paid (premium) price - water price
                                 (H3 น้ำเปล่า list price, ฿16.05) — the lever is conversion from water
      tradeup                  = list price ทรงเครื่อง - ธรรมดา per family, weighted by the branch's
                                 units in that family (≈ ฿54)
      set                      = sales_web.set_incremental.incremental_thb_per_set (measured)
    uplift_thb_month = (target_used - current) x meals_30d x value_per_unit_thb.
    Plus one `dead_hours` row per branch (channel dine_in, same peer / own-best logic per off-peak
    hour): see build_dead_hours."""
    cur.execute("drop table if exists t_lev")
    cur.execute(f"""
      create temp table t_lev on commit drop as
      -- daily numerators / denominators per branch x channel x lever (own-best needs daily grain)
      with meals as (
        select location_id, channel, business_date, sum(main_units + set_units) m
        from t_ord where channel in {INSTORE}
          and business_date between current_date-{OWN_BEST_DAYS} and current_date-1
        group by 1, 2, 3),
      u as (
        select location_id, channel, business_date,
               case when tree_category = 'side' then 'side'
                    when bev_tier = 'paid' then 'bev_paid'
                    when bev_tier = 'premium' then 'bev_premium'
                    when tree_category = 'dessert' then 'dessert'
                    when tree_category = 'topping' then 'topping'
                    when tree_category = 'set' then 'set' end lever,
               sum(qty) n
        from t_units
        where channel in {INSTORE} and source <> 'set'
          and business_date between current_date-{OWN_BEST_DAYS} and current_date-1
        group by 1, 2, 3, 4),
      fam_ts as (   -- families that HAVE a ทรงเครื่อง item (menu-wide)
        select distinct {family_sql('name_th')} fam from mp_clean.items
        where tree_category = 'main' and {variant_sql('name_th')} = 'ทรงเครื่อง'),
      tu as (
        select t.location_id, t.channel, t.business_date,
               sum(t.qty) filter (where {variant_sql('it.name_th')} = 'ทรงเครื่อง') n,
               sum(t.qty) d
        from t_units t join mp_clean.items it on it.itemid = t.itemid
        where t.channel in {INSTORE} and t.tree_category = 'main' and t.source = 'menu'
          and t.business_date between current_date-{OWN_BEST_DAYS} and current_date-1
          and {variant_sql('it.name_th')} is not null
          and {family_sql('it.name_th')} in (select fam from fam_ts)
        group by 1, 2, 3)
      select m.location_id, m.channel, m.business_date, l.lever, coalesce(u.n, 0) n, m.m d
      from meals m
      cross join (values ('side'),('bev_paid'),('bev_premium'),('dessert'),('topping'),('set')) l(lever)
      left join u on u.location_id = m.location_id and u.channel = m.channel
                 and u.business_date = m.business_date and u.lever = l.lever
      union all
      select location_id, channel, business_date, 'tradeup', coalesce(n, 0), d from tu""")
    # per-branch value of one unit for each lever
    cur.execute("drop table if exists t_val")
    cur.execute(f"""
      create temp table t_val on commit drop as
      with w as (select coalesce((select retail_price_thb from mp_clean.items where item_code = 'H3'
                                  order by itemid limit 1), 16.05) p),
      c as (
        select location_id,
               case when tree_category in ('side','dessert','topping') then tree_category
                    when bev_tier = 'paid' then 'bev_paid' when bev_tier = 'premium' then 'bev_premium' end lever,
               sum(thb) / nullif(sum(qty), 0) p
        from t_units
        where {OPP_WIN} and channel in {INSTORE} and source <> 'set'
        group by 1, 2),
      lp as (   -- list price per family x variant
        select {family_sql('name_th')} fam, {variant_sql('name_th')} v, avg(retail_price_thb) p
        from mp_clean.items where tree_category = 'main' and {variant_sql('name_th')} is not null
          and retail_price_thb > 0
        group by 1, 2),
      fd as (select a.fam, a.p - b.p d from lp a join lp b on b.fam = a.fam and b.v = 'ธรรมดา'
             where a.v = 'ทรงเครื่อง'),
      fu as (
        select t.location_id, {family_sql('it.name_th')} fam, sum(t.qty) n
        from t_units t join mp_clean.items it on it.itemid = t.itemid
        where t.{OPP_WIN} and t.channel in {INSTORE} and t.tree_category = 'main' and t.source = 'menu'
          and {variant_sql('it.name_th')} is not null
        group by 1, 2)
      select location_id, lever,
             case when lever in ('bev_paid','bev_premium') then greatest(p - (select p from w), 0) else p end v,
             null::text basis
      from c where lever is not null
      union all
      select fu.location_id, 'tradeup', sum(fu.n * fd.d) / nullif(sum(fu.n), 0), null
      from fu join fd using (fam) group by 1
      union all
      select location_id, 'set', incremental_thb_per_set, null from sales_web.set_incremental""")
    rebuild(cur, "opportunity",
        """location_id text, channel text, lever text, current_rate numeric, peer_target numeric,
           peer_best_loc text, own_best numeric, own_best_window date, target_used numeric,
           gap_pp numeric, meals_30d numeric, value_per_unit_thb numeric, uplift_thb_month numeric,
           note text""",
        f"""with cur_ as (
              select location_id, channel, lever, sum(n) n, sum(d) d, sum(n) / nullif(sum(d), 0) rate
              from t_lev where {OPP_WIN} group by 1, 2, 3),
            meals as (select location_id, channel, sum(d) m from t_lev
                      where lever = 'side' and {OPP_WIN} group by 1, 2),
            r as (select c.*, m.m meals from cur_ c join meals m using (location_id, channel)
                  where m.m >= {OPP_MIN_MEALS} and c.d > 0),
            win as (
              select k, current_date - 1 - 7 * k e, current_date - 28 - 7 * k s
              from generate_series(0, ({OWN_BEST_DAYS} - 28) / 7) k),
            wr as (
              select l.location_id, l.channel, l.lever, w.s, sum(l.n) / nullif(sum(l.d), 0) rate
              from t_lev l join win w on l.business_date between w.s and w.e
              group by 1, 2, 3, 4 having sum(l.d) >= {OPP_MIN_MEALS}),
            own as (select distinct on (location_id, channel, lever) location_id, channel, lever, rate, s
                    from wr order by location_id, channel, lever, rate desc, s desc),
            -- peer pool: office branches whose denominator is >= OPP_MIN_MEALS (for tradeup that is
            -- variant bowls, not meals — take-away tradeup bases of 24-78 bowls are noise)
            off as (select * from r where location_id not in {_sql_list(PEERLESS)} and d >= {OPP_MIN_MEALS}),
            q as (select channel, lever, location_id, rate,
                         row_number() over (partition by channel, lever order by rate desc) rk,
                         count(*) over (partition by channel, lever) nq
                  from off where meals >= {PEER_MIN_MEALS}),
            peer as (
              select r.location_id, r.channel, r.lever,
                     case when r.location_id in {_sql_list(PEERLESS)} then null
                          when q2.location_id is not null then q2.rate
                          else (select o.rate from off o where o.channel = r.channel and o.lever = r.lever
                                  and o.location_id <> r.location_id order by o.rate desc limit 1) end pt,
                     case when r.location_id in {_sql_list(PEERLESS)} then null
                          when q2.location_id is not null then q2.location_id
                          else (select o.location_id from off o where o.channel = r.channel and o.lever = r.lever
                                  and o.location_id <> r.location_id order by o.rate desc limit 1) end pl
              from r left join q q2 on q2.channel = r.channel and q2.lever = r.lever and q2.rk = 2 and q2.nq >= 3),
            x as (
              select r.location_id, r.channel, r.lever, r.rate, p.pt, p.pl, o.rate ob, o.s obw,
                     case when r.lever = 'tradeup' then r.d else r.meals end base,
                     v.v val,
                     least(case when p.pt > r.rate then p.pt end,
                           case when o.rate > r.rate then o.rate end) tu
              from r join peer p using (location_id, channel, lever)
              left join own o using (location_id, channel, lever)
              left join t_val v on v.location_id = r.location_id and v.lever = r.lever)
            select location_id, channel, lever, round(rate, 4), round(pt, 4), pl, round(ob, 4), obw,
                   round(tu, 4), round(100 * coalesce(tu - rate, 0), 2), base, round(coalesce(val, 0), 2),
                   round(coalesce(tu - rate, 0) * base * coalesce(val, 0)),
                   case when pt is null and ob is null then 'no target (sample too small)'
                        when tu is null then 'already at target' else '' end
                   || case when lever = 'tradeup' then
                        case when tu is null then ' · ' else '' end
                        || 'base = ' || base::int || ' bowls in families with ทรงเครื่อง' else '' end
            from x""")
    # tradeup info: เครื่องใน share (information only, not a lever)
    cur.execute(f"""
      update sales_web.opportunity o set note = o.note || ' · เครื่องใน ' || round(100 * k.s) || '%'
      from (select t.location_id, t.channel,
                   sum(t.qty) filter (where {variant_sql('it.name_th')} = 'เครื่องใน') / nullif(sum(t.qty), 0) s
            from t_units t join mp_clean.items it on it.itemid = t.itemid
            where t.{OPP_WIN} and t.channel in {INSTORE} and t.tree_category = 'main' and t.source = 'menu'
              and {variant_sql('it.name_th')} is not null
              and {family_sql('it.name_th')} in (select {family_sql('name_th')} from mp_clean.items
                                                 where {variant_sql('name_th')} = 'ทรงเครื่อง')
            group by 1, 2) k
      where o.lever = 'tradeup' and k.location_id = o.location_id and k.channel = o.channel""")
    build_dead_hours(cur)


def _sql_list(xs) -> str:
    return "(" + ",".join(f"'{x}'" for x in xs) + ")"


def build_dead_hours(cur):
    """Dead hours on the SAME benchmark logic as every other lever (coordinator ruling 2026-10-08,
    replaces the fixed 50% target). Off-peak hours DEAD_HOURS (10, 11, 14, 15, 16, 20 — lunch 12-13 and
    dinner 17-19 excluded); WEEKDAYS only (Mon-Fri, not in sales_web.holidays, branch open = >= 1 dine-in
    order in tree_daily). Per branch x hour:
      current   = occupancy over the last OPP_DAYS full days = sum(seat_minutes) / (seats x 60 x open weekdays)
      peer      = 2nd-highest office branch occupancy for that hour among office branches with
                  >= DEAD_PEER_MIN_DAYS open weekdays in the window (< 3 qualify -> highest OTHER office branch)
      own best  = best 28-day window (stepping 7 days, last OWN_BEST_DAYS days, window needs
                  >= DEAD_OWN_MIN_DAYS open weekdays)
      target    = the smaller of the two above current (none above -> hour adds 0)
      persons/day = (target - current) x seats x 60 / dwell(hour)   [dwell = branch avg off-peak dwell when
                  the hour has < 1 bill opened per open weekday]
      ฿/day = persons x ticket/head (dine-in net / meals, last OPP_DAYS days)
    ฿/month = sum over hours of ฿/day x open weekdays in the window (gap measured on weekdays, applied to
    weekdays only). Detail -> sales_web.dead_hours (feed dead_hours); one `dead_hours` row per branch ->
    sales_web.opportunity. Rama 9 (DEAD_SKIP) excluded: its occupancy is ~100% imputed (estimate only)."""
    hrs = ",".join(str(h) for h in DEAD_HOURS)
    rebuild(cur, "dead_hours",
        """location_id text, hour int, occ numeric, peer_target numeric, peer_loc text, own_best numeric,
           own_best_window date, target_used numeric, dwell_min numeric, persons_day numeric,
           thb_day numeric, open_days int, ticket_thb numeric""",
        f"""with od as (   -- open weekdays per branch (last OWN_BEST_DAYS days)
              select location_id, business_date d from sales_web.tree_daily
              where channel = 'dine_in' and orders > 0
                and business_date between current_date-{OWN_BEST_DAYS} and current_date-1
                and extract(isodow from business_date) <= 5
                and business_date not in (select d from sales_web.holidays)
                and location_id not in {_sql_list(DEAD_SKIP)}
              group by 1, 2),
            oh as (select o.location_id, o.business_date d, o.hour, o.seat_minutes sm, o.turns tu,
                          o.dwell_min_sum ds, s.seats
                   from sales_web.occupancy_hourly o
                   join od on od.location_id = o.location_id and od.d = o.business_date
                   join sales_web.seats s on s.location_id = o.location_id
                   where o.hour in ({hrs}) and s.seats > 0),
            n30 as (select location_id, count(*) n from od where d >= current_date-{OPP_DAYS} group by 1),
            bd as (select location_id, sum(ds) / nullif(sum(tu), 0) d from oh
                   where d >= current_date-{OPP_DAYS} group by 1),
            c as (select oh.location_id, oh.hour, max(oh.seats) seats, n30.n,
                         sum(oh.sm) / (max(oh.seats) * 60.0 * n30.n) occ,
                         case when sum(oh.tu) >= n30.n then sum(oh.ds) / sum(oh.tu) else max(bd.d) end dw
                  from oh join n30 using (location_id) join bd using (location_id)
                  where oh.d >= current_date-{OPP_DAYS} group by 1, 2, n30.n),
            q as (select location_id, hour, occ,
                         row_number() over (partition by hour order by occ desc) rk,
                         count(*) over (partition by hour) nq
                  from c where n >= {DEAD_PEER_MIN_DAYS} and location_id not in {_sql_list(PEERLESS)}),
            peer as (
              select c.location_id, c.hour,
                     coalesce(q2.occ, (select q3.occ from q q3 where q3.hour = c.hour and q3.location_id <> c.location_id
                                       order by q3.occ desc limit 1)) pt,
                     coalesce(q2.location_id, (select q3.location_id from q q3 where q3.hour = c.hour
                                       and q3.location_id <> c.location_id order by q3.occ desc limit 1)) pl
              from c left join q q2 on q2.hour = c.hour and q2.rk = 2 and q2.nq >= 3),
            win as (select current_date - 1 - 7 * k e, current_date - 28 - 7 * k s
                    from generate_series(0, ({OWN_BEST_DAYS} - 28) / 7) k),
            nw as (select od.location_id, w.s, count(*) n from od join win w on od.d between w.s and w.e group by 1, 2),
            wr as (select oh.location_id, oh.hour, w.s, sum(oh.sm) / (max(oh.seats) * 60.0 * max(nw.n)) occ
                   from oh join win w on oh.d between w.s and w.e
                   join nw on nw.location_id = oh.location_id and nw.s = w.s
                   where nw.n >= {DEAD_OWN_MIN_DAYS} group by 1, 2, 3),
            own as (select distinct on (location_id, hour) location_id, hour, occ, s
                    from wr order by location_id, hour, occ desc, s desc),
            tk as (select location_id, sum(net_thb) / nullif(sum(main_units + set_units), 0) t
                   from sales_web.tree_daily where {OPP_WIN} and channel = 'dine_in' group by 1),
            x as (select c.*, p.pt, p.pl, o.occ ob, o.s obw, tk.t,
                         least(case when p.pt > c.occ then p.pt end, case when o.occ > c.occ then o.occ end) tu
                  from c join peer p using (location_id, hour) left join own o using (location_id, hour)
                  join tk using (location_id))
            select location_id, hour, round(occ, 4), round(pt, 4), pl, round(ob, 4), obw, round(tu, 4),
                   round(dw, 1), round(coalesce(tu - occ, 0) * seats * 60 / nullif(dw, 0), 2),
                   round(coalesce(tu - occ, 0) * seats * 60 / nullif(dw, 0) * t, 2), n, round(t, 2)
            from x""")
    cur.execute(f"""
      insert into sales_web.opportunity
      select location_id, 'dine_in', 'dead_hours',
             round(avg(occ), 4), round(avg(peer_target), 4), null, round(avg(own_best), 4), null,
             round(avg(target_used) filter (where target_used is not null), 4),
             round(100 * avg(target_used - occ) filter (where target_used is not null), 2),
             round(sum(persons_day) * max(open_days)), max(ticket_thb),
             round(sum(thb_day) * max(open_days)),
             coalesce('hours ' || string_agg(hour::text, ',' order by hour) filter (where thb_day > 0),
                      'already at target')
             || ' · ' || max(open_days) || ' open weekdays'
      from sales_web.dead_hours group by location_id""")
    cur.execute("select count(*) from sales_web.opportunity where lever = 'dead_hours'")
    print(f"  dead_hours rows: {cur.fetchone()[0]}")


# ---------- Step 6: Grab -> ERS match ----------

def build_grab_match(cur):
    cur.execute("select to_regclass('sales_web.grab_orders')")
    if not cur.fetchone()[0]:
        print("  grab_orders missing - skip grab_match")
        return
    # pos_sale_tabs.starttime is `timestamp without time zone` holding Bangkok
    # wall time (verified 2026-10-06: starttime 19:51 = orders.opened_at 12:51Z),
    # so its calendar date is simply starttime::date.
    rebuild(cur, "grab_match",
        "booking_id text, location_id text, business_date date, gf_no text, bill_id text, ambiguous boolean",
        r"""with tabs0 as (
              select l.location_id, st.branchid||'-'||st.saleid bill_id, st.starttime::date d,
                     coalesce(substring(st.refdeliveryorder from '^\s*(?:[Gg][Ff]\s*-?\s*)?(\d{3,4})\s*$'),
                              substring(st.takehomename   from '^\s*(?:[Gg][Ff]\s*-?\s*)?(\d{3,4})\s*$')) raw_num,
                     coalesce(o.is_finalized and not o.is_voided, false) ok
              from mp_raw.pos_sale_tabs st
              join mp_clean.locations l on l.branchid = st.branchid
              left join mp_clean.orders o on o.order_id = st.branchid||'-'||st.saleid
              where st.starttime >= (current_date - __DAYS__)::timestamp),
            tabs as (select *, case when length(raw_num) <= 3 then lpad(raw_num,3,'0') else raw_num end num
                     from tabs0),
            exact as (select g.booking_id, g.location_id, g.business_date, g.gf_no, t.bill_id, t.ok
                      from sales_web.grab_orders g
                      join tabs t on t.location_id=g.location_id and t.d=g.business_date and t.num=g.gf_no
                      where g.category='payment'),
            -- Grab ids longer than 3 digits (GF-5265) with no exact hit may match an ERS tab
            -- keyed on the last 3 digits, unless that tab is already taken exactly
            fallback as (select g.booking_id, g.location_id, g.business_date, g.gf_no, t.bill_id, t.ok
                         from sales_web.grab_orders g
                         join tabs t on t.location_id=g.location_id and t.d=g.business_date
                                    and t.num=right(g.gf_no,3)
                         where g.category='payment' and length(g.gf_no) > 3
                           and not exists (select 1 from exact e where e.booking_id=g.booking_id)
                           and not exists (select 1 from exact e where e.bill_id=t.bill_id)),
            cand as (select * from exact union all select * from fallback),
            ranked as (select *, count(*) filter (where ok) over (partition by booking_id) n_ok,
                              row_number() over (partition by booking_id order by ok desc, bill_id) rn from cand)
            -- only finalized, non-voided tabs match; a key with only voided tabs gets NO match
            select booking_id, location_id, business_date, gf_no, bill_id, n_ok > 1 from ranked where rn = 1 and ok"""
        .replace("__DAYS__", str(WINDOW_DAYS + 10)))


OCC_IMPUTE_DAYS = 90       # look-back for the late-keyed dwell median
OCC_IMPUTE_MIN_HOUR = 10   # >= this many normal bills for an hour-of-day median, else branch median
OCC_IMPUTE_MIN_ALL = 30    # >= this many normal bills for the branch median, else OCC_IMPUTE_FALLBACK
OCC_IMPUTE_FALLBACK = 35   # minutes


def build_occupancy(cur):
    """Hourly seat occupancy (Point 2026-10-07, replaces the daypart utilization).
    One row per live branch x trading day (any dine-in order) x hour, hours = sales_web.seats
    open_hour..close_hour clipped to 10-20. Per dine-in order (finalized, not voided — t_ord):
      interval  = [opened_at, closed_at] (Bangkok); if either is missing ->
                  mp_clean.table_sessions seated_at/left_at. SPLIT CHILDREN (pos_sale_tabs
                  parentsaletabid > 0, splittabname 'Split%') are opened at the moment of the
                  split (dwell ~0-1 min), so they start at their MASTER tab's opened_at — without
                  this ~40% of Silom's lunch bills (one per person paying) fell under the 3-min
                  floor and their customers vanished.
      LATE-KEYED bills (Point 2026-10-08): a non-split bill with opened_at and closed_at both set and
                  closed - opened < 3 min was opened at payment time (OCC ~22% of lunch bills,
                  Rama 9 ~100%) — the customers sat, the POS just has no seat time. Their interval is
                  IMPUTED: opened := closed - median dwell, median = the branch's median dwell of
                  normal bills (3-240 min) opened in the same hour-of-day (hour of the recorded
                  opened_at) over the last OCC_IMPUTE_DAYS days (>= OCC_IMPUTE_MIN_HOUR bills); fallback
                  the branch's overall median (>= OCC_IMPUTE_MIN_ALL bills); fallback OCC_IMPUTE_FALLBACK
                  min. imputed_bills = such bills overlapping the hour. Other intervals < 3 or
                  > 240 min are dropped.
      persons   = main_units + set_units of the order (>= 1) — bowls as persons, ALWAYS, never keyed
                  pax, whatever pax_trust says (Point 2026-10-07): staff may key pax wrong and
                  people join tables later; on average 1 person = 1 main.
    seat_minutes = sum(persons x overlap minutes with the hour); bills_open = orders
    overlapping the hour; turns = orders OPENED in the hour; dwell_min_sum = sum of the dwell
    of those opened orders (avg dwell by hour = dwell_min_sum / turns; imputed bills count with the
    median dwell); persons_opened = sum of persons of those opened orders (served).
    PEAK (Point 2026-10-08): the hourly average hides the ~30-min rush, so per branch-day-hour
    peak_persons = max over the six 10-minute slot starts hh:00, :10 .. :50 of persons seated at that
    instant (bills with open <= t < close), peak_slot = 0-5 of that max (earliest on ties).
    UI (LOCKED 2026-10-07, peak 2026-10-08): headline % = avg over open days of peak_persons / seats,
    second line = hourly average sum(seat_minutes) / (seats x 60 x open days), open days =
    distinct dates in range with a dine-in row in the tree feed (orders > 0)."""
    for c in ("persons_opened int", "imputed_bills int", "peak_persons numeric", "peak_slot smallint"):
        cur.execute(f"alter table if exists sales_web.occupancy_hourly add column if not exists {c}")
    cur.execute("drop table if exists t_occ_lv")
    cur.execute(f"""
        create temp table t_occ_lv on commit drop as
        with ts as (
              select order_id, min(seated_at) seated_at, max(left_at) left_at
              from mp_clean.table_sessions where not coalesce(is_cancelled, false) group by 1),
            sp as (   -- split child -> its master order's opened_at
              select st.branchid || '-' || st.saleid order_id, min(mo.opened_at) master_open
              from mp_raw.pos_sale_tabs st
              join mp_raw.pos_sale_tabs m on m.branchid = st.branchid and m.saletabid = st.parentsaletabid
              join mp_clean.orders mo on mo.order_id = m.branchid || '-' || m.saleid
              where st.parentsaletabid > 0 and st.splittabname like 'Split%'
                and st.starttime >= (current_date - {WINDOW_DAYS + 2})::timestamp
              group by 1),
            iv as (
              select t.order_id, t.location_id, t.business_date,
                     case when mo.opened_at is not null and mo.closed_at is not null
                          then least(coalesce(sp.master_open, mo.opened_at), mo.opened_at)
                          else ts.seated_at end s,
                     case when mo.opened_at is not null and mo.closed_at is not null
                          then mo.closed_at else ts.left_at end e,
                     (mo.opened_at is not null and mo.closed_at is not null and sp.order_id is null
                      and mo.closed_at - mo.opened_at < interval '3 min') late,
                     greatest(t.main_units + t.set_units, 1) pu
              from t_ord t
              join mp_clean.orders mo on mo.order_id = t.order_id
              left join ts on ts.order_id = t.order_id
              left join sp on sp.order_id = t.order_id
              where t.channel = 'dine_in'),
            nv as (   -- normal intervals
              select order_id, location_id, business_date, pu, s {BKK} sl, e {BKK} el,
                     extract(epoch from e - s) / 60 dm
              from iv where not late and e - s between interval '3 min' and interval '240 min'),
            mh as (select location_id, extract(hour from sl)::int hr,
                          percentile_cont(0.5) within group (order by dm) med, count(*) n
                   from nv where business_date >= current_date - {OCC_IMPUTE_DAYS} group by 1, 2),
            ma as (select location_id, percentile_cont(0.5) within group (order by dm) med, count(*) n
                   from nv where business_date >= current_date - {OCC_IMPUTE_DAYS} group by 1),
            lt as (   -- late-keyed: opened := closed - median dwell
              select iv.order_id, iv.location_id, iv.business_date, iv.pu, iv.e {BKK} el,
                     coalesce(case when mh.n >= {OCC_IMPUTE_MIN_HOUR} then mh.med end,
                              case when ma.n >= {OCC_IMPUTE_MIN_ALL} then ma.med end,
                              {OCC_IMPUTE_FALLBACK})::numeric dm
              from iv
              left join mh on mh.location_id = iv.location_id and mh.hr = extract(hour from iv.s {BKK})
              left join ma on ma.location_id = iv.location_id
              where iv.late)
        select order_id, location_id, business_date, pu, sl, el, dm, false imputed from nv
        union all
        select order_id, location_id, business_date, pu, el - dm * interval '1 min', el, dm, true from lt""")
    cur.execute("create index on t_occ_lv (location_id, business_date)")
    cur.execute("analyze t_occ_lv")
    cur.execute("select imputed, count(*) from t_occ_lv group by 1 order by 1")
    print("    intervals (imputed?, n):", cur.fetchall())
    rebuild(cur, "occupancy_hourly",
        """location_id text, business_date date, hour int, seat_minutes numeric, bills_open int,
           turns int, dwell_min_sum numeric, persons_opened int, imputed_bills int,
           peak_persons numeric, peak_slot smallint""",
        f"""with grid as (
              select d.location_id, d.business_date, h,
                     d.business_date + h * interval '1 hour' hs
              from (select distinct location_id, business_date from t_ord where channel = 'dine_in') d
              join sales_web.seats s on s.location_id = d.location_id
              cross join lateral generate_series(greatest(coalesce(s.open_hour, 10), 10),
                                                 least(coalesce(s.close_hour, 20), 20)) h),
            agg as (
              select g.location_id, g.business_date, g.h,
                     round(coalesce(sum(lv.pu * extract(epoch from least(lv.el, g.hs + interval '1 hour')
                                                                 - greatest(lv.sl, g.hs)) / 60), 0), 1) sm,
                     count(lv.order_id) bo,
                     count(*) filter (where lv.sl >= g.hs and lv.sl < g.hs + interval '1 hour') tu,
                     round(coalesce(sum(lv.dm) filter (where lv.sl >= g.hs and lv.sl < g.hs + interval '1 hour'), 0), 1) ds,
                     coalesce(sum(lv.pu) filter (where lv.sl >= g.hs and lv.sl < g.hs + interval '1 hour'), 0)::int po,
                     count(*) filter (where lv.imputed) ib
              from grid g
              left join t_occ_lv lv on lv.location_id = g.location_id and lv.business_date = g.business_date
                          and lv.sl < g.hs + interval '1 hour' and lv.el > g.hs
              group by 1, 2, 3),
            sl as (   -- persons seated at each 10-minute slot start
              select g.location_id, g.business_date, g.h, k, coalesce(sum(lv.pu), 0) p
              from grid g cross join generate_series(0, 5) k
              left join t_occ_lv lv on lv.location_id = g.location_id and lv.business_date = g.business_date
                          and lv.sl <= g.hs + k * interval '10 min' and lv.el > g.hs + k * interval '10 min'
              group by 1, 2, 3, 4),
            pk as (select distinct on (location_id, business_date, h) location_id, business_date, h, p, k
                   from sl order by location_id, business_date, h, p desc, k)
            select a.location_id, a.business_date, a.h, a.sm, a.bo, a.tu, a.ds, a.po, a.ib, pk.p, pk.k
            from agg a join pk using (location_id, business_date, h)""")


# ---------- Step 7: main ----------

BUILDERS = (build_tree, build_hourly, build_dwell, build_pax_trust, build_items, build_options,
            build_pairs, build_distributions, build_members, build_promo, build_calendar,
            build_grab_match, build_occupancy,
            # calculator v2 last: needs tree_daily + occupancy_hourly (dead hours) + set_incremental
            build_set_incremental, build_pair_attach, build_opportunity)


def check():
    with bb.conn.cursor() as cur:
        for t in TABLES:
            cur.execute("select to_regclass(%s)", (f"sales_web.{t}",))
            if not cur.fetchone()[0]:
                print(f"  sales_web.{t}: (missing)")
                continue
            cur.execute(f"select count(*) from sales_web.{t}")
            print(f"  sales_web.{t}: {cur.fetchone()[0]} rows")
    bb.conn.rollback()


def main():
    t0 = dt.datetime.now()
    try:
        with bb.conn.cursor() as cur:
            cur.execute("set local statement_timeout = '280s'")
            print("build_base"); build_base(cur)
            for fn in BUILDERS:
                t1 = dt.datetime.now()
                print(fn.__name__)
                fn(cur)
                print(f"    {(dt.datetime.now()-t1).total_seconds():.1f}s")
            cur.execute("""insert into sales_web.app_status (id, last_build, data_through) values (1, now(), current_date-1)
                           on conflict (id) do update set last_build=excluded.last_build, data_through=excluded.data_through""")
        bb.conn.commit()
    except Exception:
        bb.conn.rollback()
        raise
    print(f"sales_tables ok in {(dt.datetime.now()-t0).total_seconds():.0f}s")


bb = Backbone(load())

if __name__ == "__main__":
    check() if "--check" in sys.argv else main()
