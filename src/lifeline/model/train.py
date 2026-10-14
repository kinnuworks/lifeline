"""Train the Sentinel-1 water U-Net on Sen1Floods11 hand labels.

Usage: uv run python -m lifeline.model.train [--epochs 80] [--loss dice_bce|lovasz]

India and Pakistan chips are removed from training and validation (they are
the Himalayan / South Asian events and are used only for testing).
Logs to runs/model/train_log.csv; best checkpoint by validation IoU goes to
runs/model/best.pt and is exported to src/lifeline/model/weights/lifeline_unet_s1.pt.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import time
from pathlib import Path

import numpy as np
import segmentation_models_pytorch as smp
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from lifeline.model.dataset import (CLIP, HELD_OUT_EVENTS, IGNORE, STATS, S1WaterDataset,
                                event_of, load_chips, load_kurosiwo, read_split)
from lifeline.model.unet import build_unet

PROJECT = Path(__file__).resolve().parents[3]
RUN_DIR = PROJECT / "runs" / "model"
WEIGHTS = Path(__file__).resolve().parent / "weights" / "lifeline_unet_s1.pt"


def seed_all(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def masked_loss(logits: torch.Tensor, y: torch.Tensor, kind: str) -> torch.Tensor:
    logits = logits[:, 0]
    valid = y != IGNORE
    if valid.sum() == 0:
        return logits.sum() * 0
    yt = y.clamp(min=0).float()
    bce = F.binary_cross_entropy_with_logits(logits[valid], yt[valid])
    p = torch.sigmoid(logits) * valid
    inter = (p * yt * valid).sum()
    dice = 1 - (2 * inter + 1) / (p.sum() + (yt * valid).sum() + 1)
    if kind == "lovasz":
        lov = smp.losses.LovaszLoss("binary", ignore_index=IGNORE)(logits[:, None], y[:, None])
        return bce + lov
    return bce + dice


@torch.no_grad()
def evaluate(model, loader, device, thr: float = 0.5) -> dict:
    model.eval()
    tp = fp = fn = tn = 0
    for x, y in loader:
        p = torch.sigmoid(model(x.to(device)))[:, 0].cpu()
        valid = y != IGNORE
        pred = p >= thr
        gt = y == 1
        tp += int((pred & gt & valid).sum()); fp += int((pred & ~gt & valid).sum())
        fn += int((~pred & gt & valid).sum()); tn += int((~pred & ~gt & valid).sum())
    iou = tp / max(tp + fp + fn, 1)
    return {"iou": iou, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def export(state: dict, cfg: dict, path: Path = WEIGHTS) -> None:
    """Save weights in float16 with the config needed to rebuild the model."""
    path.parent.mkdir(parents=True, exist_ok=True)
    half = {k: (v.half() if v.is_floating_point() else v) for k, v in state.items()}
    torch.save({"state_dict": half, "config": cfg}, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--wd", type=float, default=1e-4)
    ap.add_argument("--channels", type=int, default=3)
    ap.add_argument("--loss", default="dice_bce", choices=["dice_bce", "lovasz"])
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--repeats", type=int, default=4, help="random crops per chip per epoch")
    ap.add_argument("--tag", default="")
    ap.add_argument("--kurosiwo", action="store_true", help="add prepared Kuro Siwo chips to training")
    ap.add_argument("--crop", type=int, default=256)
    ap.add_argument("--samples", type=int, default=0, help="training samples per epoch (0 = all)")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    seed_all(args.seed)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    keep = lambda ids: [i for i in ids if event_of(i) not in HELD_OUT_EVENTS]
    tr_ids, va_ids = keep(read_split("train")), keep(read_split("valid"))
    print(f"train {len(tr_ids)} chips, valid {len(va_ids)} chips, device {device}", flush=True)
    tr_chips, va_chips = load_chips(tr_ids), load_chips(va_ids)

    pool = tr_chips * args.repeats
    n_kuro = 0
    if args.kurosiwo:
        kuro = load_kurosiwo()
        n_kuro = len(kuro)
        print(f"kuro siwo chips: {n_kuro}", flush=True)
        pool = pool + kuro
    tr_ds = S1WaterDataset(pool, train=True, channels=args.channels, seed=args.seed, crop=args.crop)
    va_ds = S1WaterDataset(va_chips, train=False, channels=args.channels)
    g = torch.Generator().manual_seed(args.seed)
    if args.samples:
        sampler = torch.utils.data.RandomSampler(tr_ds, replacement=True, num_samples=args.samples, generator=g)
        tr_dl = DataLoader(tr_ds, batch_size=args.batch, sampler=sampler, drop_last=True)
    else:
        tr_dl = DataLoader(tr_ds, batch_size=args.batch, shuffle=True, generator=g, drop_last=True)
    va_dl = DataLoader(va_ds, batch_size=4)

    model = build_unet(args.channels, pretrained=True).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.wd)
    steps = args.epochs * len(tr_dl)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps,
                                                pct_start=0.05, anneal_strategy="cos")
    cfg = {"arch": "Unet", "encoder": "resnet34", "in_channels": args.channels,
           "clip": CLIP, "stats": STATS, "threshold": 0.5, "loss": args.loss,
           "train_chips": len(tr_ids), "kurosiwo_chips": n_kuro, "held_out_events": list(HELD_OUT_EVENTS),
           "seed": args.seed, "pixel_m_train": [10, 20]}

    tag = f"_{args.tag}" if args.tag else ""
    log_path = RUN_DIR / f"train_log{tag}.csv"
    best, best_ep, t0 = -1.0, -1, time.time()
    with open(log_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "train_loss", "valid_iou", "lr", "seconds"])
        for ep in range(1, args.epochs + 1):
            model.train()
            tot, n = 0.0, 0
            for x, y in tr_dl:
                x, y = x.to(device), y.to(device)
                loss = masked_loss(model(x), y, args.loss)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                opt.step(); sched.step()
                tot += loss.item(); n += 1
            m = evaluate(model, va_dl, device)
            el = time.time() - t0
            w.writerow([ep, f"{tot/n:.4f}", f"{m['iou']:.4f}", f"{sched.get_last_lr()[0]:.2e}", f"{el:.0f}"])
            f.flush()
            print(f"ep {ep:3d} loss {tot/n:.4f} valid IoU {m['iou']:.4f} ({el:.0f}s)", flush=True)
            if m["iou"] > best:
                best, best_ep = m["iou"], ep
                torch.save({"state_dict": model.state_dict(), "config": cfg}, RUN_DIR / f"best{tag}.pt")
            elif ep - best_ep >= args.patience:
                print(f"early stop at epoch {ep}", flush=True)
                break
    summary = {"best_valid_iou": best, "best_epoch": best_ep, "train_seconds": round(time.time() - t0),
               "args": vars(args)}
    (RUN_DIR / f"train_summary{tag}.json").write_text(json.dumps(summary, indent=2))
    print(summary, flush=True)
    if not args.no_export:
        ck = torch.load(RUN_DIR / f"best{tag}.pt", map_location="cpu")
        cfg["best_valid_iou"], cfg["best_epoch"] = best, best_ep
        export(ck["state_dict"], cfg)
        print("exported", WEIGHTS, flush=True)


if __name__ == "__main__":
    main()
