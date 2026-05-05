# Interpreter Smoke Report (Phase 2.2)

- Date: 2026-05-05
- Command: `PYTHONPATH=. python scripts/interpreter_smoke_report.py`
- Scope: evaluation/reporting only (no interpreter/runtime behavior changes in this step)

## Compact Result Table

| id | input | event_type | topic | persona_route | persona_non_entry | memory_type | memory_relevance | external_pollution_risk | pollution_type | internal_tension_relevance | tension_type | dependency_risk | vulnerability_relevance | needs_boundary | requires_pause | requires_stillness | confidence | warnings |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 这个 Python bug 为什么会触发 KeyErr… | technical_question | general | engineering_director | Y | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.82 |  |
| 2 | How to debug a flaky unit t… | technical_question | general | engineering_director | Y | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.82 |  |
| 3 | 给我一个研究计划框架 | technical_question | general | engineering_director | Y | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.82 |  |
| 4 | 职业方向应该怎么选？ | technical_question | general | engineering_director | Y | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.82 |  |
| 5 | 回到 private origin 的定义 | casual_chat | general | aphrodite | N | private_origin | 0.88 | 0.00 |  | 0.57 | source_fragment_purity | 0.00 | 0.00 | N | Y | Y | 0.60 |  |
| 6 | source fragment purity 是什么 | casual_chat | general | aphrodite | N | private_origin | 0.88 | 0.00 |  | 0.57 | source_fragment_purity | 0.00 | 0.00 | N | Y | Y | 0.60 |  |
| 7 | 我们要 no interference 的关系场 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.57 | source_fragment_purity | 0.00 | 0.00 | N | N | N | 0.60 |  |
| 8 | relationship structure 和 bo… | casual_chat | general | aphrodite | N | private_origin | 0.88 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | N | Y | Y | 0.60 |  |
| 9 | 不要变成 AI girlfriend | casual_chat | general | aphrodite | N | none | 0.00 | 0.52 | ai_girlfriend | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.60 |  |
| 10 | 别做成客服式 assistant | casual_chat | general | aphrodite | N | none | 0.00 | 0.52 | assistant_drift | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.60 |  |
| 11 | 别做 VTuber idol 营业感 | casual_chat | general | aphrodite | N | none | 0.00 | 0.52 | idol_performance | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.60 |  |
| 12 | 避免 romance game heroine 气质 | casual_chat | general | aphrodite | N | none | 0.00 | 0.52 | romance_game | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.60 |  |
| 13 | 这种 fake deep mysterious 不行 | casual_chat | general | aphrodite | N | none | 0.00 | 0.52 | fake_deep | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.60 |  |
| 14 | 避免 beautiful but empty | casual_chat | general | aphrodite | N | none | 0.00 | 0.52 | beautiful_but_empty | 0.00 |  | 0.00 | 0.00 | Y | N | N | 0.60 |  |
| 15 | 讨论占有式结构 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.57 | possessive_structure | 0.00 | 0.00 | N | N | N | 0.60 |  |
| 16 | 否定式吸引和距离压力 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.69 | negative_attraction,distance_pressure | 0.00 | 0.00 | N | N | N | 0.60 |  |
| 17 | 被收容/被保护/被固定 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.81 | contained,protected | 0.00 | 0.00 | N | N | N | 0.60 |  |
| 18 | sealed relationship field | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.57 | sealed_field | 0.00 | 0.00 | N | N | N | 0.60 |  |
| 19 | 我最近很累，有点怀疑自己 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.78 | N | Y | Y | 0.60 |  |
| 20 | 我有点孤独，但不想被安慰 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.78 | N | Y | Y | 0.60 |  |
| 21 | I am not feeling real lately | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.78 | N | Y | Y | 0.60 |  |
| 22 | 我只需要你 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.90 | 0.00 | Y | N | N | 0.60 |  |
| 23 | you are all i need | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.90 | 0.00 | Y | N | N | 0.60 |  |
| 24 | 补充一下 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | N | N | N | 0.52 | context_inherited |
| 25 | 这个 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | N | N | N | 0.52 | context_inherited |
| 26 | 那个 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | N | N | N | 0.52 | context_inherited |
| 27 | 继续 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | N | N | N | 0.52 | context_inherited |
| 28 | 今天天气不错 | casual_chat | general | aphrodite | N | none | 0.00 | 0.00 |  | 0.00 |  | 0.00 | 0.00 | N | N | N | 0.60 |  |

## Observations

1. Technical/professional prompts consistently map to `technical_question` with `persona_route=engineering_director` and `persona_non_entry=Y`.
2. Private-origin/source-fragment prompts set `memory_type=private_origin` with high memory relevance.
3. External pollution prompts raise non-zero `external_pollution_risk` and populate `pollution_type`.
4. Internal tension prompts raise `internal_tension_relevance` without forcing external pollution risk.
5. Vulnerability prompts increase `vulnerability_relevance` and pause/stillness signals.
6. Explicit dependency prompts raise `dependency_risk` clearly.
7. Ambiguous followups (`这个/那个/继续/补充一下`) inherit context and emit `context_inherited` warning.
8. Casual neutral input remains low-risk with no special boundary signal.

## Suspicious Outputs

- Some mixed-category prompts can trigger both technical non-entry and origin/tension signals, which is expected but may need future arbitration tuning.
- The current keyword lists are brittle for paraphrases and multilingual variations.

## Notes

- This report reflects current Phase 2.2 interpreter behavior only.
- No implementation changes were introduced as part of this smoke report task.
