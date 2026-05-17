import json
import time
import logging
from abc import ABC, abstractmethod

import anthropic

from config import ANTHROPIC_MODEL, ANTHROPIC_MAX_TOKENS, LOGS_DIR
from utils.logger import get_logger


class BaseAgent(ABC):
    def __init__(self, client: anthropic.Anthropic, logger: logging.Logger = None):
        self.client = client
        self.logger = logger or get_logger(self.__class__.__name__, LOGS_DIR)

    def _call_claude(self, system: str, user: str, max_tokens: int = None) -> str:
        max_tokens = max_tokens or ANTHROPIC_MAX_TOKENS
        return self._retry_with_backoff(lambda: self._do_call(system, user, max_tokens))

    def _do_call(self, system: str, user: str, max_tokens: int) -> str:
        response = self.client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    def _call_claude_json(self, system: str, user: str, schema_hint: str = "", max_tokens: int = None) -> dict:
        system_json = system
        if schema_hint:
            system_json += f"\n\nCRITICAL: Respond with ONLY valid JSON matching this schema: {schema_hint}\nNo markdown fences, no explanation, just JSON."
        else:
            system_json += "\n\nCRITICAL: Respond with ONLY valid JSON. No markdown fences, no explanation."

        for attempt in range(3):
            raw = self._call_claude(system_json, user, max_tokens=max_tokens)
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                if attempt < 2:
                    self.logger.warning(f"JSON parse failed attempt {attempt+1}, retrying")
                    user = user + "\n\nYour previous response was not valid JSON. Return ONLY valid JSON."
                else:
                    self.logger.error(f"JSON parse failed after 3 attempts. Raw: {raw[:200]}")
                    return {}

    def _retry_with_backoff(self, func, max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                return func()
            except anthropic.RateLimitError:
                wait = 10 * (2 ** attempt)
                self.logger.warning(f"Rate limit hit, waiting {wait}s")
                time.sleep(wait)
            except anthropic.APIConnectionError:
                self.logger.warning("Connection error, waiting 5s")
                time.sleep(5)
            except anthropic.APIStatusError as e:
                if e.status_code == 529:
                    self.logger.warning("API overloaded, waiting 30s")
                    time.sleep(30)
                else:
                    raise
        raise RuntimeError(f"Claude API failed after {max_retries} retries")

    @abstractmethod
    def run(self, *args, **kwargs):
        pass
