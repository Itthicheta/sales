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
