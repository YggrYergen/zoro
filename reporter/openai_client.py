"""
OpenAI client wrapper for the reporter CLI.

This module encapsulates interactions with OpenAI's Responses API and
Agents API.  It centralizes error handling, cost estimation, and
configuration of API requests.  By abstracting the OpenAI library here,
other parts of the application remain decoupled from the specific
service interface, improving testability and maintainability.

The Responses API is used to generate unified diffs given a prompt
context, while the Agents API (when needed) can be used for more
complex, multi-step workflows.  Only the Responses API is exercised in
the MVP implementation.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import openai  # type: ignore[import]
except ImportError:
    openai = None  # type: ignore[assignment]

try:
    import tiktoken  # type: ignore[import]
except ImportError:
    tiktoken = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class OpenAIClient:
    """Wrapper around the OpenAI API with cost estimation and error handling."""

    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required but not provided.")
        self.api_key = api_key
        self.model = model
        # Instantiate client once; openai library caches per thread
        if openai is None:
            raise ImportError(
                "The 'openai' package is not installed. Please install it to use API features."
            )
        self.client = openai.OpenAI(api_key=api_key, timeout=2160.0)

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens used by a text for the configured model."""
        # Fall back to approximate token count if tiktoken is unavailable
        if tiktoken is None:
            # Roughly assume 4 characters per token as a heuristic
            return max(1, len(text) // 4)
        try:
            encoding = tiktoken.encoding_for_model(self.model)
        except Exception:
            # Default to cl100k_base if model unknown
            encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text)
        return len(tokens)

    def _supports_reasoning(self) -> bool:
        """Heuristic/local whitelist for models that support reasoning effort."""
        m = (self.model or "").lower()
        # GPT-5 family supports reasoning effort
        if m.startswith("gpt-5"):
            return True
        # Known families that support reasoning-style parameters
        if m.startswith(("o1", "o3")):
            return True
        if "reasoning" in m:
            return True
        if m in {"o4-mini"}:
            return True
        return False

    def _supports_verbosity(self) -> bool:
        """Return True if the model supports the 'verbosity' parameter."""
        m = (self.model or "").lower()
        # GPT-5 introduces 'verbosity' control
        if m.startswith("gpt-5"):
            return True
        # Future-proof: allow families that document verbosity explicitly
        if "verbosity" in m:
            return True
        return False
    

    def _supports_temperature(self) -> bool:
        """Reasoning-first models generally don't accept temperature/top_p.
        Return False for those to avoid server-side errors."""
        m = (self.model or "").lower()
        if m.startswith(("o1", "o3", "gpt-5")) or "reasoning" in m:
            return False
        return True

    # ----------------
    # Status helpers
    # ----------------
    @staticmethod
    def _is_non_terminal(status: Optional[str]) -> bool:
        return (status or "").lower() in {"queued", "in_progress", "incomplete", "requires_action"}

    @staticmethod
    def _is_terminal(status: Optional[str]) -> bool:
        return (status or "").lower() in {"completed", "failed", "cancelled", "errored"}

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD given input and output token counts.

        Prices are approximate and follow published rates for GPT‑4o at
        $5 per million input tokens and $15 per million output tokens.
        If another model is used, adjust accordingly.
        """
        # Using gpt-4o pricing as of mid‑2025
        cost_per_input = 0.000005  # 5 USD / 1,000,000 tokens
        cost_per_output = 0.000015  # 15 USD / 1,000,000 tokens
        return input_tokens * cost_per_input + output_tokens * cost_per_output

    def _poll_until_complete(self, rid: str, timeout_s: float = 2160.0, interval_s: float = 55.0) -> Any:
        """Poll responses.retrieve(id) until reaching a terminal status or timeout."""
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            last = self.client.responses.retrieve(rid)
            status = getattr(last, "status", None)
            if self._is_terminal(status):
                return last
            # Continue polling while non-terminal
            if not self._is_non_terminal(status):
                # Unknown status → break to avoid infinite loop
                return last
            time.sleep(interval_s)
        return last

    def _stringify_part(self, part: Any) -> str:
        """Best-effort to normalize content parts to text."""
        # Attribute-based shapes (SDK objects)
        try:
            ptype = getattr(part, "type", None)
            if ptype in {"output_text", "input_text", "text"}:
                t = getattr(part, "text", None)
                if isinstance(t, str):
                    return t
                if hasattr(t, "value"):
                    return str(getattr(t, "value"))
        except Exception:
            pass
        # Dict-like shapes
        if isinstance(part, dict):
            ptype = part.get("type")
            if ptype in {"output_text", "input_text", "text"} and "text" in part:
                if isinstance(part["text"], str):
                    return part["text"]
                if isinstance(part["text"], dict) and "value" in part["text"]:
                    return str(part["text"]["value"])
            if isinstance(part.get("content"), str):
                return part["content"]
        return ""

    def extract_output_text(self, response: Any) -> str:
        """Extract assistant text across Responses API shapes."""
        # 1) SDK convenience
        try:
            t = getattr(response, "output_text", None)
            if isinstance(t, str) and t.strip():
                return t.strip().strip("`")  # light fence trim
        except Exception:
            pass
        # 2) response.output[*].content[*]
        try:
            out = getattr(response, "output", None)
            if isinstance(out, list) and out:
                buf: List[str] = []
                for item in out:
                    content = getattr(item, "content", None)
                    if isinstance(content, list):
                        for part in content:
                            s = self._stringify_part(part)
                            if s:
                                buf.append(s)
                txt = "".join(buf).strip()
                if txt:
                    return txt
        except Exception:
            pass
        # 3) Legacy chat-like
        try:
            ch0 = getattr(response, "choices", [{}])[0]
            msg = ch0.get("message", {})
            txt = msg.get("content", "")
            if isinstance(txt, str) and txt.strip():
                return txt.strip()
        except Exception:
            pass
        return ""


    def call_responses_api(
        self,
        messages: List[Dict[str, str]],
        instructions: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        previous_response_id: Optional[str] = None,
        max_output_tokens: int = 2048,
        temperature: float = 0,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Call the OpenAI Responses API with the given parameters.

        Returns the full response object.  The caller can extract
        `output_text` from the returned dictionary.
        """
        # Estimate cost for budgeting purposes
        total_input_text = instructions + "\n" + "\n".join(msg["content"] for msg in messages)
        input_tokens = self.estimate_tokens(total_input_text)
        # Roughly estimate output tokens equal to `max_output_tokens` but actual output may be less
        estimated_cost = self.estimate_cost(input_tokens, max_output_tokens)
        logger.info(
            "Estimated request will use ~%s input tokens and cost up to $%.4f", input_tokens, estimated_cost
        )
        logger.debug("Preparing Responses call with model=%s, prev_id=%s, temperature=%s, max_output_tokens=%s",
                     self.model, bool(previous_response_id), temperature, max_output_tokens)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": "\n".join([msg["content"] for msg in messages]),
            "previous_response_id": previous_response_id,
            "tools": tools or [{"type": "web_search"}],
            "max_output_tokens": max_output_tokens,
        }
        # Temperature → solo si el modelo lo soporta
        if self._supports_temperature():
            kwargs["temperature"] = temperature
        else:
            logger.info("Model '%s' does not use temperature; sending request without it.", self.model)

        # Reasoning effort (reasoning.effort)
        if reasoning_effort:
            if self._supports_reasoning():
                # Pass reasoning effort only for supported models
                kwargs["reasoning"] = {"effort": reasoning_effort}
                logger.info("Using reasoning effort '%s' with model '%s'.", reasoning_effort, self.model)
            else:
                logger.info(
                    "Model '%s' does not support reasoning effort; ignoring --reasoning-effort.",
                    self.model,
                )
        # Verbosity (per GPT-5 docs lives under the text object)
        if verbosity:
            if self._supports_verbosity():
                text_cfg = dict(kwargs.get("text") or {})
                text_cfg["verbosity"] = verbosity
                kwargs["text"] = text_cfg
                logger.info("Using verbosity '%s' (attached under 'text') with model '%s'.", verbosity, self.model)
            else:
                logger.info("Model '%s' does not support verbosity; ignoring requested verbosity.", self.model)
        logger.debug("Final kwargs keys for responses.create: %s", list(kwargs.keys()))

        # Perform request, poll if needed, normalize .output_text
        try:
            resp = self.client.responses.create(**kwargs)
            print(inspect.signature(self.client.responses.create).parameters)
        except Exception as e:
            # Retry once without unsupported args if server complains
            msg = str(e)
            if "Unsupported parameter: 'temperature'" in msg and "temperature" in kwargs:
                logger.info("Retrying without 'temperature' because the model rejected it.")
                kwargs.pop("temperature", None)
                resp = self.client.responses.create(**kwargs)
                print(inspect.signature(self.client.responses.create).parameters)
            elif ("Unrecognized request argument: reasoning" in msg or "Unsupported parameter: 'reasoning'" in msg) and "reasoning" in kwargs:
                logger.info("Retrying without 'reasoning' because the model rejected it.")
                kwargs.pop("reasoning", None)
                resp = self.client.responses.create(**kwargs)
                print(inspect.signature(self.client.responses.create).parameters)
            else:
                raise

        status = getattr(resp, "status", None)
        rid = getattr(resp, "id", None)
        # Some models return non-terminal statuses with empty text → poll
        if rid and (self._is_non_terminal(status) or not bool(self.extract_output_text(resp))):
            logger.debug("Initial response status=%s; polling id=%s until completion...", status, rid)
            resp = self._poll_until_complete(rid)

        # Ensure caller gets .output_text populated even if SDK shape differs
        try:
            text = self.extract_output_text(resp)
            if isinstance(text, str):
                setattr(resp, "output_text", text)
        except Exception:
            pass
        return resp

    # Placeholder for Agents API calls; future versions may use this
    def call_agents_api(
        self,
        agent_name: str,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0,
    ) -> Any:
        """Call the Agents API.  Not implemented in the MVP."""
        raise NotImplementedError("Agents API integration is planned for a future release.")