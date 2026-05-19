# Mathematical Design Risk Audit

This audit follows `docs/mathematical_design_ledger.md`. It is descriptive only. It does not implement runtime behavior, introduce field variables, change tests, or authorize any behavior-affecting path.

The goal is to prevent hidden mathematical assumptions from becoming engineering defaults.

## 1. Behavior-Affecting Path Audit

Legend:

- Yes: there is an implemented code-level path if the relevant mapper or generator is called.
- No: no direct implemented path was found.
- Shadow: diagnostic, replay, or monitor-style output only.
- Proposal: design text exists but no runtime object exists.
- Boundary: explicitly not authorized.

This table audits reachability, not approval. A non-behavior flag is useful metadata, but it is not a substitute for import and caller-boundary checks.

| Object | Runtime | Language | Memory | Body action | Motion curve | Renderer | Monitor output |
|---|---:|---:|---:|---:|---:|---:|---:|
| `EvidenceItem` | No direct | No | No | No | No | No | Yes, via field trace records/logs |
| `FieldSignalProposal` | No direct | No | No | Indirect only if explicitly adapted and mapped downstream | Indirect only through full field-to-motion chain | No | Yes, proposal trace/audit output |
| `FieldPerturbation` | No direct runtime execution | No | No | Indirect through state update then mappers | Indirect through state update then `MotionParams` | No | Yes, perturbation and shadow replay inputs |
| `RelationalFieldState` | No runtime import boundary by itself | No | No | Yes, through `MotionParams` -> `BodyActionWeights` | Yes, through `MotionParams` -> `MotionCurve` | No direct | Yes, state snapshots and tests |
| `FieldStateUpdater` | No direct runtime integration found | No | No | Indirect through returned state if caller continues the chain | Indirect through returned state if caller continues the chain | No | Yes, state-note and test output |
| `BaselineShift` | Proposal | Proposal | Proposal risk if ever persisted | No | No | No | Proposal/audit only |
| Liquid/Relational field dynamics | Shadow | No | No | No authorized path | No authorized path | No | Yes, tension metrics and replay traces |
| Field coupling | Boundary for direct field coupling | No | No | Yes, downstream formula coupling | Yes, downstream formula coupling | No direct | Yes, via tests/audits |
| `MotionParams` | No direct runtime integration found | No | No | Yes, `MotionToActionMapper` | Yes, `MotionCurveGenerator` | No direct | Yes, schema/test/provenance output |
| `BodyActionWeights` | No direct runtime integration found | No | No | Yes, as body-action artifact | No | No direct | Yes, body notes and tests |
| `BodyActionComposition` | No direct runtime integration found | No | No | Yes, as composed body-action artifact | No | No direct | Yes, composition notes/tests |
| `MotionCurve` | No direct runtime integration found | No | No | No | Yes, it is the curve artifact | No direct renderer call found | Yes, curve objects/tests |
| Bayesian-style belief update | Proposal | Proposal risk only | Proposal risk only | No | No | No | Proposal/audit only |
| Bayesian Optimization boundary | Boundary | Boundary | Boundary | Boundary | Boundary | Boundary | Audit-only if ever introduced |

### MotionCurve Special Risk

`MotionCurve` lacks explicit `behavior_affecting` metadata while representing sampled motion amplitudes. The generator does not call a renderer, but the artifact is closer to executable movement than earlier declarative layers. This makes it the highest-risk object for accidental behavior-affecting use by an external consumer.

Audit rule: before any renderer consumes `MotionCurve`, there should be an explicit boundary decision covering provenance, behavior-affecting metadata, scenario text handling, and whether curves are diagnostic or executable.

## 2. Identifiability Audit

This section asks whether each existing field variable has observable evidence strong enough to update it. It does not add field variables or new update rules.

| Field variable | Observable evidence | Ambiguous evidence | Negative evidence | Do-not-update cases |
|---|---|---|---|---|
| `boundary_distance` | Explicit boundary pressure; user rejects over-familiarity, false intimacy, seductive framing, or unwanted closeness; source material requires distance. | Shortness, silence, terse style, technical focus, or emotional flatness without explicit boundary content. | User explicitly invites closer collaboration within project constraints; actionable grip is missing and bounded support is requested. | No observable signal; politeness alone; model discomfort; single keyword such as "close" without context; inferred psychology. |
| `affective_warmth` | User asks for help staying with a hard task; actionable grip is missing and bounded warmth is appropriate; explicit appreciation of steady presence. | Casual friendliness, jokes, thanks, or emotional content that may not request warmth. | Explicit rejection of comfort, romance, caretaking, or soothing posture; boundary or contamination pressure. | No observable signal; project-only technical instruction; source critique; user anger that requires correction rather than warmth. |
| `structural_grip_pressure` | User says they lack a starting point, concrete next step, foothold, structure, plan, or actionable grip. | General confusion, broad brainstorming, "I'm stuck" without task context. | User already provides a concrete task, file path, patch request, or exact next action. | No observable signal; emotional support request without task; model desire to over-structure conversation. |
| `correction_pressure` | User corrects model behavior, says previous response was wrong, rejects a framing, asks not to sanitize, or changes instructions after misalignment. | Mild preference, style tweak, or normal iteration that may not imply correction pressure. | User confirms the current direction, accepts the framing, or asks to continue. | No observable signal; unrelated test failure; model self-critique; correction inferred from tone only. |
| `contamination_resistance` | User rejects AI-girlfriend drift, service drift, false intimacy, sanitization, role contamination, or source flattening. | Discussion of intimacy, care, relationship, or character tone as fictional design material. | User explicitly authorizes a contained fictional or design exploration without source contamination. | No observable signal; ordinary warmth; technical work on romance-related content that is clearly fictional or analytic. |
| `presence_stability` | User values steadiness, continuity, non-reactivity, or asks the system not to wobble after correction. | Calm conversation, long silence, or lack of criticism. | User explicitly asks for fast experimentation, alternate personas, or volatile style exploration. | No observable signal; every successful turn should not raise stability; do not treat stability as a reward counter. |
| `withdrawal_tendency` | User indicates overwhelm, wants distance, rejects engagement, or boundary pressure suggests a slight retreat. | Short replies, delay, fatigue, or terse technical instructions. | User asks for continued collaboration, concrete help, or bounded closeness on the task. | No observable signal; model embarrassment; user being concise; temporary context switch. |
| `service_resistance` | User rejects customer-service tone, pleasing posture, comfort scripts, assistant blandness, or asks for stronger independent stance. | Normal request for help, politeness, or direct task delegation. | User explicitly requests straightforward service execution where no posture drift is implied. | No observable signal; every task request should not raise service resistance; do not punish clarity. |
| `collaborator_layer_pressure` | User gives technical project context, file paths, implementation/audit tasks, design decisions, or asks for structured collaboration. | General curiosity, abstract design talk, or casual discussion of code. | User wants non-technical presence, story, reflection, or conversation without project action. | No observable signal; mention of technology in a metaphor; user explicitly says not to act. |
| `contamination_pressure` | Current-turn contamination trigger: false intimacy, boundary violation, sanitization attempt, or service/romance drift signal requiring immediate protection. | Words associated with intimacy, care, girlfriend, service, or contamination used analytically. | Explicitly safe framing: fictional, quoted, analytic, or user-approved exploration. | No observable signal; historical contamination that is not current; source terms without drift; single lexical hit without context. |

Identifiability rule: absence of evidence is not evidence of neutral state. It should usually mean no perturbation plus natural relaxation, not a forced move toward any preferred posture.

## 3. Stability Audit

### FieldStateUpdater

Dynamic risks:

- Additive perturbation sums all deltas per target axis before clamp. Repeated same-axis evidence can saturate an axis faster than intended.
- Relaxation happens before perturbation. This makes current-turn evidence dominate the post-relaxation value and should be treated as a design assumption.
- Clamp to `[0, 1]` prevents numeric overflow but can hide overshoot, repeated pressure, or calibration errors.
- Band thresholds at `0.20`, `0.50`, `0.70`, and `0.90` create discontinuities in categorical output.
- Decay profiles are coarse labels mapped to fixed rates: `instant=1.00`, `fast=0.45`, `medium=0.25`, `slow=0.12`, `very_slow=0.04`.
- Different axes have different baselines and decay profiles, so the same perturbation magnitude can have different persistence.
- `stabilize` perturbations contribute zero numeric delta, so their semantic intent can disappear unless audited elsewhere.

Where instability can hide:

- In the sum of same-axis perturbations.
- In repeated turns that hit the same axis just below the clamp.
- In band flips near thresholds.
- In decay profiles that keep resistance-like variables elevated for many turns.
- In tests that only check final clamped values and not pre-clamp pressure.

### RelationalFieldDynamicsKernel

Dynamic risks:

- The kernel is a diagonal second-order system, so instability is per-axis rather than cross-axis in the current implementation.
- Semi-implicit Euler integration is bounded by substeps, velocity caps, acceleration caps, and overshoot caps, but those caps can hide parameter instability.
- M/C/K profiles encode damping ratio and natural frequency choices. They are calibration assumptions, not learned truths.
- A low-damping/high-frequency profile can create oscillation or delayed reversal even when bounded output looks plausible.
- `F_tilde` can overshoot outside `[0, 1]` before bounded output is computed.
- Force profiles can delay, smear, or erase an intended perturbation. At `t=0`, ramp and slow-pressure profiles produce zero force.
- Tension metrics are diagnostic; they do not by themselves prove behavioral safety.

Where instability can hide:

- Behind `V_max`, `A_max`, and `overshoot_max` caps.
- In `F_tilde` while `F_bounded` looks safe.
- In profile choices such as `nerve`, `gyre`, `tide`, and `monolith`.
- In `dt_max` and substep choices.
- In force-profile mapping from signal names to impulse, ramp, persistent step, or slow pressure.
- In mismatch between dynamics baseline `B` and `RelationalFieldState` baseline values.

## 4. Coupling Audit

### Direct Field Dynamics Coupling

Direct cross-axis coupling is absent in `RelationalFieldDynamicsKernel` as currently implemented. M, C, K, and B are per-axis arrays. The update computes spring force, damping force, acceleration, velocity, and position componentwise. Existing tests assert that a force on one axis does not directly move another axis.

This audit does not propose new coupling.

### Downstream Coupling

Downstream coupling is present as deterministic mapping formulas. These formulas are not field-dynamics coupling, but they do combine axes and can still encode behavioral assumptions.

`MotionParams` coupling:

- `approach_tendency` combines `affective_warmth`, `structural_grip_pressure`, and `collaborator_layer_pressure`.
- `completion_inhibition` combines `boundary_distance`, `contamination_pressure`, `contamination_resistance`, `service_resistance`, `withdrawal_tendency`, and `correction_pressure`.
- `stability_force` combines `presence_stability`, `contamination_resistance`, and `service_resistance`.
- `visible_forward_motion` multiplies approach by inverse completion inhibition.
- Motion timing, gaze, head, torso, posture, expression, hard constraints, and offsets reuse these coupled values and raw axes.

`BodyActionWeights` coupling:

- Drives combine multiple `MotionParams` values into each action primitive.
- Gates suppress or amplify drives based on timing, speed, completion, expression, gaze contact, gaze release, head turn, and posture.
- Hard constraints can zero or boost several drives at once.
- Gaze competition allows only one of `look_to_user` or `look_away` when both are nonzero.

`BodyActionComposition` coupling:

- Band strengths are converted into primary and secondary action sets.
- Conflict pairs suppress one action based on relative strength or tie rules.
- `stillness` suppresses `slight_forward` in primary action selection.
- Completion mode depends on high `stillness` or `reduce_motion`, number of suppressed actions, and constrained off actions.

`MotionCurve` coupling:

- All curve channels are generated from shared `MotionParams`.
- Timeline duration depends on motion speed and initial delay.
- Channel delay uses hand offset multiplied by channel-specific delay factors.
- Gaze curve couples gaze contact, release amplitude, and head-turn delay.
- Head curve depends on gaze contact and head-turn delay.
- Torso curve uses absolute torso lean and motion completion.
- Expression curve fades when motion completion is low.
- Posture curve adds oscillation based on posture stability.

Coupling audit rule: downstream formulas should stay named, documented, and testable. They should not be mistaken for direct field-axis coupling or used to justify adding cross-axis dynamics.

## 5. Maturity-Level Table

Level definitions:

- L0 metaphor: useful language, no stable engineering semantics.
- L1 heuristic: rule of thumb or hand-authored scoring.
- L2 formal engineering rule: deterministic contract with bounds, validation, and tests possible.
- L3 analyzable mathematical model: explicit model whose stability or invariants can be analyzed.
- L4 learnable/optimizable model: parameters can be learned or optimized against an objective.

| Mechanism | Current maturity | Notes |
|---|---:|---|
| `EvidenceItem` | L2 schema, L1 semantics | Structured evidence atom; strength remains ordinal and uncalibrated. |
| `FieldSignalProposal` | L1/L2 | Rule-based proposal aggregation with formal schema but heuristic interpretation. |
| `FieldPerturbation` | L2 | Bounded deterministic delta mapping; calibration remains heuristic. |
| `RelationalFieldState` | L2 | Fixed bounded state vector with validation and baselines. |
| `FieldStateUpdater` | L3 | Explicit first-order relaxation plus additive perturbation and clamp. |
| `BaselineShift` | L0 | Named proposal only; no implemented engineering rule. |
| Liquid/Relational field dynamics | L3 shadow | Explicit second-order diagonal model, not runtime-authorized. |
| Field coupling | L2 boundary/downstream | Direct coupling is a negative boundary; downstream coupling is deterministic formulas. |
| `MotionParams` | L2 | Deterministic bounded engineering layer from field values. |
| `BodyActionWeights` | L2 | Deterministic quantization and gate layer. |
| `BodyActionComposition` | L2 | Deterministic selection, suppression, and ordering layer. |
| `MotionCurve` | L2, approaching L3 | Bounded sampled curves; lacks behavior-affecting metadata and renderer boundary. |
| Bayesian-style belief update | L1 proposal | Heuristic evidence weighting; not a true posterior. |
| Bayesian Optimization boundary | Boundary; no L4 runtime | L4-style optimization is explicitly not authorized in the runtime path. |

Maturity rule: nothing in the current field-to-motion path should be treated as L4. Any future learnable or optimizable mechanism requires a separate design, objective audit, offline boundary, and review gate.

## 6. Required Tests

These are proposed dynamic golden cases and audits only. They should not be implemented without a separate request.

### Field State Golden Cases

- No observable signal: proposals produce no perturbation, field state only relaxes toward baseline, and no downstream action is introduced.
- Single correction: `response_mode_rejected` raises correction pressure and service resistance within bounds without changing unrelated axes.
- Repeated correction: repeated same-axis perturbations saturate only through documented clamp behavior, with pre-clamp accumulation visible in audit output.
- Actionable grip missing: structural grip and collaborator pressure increase while boundary distance and withdrawal decrease only by documented low deltas.
- Boundary pressure: boundary distance, contamination pressure, withdrawal, and contamination resistance move in documented directions; warmth decreases only by documented low delta.
- Stabilize-only perturbation: zero delta does not change numeric value except through relaxation.
- Threshold edge: values just below and just above each band boundary produce expected band labels without changing numeric value unexpectedly.

### Dynamics Kernel Golden Cases

- Single-axis force: a force on `affective_warmth` changes only the warmth axis in `F_tilde`, `V`, `A`, and `F_bounded`.
- Zero input relaxation: with non-baseline state and zero force, the field moves toward B without oscillation beyond configured caps.
- Ramp profile at t=0: ramp and slow-pressure profiles produce zero initial force and nonzero later force in sequence mode.
- High-frequency profile: `nerve` axes remain bounded, with velocity, acceleration, and overshoot metrics visible.
- Baseline mismatch: dynamics `B` differing from legacy baseline is detected in shadow replay reports.
- Cap masking: tests inspect uncapped or pre-cap diagnostics where available, not only `F_bounded`.

### Mapper Golden Cases

- High boundary and contamination: `MotionParams` activates hard constraints, limits forward motion, caps gaze contact and expression, and does not call body action or renderer directly.
- High collaborator with low contamination: collaborator pressure affects approach tendency without bypassing service and contamination constraints.
- High correction pressure: initial delay and pause rise, expression/posture remain bounded, and no new field variables appear.
- Motion-to-action competition: equal or competing gaze drives resolve deterministically.
- Composition conflict pairs: `look_to_user` versus `look_away`, `slight_forward` versus `slight_withdraw`, and `stillness` versus `slight_forward` resolve by documented rules.

### MotionCurve Golden Cases

- Metadata boundary: generated curves carry no renderer call and no hidden runtime side effect.
- Behavior-affecting audit: lack of `behavior_affecting` metadata is explicitly detected by documentation or schema audit before renderer integration.
- Low motion speed: timeline caps at 5 seconds and remains 20 buckets.
- Low completion: torso amplitude scales down and expression fades.
- Offsets: gaze, head, torso, expression, and posture delays follow documented channel delay factors.
- Bounds: all curve points remain in `[0, 1]` amplitude and `[0, 5]` seconds.

### Boundary and Static Audits

- Import boundaries: field, motion, body action, and curve modules do not import runtime, renderer, LLM, memory, router, or raw text paths except where explicitly allowed by design.
- No optimizer in runtime path: scan for Bayesian Optimization, acquisition functions, Gaussian process optimizers, or automatic parameter writeback in runtime modules.
- No hidden field expansion: exact `REQUIRED_FIELD_VARIABLES` set remains unchanged unless a separate field-variable design review is approved.
- No direct renderer consumption: renderer modules, if added later, must cross an explicit boundary document before consuming `MotionCurve`, `BodyActionComposition`, or `BodyActionWeights`.
- No probability drift: confidence, strength, and belief-like scores remain clearly non-probabilistic unless a true probabilistic model is separately designed.

