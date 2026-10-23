"""Unit tests for the voice profile: scoring, selection, lock state."""

import io
import math
import struct
import wave
from pathlib import Path

import pytest

from jarvis.voice_profile import VoiceProfile, level_dbfs, preprocess_clip


def make_wav(seconds: float, amp: int = 4000, rate: int = 16000, silence: float = 0.0) -> bytes:
    """A sine 'voice' of the given length, optionally padded with silence on both ends."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        pad = b"\x00\x00" * int(rate * silence)
        tone = b"".join(struct.pack("<h", int(amp * math.sin(i / 20))) for i in range(int(rate * seconds)))
        w.writeframes(pad + tone + pad)
    return buf.getvalue()


def test_preprocess_trims_silence_and_normalises_level():
    quiet_padded = make_wav(4.0, amp=300, silence=2.0)
    assert level_dbfs(quiet_padded) < -32
    out = preprocess_clip(quiet_padded)
    with wave.open(io.BytesIO(out)) as w:
        seconds = w.getnframes() / w.getframerate()
    assert 4.0 <= seconds <= 4.5  # padding removed, 150 ms kept each side
    assert -21.5 <= level_dbfs(out) <= -18.5


def test_preprocess_never_clips():
    loud = make_wav(3.0, amp=32000)
    out = preprocess_clip(loud)
    with wave.open(io.BytesIO(out)) as w:
        frames = w.readframes(w.getnframes())
    peak = max(abs(struct.unpack("<h", frames[i:i + 2])[0]) for i in range(0, len(frames), 2))
    assert peak <= 32767 * 0.98


def test_enrolled_clips_outrank_equal_fragments(tmp_path: Path):
    profile = VoiceProfile(tmp_path / "v")
    fragment = profile.add(make_wav(8.0), "command fragment")
    enrolled = profile.add(make_wav(8.0), "read on request", enrolled=True)
    assert enrolled.score > fragment.score
    assert profile.reference_clips()[0] == enrolled.path


def test_enrollment_allowed_while_locked(tmp_path: Path):
    profile = VoiceProfile(tmp_path / "v")
    profile.lock()
    assert profile.add(make_wav(8.0), "fragment") is None
    assert profile.add(make_wav(8.0), "enrolled", enrolled=True) is not None


@pytest.fixture
def profile(tmp_path: Path) -> VoiceProfile:
    return VoiceProfile(tmp_path / "voice")


def test_skips_clips_outside_length_range(profile):
    assert profile.add(make_wav(1.0), "too short") is None
    assert profile.add(make_wav(30.0), "too long") is None
    assert profile.add(make_wav(3.0), "fine").seconds == 3.0


def test_quality_scoring_prefers_clear_normal_level_clips(profile):
    normal = profile.add(make_wav(8.0, 4000), "normal")
    quiet = profile.add(make_wav(8.0, 150), "quiet")
    clipping = profile.add(make_wav(8.0, 32767), "clipping")
    short = profile.add(make_wav(3.0, 4000), "short")
    assert normal.score > quiet.score
    assert normal.score > clipping.score
    assert normal.score > short.score


def test_reference_clips_ordered_by_score_within_budget(profile):
    profile.add(make_wav(8.0, 4000), "best")
    profile.add(make_wav(8.0, 150), "quiet")
    profile.add(make_wav(4.0, 4000), "ok")
    refs = profile.reference_clips(budget_seconds=12.0)
    names = {s.path: s.transcript for s in profile.samples()}
    assert [names[p] for p in refs] == ["best", "ok"]


def test_lock_stops_collection_and_unlock_resumes(profile):
    profile.add(make_wav(8.0), "first")
    profile.lock()
    assert profile.locked
    assert profile.add(make_wav(8.0), "ignored") is None
    assert len(profile.samples()) == 1
    profile.unlock()
    assert not profile.locked
    assert profile.add(make_wav(8.0), "second") is not None
    assert len(profile.samples()) == 2


def test_status_is_speakable(profile):
    assert "no samples" in profile.status()
    profile.add(make_wav(8.0), "x")
    assert "still learning" in profile.status()
    profile.lock()
    assert "locked" in profile.status()


def test_clear_removes_files_and_state(profile):
    profile.add(make_wav(8.0), "x")
    profile.lock()
    assert profile.clear() == 1
    assert profile.samples() == []
    assert not profile.locked
