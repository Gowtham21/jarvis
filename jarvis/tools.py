"""Local actions Claude can take on this machine, exposed as tools."""

from __future__ import annotations

import datetime as dt
import os
import platform
import random
import subprocess
import webbrowser
from pathlib import Path

from anthropic import beta_tool

from jarvis.config import Settings
from jarvis.voice_profile import VoiceProfile

MEDIA_EXTENSIONS = {
    "music": {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm"},
}


def _open_with_default_app(path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(path)  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def build_tools(settings: Settings, profile: VoiceProfile | None = None) -> list:
    """Return the client-side tools, bound to the current settings and voice profile."""

    @beta_tool
    def get_current_time() -> str:
        """Get the current local date and time on the user's computer.

        Use this whenever the user asks about the time, date, or day of the week,
        or when you need to decide on a greeting such as good morning or good evening.
        """
        now = dt.datetime.now()
        return now.strftime("%A, %d %B %Y, %I:%M %p")

    @beta_tool
    def open_website(url: str) -> str:
        """Open a web page in the user's default browser.

        Args:
            url: Full URL to open, for example https://www.youtube.com. If the user
                names a site without a scheme, add https://.
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)
        return f"Opened {url}"

    @beta_tool
    def play_media(kind: str, query: str = "") -> str:
        """Play a music or video file from the user's configured media folder.

        Args:
            kind: Either "music" or "video".
            query: Optional words from the file name to match. If empty, a random
                file from the folder is played.
        """
        kind = kind.lower().strip()
        folder = settings.music_dir if kind == "music" else settings.video_dir
        if kind not in MEDIA_EXTENSIONS:
            return 'kind must be "music" or "video"'
        if folder is None or not folder.is_dir():
            return (
                f"No {kind} folder is configured. Ask the user to set "
                f"JARVIS_{kind.upper()}_DIR in their .env file."
            )

        files = [p for p in folder.iterdir() if p.suffix.lower() in MEDIA_EXTENSIONS[kind]]
        if not files:
            return f"The {kind} folder {folder} has no playable files."

        words = [w for w in query.lower().split() if w]
        if words:
            matches = [p for p in files if all(w in p.stem.lower() for w in words)]
            choice = matches[0] if matches else None
            if choice is None:
                names = ", ".join(p.stem for p in files[:15])
                return f"No file matched '{query}'. Available: {names}"
        else:
            choice = random.choice(files)

        _open_with_default_app(choice)
        return f"Playing {choice.name}"

    @beta_tool
    def flip_coin() -> str:
        """Flip a fair coin and return "heads" or "tails". Use for the head-or-tail game."""
        return random.choice(["heads", "tails"])

    @beta_tool
    def manage_voice(action: str) -> str:
        """Control how Jarvis learns and uses the user's own voice for its replies.

        Jarvis keeps collecting samples of the user's voice from what they say and
        answers in a clone of it. Call this when the user talks about that voice.

        Args:
            action: One of:
                "status" - how many samples exist, quality, and whether learning is on.
                "lock" - the user is happy with the voice ("that's perfect", "sounds like
                    me, keep it"): freeze it and stop collecting new samples.
                "unlock" - the user wants it to keep improving ("learn my voice more",
                    "it doesn't sound like me yet"): resume collecting samples.
                "reset" - the user wants to start over: delete every sample.
        """
        if profile is None:
            return "Voice learning is not available in this session."
        action = action.lower().strip()
        if action == "status":
            return profile.status(settings.clone_min_seconds)
        if action == "lock":
            profile.lock()
            return "Voice locked. I will keep speaking like this and stop collecting samples."
        if action == "unlock":
            profile.unlock()
            return "Learning resumed. I will keep collecting samples as the user talks."
        if action == "reset":
            n = profile.clear()
            return f"Deleted {n} samples. I will learn the voice again from scratch."
        return 'action must be "status", "lock", "unlock" or "reset"'

    return [get_current_time, open_website, play_media, flip_coin, manage_voice]


def web_search_tool(max_uses: int) -> dict:
    """Anthropic's server-side web search tool; runs on their side, nothing to implement."""
    return {"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}
