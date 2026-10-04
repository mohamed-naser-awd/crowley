"""Functions defined in their own module, the way an application would."""

from crowley import FunctionContext, FunctionRegistry

functions = FunctionRegistry("acme")


@functions.function
def clean_price(text: str, currency: str = "£") -> float:
    return float(text.replace(currency, "").strip())


@functions.function("shout")
async def loud(text: str) -> str:
    return text.upper()


@functions.function(name="tools.count", pure=True)
def count(ctx: FunctionContext, prev: list[str]) -> int:
    return len(prev)
