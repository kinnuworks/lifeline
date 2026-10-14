# Training data for the Bato water segmentation model

The model is trained only on the two datasets the hackathon brief allows: Sen1Floods11 and Kuro Siwo. The encoder starts from ImageNet weights (ResNet34), which the organisers confirmed is allowed. No other flood data, flood map or damage map is used.

## Sen1Floods11 (hand-labelled split)

- Source: public Google Cloud bucket `gs://sen1floods11`, folder `v1.1/data/flood_events/HandLabeled/`, fetched over HTTPS by `scripts/download_sen1floods11.py`.
- Downloaded: `S1Hand` (446 chips, 2 bands VV and VH, sigma0 in dB, 512 x 512 at 10 m, 881 MB), `LabelHand` (446 masks, 3.3 MB) and the four official split CSVs (`v1.1/splits/flood_handlabeled/`). Stored in `data/sen1floods11/`.
- Labels: 1 water, 0 not water, -1 no label. Pixels labelled -1 or with missing radar are ignored in training and in every metric.
- 11 flood events: Bolivia 15, Ghana 53, India 68, Mekong 30, Nigeria 18, Pakistan 28, Paraguay 67, Somalia 26, Spain 30, Sri Lanka 42, USA 69 chips.
- Official splits: train 252, valid 89, test 90, Bolivia 15 (Bolivia is a whole event left out of the other splits and is the usual out-of-distribution test).

### Held out: South Asian events

The brief asks how the model works on Himalayan scenes it has never seen. Two Sen1Floods11 events are in or next to the Himalayan region, so every chip from them is removed from training and validation:

| Event | Location of chips | Chips (train / valid / test) | Why held out |
|---|---|---|---|
| India | 92.4 to 94.0 E, 25.7 to 27.3 N (Assam, Brahmaputra valley, at the foot of the eastern Himalaya) | 40 / 14 / 14 | Himalayan-fed floodplain |
| Pakistan | 70.4 to 72.4 E, 29.2 to 33.8 N (Indus and Punjab plains, northern chips near the foothills) | 16 / 6 / 6 | South Asian monsoon floods, northern chips close to the foothills |

What is left for training: 196 train chips and 69 validation chips from the other 9 events. The held-out chips (96 in total) are used as an extra test set. None of the Sen1Floods11 chips is in mountain terrain like Rasuwa; the India chips are the closest the allowed data gets.

## Kuro Siwo (partial)

- Source: Hugging Face `orion-ai-lab/Kuro-Siwo-Webdataset`, folder `train_GRD` (CC BY 4.0). The full GRD release is about 170 GB of webdataset shards (each about 11 GB), or 700 GB as GeoTIFF tars, which is beyond what we can download here. We took a subset with HTTP range requests: the first 3.0 GB of `shard-00000.tar` and the first 0.75 GB of `shard-00001` to `shard-00004` (6.4 GB in `data/kurosiwo/raw/`). A tar file can be read from its start, so every complete sample in those bytes is usable; the cut-off last sample of each shard is dropped.
- `scripts/prepare_kurosiwo.py` keeps the post-event image only (VV, VH, converted from linear power to dB), sets the label to 1 for permanent water or flood (mask values 1 and 2), 0 otherwise, and -1 where `valid_mask` is 0. Chips that are more than half invalid are dropped (16). Output: 3,080 chips of 224 x 224 at 10 m in `data/kurosiwo/chips/` (1.0 GB).
- Events in the subset (Kuro Siwo activation id, count): Togo 470 (166), France 492 (213), Italy 496 (21), Germany 497 (398), France 498 (102), Ireland 502 (438), Belgium 518 (306), Netherlands 520 (78), Australia 554 (40), USA 1111004 (438), Peru 1111010 (77), Philippines 1111011 (362), USA 1111013 (441).
- Held out: Kuro Siwo has two South Asian events, Pakistan 2022 (1111009, Larkana) and the event catalogued as Nepal 2019 (1111007, Patna area in Bihar). 442 Pakistan samples were in our subset and were skipped; no 1111007 samples were in it. They are excluded by `HELD_OUT_ACTS` in the preparation script.
- Use: a second model was trained on Sen1Floods11 plus these chips. It scored lower on Sen1Floods11 validation (IoU 0.627 against 0.696) and is not the shipped model; its test numbers are in `runs/model/metrics_kuro.json` and in RESULTS.md.

## Preprocessing

- Inputs to the model: VV dB clipped to [-35, 5], VH dB clipped to [-42, -5], and VV minus VH (the dB form of the VV/VH ratio) clipped to [-5, 25]. Each is standardised with means and standard deviations measured on the training chips. Missing values become 0 after standardisation and are masked in the output.
- Augmentation: random 256 x 256 crops, random 90 degree rotations and flips, a random gain offset of about 1 dB per band, and in half of the samples the whole chip is first averaged 2 x 2 in linear power to 20 m. The pipeline runs on a 20 m grid, so the model sees both 10 m and 20 m pixels during training.

## Citations

- Bonafilia, D., Tellman, B., Anderson, T., Issenberg, E. (2020). Sen1Floods11: a georeferenced dataset to train and test deep learning flood algorithms for Sentinel-1. Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR) Workshops, pp. 210-211. Data: https://github.com/cloudtostreet/Sen1Floods11
- Bountos, N. I., Sdraka, M., Zavras, A., Karavias, A., Karasante, I., Herekakis, T., Thanasou, A., Michail, D., Papoutsis, I. (2024). Kuro Siwo: 33 billion m2 under the water. A global multi-temporal satellite dataset for rapid flood mapping. Advances in Neural Information Processing Systems 37 (NeurIPS 2024), Datasets and Benchmarks Track. Data: https://github.com/Orion-AI-Lab/KuroSiwo (CC BY 4.0)
- Encoder initialisation: ResNet34 ImageNet weights as distributed with segmentation-models-pytorch (He et al., 2016; Deng et al., 2009).
