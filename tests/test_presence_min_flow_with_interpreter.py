from agentlib.runtime_engine import RuntimeEngine


def test_presence_flow_uses_interpreter_and_has_non_entry_signal_for_technical_question():
    eng = RuntimeEngine()
    tr = eng._presence_min_flow(user_text='这个 Python bug 为什么会触发 KeyError？', assistant_text='先看栈信息。', trace_id='t6', event_id='e6')
    assert tr['interpreted_event']['boundary_signal']['persona_non_entry'] is True
    assert tr['interpreted_event']['semantic_event']['persona_route'] == 'engineering_director'


def test_ambiguous_followup_inherits_context_warning():
    eng = RuntimeEngine()
    eng._presence_min_flow(user_text='这个 Python bug 为什么会触发 KeyError？', assistant_text='先看栈信息。', trace_id='ta', event_id='ea')
    tr = eng._presence_min_flow(user_text='这个', assistant_text='继续。', trace_id='tb', event_id='eb')
    assert tr['interpreted_event']['boundary_signal']['context_inherited'] is True
    assert 'context_inherited' in tr['interpreted_event']['warnings']
