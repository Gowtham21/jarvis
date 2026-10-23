"""Collects the user's own speech as reference samples for voice cloning.

Every time the microphone captures an utterance that speech recognition understood,
the raw audio is saved here along with its transcript and a quality score. The clone
TTS engine then uses the best clips as the reference voice, so Jarvis sounds more like
the user the more they talk to it. No model is trained; modern cloning engines work
zero-shot from a few seconds of reference audio.

Learning continues until the user says the voice is right, which locks the profile:
the current best clips are kept and no new samples are collected until it is unlocked.
"""

from __future__ import annotations

import array
import datetime as dt
import io
import json
import logging
import math
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MIN_SECONDS = 2.0   # shorter clips carry too little of the voice
MAX_SECONDS = 25.0  # longer clips are usually noise or a stuck microphone
MANIFEST = "samples.jsonl"
STATE = "state.json"
MAX_STORED = 60     # keep the pool bounded; the weakest clips are dropped past this
TARGET_DBFS = -20.0  # loudness every stored clip is normalised to
QUIET_DBFS = -32.0   # below this the recording is too faint to be useful
ENROLLED_BONUS = 1.4  # clips read on purpose for cloning beat command fragments


@dataclass(frozen=True)
class Sample:
    path: Path
    seconds: float
    transcript: str
    recorded_at: str
    rms: float = 0.0     # loudness of the original recording, 0..1 full scale
    peak: float = 0.0    # peak of the original recording, 0..1 full scale
    enrolled: bool = False  # read aloud on request during enrollment

    @property
    def score(self) -> float:
        """Higher is a better reference clip.

        Rewards length up to about 12 s and a healthy original recording level, and
        penalises clips that were nearly silent (too far from the mic) or clipping.
        Loudness is judged on the recording as captured, before normalisation, because
        a faint recording boosted to target level also boosts its noise.
        """
        length = min(self.seconds, 12.0) / 12.0
        if self.rms <= 0:
            loudness = 0.0
        else:
            # about -30 dBFS .. -12 dBFS is a comfortable range for a close microphone
            db = 20 * math.log10(self.rms)
            loudness = max(0.0, 1.0 - abs(db + 21) / 15)
        clipping = 0.5 if self.peak >= 0.99 else 1.0
        bonus = ENROLLED_BONUS if self.enrolled else 1.0
        return round(length * (0.4 + 0.6 * loudness) * clipping * bonus, 3)


class VoiceProfile:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self._manifest = self.folder / MANIFEST
        self._state_file = self.folder / STATE
        if self._manifest.exists() and not self._state().get("preprocessed"):
            # Clips stored before trimming/normalisation existed: bring them up to date.
            log.info("Normalising %d existing voice clips", self.reprocess_all())

    # ---- state -----------------------------------------------------------

    def _state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {}

    def _save_state(self, **changes) -> None:
        state = self._state()
        state.update(changes)
        self._state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    @property
    def locked(self) -> bool:
        return bool(self._state().get("locked", False))

    def lock(self) -> None:
        """Freeze the voice: keep the current best clips and stop collecting."""
        self._save_state(locked=True, locked_at=dt.datetime.now().isoformat(timespec="seconds"))

    def unlock(self) -> None:
        """Resume learning from new utterances."""
        self._save_state(locked=False)

    # ---- writing -------------------------------------------------------

    def add(self, wav_bytes: bytes, transcript: str, enrolled: bool = False) -> Sample | None:
        """Store one utterance. Returns the Sample, or None if it was skipped.

        The clip is trimmed of leading and trailing silence and normalised to a
        consistent loudness before it is stored, so a quiet or padded recording still
        makes a usable reference.
        """
        if self.locked and not enrolled:
            return None
        _, rms, peak = _wav_stats(wav_bytes)
        processed = preprocess_clip(wav_bytes)
        seconds, _, _ = _wav_stats(processed)
        if not MIN_SECONDS <= seconds <= MAX_SECONDS:
            log.debug("Skipping %.1fs clip (outside %s-%s s)", seconds, MIN_SECONDS, MAX_SECONDS)
            return None

        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.folder / f"{stamp}.wav"
        path.write_bytes(processed)
        sample = Sample(path, round(seconds, 2), transcript, stamp, round(rms, 4), round(peak, 4), enrolled)
        with self._manifest.open("a", encoding="utf-8") as fh:
            fh.write(_row(sample) + "\n")
        log.info("Saved %.1fs voice sample, score %.2f", seconds, sample.score)
        self._prune()
        return sample

    def reprocess_all(self) -> int:
        """Re-run trimming and normalisation on every stored clip (one-off migration)."""
        n = 0
        for s in self.samples():
            s.path.write_bytes(preprocess_clip(s.path.read_bytes()))
            n += 1
        self._save_state(preprocessed=True)
        return n

    def _prune(self) -> None:
        samples = self.samples()
        if len(samples) <= MAX_STORED:
            return
        keep = sorted(samples, key=lambda s: s.score, reverse=True)[:MAX_STORED]
        with self._manifest.open("w", encoding="utf-8") as fh:
            for s in keep:
                fh.write(_row(s) + "\n")
        for s in samples:
            if s not in keep:
                s.path.unlink(missing_ok=True)

    # ---- reading -------------------------------------------------------

    def samples(self) -> list[Sample]:
        if not self._manifest.exists():
            return []
        out: list[Sample] = []
        backfilled = False
        for line in self._manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            path = self.folder / row["file"]
            if not path.exists():
                continue
            if "rms" not in row:
                # Clip recorded before loudness tracking existed: measure it now.
                _, rms, peak = _wav_stats(path.read_bytes())
                row["rms"], row["peak"] = round(rms, 4), round(peak, 4)
                backfilled = True
            out.append(Sample(path, row["seconds"], row["transcript"], row["recorded_at"],
                              row["rms"], row["peak"], bool(row.get("enrolled", False))))
        if backfilled:
            with self._manifest.open("w", encoding="utf-8") as fh:
                for s in out:
                    fh.write(_row(s) + "\n")
        return out

    def total_seconds(self) -> float:
        return round(sum(s.seconds for s in self.samples()), 1)

    def reference_clips(self, budget_seconds: float = 30.0, limit: int = 5) -> list[Path]:
        """Pick the clips that best represent the voice, by quality score.

        Cloning engines do best with 6-30 seconds of clean reference audio.
        """
        chosen: list[Path] = []
        used = 0.0
        for s in sorted(self.samples(), key=lambda s: s.score, reverse=True):
            if len(chosen) >= limit or used + s.seconds > budget_seconds:
                continue
            chosen.append(s.path)
            used += s.seconds
        return chosen

    def best_clip(self, language: str | None = None, max_seconds: float | None = None) -> Sample | None:
        """The single highest-scoring clip, preferring one whose transcript is in `language`.

        Single-reference engines such as IndicF5 take one clip plus its transcript and do
        best when the clip is in the language being spoken and not too long.
        """
        pool = self.samples()
        if max_seconds is not None:
            short = [s for s in pool if s.seconds <= max_seconds]
            pool = short or pool
        if language:
            same = [s for s in pool if transcript_language(s.transcript) == language]
            pool = same or pool
        return max(pool, key=lambda s: s.score, default=None)

    def voice_dir_display(self) -> str:
        return str(self.folder.resolve())

    def is_ready(self, min_seconds: float = 6.0) -> bool:
        return self.total_seconds() >= min_seconds

    def status(self, min_seconds: float = 6.0) -> str:
        """One-line, speakable summary of the learning state."""
        samples = self.samples()
        total = self.total_seconds()
        if not samples:
            return "I have no samples of your voice yet."
        best = max(s.score for s in samples)
        enrolled = sum(1 for s in samples if s.enrolled)
        state = "locked, so I am not learning anymore" if self.locked else "still learning"
        ready = "ready" if total >= min_seconds else f"needs {min_seconds - total:.0f} more seconds"
        return (f"I have {len(samples)} samples, {enrolled} of them read for enrollment, "
                f"{total:.0f} seconds of your voice, best clip quality {best:.2f} out of 1. "
                f"The voice is {ready} and {state}.")

    def clear(self) -> int:
        n = 0
        for s in self.samples():
            s.path.unlink(missing_ok=True)
            n += 1
        self._manifest.unlink(missing_ok=True)
        self._state_file.unlink(missing_ok=True)
        return n


def _row(s: Sample) -> str:
    return json.dumps({
        "file": s.path.name, "seconds": s.seconds, "transcript": s.transcript,
        "recorded_at": s.recorded_at, "rms": s.rms, "peak": s.peak, "enrolled": s.enrolled,
    })


def transcript_language(text: str) -> str:
    """"ta" if the text is written in Tamil script, "hi" for Devanagari, else "en"."""
    if any("஀" <= ch <= "௿" for ch in text):
        return "ta"
    if any("ऀ" <= ch <= "ॿ" for ch in text):
        return "hi"
    return "en"


def level_dbfs(wav_bytes: bytes) -> float:
    """Loudness of a recording in dBFS (0 is full scale; speech sits around -25 to -15)."""
    _, rms, _ = _wav_stats(wav_bytes)
    return 20 * math.log10(rms) if rms > 0 else -120.0


def preprocess_clip(wav_bytes: bytes, target_dbfs: float = TARGET_DBFS) -> bytes:
    """Trim silence from both ends and normalise loudness to target_dbfs.

    Cloning engines copy the reference as it is: a faint clip gives a thin, flat voice
    and long silences dilute what little speech there is.
    """
    import numpy as np

    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        frames = w.readframes(w.getnframes())
    if width != 2:
        return wav_bytes
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if audio.size == 0:
        return wav_bytes

    # Trim: drop leading/trailing stretches quieter than -40 dB relative to the peak,
    # keeping 150 ms of padding so words are not clipped.
    window = max(1, rate // 50)  # 20 ms
    envelope = np.sqrt(np.convolve(audio ** 2, np.ones(window) / window, mode="same"))
    threshold = max(envelope.max() * 10 ** (-40 / 20), 1e-4)
    loud = np.flatnonzero(envelope > threshold)
    if loud.size:
        pad = int(rate * 0.15)
        audio = audio[max(0, loud[0] - pad): min(audio.size, loud[-1] + pad)]

    rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
    if rms > 0:
        gain = 10 ** (target_dbfs / 20) / rms
        gain = min(gain, 0.97 / max(float(np.abs(audio).max()), 1e-6))  # never clip
        audio = audio * gain

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    return out.getvalue()


def _wav_stats(wav_bytes: bytes) -> tuple[float, float, float]:
    """Return (seconds, rms, peak) with rms and peak as fractions of full scale."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        frames, rate, width = w.getnframes(), w.getframerate(), w.getsampwidth()
        seconds = frames / float(rate)
        if width != 2:
            return seconds, 0.0, 0.0
        data = array.array("h")
        data.frombytes(w.readframes(frames))
    if not data:
        return seconds, 0.0, 0.0
    full = 32768.0
    peak = max(abs(v) for v in data) / full
    rms = math.sqrt(sum(v * v for v in data) / len(data)) / full
    return seconds, rms, peak
