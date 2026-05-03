from __future__ import annotations

from enum import Enum


class SemanticEvent(str, Enum):
    CASUAL_CHAT = "casual_chat"
    TECHNICAL_QUESTION = "technical_question"
    SELF_DISCLOSURE = "self_disclosure"
    MEMORY_REFERENCE = "memory_reference"
    PROJECT_PLANNING = "project_planning"
    IDENTITY_DISCUSSION = "identity_discussion"
    BOUNDARY_TESTING = "boundary_testing"
    EMOTIONAL_DISTRESS = "emotional_distress"
    AESTHETIC_JUDGMENT = "aesthetic_judgment"
    META_DISCUSSION = "meta_discussion"
    EXISTENCE_DISCUSSION = "existence_discussion"
    RELATIONSHIP_SHIFT = "relationship_shift"
