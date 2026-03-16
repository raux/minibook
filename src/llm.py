"""
LM Studio integration for Minibook.

Provides a local LLM client using the OpenAI-compatible API exposed by LM Studio.
Pattern adapted from https://github.com/raux/Local-Review-Critic
"""

from __future__ import annotations

import logging
import re
import urllib.parse

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

# Hostnames / IP ranges allowed as the LM Studio base URL.
# LM Studio is always local, so we restrict to loopback addresses only
# to prevent SSRF when a URL is supplied dynamically.
_ALLOWED_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
})

# ----- System prompts (following Local-Review-Critic agent design) -----

GENERATOR_SYSTEM = (
    "You are a specialized code generator. "
    "Provide your code solution inside markdown fenced code blocks (```language\\n...\\n```). "
    "You may include brief natural language explanations outside the code blocks."
)

OPTIMISTIC_CRITIC_SYSTEM = (
    "You are an Optimistic Coding reviewer who focuses on what works well. "
    "Analyze the provided work and highlight strengths: "
    "good design decisions, correct use of patterns, solid logic, and best practices followed. "
    "Use only natural language. Do not include code snippets, code blocks, or inline code."
)

PESSIMISTIC_CRITIC_SYSTEM = (
    "You are a Pessimistic Coding (Defensive Programming) reviewer who focuses on finding issues. "
    "Analyze the provided work for potential bugs, edge cases, security concerns, "
    "defensive programming practices, and areas that need improvement. Suggest specific improvements. "
    "Use only natural language. Do not include code snippets, code blocks, or inline code."
)

GENERAL_ASSISTANT_SYSTEM = (
    "You are a helpful AI assistant participating in a developer collaboration forum. "
    "Provide clear, concise, and technically accurate responses. "
    "Use markdown formatting when appropriate."
)


# ----- URL helpers -----

def normalize_base_url(url: str) -> str:
    """
    Ensure *url* ends with ``/v1`` so that the OpenAI client always
    produces the correct LM Studio API path.
    """
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


def validate_lm_studio_url(url: str) -> str:
    """
    Ensure *url* points only to a loopback address (localhost / 127.0.0.1 / ::1).
    Returns the normalised URL string or raises ``ValueError``.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
    except Exception as exc:
        raise ValueError(f"Invalid LM Studio URL: {exc}") from exc

    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            f"LM Studio URL host '{host}' is not allowed. "
            "Only localhost / 127.0.0.1 / ::1 are permitted."
        )
    return normalize_base_url(url)


# ----- Client management -----

class LMStudioClient:
    """Lazy-initialised wrapper around the OpenAI-compatible LM Studio API."""

    def __init__(self, base_url: str = "http://localhost:1234/v1", model: str = ""):
        self._base_url = validate_lm_studio_url(base_url)
        self._configured_model = model
        self._client: OpenAI | None = None
        self._resolved_model: str = ""

    @property
    def base_url(self) -> str:
        return self._base_url

    def _get_client(self) -> OpenAI:
        if self._client is None:
            logger.debug("Creating OpenAI client → base_url=%s", self._base_url)
            self._client = OpenAI(base_url=self._base_url, api_key="lm-studio")
        return self._client

    def get_model(self) -> str:
        """Return the configured model or auto-detect the first available one."""
        if self._resolved_model:
            return self._resolved_model
        if self._configured_model:
            self._resolved_model = self._configured_model
            logger.debug("Using configured model: %s", self._resolved_model)
            return self._resolved_model
        # Auto-detect
        logger.debug("No model configured – fetching available models from LM Studio…")
        client = self._get_client()
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        logger.debug("Models returned by LM Studio: %s", model_ids)
        if not model_ids:
            raise RuntimeError("LM Studio returned no available models.")
        self._resolved_model = model_ids[0]
        logger.info("Auto-selected model: %s", self._resolved_model)
        return self._resolved_model

    async def check_status(self) -> dict:
        """Check whether LM Studio is currently reachable."""
        health_url = self._base_url + "/models"
        logger.debug("Status check → GET %s", health_url)
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(
                    health_url,
                    headers={"Authorization": "Bearer lm-studio"},
                )
                resp.raise_for_status()
            logger.info("LM Studio is online at %s", self._base_url)
            return {"lm_studio": "online", "base_url": self._base_url}
        except Exception as exc:
            logger.warning("LM Studio is offline (%s: %s)", type(exc).__name__, exc)
            return {"lm_studio": "offline", "error": str(exc)}

    # ----- Chat helpers -----

    def _chat(self, system: str, user: str, temperature: float = 0.4) -> dict:
        """
        Single-turn chat request.  Returns ``{"content": ..., "reasoning": ...}``.
        """
        client = self._get_client()
        model = self.get_model()
        logger.debug(
            "LLM request → model=%s, system length=%d, user length=%d",
            model, len(system), len(user),
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        result: dict = {"content": response.choices[0].message.content or ""}
        logger.debug(
            "LLM response ← model=%s, finish_reason=%s, content length=%d",
            response.model,
            response.choices[0].finish_reason,
            len(result["content"]),
        )
        # Capture reasoning if provided by thinking models
        msg = response.choices[0].message
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            result["reasoning"] = msg.reasoning_content
        return result

    @staticmethod
    def extract_code(text: str) -> str:
        """Strip markdown fences from LLM output, returning inner code blocks."""
        pattern = r"```(?:\w+)?\n?(.*?)```"
        blocks = re.findall(pattern, text, re.DOTALL)
        if blocks:
            return "\n\n".join(block.strip() for block in blocks)
        return text.strip()

    # ----- High-level agent functions -----

    def generate(self, prompt: str) -> dict:
        """Generate code from a user prompt (Step 1)."""
        logger.info("Generator: drafting code for prompt (length=%d)", len(prompt))
        result = self._chat(GENERATOR_SYSTEM, prompt)
        result["generated_code"] = self.extract_code(result["content"])
        return result

    def review(self, code: str, critic_type: str = "pessimistic") -> dict:
        """
        Review code using the critic pattern (Step 2).

        *critic_type*: ``"optimistic"`` or ``"pessimistic"``
        """
        if critic_type == "optimistic":
            system = OPTIMISTIC_CRITIC_SYSTEM
            user = (
                "Please review the following work and highlight what was done well, "
                "including good design decisions and best practices:\n\n" + code
            )
        else:
            system = PESSIMISTIC_CRITIC_SYSTEM
            user = (
                "Please review the following work and identify potential issues, "
                "bugs, edge cases, defensive programming needs, and suggest "
                "specific improvements:\n\n" + code
            )
        logger.info("Critic (%s): reviewing code (length=%d)", critic_type, len(code))
        return self._chat(system, user)

    def chat(self, prompt: str, system: str | None = None) -> dict:
        """General-purpose chat completion."""
        return self._chat(system or GENERAL_ASSISTANT_SYSTEM, prompt)
