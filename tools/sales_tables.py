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
          "opportunity", "grab_match", "occupancy_hourly"]


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


def build_opportunity(cur):
    """Opportunity calculator, last 30 full days, per branch x channel x lever.
    Denominator = main occasions (main + set units — a set is a meal).
    Upsell levers (side / bev_paid / bev_premium / dessert / topping) use the อัพเซล layer
    only: units sold as a menu line or a paid option (source <> 'set'), so the forced
    picks inside a set don't inflate a branch's attach. Price = that layer's avg ฿/unit.
    Lever `set` (2026-10-07) = set units / main occasions, priced at the avg set price.
    best = highest branch rate for that channel;
    uplift ฿/month = (best - current) x main occasions 30d x avg unit price of the lever.
    POS channel 'delivery' is excluded: those are Grab orders keyed at POS markup
    prices (Grab money comes only from the Grab files). Drink price is per tier
    (paid / premium); water baht never enters a drink price."""
    rebuild(cur, "opportunity",
        """location_id text, channel text, lever text, current_rate numeric, best_location_id text,
           best_rate numeric, main_units_30d numeric, unit_price_thb numeric, uplift_thb_month numeric""",
        """with u as (
             select location_id, channel,
                    case when tree_category = 'side' then 'side'
                         when bev_tier = 'paid' then 'bev_paid'
                         when bev_tier = 'premium' then 'bev_premium'
                         when tree_category = 'dessert' then 'dessert'
                         when tree_category = 'topping' then 'topping'
                         when tree_category = 'set' then 'set' end lever,
                    sum(qty) units, sum(thb) thb
             from t_units
             where business_date between current_date-30 and current_date-1
               and channel <> 'delivery' and source <> 'set'
             group by 1,2,3),
           m as (
             select location_id, channel, sum(main_units + set_units) mu
             from t_ord
             where business_date between current_date-30 and current_date-1
               and channel <> 'delivery' group by 1,2 having sum(main_units + set_units) >= 100),
           -- a branch that sells none of a lever is priced at the channel's avg price
           pc as (select channel, lever, sum(thb)/nullif(sum(units),0) p from u group by 1,2),
           r as (
             select m.location_id, m.channel, l.lever, coalesce(u.units,0)/m.mu rate, m.mu,
                    coalesce(u.thb/nullif(u.units,0), pc.p) price
             from m
             cross join (values ('side'),('bev_paid'),('bev_premium'),('dessert'),('topping'),('set')) l(lever)
             left join u on u.location_id=m.location_id and u.channel=m.channel and u.lever=l.lever
             left join pc on pc.channel=m.channel and pc.lever=l.lever),
           best as (select distinct on (channel, lever) channel, lever, location_id, rate
                    from r where rate is not null order by channel, lever, rate desc)
           select r.location_id, r.channel, r.lever, round(r.rate,3), b.location_id, round(b.rate,3),
                  r.mu, round(coalesce(r.price,0)),
                  round(greatest(b.rate - coalesce(r.rate,0), 0) * r.mu * coalesce(r.price,0))
           from r join best b on b.channel=r.channel and b.lever=r.lever""")


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


def build_occupancy(cur):
    """Hourly seat occupancy (Point 2026-10-07, replaces the daypart utilization).
    One row per live branch x trading day (any dine-in order) x hour, hours = sales_web.seats
    open_hour..close_hour clipped to 10-20. Per dine-in order (finalized, not voided — t_ord):
      interval  = [opened_at, closed_at] (Bangkok); if either is missing ->
                  mp_clean.table_sessions seated_at/left_at. SPLIT CHILDREN (pos_sale_tabs
                  parentsaletabid > 0, splittabname 'Split%') are opened at the moment of the
                  split (dwell ~0-1 min), so they start at their MASTER tab's opened_at — without
                  this ~40% of Silom's lunch bills (one per person paying) fell under the 3-min
                  floor and their customers vanished. Intervals < 3 or > 240 min are dropped.
      persons   = main_units + set_units of the order (>= 1) — bowls as persons, ALWAYS, never keyed
                  pax, whatever pax_trust says (Point 2026-10-07): staff may key pax wrong and
                  people join tables later; on average 1 person = 1 main.
    seat_minutes = sum(persons x overlap minutes with the hour); bills_open = orders
    overlapping the hour; turns = orders OPENED in the hour; dwell_min_sum = sum of the dwell
    of those opened orders (avg dwell by hour = dwell_min_sum / turns).
    UI: occupancy % = sum(seat_minutes) / (seats x 60 x days)."""
    rebuild(cur, "occupancy_hourly",
        """location_id text, business_date date, hour int, seat_minutes numeric, bills_open int,
           turns int, dwell_min_sum numeric""",
        f"""with ts as (
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
                     greatest(t.main_units + t.set_units, 1) pu
              from t_ord t
              join mp_clean.orders mo on mo.order_id = t.order_id
              left join ts on ts.order_id = t.order_id
              left join sp on sp.order_id = t.order_id
              where t.channel = 'dine_in'),
            lv as (
              select order_id, location_id, business_date, pu,
                     s {BKK} sl, e {BKK} el, extract(epoch from e - s) / 60 dm
              from iv where e - s between interval '3 min' and interval '240 min'),
            grid as (
              select d.location_id, d.business_date, h,
                     d.business_date + h * interval '1 hour' hs
              from (select distinct location_id, business_date from t_ord where channel = 'dine_in') d
              join sales_web.seats s on s.location_id = d.location_id
              cross join lateral generate_series(greatest(coalesce(s.open_hour, 10), 10),
                                                 least(coalesce(s.close_hour, 20), 20)) h)
            select g.location_id, g.business_date, g.h,
                   round(coalesce(sum(lv.pu * extract(epoch from least(lv.el, g.hs + interval '1 hour')
                                                               - greatest(lv.sl, g.hs)) / 60), 0), 1),
                   count(lv.order_id),
                   count(*) filter (where lv.sl >= g.hs and lv.sl < g.hs + interval '1 hour'),
                   round(coalesce(sum(lv.dm) filter (where lv.sl >= g.hs and lv.sl < g.hs + interval '1 hour'), 0), 1)
            from grid g
            left join lv on lv.location_id = g.location_id and lv.business_date = g.business_date
                        and lv.sl < g.hs + interval '1 hour' and lv.el > g.hs
            group by 1, 2, 3""")


# ---------- Step 7: main ----------

BUILDERS = (build_tree, build_hourly, build_dwell, build_pax_trust, build_items, build_options,
            build_pairs, build_distributions, build_members, build_promo, build_calendar,
            build_opportunity, build_grab_match, build_occupancy)


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
