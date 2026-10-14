# Water segmentation model: results

Model: U-Net with a ResNet34 encoder started from ImageNet weights, three inputs (VV dB, VH dB, VV minus VH), one output (probability of open water). Trained on 196 Sen1Floods11 hand-labelled chips, validated on 69, with the India and Pakistan events held out completely (see DATA.md). Weights: `src/lifeline/model/weights/lifeline_unet_s1.pt` (float16, 49 MB). Raw numbers: `runs/model/metrics.json`.

## Training

- AdamW (lr 5e-4, weight decay 1e-4), one-cycle cosine schedule, batch 16 of 256 x 256 crops, loss BCE + Dice with label -1 ignored, seed 42, float32 on the Apple M5 GPU (mps). Mixed precision was not used.
- Early stopping on validation IoU (patience 15). Best epoch 26 of 41, validation IoU 0.696. Training time 21 minutes. Log: `runs/model/train_log.csv`.
- Reproduce: `uv run python scripts/download_sen1floods11.py`, `uv run python -m lifeline.model.train --epochs 50 --repeats 3 --patience 15`, `uv run python -m lifeline.model.evaluate`.

## Metrics on Sen1Floods11

Pixel-level scores for the water class over all labelled pixels in each set, threshold 0.5. Two Otsu baselines on VV: one threshold per chip (the common Sen1Floods11 baseline), and one fixed threshold (-13.1 dB) found by Otsu on all training pixels together.

| Set | Chips | Model IoU | Model F1 | Precision | Recall | Otsu fixed IoU | Otsu fixed F1 | Otsu per chip IoU |
|---|---|---|---|---|---|---|---|---|
| Official test split | 90 | **0.669** | **0.802** | 0.795 | 0.808 | 0.364 | 0.534 | 0.211 |
| Official test, without India and Pakistan | 70 | 0.723 | 0.839 | 0.826 | 0.852 | 0.318 | 0.483 | 0.176 |
| Bolivia (event never seen) | 15 | **0.661** | **0.796** | 0.841 | 0.755 | 0.617 | 0.763 | 0.349 |
| Held out: India (Assam), all splits | 68 | 0.523 | 0.687 | 0.686 | 0.687 | 0.471 | 0.641 | 0.220 |
| Held out: Pakistan, all splits | 28 | 0.307 | 0.470 | 0.387 | 0.598 | 0.169 | 0.290 | 0.113 |
| Held out: India and Pakistan | 96 | 0.470 | 0.640 | 0.611 | 0.671 | 0.368 | 0.538 | 0.192 |

At 20 m (chips averaged 2 x 2, as on the pipeline grid): official test IoU 0.677 (Otsu fixed 0.388), Bolivia IoU 0.691 (Otsu fixed 0.650).

The official test split contains 20 India and Pakistan chips. Our model never saw those events, so its official-test number is not directly comparable with models trained on all of the training split.

What the numbers say:
- On scenes like the training data the model is much better than a backscatter threshold (IoU 0.72 against 0.32).
- On new events the gap closes. On Bolivia, a flat floodplain with large open water, a single threshold already does well and the model adds 0.04 IoU. On the South Asian events the model is ahead by 0.10 IoU but is clearly weaker than on the events it trained on (0.47 against 0.72). This is the honest estimate of how it transfers to a region it has not seen.

## Other models tried

Selection should follow validation IoU. We trained three models and report all of them.

| Model | Valid IoU | Test IoU | Bolivia IoU | India+Pakistan IoU (recall) |
|---|---|---|---|---|
| A. BCE + Dice, Sen1Floods11 only (shipped) | 0.696 | 0.669 | 0.661 | 0.470 (0.671) |
| B. BCE + Dice, plus 3,080 Kuro Siwo chips | 0.627 | 0.640 | 0.726 | 0.431 (0.638) |
| C. BCE + Lovász, Sen1Floods11 only | 0.705 | 0.676 | 0.673 | 0.466 (0.566) |

Model C has the highest validation IoU by 0.009, which is smaller than the change in validation IoU from one epoch to the next (often 0.05). We ship model A. Two reasons, stated plainly because the second one uses the case-study scene:
1. On the 96 held-out South Asian chips, A and C have the same IoU but C finds less of the water (recall 0.57 against 0.67). Lovász loss optimises the overlap score and leaves the probabilities poorly calibrated, so a 0.5 cut-off is less reliable on new terrain.
2. On the Trishuli scene (an earlier run of the pipeline), C marked only 10 percent of the pixels the change detection called open water, against 48 percent for A. This is an agreement check, not ground truth, but it confirms point 1. No labels from the case study were used.

Kuro Siwo did help on Bolivia but hurt on the other test sets. With only part of Kuro Siwo downloaded (13 events, mostly European), it shifted the model toward those scenes.

## Himalayan check: Trishuli case study

`scripts/eval_himalaya.py runs/trishuli-2026` runs the model on the post-event radar of the case study (2920 x 2600 pixels at 20 m, 3.6 s on the M5) and compares it with the pipeline's change-detection classes in `flood_class.tif`. There are no labels for this scene, so these are agreement numbers between two independent methods. The summary is saved to `runs/trishuli-2026/model_agreement.json`.

The pipeline calls `run_on_run_dir()`, which writes two rasters:
- `model_prob_raw.tif`: the model output as is.
- `model_prob.tif`: the same, set to 0 where no radar pass sees the ground (`observed.tif` = 0), where the slope is above 15 degrees, or where the height above nearest drainage is above 25 m. Water cannot lie on such ground, and the model cannot tell radar shadow from water.

| | Raw | Terrain-masked |
|---|---|---|
| Model water area | 56.0 km2 | 4.8 km2 |
| Of that, on slopes above 20 degrees | 48.3 km2 | 0 |
| Share of pipeline open water (class 1, 2.3 km2) the model also calls water | 64 % | 60 % |
| Model water the pipeline does not call open water (observed ground) | 42.5 km2 | 3.5 km2 |
| Model water on pipeline debris / scour pixels (class 2) | 1.1 km2 | 1.0 km2 |

By slope, before masking: on ground flatter than 5 degrees the model and the pipeline agree with IoU 0.30 and the model finds 77 percent of the pipeline's open water. Above 30 degrees the IoU is 0.001: almost all of the 48 km2 there is radar shadow on steep slopes, which is dark in both VV and VH and looks like water to a model that has never seen mountains.

In an earlier run, about half of the masked "model only" water was already dark (VV below -15 dB) in the pre-event image. That is the permanent Trishuli river channel, which the model maps as water and the change detection leaves out on purpose because it only reports new water.

## What the model can and cannot do

Good at:
- Open, calm water on flat ground: rivers, flooded fields, lakes. This is what all its training labels describe.
- 20 m input works as well as 10 m (training mixed both).

Cannot do:
- Debris. A debris flow or a fresh deposit of mud and boulders is rough ground, not water, and is often brighter in radar. The model was trained to find water only and will not mark it; it marked 8 percent of the pipeline's debris / scour pixels. Debris has to come from the change-detection part of the pipeline.
- Steep terrain. Radar shadow, layover and foreshortening in narrow valleys produce dark or distorted pixels. The training data has no mountain scenes, so the raw output must not be used above about 15 degrees of slope; use the masked `model_prob.tif`.
- Water under vegetation or in narrow channels a few pixels wide. At 20 m a 30 m wide river is one or two pixels.
- Wind-roughened water or wet snow, which change backscatter in ways not covered by the training events.
- It looks at the post-event image only. It cannot tell flood water from water that was there before; the pipeline's before-and-after comparison does that.

## Files

- `src/lifeline/model/dataset.py`, `unet.py`, `train.py`, `infer.py`, `evaluate.py`
- `src/lifeline/model/infer.py`: `predict_water(vv_db, vh_db, tile=512, overlap=64) -> np.ndarray` (float32, 0 to 1, NaN where input is NaN, any array size) and `run_on_run_dir(run_dir) -> dict` (pipeline entry point)
- `scripts/download_sen1floods11.py`, `scripts/prepare_kurosiwo.py`, `scripts/eval_himalaya.py`
- `runs/model/metrics.json` (shipped model), `metrics_kuro.json`, `metrics_lovasz.json`, `train_log*.csv`
- `tests/test_model_infer.py`
