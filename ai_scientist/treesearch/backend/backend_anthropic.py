import copy
import time
from collections.abc import Sequence
from typing import Any

import anthropic
import jsonschema
from funcy import notnone, select_values

from .utils import FunctionSpec, OutputType, backoff_create, opt_messages_to_list

ANTHROPIC_TIMEOUT_EXCEPTIONS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.APIStatusError,
)


def get_ai_client(model: str, max_retries=2) -> anthropic.Anthropic:
    """Create a client for Anthropic's first-party API.

    The SDK reads ``ANTHROPIC_API_KEY`` from the environment.
    """
    return anthropic.Anthropic(max_retries=max_retries)


def _anthropic_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove OpenAI-only schema metadata before sending it to Anthropic."""
    schema = copy.deepcopy(schema)
    if isinstance(schema, dict):
        schema.pop("strict", None)
        for value in schema.values():
            if isinstance(value, (dict, list)):
                _anthropic_input_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            if isinstance(value, (dict, list)):
                _anthropic_input_schema(value)
    return schema


def _to_anthropic_content(content: str | Sequence[dict[str, Any]]) -> Any:
    """Convert OpenAI-style text and image blocks to the Messages API format."""
    if isinstance(content, str):
        return content

    converted: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") != "image_url":
            converted.append(block)
            continue

        url = block.get("image_url", {}).get("url")
        if not isinstance(url, str):
            raise ValueError("An image_url block must contain a string URL.")

        if url.startswith("data:"):
            header, separator, data = url.partition(",")
            if not separator or not header.endswith(";base64"):
                raise ValueError("Anthropic image data URLs must be base64-encoded.")
            media_type = header.removeprefix("data:").removesuffix(";base64")
            converted.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )
        else:
            converted.append(
                {
                    "type": "image",
                    "source": {"type": "url", "url": url},
                }
            )
    return converted


def query(
    system_message: str | None,
    user_message: str | Sequence[dict[str, Any]] | None,
    func_spec: FunctionSpec | None = None,
    **model_kwargs,
) -> tuple[OutputType, float, int, int, dict]:
    client = get_ai_client(model_kwargs.get("model"), max_retries=0)

    filtered_kwargs: dict = select_values(notnone, model_kwargs)  # type: ignore
    if "max_tokens" not in filtered_kwargs:
        filtered_kwargs["max_tokens"] = 8192  # default for Claude models

    # Claude Sonnet 5 uses adaptive thinking with medium effort.
    if filtered_kwargs["model"].startswith("claude-sonnet-5"):
        filtered_kwargs.setdefault("thinking", {"type": "adaptive"})
        output_config = filtered_kwargs.setdefault("output_config", {})
        output_config.setdefault("effort", "medium")

    # Anthropic doesn't allow not having a user messages
    # if we only have system msg -> use it as user msg
    if system_message is not None and user_message is None:
        system_message, user_message = user_message, system_message

    # Anthropic passes the system messages as a separate argument
    if system_message is not None:
        filtered_kwargs["system"] = system_message

    messages = opt_messages_to_list(None, _to_anthropic_content(user_message))

    if func_spec is not None:
        filtered_kwargs["tools"] = [
            {
                "name": func_spec.name,
                "description": func_spec.description,
                "input_schema": _anthropic_input_schema(func_spec.json_schema),
            }
        ]
        filtered_kwargs["tool_choice"] = {"type": "tool", "name": func_spec.name}

    t0 = time.time()
    message = backoff_create(
        client.messages.create,
        ANTHROPIC_TIMEOUT_EXCEPTIONS,
        messages=messages,
        **filtered_kwargs,
    )
    req_time = time.time() - t0
    if func_spec is None:
        output = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        if not output:
            raise ValueError("Anthropic response did not contain a text block.")
    else:
        tool_use = next(
            (
                block
                for block in message.content
                if getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == func_spec.name
            ),
            None,
        )
        if tool_use is None:
            raise ValueError(
                f"Anthropic response did not call required tool {func_spec.name!r}."
            )
        output = tool_use.input
        try:
            jsonschema.validate(output, func_spec.json_schema)
        except Exception as error:
            raise ValueError(
                f"Anthropic tool input for {func_spec.name!r} did not match its schema."
            ) from error

    in_tokens = message.usage.input_tokens
    out_tokens = message.usage.output_tokens

    info = {
        "stop_reason": message.stop_reason,
    }

    return output, req_time, in_tokens, out_tokens, info
