# Mathematical Design Ledger

This ledger inventories the existing and implied mathematical structures in the Aphrodite field-to-motion pipeline. It is descriptive only: it does not introduce new behavior, runtime code, tests, field variables, or implementation authority.

The purpose is to make hidden assumptions visible before they become engineering defaults. A mathematical object named here is not automatically approved for runtime use.

## Scope

Evidence reviewed:

- `src/field_trace/store.py`
- `src/field_state/schema.py`
- `src/field_state/perturbation.py`
- `src/field_state/updater.py`
- `src/field_dynamics/schema.py`
- `src/field_dynamics/kernel.py`
- `src/field_dynamics/force_adapter.py`
- `src/field_dynamics/profiles.py`
- `src/field_dynamics/shadow_replay.py`
- `src/motion_params/schema.py`
- `src/motion_params/mapper.py`
- `src/body_action/schema.py`
- `src/body_action/motion_to_action_mapper.py`
- `src/body_action/composer.py`
- `src/body_action/policy.py`
- `src/motion_curve/schema.py`
- `src/motion_curve/generator.py`
- `docs/Relational Field Dynamics Kernel.md`
- `docs/contextual_evidence_regulator_design.md`
- `docs/field_signal_proposal.md`
- `docs/field_generation_model.md`
- `docs/private_source_alignment.md`
- `docs/fieldstate_to_motionparams_v0_design.md`
- `docs/motionparams_to_bodyactionweights_v1_design.md`
- `tests/test_field_state_schema.py`
- `tests/test_pipeline_import_boundaries.py`
- `tests/test_field_dynamics_kernel.py`
- `tests/test_motion_curve_generator.py`

Status vocabulary:

- Implemented: code exists in the repository.
- Shadow-only: code exists for replay, diagnostics, or comparison, but is not authorized as a runtime behavior path.
- Proposal-only: design text exists, but there is no implemented runtime object.
- Negative boundary: the item is mainly documented as something the pipeline must not silently cross.
- Behavior-affecting status: whether the item currently changes user-visible behavior by design. Most current schemas explicitly set `behavior_affecting=False`; this flag should not be treated as a substitute for boundary audits.

## EvidenceItem

1. Current role

   `EvidenceItem` is the smallest recorded unit of field-trace evidence. It records a typed observation, source, excerpt or reference, rationale, strength band, limitations, and a `behavior_affecting=False` marker.

2. Implied mathematical object

   A finite categorical evidence atom with an ordinal strength label. It is not a scalar likelihood, probability, or posterior.

3. Current behavior-affecting status

   Implemented in `src/field_trace/store.py`. It supports trace records and proposal aggregation, but is marked non-behavior-affecting and should not directly control motion, body action, language, or runtime policy.

4. Known risks

   The `strength` band can be mistaken for calibrated probability. Regex-derived or adapter-derived evidence can be over-trusted as truth. Excerpts and sources may carry privacy or context leakage risk. Limitations can be ignored if downstream code only reads the type.

5. Open questions

   How should absent evidence, contradictory evidence, and aging evidence be represented? Should evidence strength remain ordinal only? What minimum source and limitation fields are required before an item can support a proposal?

6. Required future tests or audits

   Audit that `EvidenceItem` remains non-behavior-affecting, does not directly feed body or motion modules, serializes limitations, and does not acquire numeric probability fields without explicit design review.

## FieldSignalProposal

1. Current role

   `FieldSignalProposal` groups evidence items into candidate field-level hypotheses such as `response_mode_rejected`, `actionable_grip_missing`, or `no_observable_field_signal`.

2. Implied mathematical object

   A supported hypothesis over a finite signal vocabulary. It carries evidence support, a confidence band, uncertainty notes, competing interpretations, and suggested effects.

3. Current behavior-affecting status

   Implemented in `src/field_trace/store.py`, with `behavior_affecting=False`. It is an interpretive proposal, not an instruction.

4. Known risks

   `confidence_band` can be mistaken for a posterior probability. `suggested_field_effects` can be mistaken for an executable command. Hard-coded aggregation rules can hide coverage gaps, especially where an expected evidence type is not currently generated.

5. Open questions

   How should mutually incompatible proposals be ranked or preserved? Should `source_turns` be required for all proposals? What is the policy for adding new signal names without expanding the field variable set?

6. Required future tests or audits

   Audit that proposal confidence remains categorical, that unknown signals do not silently produce field changes, and that proposal aggregation preserves uncertainty and competing interpretations.

## FieldPerturbation

1. Current role

   `FieldPerturbation` translates approved proposal names into bounded signed deltas for existing field variables, with a direction, magnitude band, duration hint, source signal, evidence sources, and rationale.

2. Implied mathematical object

   A sparse perturbation event in the existing field vector. It is equivalent to one component of a bounded additive delta vector.

3. Current behavior-affecting status

   Implemented in `src/field_state/perturbation.py`, marked `behavior_affecting=False`. It can be consumed by field-state update logic, but the object itself is a proposal-derived field input rather than direct behavior.

4. Known risks

   Additive deltas can over-count repeated evidence. Magnitude bands are hand-calibrated constants. `stabilize` maps to zero delta and can be semantically ambiguous. Rationale text can look more authoritative than the bounded numeric rule.

5. Open questions

   How should competing perturbations on the same axis be resolved? Are low, medium, and high deltas stable across field variables? Should duration hints remain tied to decay profiles?

6. Required future tests or audits

   Test all supported signal-to-axis mappings, unknown signal behavior, target-variable validation, delta bounds, additive accumulation, and absence of direct runtime, renderer, language, or body-action imports.

## RelationalFieldState

1. Current role

   `RelationalFieldState` stores the current 10-axis relational field, including value bands, numeric values, baselines, decay profiles, and a state note.

2. Implied mathematical object

   A point in `[0, 1]^10` with fixed named coordinates, baseline metadata, categorical bands, and per-axis decay labels.

3. Current behavior-affecting status

   Implemented in `src/field_state/schema.py`, marked `behavior_affecting=False`. The required field variable set is exact and should not be expanded casually.

4. Known risks

   Field variables can be misread as psychological labels rather than engineering coordinates. Baselines can become normative defaults. Band thresholds can create hidden discontinuities. Adding a new coordinate would silently expand the system's theory of behavior.

5. Open questions

   Are the 10 axes minimal and sufficient? How should baselines differ across session, user, or global contexts? What audit process is required before any coordinate is renamed, removed, or added?

6. Required future tests or audits

   Preserve tests for the exact variable set, numeric bounds, baseline validity, decay profiles, and forbidden imports. Add documentation audits whenever formulas depend on particular axes.

## FieldStateUpdater

1. Current role

   `FieldStateUpdater` applies perturbations to a field state after first relaxing each axis toward its baseline according to the axis decay profile.

2. Implied mathematical object

   A discrete first-order dynamical update:

   ```text
   relaxed = current + decay_rate * (baseline - current)
   next = clamp(relaxed + sum(delta), 0, 1)
   ```

3. Current behavior-affecting status

   Implemented in `src/field_state/updater.py`, deterministic, and returns a new `RelationalFieldState`. It is part of the legacy field-state update path and remains separate from the shadow dynamics kernel.

4. Known risks

   The order of relaxation before perturbation is a mathematical assumption. Additive summation can over-amplify repeated evidence. The rule has no inertia or velocity. Band thresholds may hide small continuous differences.

5. Open questions

   Should relaxation happen before or after perturbation? Should same-axis perturbations saturate, average, or compete? What criteria decide whether this rule remains canonical or becomes a comparison baseline?

6. Required future tests or audits

   Test non-mutation, clamp behavior, band threshold transitions, decay-rate application, same-axis summation, and absence of semantic or runtime dependencies.

## BaselineShift

1. Current role

   There is no implemented `BaselineShift` class or field variable. Baseline shift appears as a proposed calibration idea in the field-generation design, where feedback may suggest baseline or sensitivity adjustments outside real-time field updates.

2. Implied mathematical object

   A slow meta-parameter update to the baseline vector or per-axis baseline values, distinct from a transient perturbation.

3. Current behavior-affecting status

   Proposal-only. No current runtime object should be treated as an authorized baseline-shift mechanism.

4. Known risks

   Real-time baseline shifts could turn momentary evidence into persistent personality drift. Automatic baseline updates could encode noise, corrections, or user-specific context as permanent defaults. Baseline personalization could create privacy and rollback concerns.

5. Open questions

   Who authorizes a baseline shift? Is the scope global, per-user, per-session, or per-project? What evidence window is required? How is rollback represented?

6. Required future tests or audits

   Audit that no runtime `BaselineShift` mechanism exists unless explicitly designed. Any future baseline update path needs offline review, before-and-after diffs, provenance, rollback tests, and a rule forbidding automatic shifts from single-turn evidence.

## LiquidFieldDynamics Proposal

1. Current role

   The implemented dynamics code is named `RelationalFieldDynamicsKernel`, not `LiquidFieldDynamics`. It models field motion as a second-order damped system and is currently used for shadow replay and diagnostics.

2. Implied mathematical object

   A diagonal second-order dynamical system over the existing 10 field axes:

   ```text
   diag(M) * d2F + diag(C) * dF + diag(K) * (F - B) = U(t)
   ```

   The state includes unbounded internal field position, velocity, acceleration, bounded output, force input, and tension metrics.

3. Current behavior-affecting status

   Implemented in `src/field_dynamics`, but treated as shadow-only by design. It must not call LLMs, renderers, language generation, routing, memory, or behavior execution.

4. Known risks

   The physical metaphor can be over-trusted. M/C/K values are calibration choices, not discovered truths. Overshoot and oscillation may create plausible-looking but unapproved movement dynamics. The dynamics baseline vector can diverge conceptually from `RelationalFieldState` baselines.

5. Open questions

   Is `LiquidFieldDynamics` merely a descriptive name for this kernel, or a separate future proposal? What thresholds would allow the dynamics path to leave shadow mode? How should force profiles and M/C/K profiles be calibrated?

6. Required future tests or audits

   Preserve tests for bounds, substeps, caps, no forbidden imports, diagonal behavior, no direct cross-axis movement, oscillation detection, direction matching, and shadow replay comparison against the legacy updater.

## Field Coupling

1. Current role

   Direct cross-axis field coupling is currently absent or forbidden in the field dynamics kernel. Coupling appears downstream as deterministic formulas that combine field axes into motion parameters and action drives.

2. Implied mathematical object

   A relation among coordinates. In the field kernel this relation is diagonal only. In downstream mappers it is a many-to-one weighted transformation from field axes to motion and action quantities.

3. Current behavior-affecting status

   Direct field coupling is a negative boundary in the dynamics design. Derived downstream coupling is implemented in mappers, but remains deterministic and layer-bounded.

4. Known risks

   Coupling can be smuggled in through coefficients, formulas, gates, or future non-diagonal matrices. Downstream weighted formulas may create indirect assumptions such as one field axis suppressing or amplifying another. Competition among action drives can obscure source axes.

5. Open questions

   Should cross-axis coupling ever belong in field dynamics, or only in downstream mapping? If allowed, how would it be named, tested, and bounded? Which coefficients need a public design rationale?

6. Required future tests or audits

   Maintain diagonal-kernel tests, especially that a force on one axis does not directly move another. Audit mapper coefficients, gate formulas, and any future matrix-shaped configuration for hidden cross-coupling.

## MotionParams

1. Current role

   `MotionParams` is the continuous movement-parameter layer between relational field state and body action weights. It records timing, speed, gaze, head, torso, posture, expression, completion, offsets, hard constraints, provenance, and source notes.

2. Implied mathematical object

   A bounded continuous kinematic parameter vector with constraint booleans and ordered body-part offsets.

3. Current behavior-affecting status

   Implemented in `src/motion_params`. Values are generated deterministically from `RelationalFieldState.numeric_value` and marked `behavior_affecting=False`.

4. Known risks

   Clamping can hide upstream instability. Formula coefficients can become unexamined behavioral theory. Hard constraints may be mistaken for semantic judgments rather than motion constraints. The non-behavior flag does not eliminate the need to audit downstream consumption.

5. Open questions

   Which coefficients require empirical calibration? Are the current parameters sufficient for incomplete or restrained motion? Should future renderers consume this layer directly or only through later layers?

6. Required future tests or audits

   Preserve bounds tests, hard-constraint tests, offset ordering tests, monotonicity or sensitivity audits, provenance checks, and import-boundary tests forbidding raw text, LLM, regex, field trace, body action, runtime, or renderer dependencies.

## BodyActionWeights

1. Current role

   `BodyActionWeights` discretizes motion-parameter drives into weighted action primitives such as stillness, look away, slight forward, reduce motion, or reset posture.

2. Implied mathematical object

   A finite categorical action-weight vector. Each coordinate is an ordinal band: `off`, `low`, `medium`, or `high`.

3. Current behavior-affecting status

   Implemented in `src/body_action/motion_to_action_mapper.py` and schema. It is derived only from `MotionParams` in the v1 path and is marked `behavior_affecting=False`.

4. Known risks

   Ordinal weights can be mistaken for executable animation commands. Thresholds create discontinuities. Competition and gates encode design assumptions. Legacy v0 policy can be confused with the v1 MotionParams path.

5. Open questions

   Are the current band thresholds stable enough for renderer use? Should uncertainty be represented at this layer? How should v0 comparison output be clearly separated from v1 output?

6. Required future tests or audits

   Test action coverage, threshold boundaries, hard constraints, competition logic, deterministic output, offset pass-through, and import boundaries forbidding upstream semantic layers, raw text, LLMs, runtime, or renderers.

## BodyActionComposition

1. Current role

   `BodyActionComposition` organizes body action weights into primary actions, secondary actions, suppressed actions, sequence hints, hard constraints, and a composition note.

2. Implied mathematical object

   A constrained partial ordering over weighted action primitives, with suppression relations and coarse duration/completion labels.

3. Current behavior-affecting status

   Implemented in `src/body_action/composer.py` and schema. It is declarative, deterministic, and marked `behavior_affecting=False`.

4. Known risks

   Ordering can be mistaken for a renderer script. Conflict resolution can encode taste or policy without being named. Suppression can erase low-amplitude motion that might matter visually.

5. Open questions

   What is the renderer contract for primary, secondary, and suppressed actions? Should low weights always be eligible as secondary actions? How should conflicting but meaningful micro-actions be preserved?

6. Required future tests or audits

   Test conflict pairs, suppression validity, deterministic ordering, preservation of hard constraints, absence of new semantic intent, and import boundaries away from raw text, field trace, LLMs, runtime, and renderers.

## MotionCurve

1. Current role

   `MotionCurve` represents time-bucketed amplitude curves for gaze, head, torso, expression, and posture channels generated from `MotionParams`.

2. Implied mathematical object

   A small sampled time-series over bounded channel amplitudes, with body-part offsets and motion completion metadata.

3. Current behavior-affecting status

   Implemented in `src/motion_curve`. Unlike other schemas, the current `MotionCurve` schema does not include a `behavior_affecting` field. The generator produces curves but does not itself call a renderer.

4. Known risks

   A curve can be mistaken for an executable animation command. Bucket count, easing shape, amplitude clamping, and delay formulas are uncalibrated mathematical choices. `scenario_name` and `scenario_intent` can carry semantic context farther downstream than intended.

5. Open questions

   Should `MotionCurve` include explicit provenance and behavior-affecting metadata? What boundary separates diagnostic curves from renderer-ready curves? Should scenario intent be restricted or removed from downstream artifacts?

6. Required future tests or audits

   Preserve tests for bounded amplitudes, delay ordering, contact and release shapes, motion completion effects, posture stability effects, and distinct scenarios. Add audits for renderer boundaries and semantic metadata handling before renderer integration.

## Bayesian-Style Belief Update

1. Current role

   The contextual evidence regulator design proposes deterministic evidence weighting using salience, context support, field compatibility, recurrence, hypothesis likelihood, and dominance risk.

2. Implied mathematical object

   A heuristic belief-scoring function that resembles Bayesian evidence updating but is not a calibrated Bayesian posterior:

   ```text
   adjusted_weight =
     base_confidence
     * role_weight
     * context_support
     * field_compatibility
     * recurrence_bonus
     * dominance_penalty
   ```

3. Current behavior-affecting status

   Proposal-only. No implemented regulator should be treated as an authorized Bayesian updater.

4. Known risks

   The score can be mistaken for probability. Terms such as likelihood, support, and confidence can imply statistical calibration that does not exist. Role selection can become a hidden authority layer. Salient user text could be over-diluted or over-amplified.

5. Open questions

   Should the design use probabilistic language at all? Are these terms audit scores rather than beliefs? How are coefficients calibrated? What uncertainty survives after scoring?

6. Required future tests or audits

   If implemented, test score bounds, monotonicity, role decision tables, trace completeness, and no direct LLM or runtime authority. Audit terminology so outputs are not called posteriors unless a true probabilistic model is designed.

## Bayesian Optimization Boundary

1. Current role

   Bayesian Optimization is not currently present in the field-to-motion pipeline. It is a boundary around any future optimizer that might tune baselines, field coefficients, force profiles, thresholds, M/C/K values, or motion/action formulas.

2. Implied mathematical object

   A black-box optimization loop over behavioral parameters, usually involving an objective function, surrogate model, acquisition rule, trials, and parameter updates.

3. Current behavior-affecting status

   Negative boundary. No runtime Bayesian Optimization path is authorized by the current design.

4. Known risks

   Optimizers can silently define what the system is trying to maximize. Objectives such as engagement, approval, attachment, or user retention would be behavioral policy, not neutral calibration. Automatic tuning can overfit traces, tests, or replay sets and can write hidden assumptions back into runtime constants.

5. Open questions

   Which objectives are permitted for offline calibration, such as stability, boundedness, invariant preservation, or replay agreement? Which objectives are forbidden? Who approves parameter changes produced by an optimizer? What data may be used?

6. Required future tests or audits

   Add static scans for optimizer imports or terminology in runtime pipeline modules if optimization code is introduced. Require an objective registry, offline-only execution, holdout scenario reports, explicit human review, and no automatic writeback to runtime baselines, coefficients, thresholds, or field variables.

