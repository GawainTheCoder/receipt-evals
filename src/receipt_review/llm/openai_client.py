from __future__ import annotations

import base64
import copy
import json
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


def openai_strict_json_schema(schema_model: type[BaseModel]) -> dict:
    schema = copy.deepcopy(schema_model.model_json_schema())

    def clean(value: object) -> object:
        if isinstance(value, dict):
            if "$ref" in value:
                return {"$ref": value["$ref"]}
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(schema)


def create_structured_response(
    *,
    client: OpenAI,
    model: str,
    schema_name: str,
    schema_model: type[SchemaT],
    instructions: str,
    user_content: list[dict[str, str]],
) -> SchemaT:
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=[
            {
                "role": "user",
                "content": user_content,
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": openai_strict_json_schema(schema_model),
                "strict": True,
            }
        },
    )

    return schema_model.model_validate(json.loads(response.output_text))
