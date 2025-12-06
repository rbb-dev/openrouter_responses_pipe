"""Open WebUI adapters.

Integration modules for Open WebUI-specific functionality:
- Artifact persistence (SQLAlchemy + Redis)
- Event emitters
- File upload handlers
- Tool execution wrappers
"""

from .persistence import (
    ArtifactPersistence,
    _sanitize_table_fragment,
    _extract_internal_file_id,
)
from .events import (
    wrap_event_emitter,
    merge_usage_stats,
    wrap_code_block,
)

__all__ = [
    "ArtifactPersistence",
    "_sanitize_table_fragment",
    "_extract_internal_file_id",
    "wrap_event_emitter",
    "merge_usage_stats",
    "wrap_code_block",
]
