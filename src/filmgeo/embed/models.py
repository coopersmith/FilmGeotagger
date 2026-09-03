"""Image embedding backends.

Two models, because they fail differently on the film-vs-phone domain gap (PLAN.md risk 1):

* **SigLIP** is language-supervised — it encodes *what the picture is of*, so it stays robust
  when colour and grain differ wildly between a scan and a phone photo.
* **DINOv2** is self-supervised on pixels — it encodes *this particular scene and viewpoint*,
  which is what distinguishes "same visit" from "same place, another day".

Both run on MPS. Vectors are L2-normalised so a dot product is cosine similarity.
"""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # scans are ~28 MP, past Pillow's decompression-bomb guard


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


class Embedder:
    """Batching, loading and normalisation. Subclasses supply the transform and the forward pass.

    Unreadable images yield a zero vector rather than shifting every later index, so the caller
    can rely on `encode(paths)[i]` corresponding to `paths[i]`. The embedding dimension is
    discovered from the first successful batch: `open_clip`'s SigLIP wraps a timm backbone that
    exposes no output_dim attribute, and hard-coding per-model dimensions would rot.
    """

    name: str
    grayscale: bool = False

    def _transform(self, im: Image.Image) -> torch.Tensor:
        raise NotImplementedError

    def _forward(self, batch: torch.Tensor) -> torch.Tensor:
        raise NotImplementedError

    def _load(self, path: str) -> Image.Image | None:
        try:
            im = Image.open(path).convert("RGB")
        except (OSError, ValueError):
            return None
        if self.grayscale:
            # Film and phone colour science diverge sharply; dropping colour is a variant M1
            # measures rather than assumes.
            im = im.convert("L").convert("RGB")
        return im

    @torch.inference_mode()
    def encode(self, paths: list[str], batch_size: int = 16) -> np.ndarray:
        feats: dict[int, np.ndarray] = {}
        dim = None
        for i in range(0, len(paths), batch_size):
            chunk = paths[i : i + batch_size]
            tensors, keep = [], []
            for j, p in enumerate(chunk):
                im = self._load(p) if p else None
                if im is not None:
                    tensors.append(self._transform(im))
                    keep.append(i + j)
            if not tensors:
                continue
            out = self._forward(torch.stack(tensors).to(self.device)).float()
            out = torch.nn.functional.normalize(out, dim=-1).cpu().numpy()
            dim = out.shape[1]
            for k, idx in enumerate(keep):
                feats[idx] = out[k]

        if dim is None:
            return np.zeros((len(paths), 1), dtype=np.float32)
        vecs = np.zeros((len(paths), dim), dtype=np.float32)
        for idx, v in feats.items():
            vecs[idx] = v
        return vecs


class SigLIP(Embedder):
    name = "siglip"

    def __init__(self, model: str = "ViT-B-16-SigLIP-384", pretrained: str = "webli", grayscale: bool = False):
        import open_clip

        self.grayscale = grayscale
        self.device = _device()
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(model, pretrained=pretrained)
        self.model = self.model.to(self.device).eval()

    def _transform(self, im):
        return self.preprocess(im)

    def _forward(self, batch):
        return self.model.encode_image(batch)


class DINOv2(Embedder):
    name = "dinov2"

    def __init__(self, model: str = "vit_base_patch14_dinov2.lvd142m", grayscale: bool = False):
        import timm

        self.grayscale = grayscale
        self.device = _device()
        self.model = timm.create_model(model, pretrained=True, num_classes=0).to(self.device).eval()
        cfg = timm.data.resolve_model_data_config(self.model)
        self.preprocess = timm.data.create_transform(**cfg, is_training=False)

    def _transform(self, im):
        return self.preprocess(im)

    def _forward(self, batch):
        return self.model(batch)
