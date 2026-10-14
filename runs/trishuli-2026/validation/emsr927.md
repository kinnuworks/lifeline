# Check against Copernicus EMS EMSR927 (first grading)

Run `trishuli-2026`. Reference: EMS grading maps (photo-interpretation of VHR optical images). Used only to check results. Credit: European Union, Copernicus Emergency Management Service data.
Ranges are 95% intervals (block bootstrap over 200 m blocks for the extent, Wilson for rates).

| AOI | Status | Extent IoU | Extent F1 | F1, observed pixels | Building hit rate | Road agreement | Bridges flagged |
|---|---|---|---|---|---|---|---|
| AOI01 Syapru Besi | compared (GRA_PRODUCT) | 0.38 (0.28 to 0.46) | 0.55 (0.44 to 0.63) | 0.57 (0.46 to 0.65) | 0.86 (0.82 to 0.90) (n=249) | 0.81 | 5/5 |
| AOI02 Timure | compared (GRA_PRODUCT) | 0.21 (0.13 to 0.29) | 0.35 (0.23 to 0.45) | 0.37 (0.25 to 0.48) | 0.65 (0.59 to 0.69) (n=339) | 0.71 | 0/1 |
| AOI03 Bidur | compared (GRA_PRODUCT) | 0.71 (0.67 to 0.73) | 0.83 (0.80 to 0.85) | 0.83 (0.81 to 0.85) | 0.98 (0.97 to 0.98) (n=1502) | 0.73 | 13/18 |
| AOI04 Bharatpur | not compared: EMS cancelled this AOI and delivered no product | | | | | | |
| AOI05 Phosretar | compared (GRA_PRODUCT) | 0.59 (0.55 to 0.63) | 0.74 (0.71 to 0.77) | 0.74 (0.71 to 0.77) | 0.62 (0.56 to 0.68) (n=269) | 0.66 | 13/22 |
| AOI06 Kyundi | not compared: EMS cancelled this AOI and delivered no product | | | | | | |

## All compared AOIs together

- Event extent: IoU 0.59 (0.57 to 0.62), precision 0.86, recall 0.66, F1 0.75 (0.72 to 0.77).
- Event extent, pixels our radar observed: IoU 0.60, F1 0.75 (0.73 to 0.77).
- Buildings: 2067 of 2359 EMS destroyed or damaged buildings flagged (hit rate 0.88 (0.86 to 0.89)). False alarm rate on EMS no visible damage: n/a (n=0). 989 EMS records had no OSM building within 15 m.
- Roads (length weighted): agreement 0.71, recall on EMS destroyed or damaged 0.89, false alarm on EMS no visible damage 0.37.
- Bridges: we flagged 31/46 EMS destroyed or damaged bridges.

## Cut-off check

- All settlements: agree on 295 of 338 (reference cut off 7, ours 50).
- Settlements whose route crosses an EMS AOI: agree on 69 of 98.
- EMS graded roads only inside its AOIs. Damage outside them is unknown to the reference, so reference cut-offs are a lower bound. The subset whose pre-event route to the nearest hub crosses an EMS AOI is the fairer comparison.
