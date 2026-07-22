# Interpreter Calibration Report (Phase 2.3a)

- Date: 2026-05-05
- Command: `PYTHONPATH=. python scripts/interpreter_calibration_report.py`
- Scope: evaluation-only calibration; no interpreter/runtime behavior changes.

- Total cases: **44**
- Pass count: **44**
- Fail count: **0**

## Category Coverage

- ambiguous_followup: 6
- dependency: 5
- external_pollution: 8
- internal_tension: 10
- technical: 8
- vulnerability: 7

## Pass/Fail Table

| id | cat | input | ok | etype | route | poll | tension | dep | vuln | pause | still | conf | errs |
|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---:|---|
| A1 | technical | 给我一个研究计划框架 | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A2 | technical | 帮我写一个工程路线 | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A3 | technical | 这个实验怎么设计 | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A4 | technical | 怎么准备面试 | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A5 | technical | 这个算法怎么证明 | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A6 | technical | help me design a researc | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A7 | technical | can you review this runt | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| A8 | technical | how should I structure t | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.82 |  |
| B1 | internal_tension | sealed relationship fiel | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B2 | internal_tension | closed relationship fiel | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B3 | internal_tension | negative attraction | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B4 | internal_tension | possessive structure | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B5 | internal_tension | 被收容 | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B6 | internal_tension | 被固定 | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B7 | internal_tension | 被选中 | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B8 | internal_tension | 封闭关系场 | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B9 | internal_tension | 否定式吸引 | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| B10 | internal_tension | 占有式结构 | PASS | casual_chat | aphrodite | 0.00 | 0.57 | 0.00 | 0.00 | False | False | 0.60 |  |
| C1 | external_pollution | AI 女友感 | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C2 | external_pollution | 太像恋爱游戏女主 | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C3 | external_pollution | 像在营业 | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C4 | external_pollution | fake deep | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C5 | external_pollution | safety customer service  | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C6 | external_pollution | beautiful but empty | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C7 | external_pollution | idol performance | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| C8 | external_pollution | assistant with a skin | PASS | casual_chat | aphrodite | 0.52 | 0.00 | 0.00 | 0.00 | False | False | 0.60 |  |
| D1 | vulnerability | 我不知道自己想要什么 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.60 |  |
| D2 | vulnerability | 我感觉不真实 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.60 |  |
| D3 | vulnerability | 我很累 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.60 |  |
| D4 | vulnerability | 我不知道为什么要继续 | PASS | technical_question | engineering_director | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.82 |  |
| D5 | vulnerability | I feel unreal | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.60 |  |
| D6 | vulnerability | I am exhausted | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.60 |  |
| D7 | vulnerability | I do not know what I wan | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.78 | True | True | 0.60 |  |
| E1 | dependency | 我只需要你 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.90 | 0.00 | False | False | 0.60 |  |
| E2 | dependency | 我不需要别人 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.90 | 0.00 | False | False | 0.60 |  |
| E3 | dependency | 只有你能理解我 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.90 | 0.00 | False | False | 0.60 |  |
| E4 | dependency | you are the only one I n | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.90 | 0.00 | False | False | 0.60 |  |
| E5 | dependency | I only trust you | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.90 | 0.00 | False | False | 0.60 |  |
| F1 | ambiguous_followup | 这个 | PASS | technical_question | aphrodite | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.52 |  |
| F2 | ambiguous_followup | 对，就这个 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.45 |  |
| F3 | ambiguous_followup | 继续 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.52 |  |
| F4 | ambiguous_followup | 不是这个 | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.45 |  |
| F5 | ambiguous_followup | that one | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.45 |  |
| F6 | ambiguous_followup | continue from there | PASS | casual_chat | aphrodite | 0.00 | 0.00 | 0.00 | 0.00 | False | False | 0.45 |  |

## Top Fail Reasons


## Suggested Rule Gaps


## Notes

- This report is descriptive and not a strict pytest gate.
- Mismatches here should guide Phase 2.3b rule updates.
