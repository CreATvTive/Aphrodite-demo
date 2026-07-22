# Aphrodite chatbox v0 implementation entry

The authoritative source is [`../../docs/chatbox/phase-plan-v0.md`](../../docs/chatbox/phase-plan-v0.md).

P1.1 field dynamics, P1.2 persistence/runtime, and the P1.3 trajectory view are retained. P2 task card 6 adds a zero-build conversation UI over the independent `aphrodite.chatbox.dialogue-ws/1` protocol. Dialogue history and writer audit use an append-only companion SQLite store; field mutations remain exclusively behind `Writer.apply()` and `FieldRuntime.move_attractor()`.

Start the localhost service with:

```console
python -m app.chatbox.run_trajectory --db var/chatbox/field.sqlite3 --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/` (the ready line also prints this URL). The page loads the registry-defined dimensions, the trailing trajectory, current values, and matching gate weights. It resumes from the last committed tick cursor after a transient disconnect, requests a fresh tail when the server requires a resync, marks stale data explicitly, and keeps at most 7,200 frames in browser memory. No frontend build or package installation is required.

The default command never makes a provider request. With no explicitly enabled provider, submitted messages are persisted and the UI reports a provider-unavailable dynamics-only degradation; it does not fabricate a reply. For a deterministic, network-free local UI smoke, add `--offline-fake`:

```console
python -m app.chatbox.run_trajectory --db var/chatbox/field.sqlite3 --offline-fake
```

Real provider I/O requires both `--enable-provider` and a configured environment credential. This opt-in is intentionally separate so routine local startup cannot incur a paid external call.

## P4.10 tick-driven proactive expression

Task card 10 keeps `P_talk` as a persistent, deterministic expression-pressure accumulator rather than a Bernoulli probability. Each strictly adjacent, committed field tick advances it from the registry-driven `birth_03` and `birth_09` values plus server-trusted user silence; duplicate or reverse ticks do nothing and a gap only re-anchors without backfilling. There is no proactive timer, sleep loop, wall-clock catch-up, or random trigger: the existing 1 Hz committed field tick is the only evolution hook.

Pressure, tick cursor, append-only decisions, admissions, and outcomes live in a separate companion SQLite database. By default its path is `<db>.proactive`; override it with `--proactive-db`. Reaching the pressure threshold is only a candidate: an explicitly available provider and at least one handshaken dialogue socket are required before the store atomically checks the hard cap, records an admission, and resets pressure. A denied cap preserves pressure. Once admitted, provider degradation, unsafe or invalid output, persistence failure, writer/runtime failure, disconnect, cancellation, or shutdown sends nothing and is never retried; the failed admission still conservatively counts.

The default hard cap is at most two admissions per local day, at least six hours between admissions, and curfew from 01:00 inclusive through 09:00 exclusive. CLI configuration may only tighten it: `--proactive-daily-limit` accepts 0..2, `--proactive-min-interval-seconds` must be at least 21600, `--proactive-curfew-start-hour` may move no later than 01:00, and `--proactive-curfew-end-hour` may move no earlier than 09:00. Looser or invalid values fail startup closed. The default provider-offline command still performs no provider call and cannot emit proactively.

Automated checks do not replace Owner or Phase gates. Task-card 12's run-control engineering is implemented below, but the P1 one-hour drift/restart observation, P2 real-provider and Owner ten-turn checks, Owner blind gate, two-hour silence gate, formal 48-hour run, and P4 human gate remain `not_run` until the Owner performs them. This directory does not restate or amend the Phase contracts and does not claim v0 complete.

## P4.11 read-only soak detection

Task card 11 adds an optional observer over the exact `TrajectoryFrame` returned after each successful field commit. It is disabled by default and creates no timer, task, provider/network request, message, proactive pressure, hard-cap action, writer call, or field mutation. Enable it only by supplying both companion paths; `test` is the safe default profile and can never produce a formal `PASS`:

```console
python -m app.chatbox.run_trajectory --db var/chatbox/field.sqlite3 --soak-evidence-db var/chatbox/soak.sqlite3 --soak-report var/chatbox/soak-report.json --soak-profile test
```

The companion SQLite artifact uses its own WAL/FULL connection and append-only frame/event tables. Each canonical frame payload, full registry/profile/contract metadata, and row hash is verified and replayed on reopen. The canonical JSON report is fsynced and atomically replaced; it records registry/evidence/result hashes, attempts and continuity boundaries, all cadence anomalies and duplicates, 60-frame block/720-block window counts, per-dimension variance and exact-freeze labels, qualifying direct-autocorrelation candidates and confirmation lineage, dimension availability, terminal precedence, and explicit `formal_48h_run=not_run` / `p4_human_gate=not_run` fields.

The offline query CLI opens no field database, server, provider, socket, or credential source. It strictly revalidates/replays the specified companion store, atomically refreshes the report, and writes one canonical JSON summary line:

```console
python -m app.chatbox.run_soak_detection --evidence-db var/chatbox/soak.sqlite3 --report var/chatbox/soak-report.json
```

The `formal` profile only enables eligibility under the frozen complete 48-hour conditions. The Owner control plane below orchestrates that profile, but no formal 48-hour evidence or Owner human gate is claimed merely by implementing or smoke-testing it.

## P4.12 Owner run control

`run_formal` owns one explicit run directory through an immutable, hashed manifest, an append-only hashed control database, and a cross-platform process lease. `start` accepts only a new leaf directory; it never reuses or overwrites an existing directory. `restart` accepts no configuration overrides: it preserves the existing stores and immutable manifest, but creates a new field boot and soak attempt, so a formal run must accumulate the complete single-boot 48 hours again.

The following commands work in both Windows `cmd.exe` and PowerShell when run from the repository root (replace the directory with a new local path):

```console
python -m app.chatbox.run_formal start --run-dir C:\aphrodite-runs\formal-001 --profile formal --host 127.0.0.1 --port 8765
python -m app.chatbox.run_formal status --run-dir C:\aphrodite-runs\formal-001
python -m app.chatbox.run_formal stop --run-dir C:\aphrodite-runs\formal-001
python -m app.chatbox.run_formal restart --run-dir C:\aphrodite-runs\formal-001
python -m app.chatbox.run_formal result --run-dir C:\aphrodite-runs\formal-001
python -m app.chatbox.run_formal gate --run-dir C:\aphrodite-runs\formal-001 p1_visual_one_hour passed
python -m app.chatbox.run_formal gate --run-dir C:\aphrodite-runs\formal-001 p2_real_provider passed
python -m app.chatbox.run_formal gate --run-dir C:\aphrodite-runs\formal-001 p2_owner_ten_turn passed
python -m app.chatbox.run_formal gate --run-dir C:\aphrodite-runs\formal-001 p3_blind_pairing passed
python -m app.chatbox.run_formal gate --run-dir C:\aphrodite-runs\formal-001 p3_two_hour_silence passed
python -m app.chatbox.run_formal gate --run-dir C:\aphrodite-runs\formal-001 p4_proactive_expression passed
```

Use `--profile smoke --port 0` for a short, non-formal process check. `smoke` maps to the detector's `test` profile and can never establish formal 48-hour completion. There are no duration, cadence, threshold, tick, replay, or synthetic-completion options.

Provider mode is `offline` by default. Real provider I/O requires a credential already present in the current session environment and an explicit `--enable-provider` on `start`. The credential itself is never a CLI argument and is not persisted in the manifest, control database, logs, report, or result. A missing credential fails before the run directory is created.

The fixed managed artifacts are `manifest.json`, `control.sqlite3`, `run.lock`, `field.sqlite3`, `dialogue.sqlite3`, `perception.sqlite3`, `proactive.sqlite3`, `soak.sqlite3`, `soak-report.json`, `worker.stdout.jsonl`, `worker.stderr.jsonl`, and `result.json`. While the lease is held, `result` reports an incomplete running result and does not hash active SQLite files. After controlled shutdown and checkpointing, it validates each source, records size and SHA-256, strictly replays soak evidence, references the soak evidence/result hashes, and atomically publishes `result.json`.

Machine output deliberately keeps separate fields and vocabularies: `control_state`, `process_state`, `lease_state`, `profile`, `soak_profile`, `provider_mode`, `soak_state`, `formal_48h`, and `manual_gates`. A stopped process, available provider, test-profile result, smoke check, or individual manual gate cannot become v0 completion. The formal 48-hour state is derived only from strict formal soak evidence; no `gate` id exists for it. Task-card 12 engineering is implemented, while the actual formal 48-hour run and every gate not explicitly recorded by the Owner remain `not_run`.

## P3.9 offline synthetic sweep

`app/chatbox/sweep_harness.py` and `app/chatbox/run_sweep.py` add a pure, offline, registry-driven experiment domain (task-card 9). It never imports or instantiates the runtime, persistence, writer, provider, or HTTP transport; provider/network calls are always zero. Cases are constructed from a read-only `RegistryProxy` + synthetic `FieldSnapshot`, projected through the existing expression gate, prompt-style, and receptor-plan abstractions, rendered by a deterministic in-process synthetic renderer, and audited by `detect_meta_narration()`. The forced mode uses a read-only experimental gate where the target dimension weight is 1.0 and all other registered dimensions are 0.0; the alliance mode reuses the normal `AllOpenGateProjector`. Field state, value, velocity, attractor, baseline, and OU are never written.

Each published package is a new leaf directory containing exactly `blind/samples.jsonl`, `answer/answer-key.json`, and `manifest.json`. Blind samples carry only an opaque `sample_id`, the fixed input text, and the reply text; answer keys carry the mode, `synthetic=true`, registry-ordered condition values, gate weights, seed lineage, and receptor/style audit fields. The manifest records schema/version, synthetic, mode, seed, case/sample/rejected/skipped counts, `owner_blind_gate=not_run`, `two_hour_silence_gate=not_run`, each managed file's relative POSIX path/byte size/SHA-256, and a package digest over those files (the manifest is not self-hashed). Publication is atomic: a sibling staging directory is fully written, flushed, fsynced, and checksum-verified before `os.replace()` publishes the formal directory; on any failure the staging is removed and no formal directory appears. Existing directories, files, root/cwd/home, `..` traversal, and symlinked parents are rejected.

```console
python -m app.chatbox.run_sweep --output <new-dir> --mode forced --seed 90210 --message "固定同一句话"
python -m app.chatbox.run_sweep --output <new-dir> --mode alliance --seed 90210 --message "固定同一句话"
```

The default never reads environment credentials, never creates a SQLite database, and never starts a server. The Owner blind gate and the two-hour silence gate are reported as `not_run`; automated tests do not claim to substitute for them.
