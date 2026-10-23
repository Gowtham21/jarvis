"""Speech input and output with swappable engines.

Listener: "google" (SpeechRecognition + Google Web Speech, online, zero setup)
          "whisper" (faster-whisper, fully offline, needs the [whisper] extra)
Speaker:  "pyttsx3" (offline system voices)
          "edge" (Microsoft neural voices via edge-tts, needs the [neural-tts] extra)
          "clone" (your own voice, learned from what you say to Jarvis; needs the [clone] extra)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jarvis.config import Settings
from jarvis.voice_profile import VoiceProfile

log = logging.getLogger(__name__)

XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"
INDIC_MODEL = "ai4bharat/IndicF5"
INDIC_SAMPLE_RATE = 24000


def script_language(text: str) -> str:
    """Guess the language of a reply from its script: "ta" for Tamil, "hi" for Hindi, else "en"."""
    if any("஀" <= ch <= "௿" for ch in text):
        return "ta"
    if any("ऀ" <= ch <= "ॿ" for ch in text):
        return "hi"
    return "en"


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


class Listener:
    """Captures one utterance from the default microphone and returns its text.

    When a VoiceProfile is given, every utterance that was understood is also saved
    there as a reference clip for the clone voice.
    """

    def __init__(self, settings: Settings, profile: VoiceProfile | None = None) -> None:
        import speech_recognition as sr

        self._sr = sr
        self._settings = settings
        self._profile = profile
        self._recognizer = sr.Recognizer()
        self._recognizer.dynamic_energy_threshold = True
        self._whisper = None

        with sr.Microphone() as source:
            log.info("Calibrating microphone for ambient noise...")
            self._recognizer.adjust_for_ambient_noise(source, duration=0.8)

    def listen(self) -> str | None:
        """Return the recognised text in lower case, or None if nothing usable was heard."""
        sr = self._sr
        try:
            with sr.Microphone() as source:
                audio = self._recognizer.listen(
                    source,
                    timeout=self._settings.listen_timeout,
                    phrase_time_limit=self._settings.phrase_time_limit,
                )
        except sr.WaitTimeoutError:
            return None

        try:
            if self._settings.stt == "whisper":
                text = self._transcribe_whisper(audio)
            else:
                text = self._recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as exc:
            log.warning("Speech service unavailable: %s", exc)
            return None

        text = (text or "").strip().lower()
        if not text:
            return None

        if self._profile is not None:
            try:
                self._profile.add(audio.get_wav_data(), text)
            except OSError as exc:
                log.warning("Could not save voice sample: %s", exc)
        return text

    def record(self, max_seconds: float = 25.0, timeout: float = 15.0) -> bytes | None:
        """Record one utterance and return raw WAV bytes without transcribing it.

        Used by voice enrollment, where the text is known because Jarvis asked for it.
        """
        sr = self._sr
        try:
            with sr.Microphone() as source:
                audio = self._recognizer.listen(source, timeout=timeout, phrase_time_limit=max_seconds)
        except sr.WaitTimeoutError:
            return None
        return audio.get_wav_data()

    def _recognize_google(self, audio) -> str:
        """Try each configured language in order; the first one that understands wins."""
        sr = self._sr
        languages = self._settings.language_list
        for i, language in enumerate(languages):
            try:
                return self._recognizer.recognize_google(audio, language=language)
            except sr.UnknownValueError:
                if i == len(languages) - 1:
                    raise
        raise sr.UnknownValueError()

    def _transcribe_whisper(self, audio) -> str:
        if self._whisper is None:
            from faster_whisper import WhisperModel

            log.info("Loading whisper model '%s'...", self._settings.whisper_model)
            self._whisper = WhisperModel(self._settings.whisper_model, compute_type="int8")

        wav = io.BytesIO(audio.get_wav_data())
        languages = self._settings.language_list
        # One language: pin it for accuracy. Several: let Whisper detect which was spoken.
        lang = languages[0].split("-")[0] if len(languages) == 1 else None
        segments, _ = self._whisper.transcribe(wav, language=lang, vad_filter=True)
        return " ".join(segment.text for segment in segments)


class Speaker:
    """Turns text into audible speech."""

    def __init__(self, settings: Settings, profile: VoiceProfile | None = None) -> None:
        self._settings = settings
        self._profile = profile
        self._xtts = None
        self._xtts_lock = threading.Lock()
        self._latents = None
        self._latents_key = None
        self._indic = None
        self._indic_lock = threading.Lock()
        self._indic_available = _module_available("f5_tts")
        self._warned_not_ready = False
        self._warned_no_indic = False
        if settings.tts in ("pyttsx3", "clone", "indic"):
            import pyttsx3  # fail early if the engine is missing

            pyttsx3.init().stop()
        if settings.tts in ("clone", "indic"):
            import sounddevice  # noqa: F401  fail early if the extra is missing
            import soundfile  # noqa: F401
        if settings.tts == "clone":
            import TTS  # noqa: F401
        if settings.tts == "indic" and not self._indic_available:
            raise ImportError("JARVIS_TTS=indic needs the IndicF5 engine: pip install -e .[indic]")

        if settings.tts in ("clone", "indic") and self._clone_ready():
            # Loading a cloning model takes close to a minute on a CPU; start now so the
            # first reply does not have to wait for it.
            if settings.tts == "clone":
                threading.Thread(target=self._load_xtts, name="xtts-preload", daemon=True).start()
            if settings.tts == "indic" or (settings.speaks_tamil and self._indic_available):
                threading.Thread(target=self._load_indic, name="indic-preload", daemon=True).start()

    def say(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._settings.tts == "edge":
            self._say_edge(text)
        elif self._settings.tts in ("clone", "indic") and self._clone_ready():
            engine = self._pick_clone_engine(text)
            if engine == "indic":
                self._say_indic(text)
            elif engine == "xtts":
                self._say_clone(text)
            else:
                self._say_pyttsx3(text)
        else:
            self._say_pyttsx3(text)

    def _pick_clone_engine(self, text: str) -> str:
        """Route a reply to the engine that can speak its language, or fall back.

        "indic" uses IndicF5 for everything. "clone" uses XTTS, except that Tamil (which
        XTTS cannot speak) goes to IndicF5 when it is installed. While a model is still
        preloading, the standard voice answers instead of making the user wait.
        """
        lang = script_language(text)
        want = "indic" if self._settings.tts == "indic" or lang == "ta" else "xtts"
        if want == "indic":
            if not self._indic_available:
                if not self._warned_no_indic:
                    print("(Tamil replies need the IndicF5 engine: pip install -e .[indic]; using the standard voice)")
                    self._warned_no_indic = True
                return "pyttsx3"
            if self._indic is None:
                print("(voice model still loading, using the standard voice for now)")
                threading.Thread(target=self._load_indic, name="indic-preload", daemon=True).start()
                return "pyttsx3"
            return "indic"
        if self._xtts is None:
            print("(voice model still loading, using the standard voice for now)")
            return "pyttsx3"
        return "xtts"

    # ---- engines -------------------------------------------------------

    def _say_pyttsx3(self, text: str) -> None:
        # A fresh engine per utterance: on Windows a reused pyttsx3 engine goes
        # silent after its first runAndWait(), so every reply after the greeting
        # was never heard.
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", self._settings.speech_rate)
        engine.setProperty("volume", 1.0)
        engine.say(text)
        engine.runAndWait()
        engine.stop()

    def _say_edge(self, text: str) -> None:
        import edge_tts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speech.mp3"
            asyncio.run(edge_tts.Communicate(text, self._settings.edge_voice).save(str(path)))
            _play_file(path)

    def _clone_ready(self) -> bool:
        ready = self._profile is not None and self._profile.is_ready(self._settings.clone_min_seconds)
        if not ready and not self._warned_not_ready:
            have = self._profile.total_seconds() if self._profile else 0.0
            print(
                f"(clone voice needs {self._settings.clone_min_seconds:.0f}s of your speech, "
                f"have {have:.1f}s; using the standard voice until then)"
            )
            self._warned_not_ready = True
        return ready

    def _load_xtts(self):
        with self._xtts_lock:
            if self._xtts is None:
                _quiet_model_libraries()
                from TTS.api import TTS

                print("(loading voice model, first run downloads it...)", flush=True)
                self._xtts = TTS(XTTS_MODEL).to("cpu")
            return self._xtts

    def _say_clone(self, text: str) -> None:
        """Synthesize sentence by sentence and play each one while the next is generated.

        On a CPU XTTS runs slower than real time, so waiting for a whole reply before
        playing anything adds the full synthesis time to the delay. Pipelining brings
        the wait down to roughly the first sentence.
        """
        tts = self._load_xtts()
        model = tts.synthesizer.tts_model
        gpt_cond, speaker_emb = self._speaker_latents(model)
        sentences = _split_sentences(text)

        def synth(sentence: str):
            # Direct inference with cached speaker latents is several times faster than
            # tts.tts(), which re-encodes the reference clips and post-processes each call.
            return model.inference(
                sentence,
                self._settings.clone_language,
                gpt_cond,
                speaker_emb,
                temperature=self._settings.clone_temperature,
                speed=self._settings.clone_speed,
            )["wav"]

        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = [pool.submit(synth, sentence) for sentence in sentences]
            for future in futures:
                _play_samples(future.result(), tts.synthesizer.output_sample_rate)

    # ---- IndicF5: Indian languages including Tamil ------------------------

    def _load_indic(self):
        with self._indic_lock:
            if self._indic is None:
                _quiet_model_libraries()
                from jarvis.indic_engine import IndicF5

                print("(loading Indian voice model, first run downloads it...)", flush=True)
                self._indic = IndicF5(device="cpu")
            return self._indic

    def _say_indic(self, text: str) -> None:
        """Speak with IndicF5, cloning from the single best reference clip.

        IndicF5 wants one reference clip of up to about 15 seconds plus its transcript,
        ideally in the language being spoken, so a Tamil reply prefers a Tamil clip.
        """
        model = self._load_indic()
        lang = script_language(text)
        # Shorter references synthesize faster on a CPU; 4-6 s is still enough to clone.
        ref = self._profile.best_clip(language=lang, max_seconds=6.0)
        if ref is None:
            self._say_pyttsx3(text)
            return

        def synth(sentence: str):
            return model.synthesize(sentence, ref.path, ref.transcript, speed=self._settings.clone_speed)

        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = [pool.submit(synth, s) for s in _split_sentences(text)]
            for future in futures:
                _play_samples(future.result(), INDIC_SAMPLE_RATE)

    def _speaker_latents(self, model):
        """Encode the reference clips once; re-encode only when the clip set changes.

        As the profile learns, better clips replace weaker ones, so the key includes the
        chosen files and their sizes and the voice follows the profile automatically.
        """
        clips = self._profile.reference_clips()
        key = tuple((str(p), p.stat().st_size) for p in clips)
        if self._latents_key != key:
            self._latents = model.get_conditioning_latents(audio_path=[str(p) for p in clips])
            self._latents_key = key
        return self._latents


def _quiet_model_libraries() -> None:
    """Keep the model stack from spamming the console while Jarvis is talking.

    None of this is actionable for the user: download progress bars, transformers'
    config nitpicks about XTTS's tokenizer, torch's Windows redirect notice, and
    torchaudio's deprecation notice about torch 2.9 (we deliberately pin below it).
    """
    import warnings

    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    warnings.filterwarnings("ignore", category=UserWarning, module=r"torchaudio(\..*)?")
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"(torch|transformers|TTS)(\..*)?")
    for name in ("torch.distributed.elastic.multiprocessing.redirects", "TTS", "trainer"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
    except ImportError:
        pass


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p] or [text]


def _play_samples(samples, samplerate: int) -> None:
    import numpy as np
    import sounddevice as sd

    sd.play(np.asarray(samples, dtype="float32"), samplerate)
    sd.wait()


def _play_file(path: Path) -> None:
    import sounddevice as sd
    import soundfile as sf

    data, samplerate = sf.read(path, dtype="float32")
    sd.play(data, samplerate)
    sd.wait()


class ConsoleIO:
    """Keyboard and screen stand-in for the microphone and speaker (used by --text)."""

    def __init__(self, prompt: str = "you> ") -> None:
        self._prompt = prompt

    def listen(self) -> str | None:
        try:
            text = input(self._prompt)
        except EOFError:
            return "bye"
        return text.strip().lower() or None

    def say(self, text: str) -> None:
        print(f"jarvis> {text}")
