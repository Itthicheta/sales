# Section 2 — heatmap % mode + hourly seat occupancy (2026-10-07)

## What changed
- **Heatmap unit toggle** (hour × branch, orders / customers / net): จำนวน | % ของสาขา | % ของชั่วโมง.
  % ของสาขา = each branch row sums to 100 (colour per row); % ของชั่วโมง = each hour column sums to 100
  across branches (colour per column). Checked live: rows sum to 99–101 (display rounding), columns too.
- **Seat occupancy by hour** (ที่นั่งถูกใช้ รายชั่วโมง) replaces the daypart utilization card.
  New table `sales_web.occupancy_hourly` (tools/sales_tables.py `build_occupancy`), payload feed
  `occupancy` (90 days, 3,276 rows; payload 6.66 MB). Cell = Σ seat-minutes ÷ (seats × 60 × days),
  capped at 100% on screen, raw in the tooltip. Second row per branch = turns per seat per hour.
  Lunch 11–13 / dinner 17–20 column groups shaded. Notes in TH + EN.
- **Dwell by hour** (avg dwell of bills opened in that hour) added above the daypart dwell table.

## Method decision found while building: split bills
A table that splits the bill creates one child bill per payer, opened at the moment of the split
(dwell ≈ 1 min). At Silom lunch that's ~39% of bills. Under the 3-min floor they were dropped,
and so were their customers (each child carries pax 1 / 1 bowl, the master keeps only its own share).
Fix: a split child (`mp_raw.pos_sale_tabs.parentsaletabid > 0`, `splittabname like 'Split%'`) starts at its
master tab's `opened_at`. 1,120 of 1,133 split children in the window have a finalized master.

## Reality check — weekday avg occupancy %, 30 days to 2026-10-06
| branch | days | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | avg dwell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| All Seasons | 22 | 1 | 18 | **63** | 23 | 7 | 4 | 2 | 5 | 12 | 9 | 1 | 36.6 |
| Gaysorn | 22 | 1 | 7 | **31** | 16 | 8 | 6 | 6 | 5 | 9 | 11 | 1 | 33.2 |
| OCC | 22 | 1 | 11 | **43** | 15 | 5 | 3 | 3 | 6 | 9 | 10 | 1 | 35.0 |
| Rama 9 | 16 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | – | – | – | – | 20.3 |
| Sathorn | 22 | 1 | 12 | **44** | 22 | 6 | 5 | 4 | 5 | 9 | 6 | 0 | 34.3 |
| Silom | 22 | 1 | 11 | **52** | 16 | 3 | 2 | 2 | 4 | 10 | 10 | 1 | 37.6 |

Weekends: 12:00 is 4–11% everywhere (office branches). Dwell 33–38 min = inside the 25–45 expected band.

**Against the expectation (12:00 ≈ 70–100% at Silom/Sathorn): it lands lower — 52% / 44%.**
16:00 is ≤ 6% everywhere, as expected. Why the hour looks less full than the floor:
1. The rush is ~30 minutes, not an hour. 5-minute snapshots (weekdays): Silom averages ~45 guests in
   house at 12:20–12:30 (62% of 73 seats; best day 60 = 82%); Sathorn ~33 (52%; best day 74 = 116%).
2. Seat % is not table %. Silom has 73 seats on 34 tables and the average party is ~1.6, so solo
   diners at 2-seat tables make the room look full with ~60–70% of seats used, and a queue forms.
3. Bill time is shorter than seat time. The bill opens when the order is keyed, after people sit down.
Possible follow-ups (not built): a 15-minute peak view, or table occupancy (bills ÷ tables).

## Odd things
- **Rama 9 can't be measured.** About 90% of its dine-in bills are opened and closed within 3 min
  (counter-style keying), so they are dropped and occupancy reads ~0%. Fixing this means changing how
  bills are keyed (open the bill when the order is taken).
- 1,746 non-split bills across branches in the last 30 days are < 3 min (Rama 9 is most of them). They
  are excluded as specified.
- Grab leg of section 2 unchanged; in Sep–Oct it shows "no Grab data" because Grab exports cover Aug only.

---

## 2026-10-08 addendum — late-keyed imputation + peak 10-minute slot (Point)

**Rule 1 — late-keyed bills.** Non-split dine-in bills with closed − opened < 3 min were opened at
payment. Now opened := closed − the branch's median dwell of normal bills (3–240 min) opened in the same
hour-of-day, last 90 days (≥ 10 bills; else branch median ≥ 30 bills; else 35 min). New column
`imputed_bills`.

**Rule 2 — peak slot.** `peak_persons` = max over hh:00, :10 … :50 of persons seated at that instant;
`peak_slot` 0–5. UI headline = peak % (avg of daily peaks ÷ seats), second line = hourly average.

### Weekdays, 30 days to 2026-10-07, 12:00 hour
| branch | seats | peak % (UI, avg of daily peaks) | busiest-slot avg % | hourly avg before | hourly avg after | modal peak slot |
|---|---|---|---|---|---|---|
| Silom | 73 | 82 (raw 81.6) | 75.5 (12:30) | 64.7 | 64.8 | 12:20 |
| All Seasons | 60 | 76 | 70.2 (12:20) | 64.3 | 64.4 | 12:10 |
| OCC | 66 | 64 | 58.7 (12:30) | 43.1 | 50.0 | 12:30 |
| Sathorn | 64 | 60 | 54.4 (12:30) | 48.0 | 48.0 | 12:30 |
| Gaysorn | 54 | 50 | 43.3 (12:40) | 35.9 | 36.1 | 12:40 |
| Rama 9 | 16 | 10 | 10.3 (12:30) | 0.0 | 4.5 | 12:30 (days with a peak > 0) |

Point's reference numbers (Silom 79, ASP 70, OCC 59, Sathorn 54, Gaysorn 43) match the **busiest-slot
average** (max over slots of the per-slot average across days). The specified UI figure — average of each
day's own peak — is 5–6 pp higher because the peak moves between 12:10 and 12:40 from day to day.

### Imputed share of dine-in bills (weekdays, 30 days)
| branch | bills | imputed | share | lunch share (closed 11–13) | imputed dwell (min) |
|---|---|---|---|---|---|
| All Seasons | 1,438 | 111 | 7.7% | 2.4% | 30.0 |
| Gaysorn | 1,500 | 43 | 2.9% | 0.7% | 31.4 |
| OCC | 1,553 | 276 | 17.8% | 23.2% | 33.5 |
| Rama 9 | 242 | 224 | 92.6% | 76.2% | 19.6 |
| Sathorn | 1,561 | 138 | 8.8% | 3.4% | 28.2 |
| Silom | 1,898 | 113 | 6.0% | 2.0% | 32.0 |

### Sathorn by tables
At each weekday's 12:00 peak slot: 38.4 persons seated, 13.5 bills open on 33 tables (41%); max 19
bills; never 33. By the POS, Sathorn is not table-full at lunch — either parties share tables, bills
open late, or the floor is fuller than the POS shows.

### Odd things
- Rama 9 imputes from only a handful of "normal" bills: its median dwell is ~20 min, against 28–34 min
  elsewhere. It is an estimate, tagged in the card, and still excluded from dead hours.
- Outside lunch, imputed bills at All Seasons and Sathorn run 8–9% (mostly off-peak), which lifts their
  off-peak averages slightly. Dead-hour lever after rebuild: ฿11k–64k/month (was ฿11k–63k).
- Pipeline: build_occupancy 1.4 s; sales_tables.py total 12 s.
