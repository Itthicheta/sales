-- migrations/011_seats_2026-10-07.sql — Sales Web reference inputs, snapshot of the live DB 2026-10-07.
-- Idempotent: safe to re-run. Seats per branch (Point 2026-10-07; seats = tables' seats + bar + round stools)
-- feed the section-2 utilization heatmap; holidays feed the calendar model (section 7).
-- Columns tables/bar_seats/stool_seats/note were added to the live table after 010 — ensured here.

alter table sales_web.seats add column if not exists tables integer;
alter table sales_web.seats add column if not exists bar_seats integer;
alter table sales_web.seats add column if not exists stool_seats integer;
alter table sales_web.seats add column if not exists note text;

insert into sales_web.seats (location_id, seats, open_hour, close_hour, tables, bar_seats, stool_seats, note) values
  ('rama9',          16, 10, 16,  6,  0, 0, 'Point 2026-10-07: โต๊ะ 6 · ที่นั่ง 16'),
  ('gaysorn',        54, 10, 20, 27,  0, 0, 'Point 2026-10-07: โต๊ะ 27 · ที่นั่ง 54'),
  ('occ',            66, 10, 20, 27, 12, 0, 'Point 2026-10-07: โต๊ะ 27 · ที่นั่ง 54 · บาร์ 12'),
  ('all-seasons',    60, 10, 20, 26,  0, 8, 'Point 2026-10-07: โต๊ะ 26 · ที่นั่ง 52 · เก้าอี้กลม 8'),
  ('sathorn-square', 64, 10, 20, 33,  0, 0, 'Point 2026-10-07: โต๊ะ 33 · ที่นั่ง 64'),
  ('silom',          73, 10, 20, 34,  0, 6, 'Point 2026-10-07: โต๊ะ 34 · ที่นั่ง 67 · เก้าอี้กลม 6')
on conflict (location_id) do update set
  seats = excluded.seats, open_hour = excluded.open_hour, close_hour = excluded.close_hour,
  tables = excluded.tables, bar_seats = excluded.bar_seats, stool_seats = excluded.stool_seats,
  note = excluded.note;

-- Thai public holidays 2026 (Bank of Thailand list), as currently in sales_web.holidays
insert into sales_web.holidays (d, name_th) values
  ('2026-01-01', 'วันขึ้นปีใหม่'),
  ('2026-01-02', 'วันหยุดพิเศษ (ธปท.)'),
  ('2026-03-03', 'วันมาฆบูชา'),
  ('2026-04-06', 'วันจักรี'),
  ('2026-04-13', 'วันสงกรานต์'),
  ('2026-04-14', 'วันสงกรานต์'),
  ('2026-04-15', 'วันสงกรานต์'),
  ('2026-05-01', 'วันแรงงานแห่งชาติ'),
  ('2026-05-04', 'วันฉัตรมงคล'),
  ('2026-05-31', 'วันวิสาขบูชา'),
  ('2026-06-01', 'วันหยุดชดเชยวันวิสาขบูชา'),
  ('2026-06-03', 'วันเฉลิมพระชนมพรรษาสมเด็จพระราชินี'),
  ('2026-07-28', 'วันเฉลิมพระชนมพรรษา ร.10'),
  ('2026-07-29', 'วันอาสาฬหบูชา'),
  ('2026-08-12', 'วันแม่แห่งชาติ'),
  ('2026-10-13', 'วันนวมินทรมหาราช'),
  ('2026-10-23', 'วันปิยมหาราช'),
  ('2026-12-05', 'วันพ่อแห่งชาติ'),
  ('2026-12-07', 'วันหยุดชดเชยวันพ่อแห่งชาติ'),
  ('2026-12-10', 'วันรัฐธรรมนูญ'),
  ('2026-12-31', 'วันสิ้นปี')
on conflict (d) do update set name_th = excluded.name_th;
