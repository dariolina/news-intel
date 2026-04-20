#!/usr/bin/env python3
"""
One-off Responses API call to inspect the full payload (status, usage, output items).
Run from repo root with venv active:

  source venv/bin/activate
  python3 debug_openai_responses.py
  python3 debug_openai_responses.py --max-output-tokens 1024
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

# Load .env before importing scorer (YOUR_CONTEXT, etc.)
load_dotenv()

from scorer import SYSTEM_PROMPT, _extract_response_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug OpenAI Responses API payload")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=256,
        help="max_output_tokens (default 256 to reproduce tight-budget issues)",
    )
    args = parser.parse_args()

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 1

    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=api_key)

    user_message = (
        "Source: Example\n"
        "Title: NIST announces post-quantum cryptography standards update\n"
        "Snippet: Draft standards for lattice-based signatures are under public review."
    )

    print("model:", model)
    print("max_output_tokens:", args.max_output_tokens)
    print()

    resp = client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=user_message,
        max_output_tokens=args.max_output_tokens,
    )

    print("status:", getattr(resp, "status", None))
    print("error:", getattr(resp, "error", None))
    print("incomplete_details:", getattr(resp, "incomplete_details", None))
    print("usage:", getattr(resp, "usage", None))
    print()
    print("output_text (SDK helper):", repr(resp.output_text))
    print("_extract_response_text:", repr(_extract_response_text(resp)))
    print()
    print("--- output items (json) ---")
    out = getattr(resp, "output", None) or []
    print(json.dumps([item.model_dump() for item in out], indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
