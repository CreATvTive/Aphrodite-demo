from src.memory.memory_gate import decide_persistence


def test_memory_gate_blocks_low_confidence_stable_write():
    c = {"confidence": 0.3, "importance": 0.9, "first_seen": False}
    result = decide_persistence(c)
    assert result["persistence_level"] == "working"


def test_memory_gate_requires_confirmation_for_first_seen():
    c = {"confidence": 0.7, "importance": 0.8, "first_seen": True}
    result = decide_persistence(c)
    assert result["persistence_level"] == "tentative"
