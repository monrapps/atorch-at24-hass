"""Offline template/patch tests. Live HA rendering is an additional validation."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("add_monthly_estimate", ROOT / "scripts/add_monthly_estimate.py")
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def is_number(value):
    # Mirror HA's finite-number guard without requiring the HA runtime locally.
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def fixture():
    # Synthetic fixture, not an export of a household dashboard.
    return {"views": [{"title": "Example", "cards": [
        {"type": "markdown", "content": "Unrelated"},
        {"type": "vertical-stack", "cards": [
            {"type": "entities", "title": "Server Room", "show_header_toggle": False,
             "entities": [{"entity": patcher.SOURCE, "name": "Potência"}, "sensor.example_voltage"]},
            {"type": "statistics-graph", "entities": ["sensor.example_voltage"]},
            {"type": "statistics-graph", "entities": [patcher.SOURCE]},
        ]},
    ]}]}


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.state, self.unit = "100", "W"
        env = Environment(undefined=StrictUndefined)
        env.globals.update(states=lambda entity: self.state,
                           state_attr=lambda entity, attr: self.unit,
                           is_number=is_number)
        self.card = patcher.load_card()
        self.template = env.from_string(self.card["content"])

    def test_native_card_and_explicit_subscription(self):
        self.assertEqual(self.card["type"], "markdown")
        self.assertEqual(self.card["entity_id"], [patcher.SOURCE])

    def test_conversion_and_precision(self):
        for state, unit, expected in [
            ("100", "W", "72,0"), ("0.1", "kW", "72,0"),
            ("0", "W", "0,0"), ("1587.8", "W", "1143,2"),
            ("200", "W", "144,0"), ("1e2", "W", "72,0"),
        ]:
            with self.subTest(state=state, unit=unit):
                self.state, self.unit = state, unit
                self.assertIn(f"{expected} kWh/mês", self.template.render())

    def test_invalid_is_unavailable_not_zero(self):
        for state in ["unknown", "unavailable", "text", "", None, "NaN", "inf", "-inf", "1e999"]:
            with self.subTest(state=state):
                self.state = state
                rendered = self.template.render()
                self.assertIn("Indisponível", rendered)
                self.assertNotIn("kWh/mês", rendered)

    def test_unsupported_units(self):
        for unit in [None, "", "Wh", "kWh", "MW"]:
            with self.subTest(unit=unit):
                self.unit = unit
                self.assertIn("Indisponível", self.template.render())

    def test_arithmetic_overflow(self):
        self.state, self.unit = "1e308", "kW"
        self.assertIn("Indisponível", self.template.render())

    def test_changed_power_and_recovery(self):
        for state, expected in [("100", "72,0"), ("200", "144,0"),
                                ("unavailable", "Indisponível"), ("50", "36,0")]:
            self.state = state
            self.assertIn(expected, self.template.render())

    def test_assumptions_are_visible(self):
        text = self.template.render()
        for expected in [patcher.LABEL, "Potência atual constante", "24 h/dia", "30 dias",
                         "não consumo histórico medido", "nem custo financeiro"]:
            self.assertIn(expected, text)


class PatchTests(unittest.TestCase):
    def test_only_one_insert_and_no_input_mutation(self):
        original = fixture()
        before = deepcopy(original)
        result = patcher.add_estimate(original)
        self.assertEqual(original, before)
        stack = result["views"][0]["cards"][1]["cards"]
        self.assertEqual(stack.pop(1), patcher.load_card())
        self.assertEqual(result, original)

    def test_idempotent(self):
        result = patcher.add_estimate(fixture())
        self.assertEqual(patcher.add_estimate(result), result)

    def test_title_is_case_insensitive_and_positions_not_hardcoded(self):
        config = fixture()
        config["views"][0]["cards"].reverse()
        config["views"][0]["cards"][0]["cards"][0]["title"] = "Server room"
        self.assertEqual(patcher.add_estimate(config)["views"][0]["cards"][0]["cards"][1], patcher.load_card())

    def test_string_entity_supported(self):
        config = fixture()
        config["views"][0]["cards"][1]["cards"][0]["entities"] = [patcher.SOURCE]
        patcher.add_estimate(config)

    def test_missing_duplicate_or_wrong_source_aborts(self):
        missing = fixture()
        missing["views"][0]["cards"].pop()
        duplicate = fixture()
        duplicate["views"].append(deepcopy(duplicate["views"][0]))
        wrong = fixture()
        wrong["views"][0]["cards"][1]["cards"][0]["entities"] = ["sensor.other"]
        for config in [missing, duplicate, wrong]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                patcher.add_estimate(config)

    def test_non_vertical_parent_aborts(self):
        config = fixture()
        config["views"][0]["cards"][1]["type"] = "horizontal-stack"
        with self.assertRaises(ValueError):
            patcher.add_estimate(config)

    def test_existing_modified_or_moved_estimate_aborts(self):
        modified = patcher.add_estimate(fixture())
        modified["views"][0]["cards"][1]["cards"][1]["content"] += "changed"
        moved = patcher.add_estimate(fixture())
        cards = moved["views"][0]["cards"][1]["cards"]
        cards.append(cards.pop(1))
        for config in [modified, moved]:
            with self.assertRaises(ValueError):
                patcher.add_estimate(config)

    def test_cli_private_output_and_no_overwrite(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.json", Path(directory) / "output.json"
            source.write_text(json.dumps(fixture()), encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/add_monthly_estimate.py"), str(source), str(output)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
