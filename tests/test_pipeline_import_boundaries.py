from __future__ import annotations

import ast
from pathlib import Path


PIPELINE_MODULES = {
    "field_state.schema": Path("src/field_state/schema.py"),
    "field_state.perturbation": Path("src/field_state/perturbation.py"),
    "field_state.updater": Path("src/field_state/updater.py"),
    "motion_params.schema": Path("src/motion_params/schema.py"),
    "motion_params.mapper": Path("src/motion_params/mapper.py"),
    "body_action.schema": Path("src/body_action/schema.py"),
    "body_action.motion_to_action_mapper": Path("src/body_action/motion_to_action_mapper.py"),
    "body_action.composer": Path("src/body_action/composer.py"),
}


FORBIDDEN_IMPORT_FRAGMENTS = (
    "agentlib",
    "runtime_engine",
    "renderer",
    "animation",
    "avatar",
    "llm",
    "prompt",
    "language",
    "memory",
    "router",
    "field_trace",
)


BODY_AND_MOTION_MODULES = {
    "motion_params.schema",
    "motion_params.mapper",
    "body_action.schema",
    "body_action.motion_to_action_mapper",
    "body_action.composer",
}


BODY_AND_MOTION_FORBIDDEN_SOURCE_TOKENS = (
    "raw_text",
    "user_text",
    "user_input",
    "input_interpreter",
    "InputInterpreter",
    "FieldTrace",
    "field_trace",
    "EvidenceItem",
    "FieldSignalProposal",
    "FieldPerturbation",
    "re.search",
    "re.match",
    "regex",
)


FORBIDDEN_EXECUTION_CALLS = (
    "runtimeengine",
    "runtime_engine",
    "render",
    "animate",
    "execute",
    "drive",
    "move",
)


EXPECTED_IMPORTS = {
    "field_state.schema": {
        "__future__",
        "dataclasses",
        "typing",
    },
    "field_state.perturbation": {
        "__future__",
        "dataclasses",
        "typing",
        "src.field_state.schema",
    },
    "field_state.updater": {
        "__future__",
        "dataclasses",
        "typing",
        "schema",
        "perturbation",
    },
    "motion_params.schema": {
        "__future__",
        "dataclasses",
    },
    "motion_params.mapper": {
        "__future__",
        "src.field_state.schema",
        "schema",
    },
    "body_action.schema": {
        "dataclasses",
        "typing",
        "src.motion_params.schema",
    },
    "body_action.motion_to_action_mapper": {
        "__future__",
        "src.body_action.schema",
        "src.motion_params.schema",
    },
    "body_action.composer": {
        "__future__",
        "src.body_action.schema",
    },
}


def _source(module_name: str) -> str:
    return PIPELINE_MODULES[module_name].read_text(encoding="utf-8")


def _tree(module_name: str) -> ast.AST:
    return ast.parse(_source(module_name), filename=str(PIPELINE_MODULES[module_name]))


def _import_modules(module_name: str) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(_tree(module_name)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                modules.append(node.module or "")
            else:
                modules.append(node.module or "")
    return modules


def test_pipeline_modules_exist():
    for path in PIPELINE_MODULES.values():
        assert path.exists()


def test_pipeline_modules_do_not_import_forbidden_runtime_or_rendering_layers():
    for module_name in PIPELINE_MODULES:
        imported_modules = _import_modules(module_name)

        for imported_module in imported_modules:
            lowered = imported_module.lower()
            for forbidden in FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in lowered, (
                    f"{module_name} must not import forbidden layer {forbidden}: "
                    f"{imported_module}"
                )


def test_pipeline_modules_import_only_expected_dependencies():
    for module_name, expected_imports in EXPECTED_IMPORTS.items():
        imported_modules = set(_import_modules(module_name))

        assert imported_modules <= expected_imports, (
            f"{module_name} has unexpected imports: "
            f"{sorted(imported_modules - expected_imports)}"
        )


def test_field_state_internal_imports_remain_local_or_schema_only():
    assert set(_import_modules("field_state.schema")) == {"__future__", "dataclasses", "typing"}
    assert set(_import_modules("field_state.updater")) == {
        "__future__",
        "dataclasses",
        "typing",
        "schema",
        "perturbation",
    }
    assert "src.field_state.schema" in set(_import_modules("field_state.perturbation"))


def test_motion_params_mapper_only_depends_on_field_state_schema_and_motion_schema():
    assert set(_import_modules("motion_params.mapper")) == {
        "__future__",
        "src.field_state.schema",
        "schema",
    }


def test_body_action_motion_mapper_import_boundary_is_motionparams_and_body_schema_only():
    assert set(_import_modules("body_action.motion_to_action_mapper")) == {
        "__future__",
        "src.body_action.schema",
        "src.motion_params.schema",
    }


def test_body_action_composer_import_boundary_is_schema_only():
    assert set(_import_modules("body_action.composer")) == {
        "__future__",
        "src.body_action.schema",
    }


def test_body_and_motion_modules_do_not_read_raw_text_or_semantic_upstream_layers():
    for module_name in BODY_AND_MOTION_MODULES:
        source = _source(module_name)
        for token in BODY_AND_MOTION_FORBIDDEN_SOURCE_TOKENS:
            assert token not in source, f"{module_name} must not reference {token}"


def test_body_and_motion_modules_do_not_use_regex_parsing():
    for module_name in BODY_AND_MOTION_MODULES:
        tree = _tree(module_name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name != "re" for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "re"


def test_pipeline_modules_do_not_call_runtime_renderer_animation_execution():
    for module_name in PIPELINE_MODULES:
        tree = _tree(module_name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                call_name = func.id.lower()
            elif isinstance(func, ast.Attribute):
                call_name = func.attr.lower()
            else:
                continue
            assert call_name not in FORBIDDEN_EXECUTION_CALLS, (
                f"{module_name} must not call runtime/rendering execution API: "
                f"{call_name}"
            )
