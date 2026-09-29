"""Owner-supplied model prices so the cost budget is enforceable, not decorative.

Token counts come from the model client; prices come only from the owner's settings. The
engineer never guesses a price: without a price table a model recipe cannot run, because a
cost budget that cannot be measured cannot be enforced.
"""

from __future__ import annotations

from pydantic import Field

from nexus.atlas.models import StrictModel

_MILLION = 1_000_000


class PriceTable(StrictModel):
    """USD per million input and output tokens for the configured model."""

    input_usd_per_mtok: float = Field(ge=0, le=10_000)
    output_usd_per_mtok: float = Field(ge=0, le=10_000)

    def cost_usd(self, *, input_tokens: int, output_tokens: int) -> float:
        """Deterministic cost, rounded to a millionth of a dollar."""

        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative")
        raw = (
            input_tokens * self.input_usd_per_mtok + output_tokens * self.output_usd_per_mtok
        ) / _MILLION
        return round(raw, 6)


__all__ = ["PriceTable"]
