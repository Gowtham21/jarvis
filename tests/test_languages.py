"""Tests for language handling: script detection, settings parsing, clip selection, STT fallback."""

from pathlib import Path

from jarvis.config import Settings
from jarvis.voice_profile import VoiceProfile, transcript_language
from tests.test_voice_profile import make_wav

TAMIL = "நாளைக்கு மழை வருமா?"
HINDI = "नमस्ते, कैसे हो?"


def test_script_detection():
    from jarvis.speech import script_language

    assert script_language("what time is it") == "en"
    assert script_language(TAMIL) == "ta"
    assert script_language(HINDI) == "hi"
    assert script_language("ok " + TAMIL) == "ta"  # mixed Tanglish counts as Tamil
    assert transcript_language(TAMIL) == "ta"


def test_language_list_parsing():
    s = Settings(_env_file=None, languages="en-IN, ta-IN")
    assert s.language_list == ["en-IN", "ta-IN"]
    assert s.speaks_tamil
    assert not Settings(_env_file=None, languages="en-IN").speaks_tamil
    assert Settings(_env_file=None, languages="").language_list == ["en-IN"]


def test_best_clip_prefers_matching_language_and_length(tmp_path: Path):
    profile = VoiceProfile(tmp_path / "v")
    long_en = profile.add(make_wav(12.0), "a long english reading", enrolled=True)
    short_en = profile.add(make_wav(6.0), "a short english one")
    tamil = profile.add(make_wav(7.0), TAMIL)
    assert profile.best_clip() == long_en
    assert profile.best_clip(language="ta") == tamil
    assert profile.best_clip(language="hi") == long_en  # no Hindi clip: best overall
    assert profile.best_clip(max_seconds=8.0) in (short_en, tamil)
    assert profile.best_clip(max_seconds=8.0).seconds <= 8.0


def test_google_tries_languages_in_order(monkeypatch):
    import speech_recognition as sr

    from jarvis.speech import Listener

    calls = []

    class FakeRecognizer:
        def recognize_google(self, audio, language):
            calls.append(language)
            if language == "en-IN":
                raise sr.UnknownValueError()
            return "வணக்கம்"

    listener = Listener.__new__(Listener)
    listener._sr = sr
    listener._settings = Settings(_env_file=None, languages="en-IN,ta-IN")
    listener._recognizer = FakeRecognizer()
    assert listener._recognize_google(object()) == "வணக்கம்"
    assert calls == ["en-IN", "ta-IN"]


def test_clone_engine_routing(monkeypatch):
    import threading

    from jarvis.speech import Speaker

    sp = Speaker.__new__(Speaker)
    sp._settings = Settings(_env_file=None, tts="clone", languages="en-IN,ta-IN")
    sp._profile = None
    sp._xtts = object()
    sp._indic = object()
    sp._indic_available = True
    sp._indic_lock = threading.Lock()
    sp._warned_no_indic = False
    assert sp._pick_clone_engine("hello there") == "xtts"
    assert sp._pick_clone_engine(TAMIL) == "indic"
    sp._settings = Settings(_env_file=None, tts="indic")
    assert sp._pick_clone_engine("hello there") == "indic"
    sp._indic_available = False
    assert sp._pick_clone_engine(TAMIL) == "pyttsx3"
