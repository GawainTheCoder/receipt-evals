from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from receipt_review.config import Settings, load_settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def get_client(settings: Settings | None = None) -> OpenAI:
    active_settings = settings or load_settings()
    if not active_settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY was not found in .env, .env.local, /env, or the environment.")
    return OpenAI(api_key=active_settings.openai_api_key)


def image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def create_structured_response(
    *,
    client: OpenAI,
    model: str,
    schema_name: str,
    schema_model: type[SchemaT],
    instructions: str,
    user_content: list[dict[str, str]],
) -> SchemaT:
    # Use the SDK parser for the v0 baseline so schema generation and Pydantic
    # parsing stay simple. Add custom schema handling only when evals justify it.
    response = client.responses.parse(
        model=model,
        instructions=instructions,
        input=[
            {
                "role": "user",
                "content": user_content,
            }
        ],
        text_format=schema_model,
    )

    if response.output_parsed is None:
        raise RuntimeError(f"Structured response parsing failed for schema {schema_name!r}.")

    return response.output_parsed
