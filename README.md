# Jarvis

A voice assistant for your desktop with a Claude brain. Say the wake word, ask anything, and Jarvis answers out loud. It can tell the time, open websites, play music or videos from your folders, search the web for live information, and play head-or-tail.

This is a rewrite of the original 2019 script. The old version matched keywords and bounced unknown questions to WolframAlpha and Wikipedia. The new one sends your request to Claude, which decides which local tool to call and replies in natural language, remembering the conversation as it goes.

## What changed

| Area | Before | Now |
| --- | --- | --- |
| Understanding | `if 'wikipedia' in query` keyword chains | Claude Opus 5.5 with tool use and conversation memory |
| Facts and news | WolframAlpha key + Wikipedia | Claude's knowledge plus Anthropic's server-side web search |
| Speech to text | Google Web Speech only | Google (default) or offline Whisper |
| Text to speech | Windows SAPI5 via pyttsx3 | pyttsx3 (default), Microsoft neural voices via edge-tts, or a clone of your own voice |
| Configuration | Paths and keys edited inside the script | `.env` file, nothing secret in git |
| Layout | One 185-line file plus a Clojure jar and a RAR | Installable Python package with a `jarvis` command |
| Testing | Needed a microphone | `--text` mode works from the keyboard |

## Requirements

- Python 3.10 or newer
- An Anthropic API key from https://console.anthropic.com
- A microphone and speakers (skip these with `--text`)

## Setup

```bash
git clone https://github.com/Gowtham21/jarvis.git
cd jarvis
python -m venv .venv
```

Activate the virtual environment (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere), then:

```bash
pip install -e .
```

Create your config:

```bash
copy .env.example .env
```

Open `.env` and set `ANTHROPIC_API_KEY` and `JARVIS_USER_NAME`. Everything else has a working default.

## Run

```bash
jarvis
```

Say "jarvis, what time is it" or "jarvis, open youtube". Say "bye" to quit.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--text` | Type and read instead of speaking. No wake word needed. Good for testing without a microphone. |
| `--no-wake` | Respond to everything, no wake word needed. |
| `--name Gowtham` | Override the name Jarvis uses for you. |
| `-v` | Show debug logging. |

You can also run it as a module with `python -m jarvis`.

## Configuration

All settings live in `.env` and start with `JARVIS_`. See `.env.example` for the full list.

| Setting | Default | Notes |
| --- | --- | --- |
| `JARVIS_USER_NAME` | `sir` | How Jarvis addresses you |
| `JARVIS_WAKE_WORD` | `jarvis` | Leave empty to disable the wake word |
| `JARVIS_MODEL` | `claude-opus-5-5` | Any current Claude model. Haiku models skip refusal fallbacks automatically. |
| `JARVIS_EFFORT` | `low` | Reasoning depth: `low`, `medium`, `high`, `xhigh`, `max`. Low is fastest for chat. |
| `JARVIS_STT` | `google` | `google` needs internet; `whisper` runs offline |
| `JARVIS_WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `JARVIS_LANGUAGES` | `en-IN` | Languages you speak, comma separated, most used first, e.g. `en-IN,ta-IN` |
| `JARVIS_TTS` | `pyttsx3` | `pyttsx3` offline; `edge` neural voices; `clone` your voice via XTTS; `indic` your voice via IndicF5 |
| `JARVIS_EDGE_VOICE` | `en-IN-PrabhatNeural` | Run `edge-tts --list-voices` to see options |
| `JARVIS_SPEECH_RATE` | `170` | Words per minute for pyttsx3 |
| `JARVIS_MUSIC_DIR` | empty | Folder for "play music" |
| `JARVIS_VIDEO_DIR` | empty | Folder for "play video" |
| `JARVIS_COLLECT_VOICE` | `true` | Save what you say as reference clips for the clone voice |
| `JARVIS_VOICE_DIR` | `voice_profile` | Where those clips are stored (ignored by git) |
| `JARVIS_CLONE_LANGUAGE` | `en` | Language the clone voice speaks |
| `JARVIS_CLONE_MIN_SECONDS` | `6` | Speech needed before the clone voice switches on |
| `JARVIS_CLONE_TEMPERATURE` | `0.75` | Expressiveness of the clone voice, 0.5 flat to 0.95 lively |
| `JARVIS_CLONE_SPEED` | `1.0` | Speaking rate of the clone voice |

## Talk back in your own voice

Jarvis can answer in a voice that sounds like you, and it learns that voice simply from you talking to it. Every command the microphone understands is saved as a short reference clip in `voice_profile/` together with its transcript. Once there are a few seconds of your speech, the clone engine uses the longest recent clips as the reference voice for every reply. Nothing is trained; the engine clones zero-shot from the reference audio, so the more and the clearer you speak, the closer it gets.

Set it up:

```bash
pip install -e .[clone]
```

Then set `JARVIS_TTS=clone` in `.env` and run `jarvis` as usual. Until enough speech is collected, Jarvis uses the standard voice and tells you how much more it needs. The first clone reply downloads the XTTS v2 model (about 1.8 GB) and loads it, which takes a minute; after that each reply is synthesised on the CPU, so expect a few seconds of delay before it speaks.

Check or reset what has been collected:

```bash
jarvis --voice-status
```

```bash
jarvis --forget-voice
```

**If the clone sounds robotic or flat**, the reference clips are the cause: short command fragments recorded quietly give the engine little to copy. Fix it in two minutes with guided enrollment, where Jarvis asks you to read four natural paragraphs and checks your microphone level as you go:

```bash
jarvis --enroll-voice
```

Enrolled clips are preferred over everyday fragments, and every clip (enrolled or not) is trimmed of silence and normalised to a consistent loudness before it is stored. Two settings tune the result: `JARVIS_CLONE_TEMPERATURE` (default 0.75; raise toward 0.9 for a livelier delivery, lower toward 0.6 for a steadier one) and `JARVIS_CLONE_SPEED` (default 1.0).

Jarvis keeps learning until you tell it to stop. Each clip gets a quality score based on length and recording level, and the best clips are always the ones used, so the voice improves as better samples arrive. Just talk to it about the voice in plain words:

- "That sounds just like me, keep it" locks the voice and stops collecting samples.
- "Learn my voice more" or "it doesn't sound like me yet" resumes learning.
- "How is my voice coming along?" reports samples, quality and whether it is locked.
- "Forget my voice and start over" deletes every sample.

Tips for a better match: speak in a quiet room, keep a steady distance from the microphone, and say a few longer sentences rather than only short commands. Samples under 2 seconds are skipped. The clips are personal data and stay on your machine; the folder is git-ignored. The XTTS v2 model is licensed for non-commercial use.

## Indian accent and Tamil

XTTS v2, the engine behind `clone`, has no Tamil and was trained mostly on non-Indian English, so it softens an Indian accent. For both, Jarvis can use IndicF5 from AI4Bharat, a voice-cloning model built for 11 Indian languages including Tamil and trained on Indian speakers.

Set it up once:

1. Accept the model terms at https://huggingface.co/ai4bharat/IndicF5 (free, instant) and create a read token at https://huggingface.co/settings/tokens. Put it in `.env` as `HF_TOKEN=hf_...`.
2. Install the engine:

```bash
pip install --no-deps git+https://github.com/ai4bharat/IndicF5.git
```

```bash
pip install -e .[indic]
```

3. In `.env`, set `JARVIS_LANGUAGES=en-IN,ta-IN` so speech recognition understands both, and pick a voice mode:
   - `JARVIS_TTS=indic` uses IndicF5 for every reply, English included, for an Indian-accented voice throughout.
   - `JARVIS_TTS=clone` keeps XTTS for English and routes Tamil replies to IndicF5 automatically.

Then run `jarvis --enroll-voice` again: with Tamil in your languages it adds three Tamil paragraphs, and Tamil replies prefer a Tamil reference clip. Speak to Jarvis in Tamil, English, or a mix; Claude answers in the language you used, and Tamil replies are written in Tamil script so the engine pronounces them properly.

IndicF5 is MIT licensed and its first use downloads about 1.4 GB. **It needs a GPU to be practical.** Measured on an 11th-gen i5 laptop with no GPU, one short sentence took 70 seconds at the fastest settings (16 diffusion steps, 5-second reference) and 290 seconds at the model's defaults, which is 60 to 130 times slower than real time, against about 7 seconds for XTTS. On a CPU, treat `indic` as a way to hear Tamil in your voice occasionally, not as the live reply engine; `clone` with Tamil routed to `indic` keeps English fast and only pays that cost for Tamil replies.

## Optional extras

Offline speech recognition with Whisper:

```bash
pip install -e .[whisper]
```

Neural text to speech:

```bash
pip install -e .[neural-tts]
```

Your own cloned voice (see above):

```bash
pip install -e .[clone]
```

## How it works

```
microphone -> Listener (Google or Whisper) -> text
text -> Assistant.ask() -> Claude tool runner
      Claude may call: get_current_time, open_website, play_media, flip_coin, web_search
reply text -> Speaker (pyttsx3 or edge-tts) -> speakers
```

- `jarvis/cli.py` runs the wake-word loop and handles API errors gracefully.
- `jarvis/assistant.py` holds the system prompt, conversation history, and the Claude call. The system prompt is cached between turns to keep costs down, and server-side refusal fallbacks are on so a declined request is retried on another model automatically.
- `jarvis/tools.py` defines the local actions Claude can take, using the SDK's `@beta_tool` decorator so schemas come from the function signatures.
- `jarvis/speech.py` wraps the speech engines behind a tiny `listen()` / `say()` interface.
- `jarvis/voice_profile.py` stores your utterances and picks the best reference clips for the clone voice.
- `jarvis/config.py` loads settings with pydantic-settings.

## Troubleshooting

- **PyAudio fails to install on Linux**: install `portaudio19-dev` first, then `pip install PyAudio`.
- **"ANTHROPIC_API_KEY is missing"**: make sure `.env` is in the folder you run from, or export the variable in your shell.
- **Nothing is heard**: run with `-v` to see calibration and recognition logs, and check the default input device in your OS settings.
- **Clone voice fails with "Could not load libtorchcodec"**: you have torch 2.9 or newer, which needs FFmpeg DLLs on Windows. Run `pip install "torch<2.9" "torchaudio<2.9"` and uninstall `torchcodec`; the `[clone]` extra pins this for you.

## License

MIT
