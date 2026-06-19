from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None:
        raise ImportError(f"Could not load module {module_name} from {file_path}")
    assert spec is not None  # Type narrowing for static type checkers
    if spec.loader is None:
        raise ImportError(f"Could not load module {module_name} from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


if "agentlib" not in sys.modules:
    pkg = types.ModuleType("agentlib")
    pkg.__path__ = [str(ROOT / "agentlib")]
    sys.modules["agentlib"] = pkg

mem_mod = _load_module("agentlib.companion_rag", ROOT / "agentlib" / "companion_rag.py")


class CompanionMemoryTests(unittest.TestCase):
    def test_extract_user_candidates(self):
        out = mem_mod._extract_user_memory_candidates("我喜欢跑步，也不喜欢熬夜。")
        self.assertTrue(any("我喜欢" in x for x in out))

    def test_retrieve_memory_context_empty_when_unavailable(self):
        with patch("agentlib.companion_rag.get_memory_store", return_value=None):
            out = mem_mod.retrieve_memory_context("hello", k=3)
        self.assertEqual(out, [])

    def test_record_turn_memory_with_explicit_items(self):
        class FakeStore:
            def __init__(self):
                self.items = []

            def add_many(self, texts):
                self.items.extend(texts)

            def retrieve(self, query, k=4):
                return self.items[-k:]

        fake_store = FakeStore()

        # patch local import path used in function body
        with patch.dict(
            sys.modules,
            {
                "agentlib.memory_store": types.SimpleNamespace(
                    should_store_memory=lambda t: True,
                )
            },
        ):
            with patch("agentlib.companion_rag.get_memory_store", return_value=fake_store):
                result = mem_mod.record_turn_memory("u", "a", explicit_items=["用户喜欢简洁回复"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["stored"], 1)
        self.assertIn("用户喜欢简洁回复", fake_store.items)


if __name__ == "__main__":
    unittest.main()
