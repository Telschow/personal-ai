"""Typed embedding results and their provider-independent contract."""

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class MalformedEmbeddingError(Exception):
    """Raised when provider output cannot be validated as an embedding."""


@dataclass(frozen=True, slots=True)
class Embedding:
    """A dense vector representation produced by one named model.

    Vectors from different models are not comparable, so the model
    identity travels with the vector from the moment it is created.
    """

    model: str
    vector: tuple[float, ...]

    @property
    def dimensions(self) -> int:
        """Number of components; agrees with ``vector`` by construction."""
        return len(self.vector)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A provider of dense vectors for plain text.

    Structural on purpose: implementations need not inherit from anything
    in this project. The input is plain text rather than a chunk so the
    primitive stays independent of document models; associating vectors
    with chunk identities is the persistence layer's responsibility.
    Implementations must reject empty input with ``ValueError`` before any
    network activity.
    """

    def embed(self, text: str) -> Embedding:
        """Return the embedding of one piece of text."""
        ...


def parse_embedding(model: str, vectors: object) -> Embedding:
    """Validate provider output into a typed :class:`Embedding`.

    Expects the vectors for exactly one input: one non-empty row of finite
    numbers. Error messages describe shapes, types, and positions only —
    never input text or component values.
    """
    if not isinstance(vectors, (list, tuple)):
        msg = f"Expected a list of embedding vectors, got {type(vectors).__name__}"
        raise MalformedEmbeddingError(msg)
    if len(vectors) != 1:
        msg = f"Expected exactly one embedding vector for one input, got {len(vectors)}"
        raise MalformedEmbeddingError(msg)

    row = vectors[0]
    if not isinstance(row, (list, tuple)):
        msg = f"Expected the embedding vector to be a list, got {type(row).__name__}"
        raise MalformedEmbeddingError(msg)
    if not row:
        msg = "The embedding vector is empty"
        raise MalformedEmbeddingError(msg)

    components: list[float] = []
    for position, value in enumerate(row):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            msg = f"Embedding component at position {position} is not a number"
            raise MalformedEmbeddingError(msg)
        component = float(value)
        if not math.isfinite(component):
            msg = f"Embedding component at position {position} is not finite"
            raise MalformedEmbeddingError(msg)
        components.append(component)

    return Embedding(model=str(model), vector=tuple(components))
