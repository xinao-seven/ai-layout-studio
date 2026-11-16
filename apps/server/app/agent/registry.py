"""Tool registry: Pydantic input models -> OpenAI tool schemas + L0 validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.agent.contracts import (
    AddItemInput,
    AddRoomInput,
    AskUserInput,
    AutoLayoutInput,
    MoveItemInput,
    QuerySceneInput,
    RemoveItemInput,
    SearchCatalogInput,
    UpdateItemInput,
    VerifyLayoutInput,
)
from app.contracts.tools import ToolBundle, ToolDefinition
from app.scene.errors import SceneError

ToolKind = Literal["read", "write", "special"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    kind: ToolKind


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="query_scene",
        description=(
            "Read the current scene, including pending proposal items. Returns rooms, "
            "wall segments with labels, openings, items (pending=true when unconfirmed) "
            "and pending proposals. Call this before writing to learn ids and labels."
        ),
        input_model=QuerySceneInput,
        kind="read",
    ),
    ToolSpec(
        name="search_catalog",
        description=(
            "Search the furniture catalog by keyword, category and size range. Returns "
            "kindId, defaultVariantId, variants with metric sizes and placement rules."
        ),
        input_model=SearchCatalogInput,
        kind="read",
    ),
    ToolSpec(
        name="add_item",
        description=(
            "Propose adding one furniture item to a room. Always use a semantic relation "
            "(against_wall/near_window/near_item/corner/center); never output raw "
            "coordinates. The proposal is shown as a ghost until the user confirms it. "
            "If validation fails, the tool returns every refusal so you can self-correct."
        ),
        input_model=AddItemInput,
        kind="write",
    ),
    ToolSpec(
        name="add_room",
        description=(
            "Propose creating one new room from a wall-centerline polygon. This is the "
            "only tool that accepts raw coordinates: [x,z] vertex pairs in meters, 3-32 "
            "points, simple outline. The server snaps vertices to the 0.1 m grid, "
            "normalizes winding and validates shape/minimum area; failures return "
            "refusals so you can self-correct. Call query_scene first and keep the new "
            "outline clear of existing rooms. One call proposes one room, shown as a "
            "dashed ghost until the user confirms it."
        ),
        input_model=AddRoomInput,
        kind="write",
    ),
    ToolSpec(
        name="move_item",
        description=(
            "Propose moving an existing item either to a new semantic relation or by a "
            "metric offset {dx,dz}. Pending items may be moved. The proposal needs user "
            "confirmation."
        ),
        input_model=MoveItemInput,
        kind="write",
    ),
    ToolSpec(
        name="remove_item",
        description="Propose removing an item. Pending items may be removed. Needs confirmation.",
        input_model=RemoveItemInput,
        kind="write",
    ),
    ToolSpec(
        name="update_item",
        description=(
            "Propose changing an existing item's variant or material overrides. Needs confirmation."
        ),
        input_model=UpdateItemInput,
        kind="write",
    ),
    ToolSpec(
        name="auto_layout",
        description=(
            "Solve a whole room at once from a furniture list and optional constraints "
            "(for example bed facing south, wardrobe, nightstand). Returns one proposal "
            "with every placement as a ghost. Use this for requests like '布置好卧室' "
            "instead of adding items one by one. Existing furniture stays as obstacles "
            "unless options.replaceExisting is true."
        ),
        input_model=AutoLayoutInput,
        kind="write",
    ),
    ToolSpec(
        name="verify_layout",
        description=(
            "Run structured layout checks and return every issue (collision, door/window "
            "clearance, walkway, design warnings) without changing the scene. Set "
            "visual=true when the user asks to visually inspect or check the layout; "
            "the server then captures labelled top-view screenshots, asks the VLM, "
            "rechecks issues geometrically and may propose a local re-layout."
        ),
        input_model=VerifyLayoutInput,
        kind="read",
    ),
    ToolSpec(
        name="ask_user",
        description=(
            "Ask the user a clarifying question and pause the turn until they answer. "
            "Use sparingly; prefer reasonable defaults when the intent is clear."
        ),
        input_model=AskUserInput,
        kind="special",
    ),
)

TOOL_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}
TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)


def _inline_refs(node: Any, definitions: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    """Expand local ``$ref`` entries and strip provider-hostile keywords."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            if name in stack:
                return {}
            resolved = _inline_refs(definitions.get(name, {}), definitions, (*stack, name))
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            if siblings:
                merged = dict(resolved)
                merged.update(_inline_refs(siblings, definitions, stack))
                return merged
            return resolved
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"$defs", "discriminator", "$schema", "$id"}:
                continue
            cleaned[key] = _inline_refs(value, definitions, stack)
        return cleaned
    if isinstance(node, list):
        return [_inline_refs(item, definitions, stack) for item in node]
    return node


def _parameter_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema(ref_template="#/$defs/{model}")
    definitions = raw.get("$defs", {})
    schema: dict[str, Any] = _inline_refs(raw, definitions)
    schema.pop("title", None)
    schema["additionalProperties"] = False
    return schema


def tool_bundle() -> ToolBundle:
    """Build the exported tools contract from the registry."""
    return ToolBundle(
        tools=[
            ToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=_parameter_schema(spec.input_model),
            )
            for spec in TOOL_SPECS
        ]
    )


def openai_tools() -> list[dict[str, Any]]:
    """OpenAI ``tools`` payload derived from the same Pydantic models."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": _parameter_schema(spec.input_model),
            },
        }
        for spec in TOOL_SPECS
    ]


def validate_arguments(name: str, arguments: dict[str, Any]) -> BaseModel:
    """L0 validation: the tool exists and its arguments match the model."""
    spec = TOOL_BY_NAME.get(name)
    if spec is None:
        raise SceneError(
            "invalid_input",
            f"Unknown tool {name!r}.",
            "Use one of: " + ", ".join(TOOL_NAMES) + ".",
        )
    try:
        return spec.input_model.model_validate(arguments)
    except ValidationError as error:
        raise SceneError(
            "invalid_input",
            f"Arguments for {name!r} do not match the tool schema.",
            "Fix field names, types and units according to the tool schema.",
            {"errors": error.errors(include_url=False, include_context=False)},
        ) from error
