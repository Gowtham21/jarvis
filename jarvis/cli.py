"""Command-line entry point: wake-word loop that connects speech to the Claude brain."""

from __future__ import annotations

import argparse
import logging
import sys

import anthropic
from dotenv import load_dotenv

from jarvis import __version__
from jarvis.assistant import Assistant
from jarvis.config import Settings, load_settings
from jarvis.voice_profile import VoiceProfile

log = logging.getLogger(__name__)

EXIT_WORDS = ("bye", "goodbye", "exit", "quit", "shut down")
NO_CREDENTIALS_MSG = (
    "No Anthropic credentials found. Copy .env.example to .env and set ANTHROPIC_API_KEY, "
    "or export ANTHROPIC_API_KEY in your shell."
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis", description="Voice assistant powered by Claude.")
    parser.add_argument("--text", action="store_true", help="type instead of speaking (no mic or speakers needed)")
    parser.add_argument("--no-wake", action="store_true", help="respond to every utterance without the wake word")
    parser.add_argument("--name", help="what Jarvis should call you (overrides JARVIS_USER_NAME)")
    parser.add_argument("--enroll-voice", action="store_true", help="read a few sentences aloud to teach Jarvis your voice properly")
    parser.add_argument("--voice-status", action="store_true", help="show how much of your voice has been collected and exit")
    parser.add_argument("--forget-voice", action="store_true", help="delete all collected voice samples and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    parser.add_argument("--version", action="version", version=f"jarvis {__version__}")
    return parser.parse_args(argv)


def _strip_wake_word(text: str, wake_word: str) -> str | None:
    """Return the command after the wake word, '' if only the wake word was said, None if absent."""
    if not wake_word:
        return text
    idx = text.find(wake_word)
    if idx == -1:
        return None
    return text[idx + len(wake_word):].strip(" ,.!?")


def _ask(assistant: Assistant, text: str) -> str:
    """Call Claude and turn transient API failures into spoken replies.

    Credential errors are not caught here; they are fatal and handled in main().
    """
    print("...", end="", flush=True)
    try:
        return assistant.ask(text)
    except anthropic.AuthenticationError:
        raise  # fatal: handled in main()
    except anthropic.RateLimitError:
        return "I'm being rate limited. Give me a moment and try again."
    except anthropic.APIConnectionError:
        return "I can't reach the Claude API. Check your internet connection."
    except anthropic.APIStatusError as exc:
        log.error("API error %s: %s", exc.status_code, exc.message)
        return "Something went wrong talking to Claude."
    finally:
        print("\r   \r", end="", flush=True)


def _run(
    args: argparse.Namespace,
    settings: Settings,
    listener,
    speaker,
    wake_word: str,
    profile: VoiceProfile,
) -> int:
    assistant = Assistant(settings, profile=profile)

    speaker.say(_ask(assistant, "Greet me briefly for the current time of day."))
    if wake_word:
        print(f'(say "{wake_word}" followed by your request; say "bye" to quit)')

    while True:
        try:
            heard = listener.listen()
        except KeyboardInterrupt:
            print()
            break
        if not heard:
            continue

        command = _strip_wake_word(heard, wake_word)
        if command is None:
            if any(word in heard for word in EXIT_WORDS):
                break
            continue
        if not args.text:
            print(f"{settings.user_name}: {heard}")

        if command == "":
            speaker.say("Yes?")
            command = (listener.listen() or "").strip()
            if not command:
                continue

        if any(command == word or command.startswith(word + " ") for word in EXIT_WORDS):
            break

        reply = _ask(assistant, command)
        if not args.text:
            print(f"jarvis: {reply}")
        speaker.say(reply)

    speaker.say(f"Goodbye, {settings.user_name}.")
    return 0


ENROLLMENT_PROMPTS = [
    "Hey, it's me. I just got back from a long day, and honestly I'm glad to be home. "
    "Let's put on some music, maybe something calm, and see what the evening brings.",
    "You know what's funny? Every time I plan to go to bed early, I end up watching one more "
    "episode. Then another. Then suddenly it's one in the morning and I regret everything.",
    "Okay, serious question. Do you think it'll rain tomorrow? Because I was hoping to go for "
    "a run in the morning, but if the weather turns, I'd rather sleep in and go in the evening.",
    "Remind me to call my friend later, we haven't spoken in weeks. Oh, and can you check "
    "whether that package was delivered? It should have arrived by now, I think.",
]

TAMIL_ENROLLMENT_PROMPTS = [
    "ஹலோ, நான் தான் பேசுறேன். இன்னைக்கு ஒரே வேலை, ரொம்ப களைப்பா இருக்கு. "
    "கொஞ்சம் பாட்டு போடு, அப்புறம் சாப்பிட்டு நிம்மதியா தூங்கலாம்னு இருக்கேன்.",
    "நாளைக்கு மழை வருமான்னு தெரியலை. காலையில ஓட போகலாம்னு நினைச்சேன். "
    "மழை வந்தா வீட்டுலயே இருந்து ஒரு நல்ல படம் பார்க்கலாம், என்ன சொல்ற?",
    "என் நண்பனுக்கு அப்புறம் போன் பண்ணனும், ஞாபகப்படுத்து. ரொம்ப நாளா பேசலை. "
    "அப்புறம் அந்த பார்சல் வந்துருச்சான்னு ஒரு தடவை பார்த்து சொல்லு.",
]


def _enroll_voice(settings: Settings, profile: VoiceProfile) -> int:
    """Guide the user through reading a few natural sentences for a better clone."""
    from jarvis.speech import Listener
    from jarvis.voice_profile import QUIET_DBFS, level_dbfs

    print("Voice enrollment: read each paragraph aloud the way you'd normally speak to a friend,")
    print("in a quiet room, about a hand's width from the microphone. Press Enter when ready.")
    print()
    listener = Listener(settings)  # no profile: enrollment saves clips itself
    prompts = list(ENROLLMENT_PROMPTS)
    if settings.speaks_tamil:
        prompts += TAMIL_ENROLLMENT_PROMPTS
        print("(Tamil is in JARVIS_LANGUAGES, so the last paragraphs are in Tamil; read them naturally.)\n")
    saved = 0
    for i, prompt in enumerate(prompts, 1):
        attempts = 0
        while attempts < 3:
            attempts += 1
            print(f"[{i}/{len(prompts)}] Read this:\n\n    {prompt}\n")
            try:
                input("    Press Enter, then start reading... ")
            except EOFError:
                return 1
            print("    listening (stop talking for a moment when you finish)...", flush=True)
            wav = listener.record()
            if wav is None:
                print("    I didn't hear anything. Let's try that one again.\n")
                continue
            level = level_dbfs(wav)
            if level < QUIET_DBFS:
                print(f"    Too quiet ({level:.0f} dB). Move closer to the microphone and speak up a little.\n")
                continue
            sample = profile.add(wav, prompt, enrolled=True)
            if sample is None:
                print("    That was too short. Please read the whole paragraph.\n")
                continue
            print(f"    Saved {sample.seconds:.1f}s at {level:.0f} dB, quality {sample.score:.2f}.\n")
            saved += 1
            break
    print(profile.status(settings.clone_min_seconds))
    if saved:
        print("Done. Start jarvis as usual; the enrolled clips are now the preferred reference voice.")
    return 0 if saved else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # The SDK warns that ANTHROPIC_API_KEY overrides profile discovery; that is intended here.
    logging.getLogger("anthropic.lib.credentials").setLevel(logging.ERROR)

    settings = load_settings()
    if args.name:
        settings.user_name = args.name
    wake_word = "" if (args.no_wake or args.text) else settings.wake_word

    profile = VoiceProfile(settings.voice_dir)
    if args.voice_status:
        print(profile.status(settings.clone_min_seconds))
        print(f"samples are in {profile.voice_dir_display()}")
        return 0
    if args.forget_voice:
        print(f"deleted {profile.clear()} voice samples")
        return 0
    if args.enroll_voice:
        return _enroll_voice(settings, profile)

    if args.text:
        from jarvis.speech import ConsoleIO

        io = ConsoleIO()
        listener, speaker = io, io
    else:
        from jarvis.speech import Listener, Speaker

        try:
            speaker = Speaker(settings, profile)
            listener = Listener(settings, profile if settings.collect_voice else None)
        except ImportError as exc:
            extra = {"clone": "clone", "indic": "indic", "edge": "neural-tts"}.get(settings.tts, "")
            hint = f'pip install -e ".[{extra}]"' if extra else "pip install -e ."
            print(
                f"JARVIS_TTS={settings.tts} needs packages that are not installed ({exc.name}).\n"
                f"Install them with:  {hint}\n"
                f"or set JARVIS_TTS=pyttsx3 in .env to use the standard voice.",
                file=sys.stderr,
            )
            return 2

    try:
        return _run(args, settings, listener, speaker, wake_word, profile)
    except anthropic.CredentialsError:
        print(NO_CREDENTIALS_MSG, file=sys.stderr)
        return 2
    except anthropic.AuthenticationError:
        print("ANTHROPIC_API_KEY was rejected by the API. Check the key in your .env file.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
