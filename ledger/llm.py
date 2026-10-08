import json
import logging
from typing import Protocol, Any, Optional
import httpx
import jsonschema

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when an LLM provider or fallback fails."""
    pass


class LLM(Protocol):
    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict:
        ...


def validate_schema(data: Any, schema: dict) -> None:
    jsonschema.validate(instance=data, schema=schema)


class GroqLLM:
    def __init__(
        self,
        api_key: str = "",
        model: str = "llama-3.3-70b-versatile",
        json_mode: bool = False,
        client: Any = None,
    ):
        self.api_key = api_key
        self.model = model
        self.json_mode = json_mode
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        from groq import AsyncGroq
        return AsyncGroq(api_key=self.api_key)

    async def _call_once(self, system: str, user: str, name: str, schema: dict) -> dict:
        client = self._get_client()
        try:
            if self.json_mode:
                system_prompt = f"{system}\n\nRespond with JSON matching this schema:\n{json.dumps(schema)}"
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user},
                ]
                resp = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=800,
                )
                raw_text = resp.choices[0].message.content or "{}"
                return json.loads(raw_text)
            else:
                tools = [{
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": "Structured output response",
                        "parameters": schema,
                    },
                }]
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
                resp = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice={"type": "function", "function": {"name": name}},
                    temperature=0,
                    max_tokens=800,
                )
                tool_calls = resp.choices[0].message.tool_calls
                if not tool_calls:
                    raise LLMError("No tool calls in Groq response")
                args = tool_calls[0].function.arguments
                return json.loads(args)
        except Exception as e:
            if not isinstance(e, LLMError):
                raise LLMError(f"Groq error: {e}") from e
            raise

    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict:
        try:
            res = await self._call_once(system, user, name, schema)
            validate_schema(res, schema)
            return res
        except (jsonschema.ValidationError, json.JSONDecodeError, LLMError) as err:
            logger.warning("Groq response validation failed, retrying once: %s", err)
            retry_user = f"{user}\n\n[Previous response was invalid: {err}. Please return valid JSON matching the schema.]"
            try:
                res = await self._call_once(system, retry_user, name, schema)
                validate_schema(res, schema)
                return res
            except Exception as final_err:
                raise LLMError(f"Groq failed validation after retry: {final_err}") from final_err


class OllamaLLM:
    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "qwen2.5-ledger",
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self._client = client

    async def _call_once(self, system: str, user: str, name: str, schema: dict) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{system}\n\nSchema:\n{json.dumps(schema)}"},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }

        try:
            if self._client is not None:
                resp = await self._client.post(f"{self.url}/api/chat", json=payload, timeout=60.0)
            else:
                async with httpx.AsyncClient(timeout=60.0) as cli:
                    resp = await cli.post(f"{self.url}/api/chat", json=payload)

            if resp.status_code == 429:
                raise LLMError(f"Ollama rate limited (HTTP 429): {resp.text}")
            resp.raise_for_status()

            data = resp.json()
            content = data.get("message", {}).get("content", "")
            return json.loads(content)
        except Exception as e:
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Ollama error: {e}") from e

    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict:
        try:
            res = await self._call_once(system, user, name, schema)
            validate_schema(res, schema)
            return res
        except (jsonschema.ValidationError, json.JSONDecodeError, LLMError) as err:
            logger.warning("Ollama response validation failed, retrying once: %s", err)
            retry_user = f"{user}\n\n[Previous response was invalid: {err}. Please return valid JSON matching schema.]"
            try:
                res = await self._call_once(system, retry_user, name, schema)
                validate_schema(res, schema)
                return res
            except Exception as final_err:
                raise LLMError(f"Ollama failed validation after retry: {final_err}") from final_err


class AnthropicLLM:
    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-haiku-4-5-20251001",
        client: Any = None,
    ):
        self.api_key = api_key
        self.model = model
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=self.api_key)

    async def _call_once(self, system: str, user: str, name: str, schema: dict) -> dict:
        client = self._get_client()
        tools = [{
            "name": name,
            "description": "Structured response output",
            "input_schema": schema,
        }]
        try:
            resp = await client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=tools,
                tool_choice={"type": "tool", "name": name},
                temperature=0,
                max_tokens=2000,
            )
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use":
                    return block.input
            raise LLMError("No tool_use block in Anthropic response")
        except Exception as e:
            if isinstance(e, LLMError):
                raise
            raise LLMError(f"Anthropic error: {e}") from e

    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict:
        try:
            res = await self._call_once(system, user, name, schema)
            validate_schema(res, schema)
            return res
        except (jsonschema.ValidationError, LLMError) as err:
            logger.warning("Anthropic response validation failed, retrying once: %s", err)
            retry_user = f"{user}\n\n[Previous response was invalid: {err}. Please return valid JSON matching schema.]"
            try:
                res = await self._call_once(system, retry_user, name, schema)
                validate_schema(res, schema)
                return res
            except Exception as final_err:
                raise LLMError(f"Anthropic failed validation after retry: {final_err}") from final_err


class FallbackLLM:
    def __init__(self, providers: list[LLM]):
        self.providers = providers

    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict:
        if not self.providers:
            raise LLMError("No LLM providers configured")

        last_error = None
        for provider in self.providers:
            provider_name = provider.__class__.__name__
            try:
                result = await provider.structured(system=system, user=user, name=name, schema=schema)
                # Log which provider answered (never the text)
                logger.info("LLM answered using provider: %s", provider_name)
                return result
            except Exception as e:
                logger.warning("LLM provider %s failed: %s", provider_name, e)
                last_error = e

        raise LLMError(f"All LLM providers failed. Last error: {last_error}")


class FakeLLM:
    """Fake LLM for testing that returns queued responses or raises queued errors."""

    def __init__(self, responses: Optional[list[Any]] = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def queue(self, item: Any) -> None:
        self.responses.append(item)

    async def structured(self, *, system: str, user: str, name: str, schema: dict) -> dict:
        self.calls.append({"system": system, "user": user, "name": name, "schema": schema})
        if not self.responses:
            raise LLMError("FakeLLM has no responses queued")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        validate_schema(item, schema)
        return item
