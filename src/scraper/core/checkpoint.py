"""Seed-row checkpoints for Phase 3 multi-location runs (--input)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CHECKPOINT_VERSION = 1


def checkpoint_path_for_output(out_csv: Path) -> Path:
    """Sidecar JSON next to the export, e.g. `out.csv.seed_checkpoint.json`."""
    return out_csv.parent / f"{out_csv.name}.seed_checkpoint.json"


@dataclass
class SeedCheckpoint:
    version: int
    input_path: str
    total_seeds: int
    next_index: int  # next row to run (0-based); seeds [0, next_index) are done

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "input_path": self.input_path,
            "total_seeds": self.total_seeds,
            "next_index": self.next_index,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SeedCheckpoint | None:
        try:
            if int(d.get("version", 0)) != CHECKPOINT_VERSION:
                return None
            return cls(
                version=CHECKPOINT_VERSION,
                input_path=str(d["input_path"]),
                total_seeds=int(d["total_seeds"]),
                next_index=int(d["next_index"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def load_seed_checkpoint(path: Path) -> SeedCheckpoint | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    return SeedCheckpoint.from_dict(raw)


def save_seed_checkpoint(path: Path, state: SeedCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def delete_seed_checkpoint(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def is_checkpoint_valid_for_run(
    state: SeedCheckpoint,
    *,
    input_path: Path,
    total_seeds: int,
) -> bool:
    if state.next_index < 0 or state.next_index > total_seeds:
        return False
    if state.total_seeds != total_seeds:
        return False
    return state.input_path == str(input_path.resolve())


def prompt_full_or_resume(
    *,
    completed: int,
    total: int,
    next_human: int,
    checkpoint_file: Path,
) -> bool:
    """
    Ask the user whether to restart or resume.

    Returns True if user chose a full run (reset checkpoint), False to resume.
    """
    print(
        f"\nSeed checkpoint found ({checkpoint_file.name}): "
        f"{completed}/{total} seed row(s) finished. Next would be seed {next_human}/{total}.",
        flush=True,
    )
    print("  [1] Full run — restart from seed 1 (clears checkpoint)", flush=True)
    print("  [2] Resume — continue from last checkpoint", flush=True)
    while True:
        choice = input("Enter choice [1/2]: ").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("Please enter 1 or 2.", flush=True)


def explain_non_interactive_resume(*, next_human: int, total: int) -> None:
    print(
        f"Non-interactive terminal: resuming from seed {next_human}/{total} "
        "(use --fresh to restart from seed 1).",
        flush=True,
    )
