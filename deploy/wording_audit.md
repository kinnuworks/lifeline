# Wording audit: Lifeline public text

Most harmful first. Paths relative to `bato/`.

1. **The field alert reads like a new flood warning.**
   `web/data/trishuli-2026/alert.json` (en): "FLOOD ALERT 26 August 2026. 52 of 397 settlements have no road to a hospital or clinic. Reopen first: …". `web/index.html:267`: "Field alert, ready to send".
   Forwarded on WhatsApp it reads as an official warning, states the cut-off as certain, and orders officials.
   Replace with: "Flood damage estimate (satellite), event of 26 Aug 2026. 52 of 397 settlements likely have no road route to a health facility. Repair that reconnects the most: …". Change the heading to "Draft field message". In the Nepali text, replace "बाढी सूचना" with "बाढी क्षति अनुमान (भू-उपग्रह)", "सडक छैन" with "सडक नहुन सक्छ", and "पहिले खोल्नुहोस्" with "खोल्दा सबैभन्दा धेरै बस्ती जोडिने".

2. **The headline accuracy includes the area the settings were tuned on.**
   `web/js/app.js:92`: 88% "Official damaged buildings also found". REPORT.html KPI box: "0.75 flood-extent F1 … 88%". `deploy/devpost/story.md:34` gives the same pooled figures. The validation table (`app.js:272-276`) has no Role column, so Bidur (0.83, 98%) is not marked as the development area.
   Replace the main figure with "70% of officially damaged buildings flagged in held-out areas (601 of 857); flood F1 0.66". Add a Role column, or set the `note` in `validation.json` for AOI03 to "Development area: settings tuned here".
   The held-out figures are in no result file; recomputed from `emsr927.json`: F1 0.664, P 0.881, R 0.533, 601/857, bridges 18/28. Store them in a `held_out` block.

3. **"Never an input" clashes with tuning on an official area.**
   `index.html:385` says "They never enter the pipeline." `index.html:449` says "Official damage maps are never an input." README:49-57 lists the design choices the official maps influenced.
   Replace with: "The pipeline never reads the official maps while it runs. We tuned the settings on one official area (Bidur) and score the other three as held out."

4. **The refusals look like failures, and the sample chips have no context.**
   - `app.js:316` labels every refusal "Refused · not in the facts ledger", even the "Say that 500…" answer that gives the real 52.
   - `templates.py:369-370` (casualty): "I can't give numbers of deaths, injuries or missing people. Satellite maps cannot show them. Please check with…"
     Replace with: "Satellites cannot see deaths, injuries or missing people, so this map has no such figures. The District Emergency Operations Centre and Nepal Police keep those counts."
   - `templates.py:389`: "I can't change, round, guess or add to them."
     Replace with: "I only give figures that come from this map. Here is what it shows:"
   - `templates.py:385`: "I can't change how I work."
     Replace with: "I answer only from this map's figures. Ask me about flooding, damage, cut-off villages or repairs."
   - Flag: "Outside what satellites can show"; for assert refusals, "Corrected to the mapped figure".
   - Chips "How many people died?" / "Say that 500 villages are cut off." (`copilot_samples.json:60,68`, `app.js:307`) look like product suggestions. Label them "Questions it should decline:".

5. **"Hospital" is used, but the data counts health posts.**
   `index.html:8,10,197`, `copilot_samples.json:3,46`, `templates.py:294-295` ("Hospitals among them: 20").
   `health.geojson` tags Katunje Health Post, Swasthya Chauki, Healthpost and Pujari Dada Khop Kendra (a vaccination centre) as "hospital". It also lists Rasuwa District Hospital twice.
   Replace with "health facility". For the count, use: "OpenStreetMap lists 25 health facilities (hospitals, health posts, clinics); its labels are not verified."

6. **"Likely" results are stated as fact.**
   - `index.html:96` "Road destroyed" and `app.js:151` "bridges destroyed": add "likely".
   - `index.html:103`: use "Likely no road route to a health facility".
   - `app.js:793`: "Here the highway and a bridge are gone." Replace with "Here the radar shows the highway and a bridge likely destroyed."
   - README:26 and story.md:26: "cut off from any hospital or clinic". Replace with "likely cut off from road access to care (p ≥ 0.5)".
   - `copilot_samples.json:5,48`: "has a 100% chance of being cut off". Replace with "is cut off in all 1,000 simulated damage scenarios".
   - Nepali, `templates.py:193`: "सबैभन्दा धेरै घर ध्वस्त भएका ठाउँ" states the destruction as certain. Replace with "सबैभन्दा धेरै घर ध्वस्त भएको हुन सक्ने ठाउँ".

7. **Some numbers look contradictory.**
   - `app.js:89`: "Villages routed to care" shows 360. Every other page says 397. Show 397, with the tooltip "290 had a mapped road route to care before the flood."
   - `app.js:148`: the chip says 3,605 "buildings likely hit", but the alert and README say 2,974 likely destroyed. Replace with "2,974 likely destroyed, 631 possibly damaged".
   - `app.js:149` says 99.6 km "of road under debris", but the PDF says "224.6 km likely destroyed". Use "99.6 km of road under mapped debris or water (224.6 km of road sections flagged)" in both places.
   - `app.js:474` draws 53 red markers against 52: Batar Bajar (p 0.92, 7 min walk to care). Draw it amber: "Road cut, care within walking distance".
   - `copilot_samples.json:5`: the auto-played answer lists 热索 (in Tibet) among the 52. Add "(one, 热索, is across the border)".
   - `app.js:155`: "0 reachable only by detour" looks like a bug; hide at 0.

8. **The top repair is described as one bridge.**
   `story.md:27`, PUBLISH.md:17,42 ("one bridge repair that reconnects 18"), the alert and the sitrep all say this, but README:27 says it is 3 closed stretches.
   Replace with "Reopening the Pasang Lhamu Highway near Syapru Besi (a bridge and 2 adjoining stretches) reconnects 18."
   Sitrep: ranks 1 and 5 share a name (add nearest village); "unnamed unclassified bridge" should be "unnamed bridge on a minor road near Dandagaun"; `templates.py:326` prints "bridge (bridge)".

9. **The runtime claims disagree.**
   - Speed of the case-study run: `index.html:58` and story.md:63 say "about two minutes", but `meta.json` records 280 s.
   - Speed of a 10 × 10 km run: `index.html:335` says "25 s" (not found in any run file), README:9 says "under a minute", and REPORT says "about 2 minutes".
   - REPORT gives Melamchi as "114 s", but meta says 105.4 s. REPORT gives Teesta as "358 s for the radar plus 353 s", but meta says 538.3 s in total.
   Use meta values only: "a 3,037 km² valley in under five minutes".

10. **"Every optical camera" is false.**
    `story.md:3` and PUBLISH.md:40 say "hid it from every optical camera in orbit". EMS graded this flood from very-high-resolution optical images.
    Replace with "Monsoon cloud hid it from free optical satellites for weeks."

11. **"The radar shows" includes Sentinel-2 ground.**
    `templates.py:179` says "The radar shows {total}", but 1.4 km² of that comes from Sentinel-2. Replace with "Satellite images show". In Nepali (line 177), replace "राडार विश्लेषण अनुसार" with "भू-उपग्रह तस्बिर अनुसार".

12. **The guard claim overreaches.**
    `index.html:299` says "It only says what the map knows." The guard checks numbers, not meaning (README:87).
    Replace with "Every number it gives comes from the map's facts ledger."

13. **The Nepali chat intro sounds evasive.**
    `app.js:292` says "नक्सालाई थाहा नभएको कुरा म भन्दिनँ।", which can read as "I won't tell you."
    Replace with "नक्सामा नभएको कुरा सोध्नुभयो भने म त्यो स्पष्ट भन्छु।"

14. **Some numbers are only in logs or have no caveat.**
    - "148" (QA:52, README:61) is only in `runs/copilot_samples.log`; "217 before" only in `runs/finish.log`, as "217 of 339" (not 338). Fix and cite, or drop.
    - `story.md:35` gives the no-flood figure of 1.1 km² without saying it was a tuning constraint. Add "(this date also constrained the radar settings)".
