"""TorchXRayVision adapter for the screening cascade.

TorchXRayVision (Cohen et al., MIDL 2022; https://github.com/mlmed/torchxrayvision)
ships chest X-ray models trained on several public datasets at once -- NIH,
PadChest, CheXpert, MIMIC-CXR, RSNA, Google, OpenI, Kaggle -- with 18 labels
including **pneumothorax** and **fracture**, which no ``transformers``
checkpoint in the zoo could check reliably. It is not a ``transformers``
pipeline, so it gets this small adapter; everything downstream sees the same
``[{label, score}]`` lists as any other zoo model.

Outputs are *operating-point normalised*: the library rescales each label so
that 0.5 sits at that label's validated operating threshold. That is why the
zoo entry uses 0.5-centred verdict thresholds rather than the 0.7/0.3 used for
plain softmax heads.

Requires ``pip install torchxrayvision`` (which needs ``torchvision`` built for
the installed ``torch``). Weights (~30 MB) download from GitHub on first use
to ``~/.torchxrayvision``.
"""

from __future__ import annotations

import contextlib
import io
import math
from pathlib import Path

from config import RUNTIME
from core.errors import BackendError
from core.logging_utils import get_logger, timed
from capabilities.hf_backend import load_local, resolve_device, run_local

_log = get_logger("xrv")


def available() -> bool:
    try:
        import torchxrayvision  # noqa: F401
    except ImportError:
        return False
    return True


def classify(weights: str, image_paths: list[str]) -> list[list[dict]]:
    """Return per-image ``[{label, score}]`` for a TorchXRayVision checkpoint."""
    if not available():
        raise BackendError(
            "torchxrayvision is not installed; run `pip install torchxrayvision` "
            "(with a torchvision build matching your torch)"
        )
    import numpy as np
    import torch

    model, transform = load_local(f"xrv:{weights}", lambda: _build(weights), label=f"xrv {weights}")
    device = next(model.parameters()).device
    out: list[list[dict]] = []

    with timed(_log, f"xrv {weights}: {len(image_paths)} image(s)"):
        for start in range(0, len(image_paths), RUNTIME.batch_size):
            batch = image_paths[start : start + RUNTIME.batch_size]
            tensor = torch.stack([_prepare(path, transform) for path in batch]).to(device)
            scores = run_local(lambda: model(tensor), label="xrv").detach().cpu().numpy()
            for row in scores:
                labels = [
                    {"label": name, "score": float(np.clip(value, 0.0, 1.0))}
                    for name, value in zip(model.pathologies, row)
                    if name and not math.isnan(float(value))
                ]
                out.append(labels)
    return out


def _build(weights: str):
    import torchvision
    import torchxrayvision as xrv

    # The library's download progress bar writes U+2588 to stdout, which
    # crashes on a Windows cp1252 console and leaves a truncated weights file
    # behind. Swallow its output and clear a partial file if loading fails.
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink):
            model = xrv.models.get_model(weights)
    except Exception:
        _remove_partial_weights(weights)
        raise
    model = model.to(resolve_device()).eval()
    transform = torchvision.transforms.Compose(
        [xrv.datasets.XRayCenterCrop(), xrv.datasets.XRayResizer(224)]
    )
    return model, transform


def _remove_partial_weights(weights: str) -> None:
    try:
        import torchxrayvision as xrv

        url = xrv.models.model_urls[weights]["weights_url"]
        local = Path.home() / ".torchxrayvision" / "models_data" / Path(url).name
        if local.exists() and local.stat().st_size < 5_000_000:
            local.unlink()
            _log.warning("removed truncated xrv weights %s", local)
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass


def _prepare(path: str, transform):
    """Greyscale -> xrv's [-1024, 1024] range -> centre crop -> 224px."""
    import numpy as np
    import torch
    import torchxrayvision as xrv
    from PIL import Image

    with Image.open(path) as image:
        array = np.asarray(image.convert("L"), dtype=np.float32)
    array = xrv.datasets.normalize(array, 255)[None, ...]
    return torch.from_numpy(transform(array))
