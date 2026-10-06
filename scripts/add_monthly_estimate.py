#!/usr/bin/env python3
"""Patch an exported Lovelace JSON document locally; never contact Home Assistant."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import yaml

SOURCE = "sensor.atorch_at24_power"
LABEL = "Consumo mensal estimado"
CARD_FILE = Path(__file__).resolve().parents[1] / "examples/lovelace/server_room_monthly_estimate.yaml"


def load_card() -> dict:
    return yaml.safe_load(CARD_FILE.read_text(encoding="utf-8"))


def add_estimate(config: dict) -> dict:
    """Insert one native card, preserving all existing data; fail on ambiguity."""
    result = deepcopy(config)
    targets = []
    existing = []
    estimate = load_card()

    def visit(node, parent=None, index=None):
        if isinstance(node, dict):
            if node.get("type") == "markdown" and LABEL in node.get("content", ""):
                existing.append(node)
            entities = node.get("entities", [])
            if (node.get("type") == "entities"
                    and str(node.get("title", "")).casefold() == "server room"
                    and any((e.get("entity") if isinstance(e, dict) else e) == SOURCE
                            for e in entities)):
                targets.append((parent, index))
            for key, value in node.items():
                if key == "cards" and isinstance(value, list):
                    for i, card in enumerate(value):
                        visit(card, node, i)
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(result)
    if len(targets) != 1:
        raise ValueError(f"Expected exactly one Server Room entities card with {SOURCE}; found {len(targets)}")
    parent, index = targets[0]
    if not parent or parent.get("type") != "vertical-stack":
        raise ValueError("Server Room entities card must be directly inside a vertical-stack")
    cards = parent["cards"]
    if existing:
        if len(existing) == 1 and index + 1 < len(cards) and cards[index + 1] is existing[0] and existing[0] == estimate:
            return result
        raise ValueError("An estimate already exists with different content or placement; inspect manually")
    cards.insert(index + 1, estimate)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Private JSON export from lovelace/config")
    parser.add_argument("output", type=Path, help="New private JSON candidate (must not exist)")
    args = parser.parse_args()
    config = json.loads(args.source.read_text(encoding="utf-8"))
    candidate = add_estimate(config)
    # Refuse overwrite; exported dashboards can contain private information.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(candidate, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print("Candidate written locally. Review the diff before saving in Home Assistant.")


if __name__ == "__main__":
    main()
