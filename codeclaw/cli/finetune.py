"""Experimental fine-tune pipeline scaffolding (hidden preview)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def handle_finetune(args) -> None:
    """Prepare local fine-tuning scaffold for Unsloth/QLoRA experiments."""
    if not args.experimental:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Fine-tune command is experimental.",
                    "hint": "Run with --experimental to acknowledge preview status.",
                },
                indent=2,
            )
        )
        sys.exit(1)

    dataset_path = Path(args.dataset).resolve() if args.dataset else Path("codeclaw_share.jsonl").resolve()
    output_dir = Path(args.output).resolve() if args.output else Path(".codeclaw/fine_tune").resolve()

    payload = {
        "ok": True,
        "experimental": True,
        "dataset": str(dataset_path),
        "output_dir": str(output_dir),
        "pipeline": {
            "framework": "unsloth-qlora",
            "status": "scaffold_only",
            "notes": [
                "No training job launched by default.",
                "Use generated config to run local experiments.",
                "Intended for private, local testing only.",
            ],
        },
        "next_steps": [
            f"Ensure dataset exists at: {dataset_path}",
            "Install preview dependencies: pip install \"codeclaw[finetune]\"",
            "Run your local trainer with the generated config scaffold.",
        ],
    }
    print(json.dumps(payload, indent=2))
