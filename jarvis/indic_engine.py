"""IndicF5: zero-shot voice cloning for 11 Indian languages, including Tamil.

The model's Hugging Face wrapper builds its networks inside the constructor and wraps
them in torch.compile. Under transformers 5 the constructor runs on the "meta" device
and torch.compile has no backend on Windows CPUs, so both break here. This module
assembles the same pipeline from the f5_tts helpers the wrapper uses, with the weights
loaded explicitly, which works on any device.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

REPO = "ai4bharat/IndicF5"
SAMPLE_RATE = 24000
DIT_CONFIG = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
MODEL_PREFIX = "ema_model._orig_mod."
VOCODER_PREFIX = "vocoder."


class IndicF5:
    def __init__(self, device: str = "cpu") -> None:
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file

        self.device = device
        vocab = hf_hub_download(REPO, "checkpoints/vocab.txt")
        weights = hf_hub_download(REPO, "model.safetensors")

        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):  # the helpers print
            self.vocoder = load_vocoder(vocoder_name="vocos", is_local=False, device=device)
            self.model = load_model(DiT, DIT_CONFIG, mel_spec_type="vocos", vocab_file=vocab, device=device)

        state = load_file(weights, device=device)
        model_state = {k[len(MODEL_PREFIX):]: v for k, v in state.items() if k.startswith(MODEL_PREFIX)}
        for key in ("mel_spec.mel_stft.mel_scale.fb", "mel_spec.mel_stft.spectrogram.window"):
            model_state.pop(key, None)
        missing, unexpected = self.model.load_state_dict(model_state, strict=False)
        if missing or unexpected:
            log.warning("IndicF5 weights: %d missing, %d unexpected keys", len(missing), len(unexpected))
        vocoder_state = {k[len(VOCODER_PREFIX):]: v for k, v in state.items() if k.startswith(VOCODER_PREFIX)}
        if vocoder_state:
            self.vocoder.load_state_dict(vocoder_state, strict=False)
        del state
        self.model.eval()
        self.vocoder.eval()

    def synthesize(
        self, text: str, ref_audio_path: Path | str, ref_text: str, speed: float = 1.0, nfe_step: int = 16
    ):
        """Return float32 audio at SAMPLE_RATE for `text`, spoken like the reference clip.

        nfe_step is the number of diffusion steps: 32 is the model's default, 16 halves the
        time at a small quality cost. On a CPU this model is very slow either way; measured
        on an 11th-gen i5: 70 s per short sentence at 16 steps with a 5 s reference, 290 s at
        32 steps with a 10 s reference. Cost scales with reference length plus output length.
        """
        import numpy as np
        import torch
        from f5_tts.infer.utils_infer import infer_process, preprocess_ref_audio_text

        ref_audio, ref_text = preprocess_ref_audio_text(
            str(ref_audio_path), ref_text, show_info=_silent, device=self.device
        )
        try:
            with torch.inference_mode():
                audio, _, _ = infer_process(
                    ref_audio,
                    ref_text,
                    text,
                    self.model,
                    self.vocoder,
                    mel_spec_type="vocos",
                    show_info=_silent,
                    progress=None,
                    nfe_step=nfe_step,
                    speed=speed,
                    device=self.device,
                )
        finally:
            # preprocess_ref_audio_text writes a temp file and never removes it
            if ref_audio != str(ref_audio_path):
                Path(ref_audio).unlink(missing_ok=True)
        audio = np.asarray(audio, dtype=np.float32)
        return _normalize(audio)


def _silent(*_args, **_kwargs) -> None:
    pass


def _normalize(audio, target_dbfs: float = -20.0):
    """Bring the output to a consistent level without clipping, as the wrapper does."""
    import numpy as np

    rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
    if rms <= 0:
        return audio
    gain = 10 ** (target_dbfs / 20) / rms
    gain = min(gain, 0.97 / max(float(np.abs(audio).max()), 1e-6))
    return (audio * gain).astype(np.float32)
