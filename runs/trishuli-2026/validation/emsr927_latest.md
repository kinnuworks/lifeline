# Check against Copernicus EMS EMSR927 (latest grading)

Run `trishuli-2026`. Reference: EMS grading maps (photo-interpretation of VHR optical images). Used only to check results. Credit: European Union, Copernicus Emergency Management Service data.
Ranges are 95% intervals (block bootstrap over 200 m blocks for the extent, Wilson for rates).

| AOI | Status | Extent IoU | Extent F1 | F1, observed pixels | Building hit rate | Road agreement | Bridges flagged |
|---|---|---|---|---|---|---|---|
| AOI01 Syapru Besi | compared (GRA_PRODUCT) | 0.38 (0.28 to 0.46) | 0.55 (0.44 to 0.63) | 0.57 (0.46 to 0.65) | 0.86 (0.82 to 0.90) (n=249) | 0.81 | 5/5 |
| AOI02 Timure | compared (GRA_PRODUCT) | 0.21 (0.13 to 0.29) | 0.35 (0.23 to 0.45) | 0.37 (0.25 to 0.48) | 0.65 (0.59 to 0.69) (n=339) | 0.71 | 0/1 |
| AOI03 Bidur | compared (GRA_MONIT01) | 0.71 (0.69 to 0.74) | 0.83 (0.81 to 0.85) | 0.84 (0.82 to 0.85) | 0.98 (0.97 to 0.98) (n=2002) | 0.74 | 18/24 |
| AOI04 Bharatpur | not compared: EMS cancelled this AOI and delivered no product | | | | | | |
| AOI05 Phosretar | compared (GRA_MONIT01) | 0.62 (0.58 to 0.65) | 0.76 (0.73 to 0.79) | 0.76 (0.73 to 0.79) | 0.60 (0.55 to 0.65) (n=325) | 0.67 | 24/39 |
| AOI06 Kyundi | not compared: EMS cancelled this AOI and delivered no product | | | | | | |

## All compared AOIs together

- Event extent: IoU 0.62 (0.59 to 0.64), precision 0.87, recall 0.68, F1 0.76 (0.74 to 0.78).
- Event extent, pixels our radar observed: IoU 0.62, F1 0.77 (0.75 to 0.79).
- Buildings: 2590 of 2915 EMS destroyed or damaged buildings flagged (hit rate 0.89 (0.88 to 0.90)). False alarm rate on EMS no visible damage: n/a (n=0). 1289 EMS records had no OSM building within 15 m.
- Roads (length weighted): agreement 0.71, recall on EMS destroyed or damaged 0.90, false alarm on EMS no visible damage 0.37.
- Bridges: we flagged 47/69 EMS destroyed or damaged bridges.

## Cut-off check

- All settlements: agree on 296 of 338 (reference cut off 8, ours 50).
- Settlements whose route crosses an EMS AOI: agree on 70 of 98.
- EMS graded roads only inside its AOIs. Damage outside them is unknown to the reference, so reference cut-offs are a lower bound. The subset whose pre-event route to the nearest hub crosses an EMS AOI is the fairer comparison.
