# Opportunity calculator v2: working report (2026-10-08)

Spec: Point's decisions of 2026-10-08. Definitions are in `Project Sales Web.md`, under "Section 3 — opportunity calculator v2".
Window: last 30 full days (2026-09-08 .. 2026-10-07). Dine-in and take-away are separate rows. Delivery is excluded.

## New top 10 (all branches, by +฿/month)
| # | Branch | Ch | Lever | Now | Peer target | Own best (4 wk from) | Used | Value basis | +฿/month |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Silom | dine | dead_hours | occ 10% | – | – | 50% occ | 16,010 people × ฿210/head | 3,359,149 |
| 2 | Sathorn | dine | dead_hours | 9% | – | – | 50% | 15,033 × ฿218 | 3,274,770 |
| 3 | OCC | dine | dead_hours | 8% | – | – | 50% | 15,373 × ฿200 | 3,080,094 |
| 4 | All Seasons | dine | dead_hours | 11% | – | – | 50% | 13,857 × ฿209 | 2,897,252 |
| 5 | Gaysorn | dine | dead_hours | 9% | – | – | 50% | 13,073 × ฿206 | 2,689,789 |
| 6 | Rama 9 | dine | Sharing | 3.3% | none (no peers) | 7.4% (30/07) | own | ฿234 avg Sharing price | 8,368 |
| 7 | Rama 9 | dine | side | 44.0% | none | 54.0% (30/07) | own | ฿68 avg side price | 5,918 |
| 8 | Silom | dine | side | 34.2% | 38.6% Gaysorn | 36.6% (27/08) | own | ฿65 | 5,559 |
| 9 | OCC | dine | bev_premium | 14.9% | 14.9% OCC (itself = 2nd) | 17.7% (23/07) | own | ฿51 = premium avg − water ฿16.05 | 4,469 |
| 10 | OCC | dine | set | 3.4% | 3.4% OCC (itself) | 5.6% (30/07) | own | ฿54.45 measured / set | 3,749 |

The next rows are OCC side ฿3,226, Gaysorn Sharing ฿2,994, OCC Sharing ฿2,983 (peer: Gaysorn), Rama 9 bev_premium ฿2,687, and All Seasons tradeup ฿1,963.
Total for the levers without dead hours: **฿70k/month**. Dead hours add up to ฿15.3M/month.

**Flag: dead hours dominate.** At a 50% target, the dead-hours row asks for about 450–530 more diners per day per branch (hours 10–20, 12:00 already ≥ 50% only at Silom). That comes to ฿2.7–3.4M/month per branch, about 4× each branch's actual in-store sales. The math follows the spec. As a ranking item, though, it is a theoretical ceiling. It takes the top 5 of the table and all 3 lines of the section 8 digest. The UI headline total excludes it and labels it as a ceiling. Possible fixes for Point to choose from: (a) restrict to the 14–17 band, (b) use a target relative to the branch's own occupancy (for example +10 pp), or (c) keep it out of the ranked list and the digest.

## set_incremental (in-store, last 90 full days)
| Branch | Set bills | Non-set bills | Δ net per meal (set − no set) | Persons / set-bill | Sets / set-bill | **฿ per set** |
|---|---|---|---|---|---|---|
| All Seasons | 94 | 2,498 | 23.78 | 2.80 | 1.43 | **46.67** |
| Gaysorn | 192 | 3,551 | 27.86 | 1.68 | 1.13 | **41.46** |
| OCC | 132 | 2,825 | 37.64 | 1.94 | 1.34 | **54.45** |
| Sathorn | 97 | 3,928 | 26.95 | 2.04 | 1.29 | **42.69** |
| Silom | 288 | 3,365 | 20.18 | 2.32 | 1.35 | **34.60** |
| Rama 9 | 0 | 641 | – | – | – | NULL (sells no sets) |

All values are positive and in the tens of baht, which passes the sanity check (the old method gave ฿230). Decision: the spec's "Δ × persons per set-bill" is the increment per set-BILL. I divided it by sets per set-bill (1.1–1.4) to get a per-SET value, because rate = sets ÷ meals. Without the division the values would be ฿34–67.

## Trade-up ธรรมดา → ทรงเครื่อง (dine-in, 30 days)
List-price step is ฿53.50 in every one of the 13 families that have a ทรงเครื่อง version, so the weighted value is **฿53.50** at every branch.
| Branch | ทรงเครื่อง share | Base bowls | เครื่องใน share (info) |
|---|---|---|---|
| All Seasons | 34.7% | 887 | 18% |
| Silom | 34.1% | 868 | 15% |
| Rama 9 | 33.7% | 181 | 20% |
| OCC | 32.0% | 771 | 15% |
| Sathorn | 31.7% | 853 | 14% |
| Gaysorn | 29.7% | 797 | 12% |

Families without a ทรงเครื่อง item are out of scope: ต้มยำน้ำใส, ต้มยำน้ำข้น and น้ำ (N7–N12, G7–G12). Value uses LIST prices, because realized line prices include option charges.

## Menu-pair scripts (example)
"ก๋วยเตี๋ยว ต้มยำแห้ง + ตำเส้นเล็ก: ออลซีซั่นส์ 13% · OCC 5% → ถ้า OCC ทำได้เท่าออลซีซั่นส์ = +฿2,462/เดือน" (top line, all branches).
Also: "ก๋วยเตี๋ยวคอหมู + ซุปบ๊วย: สาทร 12% · OCC 6% → +฿2,282/เดือน".

## Dead-hour value at 50% target (last 30 days)
| Branch | Hours below 50% | People/day | ฿/head | ฿/day | ฿/month |
|---|---|---|---|---|---|
| Silom | 10, 11, 13–20 | 534 | 210 | 111,972 | 3,359,149 |
| Sathorn | 10–20 | 518 | 218 | 112,923 | 3,274,770 (29 open days) |
| OCC | 10–20 | 512 | 200 | 102,670 | 3,080,094 |
| All Seasons | 10–20 | 462 | 209 | 96,575 | 2,897,252 |
| Gaysorn | 10–20 | 436 | 206 | 89,660 | 2,689,789 |
| Rama 9 | not measurable (counter-style keying) | | | | |

## Decisions taken in this build
- Set value is per set, not per set-bill (see above). set_incremental uses a 90-day window because sets are few.
- Tradeup value uses list prices (฿53.50). Its `meals_30d` holds the variant-bowl base, not meals.
- The peer pool needs a denominator ≥ 100. As a result, take-away tradeup (bases 24–78 bowls) gets no target: it is flagged "no target (sample too small)" with uplift 0.
- With fewer than 3 qualifiers (every take-away case), the peer target is the highest other office branch.
- Dead hours: an hour with < 1 bill opened per open day uses the branch avg dwell. Without this, 20:00 had 8-min dwells and the people needed exploded. Rama 9 is excluded.
- Pair scripts leave out water-tier drinks and set picks. Channels are summed in the UI. Each branch is compared with the best OTHER office branch, with ≥ 30 main bills on both sides. Rama 9 is not scripted because it has no peers. `pair_attach` has an extra `item_price_thb` column.
- The UI headline total excludes dead hours.
