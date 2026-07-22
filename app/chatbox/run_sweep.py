"""P3.9 offline sweep CLI entry point.

Runs the pure synthetic sweep harness and publishes a blind/answer/manifest
package to a user-specified new leaf directory.  Never starts a server, never
reads environment credentials, never creates a SQLite database, and never
touches the network.  Provider/HTTP transport is never imported or
instantiated.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from app.chatbox.field_dynamics import (
    DimensionRegistration,
    DimensionSnapshot,
    FieldSnapshot,
    build_birth_registry,
)
from app.chatbox.field_runtime import RegistryProxy
from app.chatbox.sweep_harness import (
    DEFAULT_MESSAGE,
    SweepError,
    generate_sweep,
    publish_package,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline P3.9 synthetic sweep and publish a blind/answer/manifest package."
    )
    parser.add_argument("--output", required=True, help="new leaf directory to publish the package into")
    parser.add_argument(
        "--mode",
        choices=("forced", "alliance"),
        default="forced",
        help="sweep mode (default: forced)",
    )
    parser.add_argument("--seed", type=int, default=90210, help="deterministic seed (default: 90210)")
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="fixed input message used for every case (default: built-in)",
    )
    parser.add_argument(
        "--spec",
        default=None,
        help=(
            "optional JSON file path with a sweep spec: "
            '{"dim_ids":[...],"forced_targets":[...],"alliance_conditions":[[...],...]}'
        ),
    )
    return parser


def _load_spec(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SweepError("invalid_spec", "spec file must contain a JSON object")
    return data


def _build_registry_from_spec(spec: dict) -> RegistryProxy:
    dim_ids = spec.get("dim_ids")
    if not isinstance(dim_ids, list) or not dim_ids:
        raise SweepError("invalid_spec", "spec.dim_ids must be a non-empty list")
    seen: set[str] = set()
    registrations: list[DimensionRegistration] = []
    for index, dim_id in enumerate(dim_ids):
        if not isinstance(dim_id, str) or not dim_id:
            raise SweepError("invalid_spec", f"dim_ids[{index}] must be a non-empty string")
        if dim_id in seen:
            raise SweepError("invalid_spec", f"duplicate dim_id {dim_id!r}")
        seen.add(dim_id)
        registrations.append(
            DimensionRegistration(
                dim_id=dim_id,
                temporary_name=f"synthetic-{index}",
                birth_time=0.0,
                strength=1.0,
                trigger_count=0,
                birth_bias=0.0,
                fast_e_fold_s=4.0,
                ou_correlation_e_fold_s=600.0,
                ou_acceleration_sigma=4.0e-7,
                soft_boundary_start=1.0,
                soft_boundary_width=0.25,
                soft_boundary_strength=(1.0 / 120.0) ** 2,
            )
        )
    return RegistryProxy(registrations=tuple(registrations))


def _default_registry() -> RegistryProxy:
    return RegistryProxy(registrations=build_birth_registry())


def _default_snapshot(registry: RegistryProxy) -> FieldSnapshot:
    return FieldSnapshot(
        tick=0,
        dimensions=tuple(
            DimensionSnapshot(
                registration=registration,
                value=0.0,
                velocity=0.0,
                attractor=0.0,
                soft_restoring_baseline=0.0,
                ou_acceleration=0.0,
            )
            for registration in registry.registrations
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        spec: dict | None = None
        if args.spec:
            spec = _load_spec(args.spec)
        if spec is not None:
            registry = _build_registry_from_spec(spec)
        else:
            registry = _default_registry()
        snapshot = _default_snapshot(registry)
        forced_targets = spec.get("forced_targets") if spec else None
        alliance_conditions = spec.get("alliance_conditions") if spec else None
        result = generate_sweep(
            registry=registry,
            snapshot=snapshot,
            mode=args.mode,
            seed=args.seed,
            message=args.message,
            forced_targets=forced_targets,
            alliance_conditions=alliance_conditions,
        )
        manifest = publish_package(result=result, output_path=args.output)
        summary = {
            "type": "sweep_published",
            "output": args.output,
            "mode": manifest.mode,
            "synthetic": manifest.synthetic,
            "seed": manifest.seed,
            "case_count": manifest.case_count,
            "sample_count": manifest.sample_count,
            "skipped": list(manifest.skipped),
            "owner_blind_gate": manifest.owner_blind_gate,
            "two_hour_silence_gate": manifest.two_hour_silence_gate,
            "package_digest": manifest.package_digest,
        }
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return 0
    except SweepError as exc:
        sys.stderr.write(
            json.dumps(
                {"type": "sweep_error", "code": exc.code, "detail": exc.detail},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stderr.flush()
        return 1
    except Exception as exc:  # pragma: no cover - defensive structured error
        sys.stderr.write(
            json.dumps(
                {"type": "sweep_error", "code": "unexpected", "detail": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
