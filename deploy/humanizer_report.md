# Humanizer report (report only, no files edited)

Skill: humanizer v3.1.0. Voice target: neutral, plain, technical. Owner rules: no em or en dashes, no "not X but Y", no title case, no hype words. Suggestions keep every fact, number, date, name and claim.

Strength: **strong** = §1 to §5, a clear tell, or the owner's dash ban. **weak** = weak-alone pattern or style preference.

## Counts

| File | Findings | Strong | Weak | Verdict |
|---|---|---|---|---|
| README.md | 6 | 3 | 3 | Clean and specific. One defensive lead-in, the tagline, one heading, dashes in a name and in date ranges. |
| web/index.html | 7 | 4 | 3 | Mostly plain. The "instead of guessing / not a guess" tic appears twice, plus one slogan pair. |
| web/js/app.js | 3 | 0 | 3 | Clean. Only small items. |
| Video (script, film JSON, v3a/v3b) | 7 | 3 | 4 | Two captions should change (they stage instead of state). The recorded voice can stay. |
| devpost/story.md | 7 | 4 | 3 | The most AI-sounding file: a split not-X-but-Y opener, an "Honest outputs" line and an aphorism closer. |
| docs/report/REPORT.html | 4 | 4 | 0 | Precise. The word "honest" appears three times as a label for the text's own candour. |
| docs/QA.md | 2 | 1 | 1 | Clean. One "instead of guessing", one "runs anywhere". |
| deploy/PUBLISH.md | 4 | 1 | 3 | Fine. One "the question that matters" line. |
| copilot/templates.py, report/alert.py | 0 | 0 | 0 | Clean. Plain, factual strings, no tells. |
| **Total** | **40** | **20** | **20** | |

| Pattern | Count |
|---|---|
| §1 Not X but Y (incl. "instead of guessing", "not a guess") | 6 |
| §2 One-line closers, fragments, restatement | 5 |
| §3 Sayings that sound deep | 2 |
| §4 Staged run-up / announcing the point | 3 |
| §5 Arguing with no one | 1 |
| §7 Repeated sentence openings | 1 |
| §8 Dashes (owner ban; includes en-dash ranges and names) | 7 |
| §13 Inflated significance ("runs anywhere") | 5 |
| §16 Sales language / self-praise ("intelligence", "honest") | 8 |
| §19 Bold as decoration | 1 |
| §20 Decorative headings | 1 |

**Shared item: the tagline.** "Radar-to-rescue intelligence for flood disasters" appears in README.md:5, index.html:6 and :56, app.js:701, story.md:7, PUBLISH.md:6, lifeline-v3.json:3, v3b.tsx:580 and voice line 31. "Intelligence" is a hype word (§16, weak). It works as a product strapline, so it may stay. If you change it, change it everywhere and keep the video end card matching the recorded voice. Suggested: "Radar-to-rescue mapping for flood disasters". The entries below list it once per file and do not repeat this text.

---

## 1. bato/README.md

1. **README.md:51**, §5 Arguing with no one, **strong**
   Current: `So nothing is hidden, this is the full list:`
   Suggested: delete the line. The heading "Everything the official maps or the no-flood date influenced" already says it. If a lead-in is needed: `The full list:`

2. **README.md:15**, §8 Dashes, **strong (owner ban)**
   Current: `on the Bhote Koshi–Trishuli corridor`
   Suggested: `on the Bhote Koshi-Trishuli corridor`

3. **README.md:137-138**, §8 Dashes (number ranges), **strong (owner ban)**
   Current: `data 2021–2026` · `© DLR e.V. 2010–2014` · `GmbH 2014–2018`
   Suggested: `data 2021-2026` · `© DLR e.V. 2010-2014` · `GmbH 2014-2018`

4. **README.md:76**, §13 Inflated significance, **weak**
   Current: `so they show that the system runs anywhere, not how accurate it is there.`
   Suggested: `so they show that the system runs on other floods, not how accurate it is there.`
   (Four other floods do not show "anywhere". The contrast stays because it corrects a real misreading.)

5. **README.md:13**, §20 Decorative heading, **weak**
   Current: `## The case study, in numbers`
   Suggested: `## Case study results`

6. **README.md:5**, §16 tagline (see shared item), **weak**

Left alone on purpose: lines 83, 86, 87, 126 and 128 use "X, not Y", but each corrects a belief a reader would really hold (exposure vs inspection, yardstick vs ground truth). The skill says to keep those.

## 2. Website: bato/web/index.html and web/js/app.js

### index.html

1. **index.html:142**, §1 Not X but Y (clipped tail), **strong**
   Current: `and marks steep slopes the radar could not see as not observed instead of guessing.`
   Suggested: `and marks steep slopes the radar could not see as not observed.`

2. **index.html:198**, §1 Not X but Y (clipped tail), **strong**
   Current: `It repeats the routing many times over uncertain damage, so each village gets a probability, not a guess.`
   Suggested: `It repeats the routing many times over uncertain damage, so each village gets a probability of being cut off.`

3. **index.html:411**, §2 Row of fragments, **strong**
   Current: `Four free sources in. One ledger of facts out.`
   Suggested: `Four free data sources go in, and one ledger of facts comes out.`

4. **index.html:495**, §8 Dashes (number ranges), **strong (owner ban)**
   Current: `© DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018`
   Suggested: `© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018`

5. **index.html:385**, §2 Restatement, **weak**
   Current: `The official rapid-mapping products are read only by the scoring step, after the run has finished; the pipeline never reads them while it runs.`
   Suggested: `The official rapid-mapping products are read only by the scoring step, after the run has finished.`

6. **index.html:299-300**, §7 Repeated openings, **weak**
   Current: `Every number comes from the map.` / `Every number the copilot may say lives in the map's facts ledger.` (line 278 also opens with "Every number")
   Suggested for line 300: `The copilot may only say numbers that are in the map's facts ledger.`

7. **index.html:6, 56**, §16 tagline (see shared item), **weak**

### app.js

1. **app.js:271**, §2 Fragment closer, **weak**
   Current: `"Used only to check results after the run. Never an input."`
   Suggested: `"Used only to check results after the run, never as an input."`

2. **app.js:18, 244, 266, 274, 275**, §8 Dash as empty-cell placeholder, **weak**
   Current: `"–"`
   Suggested: `"n/a"`. This one is optional, since it is a table glyph and not prose.

3. **app.js:701**, §16 tagline in `document.title` (see shared item), **weak**

Checked and clean: the "how:" tooltips (85-96), chips (142-157), the chat intro, refusal flags, run and engine messages (594-650) and the four tour captions (789-798).

## 3. Video: lifeline-video

### On-screen text (can change)

1. **lifeline-v3.json:2882 (scene 14 caption)**, §4 Announcing the point, **strong**
   Current: `We are open about where it is *weak*`
   Suggested: `Debris barely changed the radar *signal*`
   (This is a contiguous phrase from voice line 27, so the caption rule holds. Re-time the caption to the new phrase.)

2. **lifeline-v3.json:3258 (scene 17 caption)**, §13 Inflated claim, **strong**
   Current: `And it runs *anywhere*`
   Suggested: `The *same code* mapped Melamchi, Teesta`
   (Contiguous phrase from line 30. Re-time it.)

3. **lifeline-v3.json:286, 3287 and v3b.tsx:590**, §8 Dashes, **strong (owner ban)**
   Current: `Bhote Koshi–Trishuli valley, Nepal` · `Bhote Koshi–Trishuli` · `Results from the Bhote Koshi–Trishuli run of 14 Oct 2026`
   Suggested: use `Bhote Koshi-Trishuli` (hyphen) in all three.

4. **v3b.tsx:444**, §2 Fragment pair, **weak**
   Current: `One language, one repository`
   Suggested: `Python throughout, in one repository`

5. **lifeline-v3.json:3 and v3b.tsx:580**, §16 tagline (see shared item), **weak**. Change it only if voice line 31 is re-recorded, so the card matches the voice.

### Voice column (already recorded; flag real problems only)

6. **script.md line 30**, §13 Inflated claim, **weak (optional re-record)**
   Current: `And it runs anywhere.`
   Suggested if you re-record anything: `And it runs on other floods.` Fixing the caption (item 2) is enough otherwise.

7. **script.md line 27**, §4 Announcing the point, **weak**
   Current: `We are open about where it is weak.`
   This is fine to keep in the voice because it introduces the weak-spot card. The caption fix in item 1 removes the on-screen version.

Left alone: line 12 ("from the data, not from the answer key") answers a concern a judge really has. Line 17 ("That is where to start.") adds an instruction. Neither needs a re-record. The other on-screen labels, legends and card text in v3a/v3b and the film JSON are plain.

## 4. bato/deploy/devpost/story.md

1. **story.md:3**, §1 Not X but Y (split across sentences), **strong**
   Current: `For a district disaster office the first question is not how many square kilometres flooded. It is **which villages can still reach a hospital, and which repair gets most of them back**.`
   Suggested: `For a district disaster office the first question is which villages can still reach a hospital, and which repair gets most of them back.`

2. **story.md:64**, §1 clipped tail + §16 self-praise, **strong**
   Current: `Honest outputs: _not observed_ instead of guessing, held-out scores up front, and a public list of every design choice the official maps influenced.`
   Suggested: `Outputs that state their limits: _not observed_ where the radar cannot see, held-out scores up front, and a public list of every design choice the official maps influenced.`

3. **story.md:69**, §3 Aphorism, **strong**
   Current: `A grey "not observed" is more useful to a rescuer than a confident wrong "clear". And a number guard is only as good as its unit checks.`
   Suggested: `A grey "not observed" is more useful to a rescuer than a confident but wrong "clear". The number guard has to check units as well as digits.`

4. **story.md:19, 85**, §8 Dashes, **strong (owner ban)**
   Current: `## Case study: Bhote Koshi–Trishuli, 26 August 2026` · `2021–2026`, `2010–2014`, `2014–2018`
   Suggested: `Bhote Koshi-Trishuli` · `2021-2026`, `2010-2014`, `2014-2018`

5. **story.md:46**, §1 Not X but Y, **weak**
   Current: `so the false-alarm rate comes from the data, not the answer key;`
   Suggested: `so the false-alarm rate is set from the data without the official maps;`
   (This is optional. The contrast answers a real judge question.)

6. **story.md:3, 13, 31**, §19 Bold as decoration, **weak**
   Current: the bold question in line 3, `**Both AI options are built and tested:**`, and mid-sentence `**only by a separate checker**`
   Suggested: remove the bold in lines 3 and 31 and keep line 13 as plain text. Bold on the case-study numbers (21-27) and the list labels is fine for a Devpost page that readers scan.

7. **story.md:7**, §16 tagline (see shared item), **weak**

## 5. bato/docs/report/REPORT.html

1. **REPORT.html:102**, §4 Staged run-up, **strong**
   Current: `This is the honest finding: a water model trained on lowland floods transfers to Himalayan valleys only with a terrain mask`
   Suggested: `A water model trained on lowland floods transfers to Himalayan valleys only with a terrain mask`

2. **REPORT.html:68**, §16 Self-praise (one of three "honest" uses), **strong**
   Current: `<b>Honest about what radar cannot see.</b>`
   Suggested: `<b>What radar cannot see.</b>`

3. **REPORT.html:127**, §16 Self-praise, **strong**
   Current: `and 80 km² is honestly marked not observed.`
   Suggested: `and 80 km² is marked not observed.`

4. **REPORT.html:41, 147**, §8 Dashes, **strong (owner ban)**
   Current: `the 26 August 2026 Bhote Koshi–Trishuli flood` · `2021–2026`, `2010–2014`, `2014–2018`
   Suggested: `Bhote Koshi-Trishuli` · `2021-2026`, `2010-2014`, `2014-2018`

Left alone: line 67 ("set by the data, not by the official maps"), line 118 and line 127 ("generality, not accuracy") each correct a real misreading. "event − 1 day" on line 59 uses a minus sign, not a dash.

## 6. bato/docs/QA.md

1. **QA.md:26**, §1 clipped tail, **strong**
   Current: `We compute them from the elevation model for each orbit and mark those pixels "not observed" instead of guessing.`
   Suggested: `We compute them from the elevation model for each orbit and mark those pixels "not observed".`

2. **QA.md:61**, §13 Inflated claim, **weak**
   Current: `so they show it runs anywhere, not how accurate it is there.`
   Suggested: `so they show it runs on other floods, not how accurate it is there.`

## 7. bato/deploy/PUBLISH.md

1. **PUBLISH.md:49 (LinkedIn)**, §3 Saying that sounds deep, **strong**
   Current: `and the question that matters is which villages can still reach a hospital.`
   Suggested: `and the first question is which villages can still reach a hospital.`

2. **PUBLISH.md:41 (YouTube chapter)**, §13 Inflated claim, **weak**
   Current: `5:31 Runs anywhere`
   Suggested: `5:31 Other floods`

3. **PUBLISH.md:60 (X)**, §2 Fragment closer, **weak**
   Current: `Free satellite data, English and Nepali.`
   Suggested: `It uses free satellite data and works in English and Nepali.`

4. **PUBLISH.md:6 (YouTube title)**, §16 tagline (see shared item), **weak**

## 8. src/lifeline/copilot/templates.py and src/lifeline/report/alert.py

No findings. Refusals, method text, report lines and the SMS alert are plain and factual, with no dashes and no staging.

---

## Side notes (not humanizer patterns; facts to check, not to rewrite)

- index.html:494 says `Copernicus Sentinel data 2026`. README, REPORT and Devpost say `2021–2026`, and the site also shows 2021 and 2023 runs.
- PUBLISH.md:51 `it finds 52 settlements cut off from care` drops "likely". Every other file keeps it.
- The top repair is described as "the 3 closed stretches ... around one bridge" (README:27, REPORT:87) but as "a bridge and two adjoining stretches" (story.md:27, PUBLISH.md:17). Check that both describe the same thing.
- templates.py:194 `The places with the most likely destroyed buildings` can be read as "most likely". `The places with the most buildings likely destroyed` is clearer.
