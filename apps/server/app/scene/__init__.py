"""Scene kernel: validation, patches, versions and pending proposals."""

from app.scene.document import document_area, validate_document
from app.scene.errors import SceneError
from app.scene.patch import apply_ops, invert_resolved_ops
from app.scene.service import CommitOutcome, SceneService

__all__ = [
    "CommitOutcome",
    "SceneError",
    "SceneService",
    "apply_ops",
    "document_area",
    "invert_resolved_ops",
    "validate_document",
]
