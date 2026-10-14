"""U-Net with a ResNet34 encoder (segmentation-models-pytorch)."""
from __future__ import annotations

import segmentation_models_pytorch as smp
import torch.nn as nn


def build_unet(in_channels: int = 3, pretrained: bool = True) -> nn.Module:
    """One-logit water segmentation U-Net.

    With pretrained=True the encoder starts from ImageNet weights. For 3 input
    channels the stem is used as is; for 2 channels smp adapts the first conv.
    """
    return smp.Unet(
        encoder_name="resnet34",
        encoder_weights="imagenet" if pretrained else None,
        in_channels=in_channels,
        classes=1,
    )
