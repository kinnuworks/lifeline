# Live judging: questions and answers

A rehearsal sheet for the live session. Read it aloud a few times; answer in your own words.

## The run

**How do I start a run on an area you choose?**
On the website, open "Run any valley", draw a box (up to about 60 x 60 km) and pick the flood date. From a terminal: `uv run lifeline run --bbox WEST SOUTH EAST NORTH --date YYYY-MM-DD`. It finds the images, maps the change, reads OpenStreetMap as it was before the flood, checks every building, road and bridge, works out who is cut off and writes the report in English and Nepali.

**Where does the data come from, and do you need an account?**
Sentinel-1 radar, Sentinel-2 optical and the Copernicus 30 m elevation model, read from the Microsoft Planetary Computer, which mirrors the Copernicus data and needs no account. OpenStreetMap comes from the ohsome API, the source the brief names, at a snapshot no later than 27 July 2026 (the newest date ohsome holds), so no edit made after the flood can get in.

**Why only images from the same orbit?**
Radar measures how much energy bounces back toward the satellite, and that depends on the angle it looks from. Two images from different orbit tracks see each slope from different angles, so their difference would mostly be geometry, not change on the ground. Images from the same track are taken from the same position 12 days apart, so a difference means the ground changed.

**What do you compare the after-image with?**
With the same pixel on three or four earlier passes of the same track. For every pixel we take the median and the spread of those normal days, and score the after-image in units of that spread (a robust z-score). A pixel that always flickers needs a big change to count; a stable pixel needs less.

**How do you pick the threshold without peeking at the official maps?**
From ground that cannot have flooded: pixels more than 150 m above the nearest stream. Whatever score only 1 in 100 of those pixels reaches by chance becomes the line. So the threshold controls the false alarm rate, and the official maps play no part.

**Why is debris different from water?**
Calm water is smooth, so it reflects the radar pulse away and looks dark. A debris flow leaves rough rock, mud and stripped slopes; the return can get brighter or darker, and the cross-polarised (VH) return changes because vegetation is gone. We map both: new dark water, and strong change in either direction.

**What are layover and shadow?**
In steep valleys, a slope facing the satellite more steeply than its viewing angle folds over onto itself (layover), and a slope facing away more steeply than 90 degrees minus the viewing angle is hidden (shadow). Neither can be measured. We compute them from the elevation model for each orbit and mark those pixels "not observed".

**Why three orbits?**
Sentinel-1D passes over this valley on one ascending and two descending tracks. Each sees different slopes. A pixel hidden from one is often visible from another, so combining them shrinks the blind area. We count any clear view of strong change: a geometry that cannot see a slope does not vote against it.

**What does "grown along the valley floor" mean?**
Debris floods fill the valley bottom. After the strong-change pixels are found, weaker change that is connected to them on low ground (less than 30 m above the stream) is added. It is the standard two-threshold (hysteresis) method used for edges in images.

**How did you choose those settings?**
On one official map area only (Bidur, area 3), used as a development area. The other delivered areas (Syapru Besi, Timure, Phosretar) are held out: their scores were recorded for every candidate but never used to choose. Held out only, flood F1 is 0.66 and we find 70% of damaged buildings. The development-only rule is in `scripts/validate_fusion_dev.py` and its output in `runs/trishuli-2026/validation/fusion_dev.json`.

## Damage and cut-off

**How do you decide a building, road or bridge was hit?**
Each feature takes the highest flood probability within one pixel (20 m) of it, because OpenStreetMap geometry and radar pixels are both a few metres off. Roads are sampled every 10 m and use their 95th percentile, so one noisy pixel does not close a road. Above 0.7 is "likely destroyed", 0.4 to 0.7 "possibly damaged". Features the radar could not see are "unobserved", not "clear".

**How do you know who is cut off?**
We build a routable network from the pre-flood roads. Each road stretch the damage map flags is closed with its own probability of being destroyed; stretches the radar could not see get a prior of 0.15; roads the map shows as clear stay open. We draw 1,000 versions of the network, and for every village count how often it can still reach a hospital or clinic. That share is the probability it is cut off. We also give travel time before and after, walking time if the road is gone, and the repair that reconnects the most villages.

**Why a probability and not yes or no?**
Because the map is not certain. A village behind one doubtful bridge is different from a village behind three destroyed ones, and rescue teams need to know which.

**Why do you say 52 settlements are cut off when the official maps imply 7?**
The official maps grade roads only inside their mapped areas, so any village whose route is broken outside them looks connected to the reference, and their 7 is a lower bound. Running our routing with only their graded roads closed (338 settlements with a road link to care before the flood), we find all 7 of theirs (recall 7 of 7) and flag 43 more. Overall we agree on 295 of 338, but saying nobody is cut off would agree on 331, so recall is the figure that matters. On the 98 whose route crosses a graded area we agree on 69. Some of our extra cut-offs may come from road false alarms (next question); we have not traced each one. With the later grading maps it is 8 of 8 found, 42 more, 296 of 338.

**How many false alarms do you raise?**
EMS lists no matching building it graded "no visible damage", so a building false-alarm rate cannot be computed directly. Of the 2,617 buildings we flag inside the EMS areas, 727 have no EMS damage record, so about 72% have one. For roads, 47% of the graded road length we flag (57.6 of 122.4 km) is on stretches EMS graded "no visible damage", and we flag 37% of all road length it graded that way. These are first-grading figures from `runs/trishuli-2026/validation/emsr927.json`.

**What did you get wrong along the way?**
An early version let every road keep a small chance of closing from its raw score. Over long hill routes those small chances added up and marked 148 villages cut off, many nowhere near the damage. We saw it on the map; only roads the map flags or cannot see can close now. We had also scored the old version against the official road grading, so say plainly that the agreement figure (now 295 of 338; 217 of 339 before) is not independent of this fix.

**Why add Sentinel-2, and how did you choose its settings?**
In the upper gorge the radar sees most of the ground, but debris there barely changed its brightness. Sentinel-2 shows bare ground where forest stood. We add optical vegetation loss only on valley ground (under 45 m above the stream) within 0.5 km of radar damage. Those three numbers were chosen on Bidur alone (`scripts/validate_optical_dev.py`); held-out F1 went from 0.28 to 0.55 at Syapru Besi and from 0.18 to 0.35 at Timure.

**Why is new water only 2.3 km²?**
The water had mostly drained by the first pass two days later. Water counts if any one track sees new dark water, the same rule the map uses for change.

**Has it worked anywhere else?**
The same code, unchanged, on Melamchi 2021, Teesta 2023, the Beas at Mandi 2023 and Chamoli 2021. At Mandi it flags the New Victoria Bridge over the Beas. None of these have official grading, so they show it runs anywhere, not how accurate it is there. OpenStreetMap had very few buildings around Mandi in 2023, so building damage there is near zero by necessity, not because nothing was hit.

**What would a district office actually do with it?**
Copy the field alert (English or Nepali, SMS length) to the response group, send the KML of cut-off villages to field teams' phones, and start with the top repair. Every number in the alert comes from the same checked facts as the report.

## AI

**What does the trained model do?**
A U-Net with a ResNet-34 encoder, trained on Sen1Floods11 (a variant with Kuro Siwo added was tried and scored worse on held-out scenes), predicts open water from Sentinel-1 VV and VH. It is a second opinion on the radar water map: masked to ground it can judge, it marks as water 60% of the new open water the change detection maps. It is good at open water; it cannot see debris-covered ground that is not water, and steep shadow looks like water to it, so its output is masked to ground it can judge.

**How does the copilot avoid inventing numbers?**
Every number it may use is in a facts ledger built from the maps. A checker reads each answer, including Nepali numerals and number words, and rejects any number that is not a fact it looked up. On a rejection it retries once, then falls back to fixed templates. Questions the maps cannot answer, such as deaths or reopening dates, are refused. A 60-question attack set, in English and Nepali, is in `tests/redteam_copilot.json`.

## Limits (say these before they ask)

- Sentinel-1 revisits each track every 12 days, so the system maps damage after it happens; it cannot warn of a glacier collapse.
- In the narrow upper gorge (Syapru Besi, Timure) the radar sees most of the ground, but the debris barely changed its brightness; recall there is 0.21 to 0.38 even with Sentinel-2.
- OpenStreetMap is incomplete in remote valleys: buildings and bridges that were never mapped cannot be counted.
- Debris that looks like bare ground on a slope that was already bare is hard to separate from no change.
- The official maps are optical damage gradings of selected areas, not a full flood outline, so they are an imperfect yardstick.
- This is an educational prototype, not an operational tool.

**How does the model do on a Himalayan scene it never saw?** On the Trishuli case study, inside the four EMS areas, 97% of the water it marks lies inside the official extent (radar water rule 94%) and it recovers 17% of that extent (rule 12%). Recall is low because the official extent is mostly debris and the model is trained on open water only (`runs/trishuli-2026/validation/model_vs_ems.json`).

**What if we pick an area where it finds nothing?** It says why. On Kathmandu (28 Sep 2024) it found no flood because the first radar passes came 4 to 11 days later and the water had drained; the run message lists those pass dates. In steep gorges (Rasuwagadhi 2025, Wayanad 2024) much of the ground is hidden from the radar and is marked not observed, never clear. See "Live runs on floods it was not built for" in the README.

**Why two flood scores, 0.66 and 0.78?** 0.66 is detected flood alone on the three held-out areas. 0.78 is the full map, which adds water the flood model finds within 300 m of detected flood and a river corridor inferred from terrain next to it, in confined valleys only. Both were chosen on Bidur with the no-flood date capped at 3 km² (it rises from 1.1 to 2.1 km²). The official extent covers the whole active channel, which is why the corridor scores; we label it inferred and never count damage on it.

**Your cut-off list has 43 more villages than the official road grading implies. Why?** We checked it three ways. Closing only the roads EMS graded destroyed, EMS implies 7 cut-off villages and we find all 7. Also closing roads it graded damaged gives 8 (we find 7); adding possibly damaged gives 11 (we find 7). Using EMS grading inside its mapped areas and our map outside them gives 10, and we find all 10. In every version our extra list stays about 40 to 43, so it is real disagreement, not a quirk of the check: our map flags some road stretches inside the EMS areas that EMS graded undamaged (37% of that road length). We chose to miss no cut-off village and flag extras for ground checks; for triage a missed cut-off village costs more than a wasted check. Making road closures stricter would cut the extras but risk missing real cut-offs.
