"""Thin OpenAI-backed structured-extraction wrapper for the live inbound analysis lane.

This is the ONLY module in the screening package that imports ``openai``. Everything else
(the live adapters in :mod:`founderlookup.screening.live_analyses`) depends on the small
structural ``Reasoner`` interface those adapters define, never on this provider, so the
neutral seam the deterministic fakes plug into is preserved and the provider stays
swappable.

The wrapper exposes exactly two capabilities:

1. :meth:`OpenAIReasoner.extract` - a single low-reasoning call that parses a source text
   into one of the caller's own flat pydantic schemas via the Responses structured-parse
   API. gpt-5.6-luna is a reasoning model, so the reasoning effort rides on
   ``reasoning={"effort": ...}`` (Responses), never on a Chat Completions temperature.
2. :meth:`OpenAIReasoner.sample_scores` - ``n`` independent low-reasoning reads of a single
   0..100 sub-score, used ONLY by the Area-of-Research-1 self-consistency confidence
   estimator (:func:`founderlookup.screening.confidence.estimate_confidence_band`). It is
   deliberately kept out of the deterministic mapping, so a sampled float can never leak
   into a frozen rubric score.

Verified against the installed openai 2.46.0 by introspection (no network):
``AsyncOpenAI().responses.parse`` is an async method accepting ``model``, ``reasoning``,
``instructions``, ``input`` (a plain ``str`` is accepted), and ``text_format`` (a plain
pydantic ``BaseModel`` subclass), and returns a ``ParsedResponse[T]`` whose
``output_parsed`` property yields ``T | None`` (``None`` on a refusal or an unparseable
response). ``openai.OpenAIError`` is the base exception and covers the parse-time
``LengthFinishReasonError``.

Fail-closed and secret hygiene
------------------------------
Any API, refusal, or parse failure raises :class:`ReasonerError`; a missing environment
key raises :class:`MissingApiKeyError`. The API key is read only from
``os.environ["OPENAI_API_KEY"]``; it is never hardcoded, never logged (this module logs
nothing), and never interpolated into any error message. Error messages carry only the
schema name and the offending exception's type name, never ``str(error)`` (which could
echo a request body) and never the key.
"""

from __future__ import annotations

import os
from typing import Literal, TypeVar

import openai
from openai import AsyncOpenAI
from openai.types.shared_params import Reasoning
from pydantic import BaseModel

DEFAULT_MODEL = "gpt-5.6-luna"

# gpt-5.6-luna is a reasoning model; effort is a subset of the installed SDK's
# ReasoningEffort literal. Both the model and the effort are constructor-configurable.
Effort = Literal["minimal", "low", "medium", "high"]
DEFAULT_EFFORT: Effort = "low"

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ReasonerError(RuntimeError):
    """Any reasoner API, refusal, or parse failure. Never carries the API key.

    Authored messages mention only the schema name and the failing exception's type name,
    never ``str(error)`` and never the key.
    """


class MissingApiKeyError(ReasonerError):
    """Raised when ``OPENAI_API_KEY`` is not present in the environment."""


class _ScoreSample(BaseModel):
    """One 0..100 sub-score read, used only by the confidence estimator."""

    score: float


class OpenAIReasoner:
    """Low-reasoning structured-extraction wrapper around the Responses parse API.

    ``model`` defaults to ``gpt-5.6-luna`` and ``effort`` to ``"low"``; both are
    configurable. A client is injected so tests can drive a stub without a network hit;
    :meth:`from_env` is the production constructor that reads the key from the environment.
    """

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str = DEFAULT_MODEL,
        effort: Effort = DEFAULT_EFFORT,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort

    @classmethod
    def from_env(
        cls,
        *,
        model: str = DEFAULT_MODEL,
        effort: Effort = DEFAULT_EFFORT,
    ) -> OpenAIReasoner:
        """Build a reasoner from ``OPENAI_API_KEY`` without ever logging or echoing it.

        The key is read from the environment only. ``raise ... from None`` drops the
        chained ``KeyError`` frame so nothing can surface the value in a traceback dump.
        """
        try:
            key = os.environ["OPENAI_API_KEY"]
        except KeyError:
            raise MissingApiKeyError("OPENAI_API_KEY is not set in the environment") from None
        return cls(AsyncOpenAI(api_key=key), model=model, effort=effort)

    async def extract(
        self,
        *,
        schema: type[SchemaT],
        instructions: str,
        content: str,
    ) -> SchemaT:
        """Parse ``content`` into ``schema`` with one low-reasoning call. Fail-closed.

        Raises :class:`ReasonerError` on any ``openai.OpenAIError`` (which covers the
        parse-time ``LengthFinishReasonError`` on truncation) and on a ``None``
        ``output_parsed`` (a refusal or an unparseable response).
        """
        try:
            response = await self._client.responses.parse(
                model=self._model,
                reasoning=Reasoning(effort=self._effort),
                instructions=instructions,
                input=content,
                text_format=schema,
            )
        except openai.OpenAIError as error:
            raise ReasonerError(
                f"extraction failed for {schema.__name__}: {type(error).__name__}"
            ) from error
        parsed = response.output_parsed
        if parsed is None:
            raise ReasonerError(f"model returned no parsed {schema.__name__} (refusal)")
        return parsed

    async def sample_scores(self, prompt: str, n: int) -> list[float]:
        """Return ``n`` independent 0..100 sub-score reads for self-consistency confidence.

        This is the sole path that reads a model number, and it feeds only the confidence
        estimator, never a frozen rubric score. A sample outside ``[0, 100]`` fails closed
        rather than being silently clamped, matching the estimator's garbage-in-surfaces
        stance.
        """
        scores: list[float] = []
        for _ in range(max(0, n)):
            sample = await self.extract(
                schema=_ScoreSample,
                instructions="Return one 0..100 sub-score for the described dimension.",
                content=prompt,
            )
            if not 0.0 <= sample.score <= 100.0:
                raise ReasonerError("score sample outside the 0..100 scale")
            scores.append(sample.score)
        return scores


__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_MODEL",
    "Effort",
    "MissingApiKeyError",
    "OpenAIReasoner",
    "ReasonerError",
]
