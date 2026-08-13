"""Data model for the unified memory system. One shape for every kind of
thing Jarvis remembers -- type/importance/confidence metadata is what
distinguishes a permanent standing rule from a fleeting inferred pattern,
not a separate database or file per kind (see agent/memory/store.py,
which still persists everything through the existing database/memory.py
file, just with structure now)."""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class MemoryType(str, Enum):
    PROFILE = "profile"
    PREFERENCE = "preference"
    FACT = "fact"
    LESSON = "lesson"
    PATTERN = "pattern"
    CONVERSATION = "conversation"
    TASK = "task"
    EVENT = "event"


class Importance(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    PERMANENT = "permanent"


class Confidence(str, Enum):
    # How this memory came to exist -- an inferred pattern must never be
    # silently treated as equivalent to something the user actually said.
    USER_EXPLICIT = "user_explicit"
    USER_INFERRED = "user_inferred"
    SYSTEM_OBSERVED = "system_observed"
    MODEL_INFERRED = "model_inferred"


@dataclass
class Memory:
    content: str
    type: MemoryType
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source: str = "user_chat"
    confidence: Confidence = Confidence.USER_EXPLICIT
    importance: Importance = Importance.NORMAL
    last_accessed: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    active: bool = True
    superseded_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "type": self.type.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
            "confidence": self.confidence.value,
            "importance": self.importance.value,
            "last_accessed": self.last_accessed,
            "tags": self.tags,
            "active": self.active,
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Memory":
        return cls(
            id=data["id"],
            content=data["content"],
            type=MemoryType(data["type"]),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            source=data.get("source", "unknown"),
            confidence=Confidence(data.get("confidence", Confidence.USER_EXPLICIT.value)),
            importance=Importance(data.get("importance", Importance.NORMAL.value)),
            last_accessed=data.get("last_accessed"),
            tags=data.get("tags", []),
            active=data.get("active", True),
            superseded_by=data.get("superseded_by"),
        )
