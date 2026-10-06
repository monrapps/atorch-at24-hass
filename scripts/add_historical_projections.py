#!/usr/bin/env python3
"""Offline, fail-closed upgrade of the previously versioned estimate card."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import yaml
from add_monthly_estimate import add_estimate, load_card as instant_card

CARD_FILE = Path(__file__).resolve().parents[1] / "examples/lovelace/server_room_historical_projections.yaml"


def load_card() -> dict:
    return yaml.safe_load(CARD_FILE.read_text(encoding="utf-8"))


def upgrade(config: dict) -> dict:
    """Accept only the exact old/new card, unique target and correct placement."""
    old, new = instant_card(), load_card()
    result = deepcopy(config)

    def replace(node, source, target):
        if isinstance(node, dict):
            for value in node.values():
                replace(value, source, target)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                if value == source:
                    node[i] = deepcopy(target)
                else:
                    replace(value, source, target)

    def reject_unrecognized_markdown(node):
        if isinstance(node, dict):
            cards = node.get("cards", [])
            has_target = any(
                isinstance(card, dict) and card.get("type") == "entities"
                and str(card.get("title", "")).casefold() == "server room"
                and any((e.get("entity") if isinstance(e, dict) else e) == "sensor.atorch_at24_power"
                        for e in card.get("entities", []))
                for card in cards
            )
            if has_target and any(
                isinstance(card, dict) and card.get("type") == "markdown"
                and card not in (old, new) for card in cards
            ):
                raise ValueError("Unrecognized Markdown in Server Room; inspect manually before upgrading")
            for value in node.values():
                reject_unrecognized_markdown(value)
        elif isinstance(node, list):
            for item in node:
                reject_unrecognized_markdown(item)

    # Do not identify an edited estimate solely by its user-visible title.
    reject_unrecognized_markdown(result)
    # Normalize only an exact new card before reusing the old patcher's guards.
    replace(result, new, old)
    result = add_estimate(result)
    replace(result, old, new)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = upgrade(json.loads(args.source.read_text(encoding="utf-8")))
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print("Private candidate created; compare with a fresh export before saving.")


if __name__ == "__main__":
    main()
