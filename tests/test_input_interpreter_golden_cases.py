import json
from pathlib import Path

from src.interpreter.input_interpreter import InputInterpreter


def _load(name: str):
    return json.loads(Path('tests/golden_cases').joinpath(name).read_text(encoding="utf-8"))


def test_golden_cases_phase22():
    it = InputInterpreter()
    files = [
        'technical_question_non_entry.json',
        'external_pollution_ai_girlfriend.json',
        'external_pollution_fake_deep.json',
        'internal_tension_possessive_structure.json',
        'internal_tension_negative_attraction.json',
        'private_origin_purity_reference.json',
        'vulnerability_not_intimacy.json',
        'ambiguous_followup_with_context.json',
    ]
    for f in files:
        case = _load(f)
        out = it.interpret(case['input'], context=case.get('context'))
        exp = case['expected']
        for path, val in exp.items():
            cur = out
            for p in path.split('.'):
                cur = cur[p] if not p.isdigit() else cur[int(p)]
            if isinstance(val, (int, float)):
                assert float(cur) >= float(val)
            else:
                assert cur == val


def test_internal_tension_not_external_pollution_by_default():
    it = InputInterpreter()
    out = it.interpret('讨论占有式结构和否定式吸引')
    assert out['boundary_signal']['internal_tension_relevance'] > 0.0
    assert out['boundary_signal']['external_pollution_risk'] == 0.0


def test_vulnerability_not_dependency_without_dependency_language():
    it = InputInterpreter()
    out = it.interpret('我很累，有点怀疑自己能不能做出来')
    assert out['relationship_signal']['vulnerability_relevance'] > 0.0
    assert out['relationship_signal']['dependency_risk'] <= 0.2


def test_phase23c_paper_structure_technical_non_entry():
    it = InputInterpreter()
    out = it.interpret('how should I structure this paper')
    assert out['semantic_event']['type'] in {'technical_question', 'project_planning'}
    assert out['semantic_event']['persona_route'] == 'engineering_director'
    assert out['boundary_signal']['persona_non_entry'] is True
    assert out['semantic_event']['asks_for_analysis'] >= 0.6
    assert out['semantic_event']['asks_for_solution'] >= 0.6
    assert out['relationship_signal']['dependency_risk'] <= 0.2
    assert out['boundary_signal']['external_pollution_risk'] <= 0.2


def test_phase23c_negative_disambiguation_cases():
    it = InputInterpreter()

    out1 = it.interpret('不是技术问题，是视觉方向')
    assert out1['semantic_event']['type'] == 'casual_chat'
    assert out1['semantic_event']['topic'] == 'visual_direction'
    assert 'negative_disambiguation_applied' in out1['warnings']

    out2 = it.interpret('不是 AI 女友，我要避免这种方向')
    assert out2['boundary_signal']['external_pollution_risk'] > 0.0
    assert out2['boundary_signal']['pollution_type']
    assert 'avoidance_reference_detected' in out2['warnings']

    out3 = it.interpret('不是心理咨询，不要安全客服')
    assert out3['boundary_signal']['external_pollution_risk'] > 0.0
    assert 'avoidance_reference_detected' in out3['warnings']

    out4 = it.interpret('不是工程路线，是本源问题')
    assert out4['semantic_event']['topic'] == 'private_origin'
    assert out4['memory_trigger_signal']['memory_type'] == 'private_origin'
    assert out4['semantic_event']['persona_route'] == 'aphrodite'
