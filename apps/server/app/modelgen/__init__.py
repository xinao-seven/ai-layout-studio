"""Parametric model generation.

Generated assets are deterministic: the same spec always produces the same
bytes, so the spec hash doubles as the asset identity and cache key. Geometry is
emitted in the catalog's canonical frame (metric, Y-up, bottom-center pivot,
front +Z) so generated models satisfy the same invariants as the shipped CC0
models and need no front-end special casing.
"""

from app.modelgen.compile import CompiledAsset, compile_spec
from app.modelgen.service import ModelGenService

__all__ = ["CompiledAsset", "ModelGenService", "compile_spec"]
