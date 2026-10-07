"""Sales Web payload builder (Task 5, 2026-10-06).

Bakes ONE JSON payload from the sales_web.* tables (materialized by
tools/sales_tables.py + tools/grab_load.py) into
  - sales_web.app_cache id=1   (served verbatim by the live edge function)
  - app/site/data/data.json (offline fallback for the SPA)

Rows are emitted as ARRAYS, not objects, to keep the payload small; the
top-level "cols" object maps every feed key -> its column list so the UI
self-documents. Key names / column orders are the SPA contract — see
Project Sales Web.md (repo Itthicheta/sales) and .superpowers/sdd/2026-10-06-sales-web/task-5-brief.md.
Money definitions pass through unchanged (net = total_thb post-discount,
gross = total + discount).

Payload growth guard (coordinator ruling 2026-10-06, after the first build came
out at 7.5 MB against a 6 MB budget):
  1. Monthly feeds (options, option_by_dish, topping_route, party, ticket, sets,
     members.monthly, grab.match_cov, grab.basket, grab.keywords, grab.options) keep only
     ym >= to_char(current_date - interval '3 months','YYYY-MM'), i.e. the
     current month + 3 prior. Daily feeds keep 90 days.
  2. option_by_dish drops rows with picks <= 2 (the unpopular-tail view reads
     `options`, which keeps every choice).
  3. Budget raised to 8 MB raw (ruling 2026-10-06, round 2; the dashboard app
     serves 8.7 MB through the same edge pattern, gzip transfer ~0.5 MB). The
     60-day auto-trim was removed: daily feeds keep the full 90 days. Over
     budget only prints a WARNING — decide a cut, don't trim silently.
  4. Money values rounded to 2 dp (MONEY column aliases); other numbers 4 dp.
Rows later than data_through (today's partial day) are KEPT; top-level
"today_partial" says whether any daily feed has them — the UI defaults its
range to data_through.

Run: ~/mamapook-data/venv/bin/python ~/sales/app/build/build.py
"""
import datetime as dt
import json
import sys
from pathlib import Path

BACKBONE = Path.home() / "mamapook-data"  # backbone repo = dependency (shared.*, .env)
sys.path.insert(0, str(BACKBONE))
# this repo's tools/ dir imported directly (NOT as package `tools` — that name
# would collide with mamapook-data/tools on sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from shared.env import load          # noqa: E402
from shared.backbone import Backbone  # noqa: E402
from sales_tables import (OPTION_ITEM_GROUPS_RE, WATER_MAX,  # noqa: E402
                                PAID_BEV_MAX, free_item_sql, choice_key_sql)  # one home for Point's rules

OUT = Path(__file__).resolve().parent.parent / "site" / "data" / "data.json"
BUDGET_MB = 8.0
MONEY = {"gross", "disc", "net", "opt_thb", "main_thb", "bev_thb", "side_thb", "top_thb", "des_thb",
         "thb", "price", "paid_thb", "menu_thb", "expected", "uplift", "avg_thb", "avg",
         "comm", "mkt", "payout", "spend", "sales", "bev_paid_thb", "bev_premium_thb",
         "est_thb", "ads_thb", "adjust_thb", "set_thb"}   # column aliases holding THB amounts
YM_MIN = "to_char(current_date - interval '3 months','YYYY-MM')"   # growth guard 1
bb = Backbone(load())

TH = {"gaysorn": "เกษร", "silom": "สีลม", "occ": "OCC", "sathorn-square": "สาทร",
      "all-seasons": "ออลซีซั่นส์", "rama9": "พระราม 9"}


def q(sql):
    with bb.conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def c(v):  # decimals/dates -> json-safe
    if isinstance(v, dt.datetime): return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, (dt.date, dt.time)): return str(v)
    if v is None or isinstance(v, (int, str, bool, list, dict)): return v
    f = float(v)
    return int(f) if f.is_integer() else round(f, 4)


def js(rows):  # object rows (small feeds)
    return [{k: c(v) for k, v in r.items()} for r in rows]


COLS = {}


def feed(key, sql):
    """Array rows; column list (from the SQL aliases) recorded in COLS[key]."""
    with bb.conn.cursor() as cur:
        cur.execute(sql)
        COLS[key] = [d[0] for d in cur.description]
        money = [i for i, k in enumerate(COLS[key]) if k in MONEY]
        rows = [[c(v) for v in r] for r in cur.fetchall()]
        for r in rows:
            for i in money:
                if isinstance(r[i], float):
                    r[i] = round(r[i], 2)
        return rows


def build():
    COLS.clear()
    st = q("select data_through from sales_web.app_status where id = 1")
    data = {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "tz": "Asia/Bangkok",
            "data_through": c(st[0]["data_through"]) if st else None}

    br = q("""select l.location_id id, s.seats,
                     coalesce(s.open_hour, l.open_hour) open, coalesce(s.close_hour, l.close_hour) close
              from mp_clean.locations l left join sales_web.seats s using (location_id)
              where l.location_type = 'branch' and l.is_active and l.is_confirmed_live
                and not l.is_suspected_test
              order by l.branchid""")
    data["branches"] = [{"id": b["id"], "th": TH.get(b["id"], b["id"]), "seats": b["seats"],
                         "open": b["open"], "close": b["close"]} for b in br]
    data["holidays"] = [c(r["d"]) for r in q("select d from sales_web.holidays order by d")]

    data["tree"] = feed("tree", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, daypart, channel, orders,
               pax_keyed pax, main_units main, bev_units bev, bev_water_units bev_w,
               bev_paid_units bev_p, bev_premium_units bev_pr, side_units side, topping_units top,
               dessert_units des, paid_option_picks opt_n, paid_option_thb opt_thb,
               gross_thb gross, discount_thb disc, net_thb net, main_thb, bev_thb, side_thb,
               topping_thb top_thb, dessert_thb des_thb,
               bev_paid_thb, bev_premium_thb,  -- appended last: earlier indices never shift
               set_units "set", set_thb,       -- appended 2026-10-07 (set menus)
               -- appended 2026-10-07 (attach sources): the ในเซต layer = picks forced inside a
               -- set. side/bev/des/top above = ALL sources (menu + option + set);
               -- อัพเซล = total - *_s. opt_n / opt_thb are deprecated (always 0).
               side_set_units side_s, bev_paid_set_units bev_p_s, bev_premium_set_units bev_pr_s,
               dessert_set_units des_s, topping_set_units top_s
        from sales_web.tree_daily where business_date >= current_date - 90
        order by business_date, location_id, daypart, channel""")
    data["hourly"] = feed("hourly", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, hour, channel, orders,
               pax_keyed pax, net_thb net, main_units main
        from sales_web.hourly where business_date >= current_date - 90
        order by business_date, location_id, hour, channel""")
    data["dwell"] = feed("dwell", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, daypart, sessions,
               avg_dwell_min avg_min, avg_party
        from sales_web.dwell where business_date >= current_date - 90
        order by business_date, location_id, daypart""")
    # hourly seat occupancy (2026-10-07, replaces the daypart utilization): dine-in only,
    # occupancy % in the UI = sum(seat_min) / (seats x 60 x days); avg dwell by hour =
    # dwell_sum / turns (dwell of orders OPENED in that hour). See sales_tables.build_occupancy.
    data["occupancy"] = feed("occupancy", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, hour, seat_minutes seat_min,
               bills_open, turns,
               dwell_min_sum dwell_sum,   -- appended: sum of dwell minutes of the `turns` orders
               persons_opened             -- appended 2026-10-07: persons of bills opened in the hour (served)
        from sales_web.occupancy_hourly where business_date >= current_date - 90
        order by business_date, location_id, hour""")
    data["pax_trust"] = feed("pax_trust", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, bowls, keyed_pax keyed,
               ratio, trusted
        from sales_web.pax_trust where business_date >= current_date - 90
        order by business_date, location_id""")
    data["items"] = feed("items", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, channel, item_name item,
               main_category cat, sub_category sub, bev_tier tier, units, thb, bills, avg_price price,
               source src   -- appended 2026-10-07: menu | option | set (route the unit was sold by)
        from sales_web.item_daily where business_date >= current_date - 90
        order by business_date, location_id, channel, item_name""")
    data["options"] = feed("options", f"""
        select location_id loc, ym, channel, modifier_group "group", choice_th choice, picks,
               paid_picks, paid_thb
        from sales_web.option_monthly where ym >= {YM_MIN} order by ym, location_id, channel, modifier_group, picks desc""")
    data["option_by_dish"] = feed("option_by_dish", f"""
        select location_id loc, ym, channel, dish_th dish, modifier_group "group", choice_th choice, picks
        from sales_web.option_by_dish where ym >= {YM_MIN} and picks > 2 order by ym, location_id, channel, dish_th, modifier_group, picks desc""")
    data["topping_route"] = feed("topping_route", f"""
        select location_id loc, ym, channel, topping, as_option_units opt_u, as_option_thb opt_thb,
               as_menu_units menu_u, as_menu_thb menu_thb
        from sales_web.topping_route where ym >= {YM_MIN} order by ym, location_id, channel, topping""")
    data["pairs"] = feed("pairs", """
        select loc, a, b, ab, na, nb, n, lift from (
          select location_id loc, item_a a, item_b b, bills_ab ab, bills_a na, bills_b nb,
                 total_bills n, lift,
                 row_number() over (partition by location_id order by lift desc, bills_ab desc) rk
          from sales_web.pair_lift where lift >= 1.2) x
        where rk <= 300 order by loc, rk""")
    data["party"] = feed("party", f"""
        select location_id loc, ym, channel, daypart, pax_bucket bucket, bills, net_thb net, main_units main
        from sales_web.party_size where ym >= {YM_MIN} order by ym, location_id, channel, daypart, pax_bucket""")
    data["ticket"] = feed("ticket", f"""
        select location_id loc, ym, channel, thb_bucket bucket, bills
        from sales_web.ticket_hist where ym >= {YM_MIN} order by ym, location_id, channel, thb_bucket""")
    data["members"] = {
        "monthly": feed("members.monthly", f"""
            select location_id loc, ym, bills, member_bills, distinct_members members
            from sales_web.members_monthly where ym >= {YM_MIN} order by ym, location_id"""),
        "winback": feed("members.winback", """
            select member_key "key", last_location_id loc, visits, to_char(last_date,'YYYY-MM-DD') last_date,
                   days_since, avg_thb
            from sales_web.member_visits where visits >= 2 and days_since >= 21
            order by visits desc, avg_thb desc limit 200"""),
    }
    data["promo"] = feed("promo", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, channel, promo, bills,
               main_units main, net_thb net
        from sales_web.promo_daily where business_date >= current_date - 90
        order by business_date, location_id, channel, promo""")
    data["sets"] = feed("sets", f"""
        select location_id loc, ym, channel, set_name "name", units, thb
        from sales_web.set_monthly where ym >= {YM_MIN} order by ym, location_id, channel, set_name""")
    data["calendar"] = feed("calendar", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, channel, orders, net_thb net,
               dow, dom, wom, is_holiday hol, rain_mm rain, expected_thb expected, gap_pct gap
        from sales_web.calendar_daily order by business_date, location_id, channel""")
    data["opportunity"] = feed("opportunity", """
        select location_id loc, channel, lever, current_rate rate, best_location_id best_loc,
               best_rate, main_units_30d main30, unit_price_thb price, uplift_thb_month uplift
        from sales_web.opportunity order by uplift_thb_month desc nulls last""")

    g = {}
    g["daily"] = feed("grab.daily", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, gross_thb gross, net_thb net,
               orders, avg_order_thb avg, rating
        from sales_web.grab_daily order by business_date, location_id""")
    g["orders"] = feed("grab.orders", """
        select booking_id booking, location_id loc, to_char(business_date,'YYYY-MM-DD') d, gf_no gf,
               extract(hour from created_at)::int hr,   -- created_at is already Bangkok local
               gross_thb gross, merchant_discount_thb disc, net_thb net, commission_thb comm,
               marketing_fee_thb mkt, payout_thb payout, status, cancel_reason
        from sales_web.grab_orders where category = 'payment'
        order by business_date, location_id, created_at""")
    # cancelled Grab orders live in category 'other' (status ยกเลิก), not 'payment';
    # est_thb = that branch-month's average order value from grab_daily (gross/orders)
    g["cancels"] = feed("grab.cancels", """
        with av as (select location_id, to_char(business_date,'YYYY-MM') ym,
                           sum(gross_thb) / nullif(sum(orders),0) a
                    from sales_web.grab_daily group by 1, 2)
        select g.location_id loc, to_char(g.business_date,'YYYY-MM-DD') d,
               extract(hour from g.created_at)::int hr,   -- created_at is already Bangkok local
               g.gf_no gf, g.cancel_reason reason, g.cancelled_by, round(av.a, 2) est_thb
        from sales_web.grab_orders g
        left join av on av.location_id = g.location_id and av.ym = to_char(g.business_date,'YYYY-MM')
        where g.category = 'other' and g.status = 'ยกเลิก'
        order by g.business_date, g.location_id, g.created_at""")
    # waterfall extras per branch-month: ads + adjustment rows (signs as stored, negative =
    # deducted) and completed-but-not-yet-paid-out orders (their fees are not final yet)
    g["fees"] = feed("grab.fees", """
        select location_id loc, to_char(business_date,'YYYY-MM') ym,
               coalesce(sum(payout_thb) filter (where category = 'ads'), 0) ads_thb,
               coalesce(sum(payout_thb) filter (where category = 'adjustment'), 0) adjust_thb,
               (count(*) filter (where category = 'payment' and status = 'เสร็จสมบูรณ์'))::int unpaid_rows
        from sales_web.grab_orders group by 1, 2 order by 2, 1""")
    g["menu"] = feed("grab.menu", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, item, units, thb
        from sales_web.grab_menu_daily order by business_date, location_id, item""")
    g["offers"] = feed("grab.offers", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, campaign, gross_thb gross,
               net_thb net, orders, spend_thb spend
        from sales_web.grab_offers order by business_date, location_id, campaign""")
    g["peak"] = feed("grab.peak", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, hour, orders
        from sales_web.grab_peak order by business_date, location_id, hour""")
    g["ads"] = feed("grab.ads", """
        select to_char(d,'YYYY-MM-DD') d, hour, campaign, spend_thb spend, impressions impr, clicks,
               menu_visits visits, add_to_cart cart, orders, sales_thb sales
        from sales_web.grab_ads_hourly order by d, hour, campaign""")
    g["keywords"] = feed("grab.keywords", f"""
        select ym, keyword kw, is_brand brand, impressions impr, clicks, spend_thb spend, orders,
               sales_thb sales
        from sales_web.grab_keywords where ym >= {YM_MIN} order by ym, impressions desc""")
    g["miwi"] = feed("grab.miwi", """
        select location_id loc, to_char(business_date,'YYYY-MM-DD') d, wrong, missing,
               total_reported reported, completed_orders completed
        from sales_web.grab_miwi_daily order by business_date, location_id""")
    g["reviews"] = feed("grab.reviews", """
        select location_id loc, to_char(review_date,'YYYY-MM-DD') d, rating, review "text", replied,
               service_type service
        from sales_web.grab_reviews order by review_date desc, location_id""")
    g["combos"] = feed("grab.combos", """
        select location_id loc, to_char(as_of,'YYYY-MM-DD') as_of, combo, n
        from sales_web.grab_combos order by as_of, location_id, n desc""")
    g["options"] = feed("grab.options", f"""
        select m.location_id loc, to_char(m.business_date,'YYYY-MM') ym, so.modifier_group "group",
               so.choice_th choice, count(*) picks
        from sales_web.grab_match m join mp_clean.sold_options so on so.order_id = m.bill_id
        where to_char(m.business_date,'YYYY-MM') >= {YM_MIN}
        group by 1,2,3,4 order by 2, 1, 3, 5 desc""")
    g["match"] = feed("grab.match", """
        select booking_id booking, location_id loc, to_char(business_date,'YYYY-MM-DD') d, gf_no gf,
               bill_id bill, ambiguous
        from sales_web.grab_match order by business_date, location_id, gf_no""")
    g["match_cov"] = feed("grab.match_cov", f"""
        select location_id loc, to_char(business_date,'YYYY-MM') ym, count(*)::int orders,
               (count(*) filter (where exists (select 1 from sales_web.grab_match gm
                                               where gm.booking_id = g.booking_id)))::int matched
        from sales_web.grab_orders g where category = 'payment'
          and to_char(business_date,'YYYY-MM') >= {YM_MIN}
        group by 1, 2 order by 2, 1""")
    # Grab basket = matched Grab orders x POS units, same source logic as the in-store tree
    # (2026-10-07): order lines (free items excluded) + option picks merged into their menu
    # item via mp_clean.map_option_item. paid_opt is DEPRECATED (always 0) — option picks now
    # sit in sides / bev / dessert. No split-dupe filter (as before).
    g["basket"] = feed("grab.basket", f"""
        with m as (select distinct location_id, to_char(business_date,'YYYY-MM') ym, bill_id
                   from sales_web.grab_match
                   where to_char(business_date,'YYYY-MM') >= {YM_MIN}),
        u as (select m.location_id, m.ym, m.bill_id, li.tree_category cat,
                     li.unit_price_inc_vat_thb price, li.qty
              from m join mp_clean.order_lines li
                on li.order_id = m.bill_id and li.is_active and not li.is_special_line
              left join mp_clean.items it on it.itemid = li.itemid
              where not {free_item_sql('it.item_code', 'li.name_th')}
              union all
              select m.location_id, m.ym, m.bill_id, it.tree_category, it.retail_price_thb,
                     so.choice_qty * mo.qty_mult
              from m join mp_clean.order_lines li
                on li.order_id = m.bill_id and li.is_active and not li.is_special_line
              join mp_clean.sold_options so on so.line_id = li.line_id
              join mp_clean.map_option_item mo on mo.choice_key = {choice_key_sql('so.choice_th')}
              join mp_clean.items it on it.item_code = mo.item_code
              where so.modifier_group ~ '{OPTION_ITEM_GROUPS_RE}')
        select location_id loc, ym,
               sum(qty) filter (where cat='main') mains,
               sum(qty) filter (where cat='side') sides,
               sum(qty) filter (where cat='beverage' and price > {WATER_MAX} and price <= {PAID_BEV_MAX}) bev_paid,
               sum(qty) filter (where cat='beverage' and price > {PAID_BEV_MAX}) bev_prem,
               sum(qty) filter (where cat='dessert') dessert,
               sum(qty) filter (where cat='topping') topping,
               0 paid_opt,
               count(distinct bill_id)::int orders,
               coalesce(sum(qty) filter (where cat='set'), 0) sets   -- appended last
        from u group by 1, 2 order by 2, 1""")
    g["months"] = [r["ym"] for r in q("""select distinct to_char(business_date,'YYYY-MM') ym
                                         from sales_web.grab_daily
                                         union select distinct to_char(business_date,'YYYY-MM')
                                         from sales_web.grab_orders order by 1""")]
    data["grab"] = g
    dt_ = data["data_through"]
    daily = [(k, data[k]) for k in ("tree", "hourly", "dwell", "occupancy", "pax_trust", "items", "promo", "calendar")] + \
            [("grab." + k, g[k]) for k in ("daily", "orders", "menu", "offers", "peak", "miwi", "match")]
    data["today_partial"] = bool(dt_) and any(
        r[COLS[k].index("d")] > dt_ for k, f in daily for r in f)
    data["cols"] = {k: COLS[k] for k in COLS}
    return data


data = build()
payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
if len(payload.encode()) / 1e6 > BUDGET_MB:
    print(f"WARNING: payload {len(payload.encode())/1e6:.2f} MB > {BUDGET_MB} MB budget")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(payload)
print(f"payload {len(payload.encode())/1e6:.2f} MB raw -> {OUT}")

with bb.conn.cursor() as cur:
    cur.execute("""insert into sales_web.app_cache (id, payload, updated_at) values (1, %s, now())
                   on conflict (id) do update set payload = excluded.payload, updated_at = now()""",
                (payload,))
bb.conn.commit()
print("app_cache updated")


def n(v): return len(v) if isinstance(v, list) else v


for k, v in data.items():
    if k == "cols": continue
    if isinstance(v, dict):
        print(f"  {k}: " + ", ".join(f"{kk}={n(vv)}" for kk, vv in v.items()))
    elif isinstance(v, list):
        print(f"  {k}={len(v)}")
