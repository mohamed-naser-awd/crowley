"""``extract.auto``: route a source to the extractor for its media type (SPEC §12.4, §13.10)."""

from typing import Any

from crowley.application.runtime import FunctionContext
from crowley.application.runtime.context import ExtractorHandle
from crowley.domain.errors import ValidationError
from crowley.domain.values import Handle, Value


async def auto(
    ctx: FunctionContext,
    source: Value,
    fields: Value,
    root: Value = None,
    using: str | None = None,
    **kwargs: Any,
) -> Value:
    return await choose(ctx, source, using).extract(source, fields, root=root)


def choose(ctx: FunctionContext, source: Value, using: str | None) -> ExtractorHandle:
    """``using:``, then a node handle's own extractor, then the source's media type."""
    if using:
        return ctx.extractor(using)
    if isinstance(source, Handle) and source.type_name.endswith(".node"):
        return ctx.extractor(source.type_name.removesuffix(".node"))
    media_type = source.get("media_type") if isinstance(source, dict) else None
    handle = ctx.extractor_for(media_type if isinstance(media_type, str) else None)
    if handle is None:
        raise ValidationError(
            "E403",
            f"no extractor handles media type {media_type!r}",
            path=ctx.step.path,
            hint="pass using: <extractor>; available: " + ", ".join(ctx.extractor_names),
        )
    return handle
