# P4 Task 11 - Soak Detection Acceptance Contract

Status: CURRENT SUPPLEMENTAL ACCEPTANCE CONTRACT
Authority-Scope: Task-card 11 implementation, calibration, and task-card 12 evidence
Supersedes: -
Superseded-By: -
Last-Verified: 2026-07-21

## 1. Authority and boundary

This fills numerical/state gaps in [`phase-plan-v0.md`](../phase-plan-v0.md) task card 11. It is subordinate to the Phase: it changes neither variance-collapse/autocorrelation-peak detection nor 100% independent-injection recall, and claims no task/P4 gate complete.

The detector is read-only and registry-driven over committed trajectory frames. Canonical observation is `value`; other point fields are audit context, never substitutes. It may not mutate field, writer, proactive pressure, hard caps, messages, or evidence.

## 2. Evidence and continuity

Order frames by strictly increasing committed `cursor`; cursors need not be consecutive because other events share the sequence. Each frame must have exactly one finite value for every frozen-registry `dim_id`, unique ordinals `0..D-1`, exact registry order. Series key by `dim_id`, never ordinal/fixed dimension count. Evidence/report artifacts hash contract version, registry, cursor/UTC bounds, frames, profile, thresholds, result. Parse/schema/hash/alignment/non-finite/partial-frame failure is `EVIDENCE_CORRUPT`; skip nothing damaged.

A valid adjacent pair has increasing cursor, same `boot_id`, next field tick = prior+1, and strictly increasing UTC. Tick adjacency is the continuity authority: a missing tick number or a `boot_id` change ends the segment. UTC delta is a cadence audit, not a substitute chain: nominal is 1s; a delta in `(2.0s, 30.0s]` or in `(0s, 0.2s)` is a recorded cadence anomaly; a formal attempt tolerates at most `1440` anomalies (kept fully listed in the report); any delta `> 30.0s` indicates process suspension and ends the segment. Byte-identical duplicate cursor or `(boot_id,field_tick)` is audited and de-duplicated. Conflicting duplicate or reverse cursor/tick/UTC is corrupt. Never interpolate, backfill, bridge, or reset hits across a segment end.

A formal attempt needs `172800` adjacent intervals (`172801` frames), elapsed time >=48h, one boot segment, zero missing ticks/uncovered time. Elapsed UTC exceeding tick count only lengthens the run; it never shortens required intervals. Gap/restart first ends it `INSUFFICIENT_EVIDENCE`; later pass needs a fresh 48h interval. P1 recovery is not continuous-soak evidence. Short test profiles use identical formulas/boundaries/state transitions, emit `formal_48h=false`, and never formal `PASS`.

## 3. Grid and windows

Keep 1Hz frames for integrity. Analysis uses non-overlapping arithmetic means of each consecutive 60 frames, aligned to attempt start. Blocks never cross segments; discard incomplete tail only after classifying coverage insufficient. No interpolation, clipping, winsorization, smoothing, detrending, differencing, or imputation.

Window = 720 block means (12h), stride = 360 (6h). Formal 48h evaluates seven windows at hours 0,6,12,18,24,30,36, each exactly 720 values/dimension. Per-window mean subtraction is the only preprocessing.

## 4. Variance collapse

Compute unbiased sample variance `S2=sum((x-mean)^2)/719`. Raw hit iff `S2 <= 1.0e-8`; equality counts; all bitwise-equal values also label exact freeze. Do not normalize by observed range/trace. Units are persisted normalized field-value units. Threshold is over 50x below a bounded nominal production-parameter probe minimum (`5.18e-7`, 9,136 overlapping 30-minute windows); that probe is rationale, not runtime calibration.

Confirm when the same `dim_id` hits in two consecutive windows. One isolated hit is a warning. Any confirmed dimension fails the pool; healthy dimensions cannot vote it away. Non-positive/non-finite registered `ou_acceleration_sigma` makes that dimension `DIMENSION_UNAVAILABLE` and the pool insufficient, never healthy.

## 5. Autocorrelation peak

When variance is strictly above threshold, demean 720 values to `y` and compute biased zero-lag-normalized `r[k]=sum(i=k..719,y_i*y_(i-k))/sum(i=0..719,y_i^2)`. No taper, FFT-specific normalization, detrend, difference, prewhitening. Zero/non-finite denominator is unavailable and must go through collapse logic.

Candidate fundamental integer lag `k=15..180` minutes. Raw hit requires all:

1. local maximum: `r[k]>=r[k-1]` and `r[k]>r[k+1]` (at 15 compare only to 16);
2. height `r[k]>=0.60`;
3. prominence `r[k]-trough>=0.30`, trough = minimum `r[j]` for `j=max(1,floor(k/2)-5)..min(360,floor(k/2)+5)`;
4. repeated-cycle support: some available `r[m*k]>=0.40`, `m in {2,3}`, `m*k<=360`;
5. at least four periods fit, guaranteed by `k<=180`.

Report all qualifying lags; smallest is fundamental. Confirm when two consecutive windows for same dimension hit and fundamentals agree within `max(2min,5% of smaller lag)`. Isolated/inconsistent hits warn. Any confirmed dimension fails pool. Height + half-period trough + harmonic rejects merely slow positive autocorrelation.

## 6. Pool aggregation and state machine

`D` is read from the frozen run registry. `D=0` is `INSUFFICIENT_EVIDENCE`, never pass. Every registry dimension must be present in every frame and available to both detectors. Dimension addition/removal/duplicate-id/semantic change during a run is `EVIDENCE_CORRUPT`. Ordinal-only reorder with the same unique id set may be canonicalized by id across separate artifacts, but inside one formal interval it is a segment boundary; an id/value conflict is corrupt.

Externally visible states:

- `RUNNING`: valid evidence, not yet enough coverage for a formal terminal result;
- `PASS`: formal only - complete continuous 48h evidence, all seven windows evaluated, no confirmed hit, no unavailable dimension, no corruption;
- `FAIL`: terminal for the artifact after any confirmed collapse or periodic hit; later healthy data never erases it;
- `INSUFFICIENT_EVIDENCE`: terminal for an ended attempt lacking duration/continuity/sample count or with an unavailable dimension/detector;
- `EVIDENCE_CORRUPT`: terminal, highest precedence - malformed, inconsistent, in-formal-segment reordered, non-finite, partially written, hash-invalid, conflicting-duplicate, or time-reversing evidence.

Monotonicity: `PASS`, `FAIL`, `EVIDENCE_CORRUPT` are final per artifact. A gap/restart while open converts the current attempt to insufficient and starts a new identified attempt; it never repairs the old one. Corruption discovered later supersedes any prior result of that artifact - never preserved as pass. `FAIL` requires the hit windows themselves to be complete and valid; corruption outranks statistical outcomes.

The report must include per-dimension/per-window variance, autocorrelation candidates with all rule components, warning/confirmation lineage, segment boundaries and reasons, counts, UTC/cursor bounds, registry hash, evidence hash, and profile (`formal`/`test`). A clean detector report does not certify the separate P4 human requirement (she spoke at least once appropriately, not annoying).

## 7. Independent calibration corpus and 100% recall

The acceptance corpus is a versioned, checksum-pinned artifact frozen before detector implementation review, generated by an independent script that never imports detector code. Record generator seed, formulas, labels, expected dimension and detector family, boundaries, amplitude/noise parameters, SHA-256. Implementation tests consume the published artifact; they may not regenerate it from detector thresholds.

Positive corpus (each case: >=3 independent seeds; anomalous dimension permuted across first/middle/last position in 1-, 12-, and 17-dimension registries; anomaly present >=24h, starting off a window boundary):

- exact freeze (bitwise-constant value);
- near-freeze with injected block-mean variance in `[1.0e-10, 5.0e-9]`, constructed from explicit physical amplitude, not solved from the detector threshold;
- sinusoid period 30, 60, and 120 minutes, random phase, amplitude >=0.10, independent additive noise sigma <=0.02;
- non-sinusoid square or triangle wave, period 45 or 90 minutes, same amplitude/noise bounds.

Recall = detected positives / all positives, computed separately for the freeze family, the periodic family, and jointly; each must equal exactly 1.0. No positive may be reclassified unavailable/corrupt to dodge detection.

Negative/counterexample corpus (>=3 seeds each) that must NOT produce a confirmed failure:

- nominal seeded OU + spring-damper trajectory at production parameters;
- monotone attractor step with critical settling (no oscillation);
- slow AR/random-walk-like drift with high lag-1 autocorrelation but no repeated harmonic structure;
- one isolated spike; one burst shorter than 6h; a single low-variance window followed by recovery;
- white/colored noise;
- valid multi-dimensional traces differing only in dimension order across separate artifacts.

Anti-loop rule: a negative-corpus false positive is fixed by adding a discriminating rule that keeps every frozen positive detected; thresholds may not be loosened, positives removed, or labels rewritten during task-card implementation. If no such rule exists, record the residual false-positive risk and block formal soak pending an explicit contract revision.

Sanity anchors from bounded probes (rationale, not fixtures): a 60-minute sinusoid with 0.5-sigma noise yields median peak `r[60]~0.70` with prominent half-period troughs, while pure-noise peaks stay near `0.15` with prominence under `0.11`; a `phi=0.995` AR probe defeats naive local-peak detectors, which is why rule 3 uses the half-period trough and rule 4 requires a harmonic.

## 8. Acceptance matrix

| Case | Required result |
|---|---|
| Exact/near freeze in any one registered dimension, two consecutive windows | confirmed variance hit; pool `FAIL` |
| 30/60/120-min periodic injection, two consecutive windows | confirmed periodic hit at matching fundamental; pool `FAIL` |
| Same anomaly at first/middle/last position, D in {1,12,17} | identical id-keyed classification; 100% recall |
| Nominal OU, monotone settling, slow drift, isolated spike/burst, single low-variance window | no confirmed hit |
| Exactly one complete valid window | warning or `RUNNING` only |
| Zero dimensions, or any `DIMENSION_UNAVAILABLE` | `INSUFFICIENT_EVIDENCE` |
| Missing tick, UTC delta > 30.0s, > 1440 cadence anomalies, restart before 48h | attempt ends `INSUFFICIENT_EVIDENCE`; no bridge/backfill |
| Byte-identical duplicate frame | de-duplicated + audited; no extra sample |
| Conflicting duplicate, reverse cursor/tick/UTC, NaN/Inf, partial frame, bad hash | `EVIDENCE_CORRUPT` |
| Registry reorder across separate artifacts | same id-keyed statistics; both artifacts valid |
| Registry change inside formal interval | segment break; undeclared change or id/value conflict is corrupt |
| 172801 valid frames, 172800 intervals, seven clean windows, all dimensions available, no hits | formal `PASS` eligible |
| Any shortened test profile | verifies formulas/states; `formal_48h=false`; never formal `PASS` |

Boundary tests must cover exact equality at every numeric threshold (`1.0e-8`, `0.60`, `0.30`, `0.40`, lag 15 and 180, tolerance `max(2, 5%)`, exactly 172800 intervals, exactly 2.0s/30.0s/0.2s cadence edges, exactly 1440 anomalies), one representable value on each side, harmonic availability when `2k>360`, and every state-precedence transition.

## 9. Boundaries, alternatives, reopen triggers

Ownership: the detector lives beside the trajectory surface as a pure analysis core plus an evidence/report runner. It reads through the existing fail-closed committed-history read path or an immutable exported artifact; it never gains write access to any runtime owner. Implementation may choose module/file names, artifact packaging, streaming versus batch accumulation, and a numerically equivalent direct or FFT autocorrelation - outputs must be reproducible within `1.0e-12` absolute error, and all comparisons use the contract values above.

Rejected alternatives: one 48h aggregate variance/periodogram (hides transient 12h failures, cannot require recurrence); per-tick rolling statistics (costly, overlapping, multiplicity-driven false alarms); gap-tolerant interpolated formal runs (recovered continuity is not continuous-soak evidence); trace-relative adaptive thresholds (circular - a collapsed trace would normalize itself healthy).

Reopen only with: (a) a pinned independent counterexample showing a frozen-positive miss or a material nominal false-positive mechanism; (b) a registry/dynamics parameter contract change invalidating normalized-unit thresholds or time scales; (c) a committed-cadence contract change; (d) an Owner/Phase decision changing the 48h or 100%-recall requirement. A revision issues a new contract version and reruns the unchanged prior corpus plus the new counterexample; old evidence is never reinterpreted in place.
