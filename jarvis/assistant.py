"""The Claude-backed brain: keeps conversation history and runs the tool loop."""

from __future__ import annotations

import logging

import anthropic

from jarvis.config import Settings
from jarvis.tools import build_tools, web_search_tool
from jarvis.voice_profile import VoiceProfile

log = logging.getLogger(__name__)

MAX_PAUSE_RESTARTS = 3

SYSTEM_PROMPT = """\
You are Jarvis, a voice assistant running on {user_name}'s computer.
Everything you say is read aloud by a text-to-speech engine, so:
- Answer in one to three short spoken sentences. No markdown, bullets, code, or URLs read out loud.
- Be warm and direct. Address the user as {user_name} occasionally, not every time.
- When the user asks for the time, date, or a greeting, call get_current_time first.
- Use open_website to open sites like YouTube, and play_media for music or videos.
- For the "head or tail" game, call flip_coin once per round and tell the user whether they won.
- Use web_search only for things you cannot answer from your own knowledge, such as
  today's news, live scores, or prices. Summarize what you find in a sentence or two.
- If speech recognition clearly garbled the request, ask a brief clarifying question.
- Reply in the language the user spoke. If they spoke Tamil, or mixed Tamil and English,
  answer in natural spoken Tamil written in Tamil script, never transliterated into Latin
  letters. Keep English technical words as they are.
- You speak in a clone of the user's own voice, learned from what they say to you. When they
  comment on that voice, call manage_voice: "lock" when they say it is perfect or good enough,
  "unlock" when they want it to keep learning or say it is not like them yet, "reset" to start
  over, "status" when they ask how it is going. Confirm what you did in one sentence.
"""


class Assistant:
    def __init__(
        self,
        settings: Settings,
        client: anthropic.Anthropic | None = None,
        profile: VoiceProfile | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic()
        self._messages: list[dict] = []
        self._tools = build_tools(settings, profile) + [web_search_tool(settings.web_search_uses)]
        self._system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT.format(user_name=settings.user_name),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def reset(self) -> None:
        self._messages.clear()

    def ask(self, text: str) -> str:
        """Send one user turn to Claude, run any tools it asks for, and return the spoken reply."""
        self._messages.append({"role": "user", "content": text})

        request: dict = dict(
            model=self._settings.model,
            max_tokens=self._settings.max_tokens,
            system=self._system,
            tools=self._tools,
            output_config={"effort": self._settings.effort},
        )
        if self._settings.uses_fallbacks:
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"

        last = None
        restarts = 0
        while True:
            runner = self._client.beta.messages.tool_runner(messages=self._messages, **request)
            last = None
            for message in runner:
                last = message
                # Mirror history: the runner keeps its own copy and does not expose it.
                self._messages.append({"role": "assistant", "content": message.content})
                tool_response = runner.generate_tool_call_response()
                if tool_response is not None:
                    self._messages.append(tool_response)
            if last is None or last.stop_reason != "pause_turn":
                break
            restarts += 1
            if restarts > MAX_PAUSE_RESTARTS:
                log.warning("Turn still paused after %d restarts; giving up", restarts)
                break

        if last is None:
            return "Sorry, I did not get a response."
        if last.stop_reason == "refusal":
            category = last.stop_details.category if last.stop_details else None
            log.info("Request declined by safety classifier (category=%s)", category)
            return "Sorry, I can't help with that one."
        if last.stop_reason == "max_tokens":
            log.warning("Reply was cut off at max_tokens=%d", self._settings.max_tokens)

        reply = "".join(block.text for block in last.content if block.type == "text").strip()
        return reply or "Done."
