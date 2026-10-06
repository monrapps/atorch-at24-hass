"""Execute the actual SQLite query and native-card template with known histories."""
from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
import sys
import unittest

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import add_historical_projections as patcher
import add_monthly_estimate as instant
from test_monthly_estimate import fixture, is_number

SQL = (ROOT / "examples/lovelace/monthly_projections.sql").read_text()
NOW = 720 * 3600


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE statistics_meta (id INTEGER PRIMARY KEY, statistic_id TEXT,
                source TEXT, mean_type INTEGER, unit_of_measurement TEXT);
            CREATE TABLE statistics (metadata_id INTEGER, start_ts REAL, mean REAL,
                UNIQUE(metadata_id, start_ts));
            INSERT INTO statistics_meta VALUES
                (1, 'sensor.atorch_at24_power', 'recorder', 1, 'W'),
                (2, 'sensor.other_power', 'recorder', 1, 'W');
        """)

    def rows(self, values, first=0, meta=1):
        self.db.executemany("INSERT INTO statistics VALUES (?, ?, ?)",
                            [(meta, first + i * 3600, value) for i, value in enumerate(values)])

    def result(self, now=NOW):
        # Only replace the clock, not the SQL algorithm under test.
        query = SQL.replace("CAST(strftime('%s', 'now') AS INTEGER)", str(now))
        return dict(self.db.execute(query).fetchone())

    def test_constant_100_w_in_every_window(self):
        self.rows([100] * 720)
        result = self.result()
        for key, count in [('24h', 24), ('7d', 168), ('30d', 720)]:
            self.assertAlmostEqual(result['monthly_' + key], 72)
            self.assertEqual(result['buckets_' + key], count)
            self.assertEqual(result['coverage_' + key], 100)

    def test_windows_select_different_history(self):
        self.rows([300] * 552 + [200] * 144 + [100] * 24)
        result = self.result()
        self.assertAlmostEqual(result['monthly_24h'], 72)
        self.assertAlmostEqual(result['monthly_7d'], (200 * 144 + 100 * 24) / 168 * .72)
        self.assertAlmostEqual(result['monthly_30d'], (300 * 552 + 200 * 144 + 100 * 24) / 720 * .72)

    def test_time_weighted_recorded_means_not_sample_average(self):
        # A known upstream hour: 100 W for 15 min, 300 W for 45 min.
        # Recorder stores the duration-weighted 250 W, not sample mean 200 W.
        self.rows([(100 * 900 + 300 * 2700) / 3600] * 720)
        self.assertAlmostEqual(self.result()['monthly_24h'], 180)

    def test_partial_boundary_has_half_weight_and_current_hour_excluded(self):
        self.rows([100] * 720 + [999999])
        self.db.execute('UPDATE statistics SET mean=300 WHERE start_ts=?', (696 * 3600,))
        r = self.result(NOW + 1800)
        self.assertAlmostEqual(r['monthly_24h'], (300 * .5 + 100 * 23) / 23.5 * .72)
        self.assertAlmostEqual(r['coverage_24h'], 23.5 / 24 * 100)
        self.assertEqual(r['newest_bucket_end'], NOW)

    def test_missing_buckets_not_zero_and_insufficient_coverage(self):
        self.rows([100] * 22, first=NOW - 24 * 3600)
        r = self.result()
        self.assertAlmostEqual(r['monthly_24h'], 72)
        self.assertAlmostEqual(r['coverage_24h'], 22 / 24 * 100)
        self.assertIsNone(r['monthly_7d'])
        self.assertIsNone(r['monthly_30d'])
        self.db.execute('DELETE FROM statistics WHERE start_ts=(SELECT MIN(start_ts) FROM statistics)')
        self.assertIsNone(self.result()['monthly_24h'])

    def test_empty_and_all_invalid_history_is_unknown(self):
        for r in [self.result()]:
            for key in ['24h', '7d', '30d']:
                self.assertIsNone(r['monthly_' + key])
                self.assertEqual(r['coverage_' + key], 0)
        invalid = [None, 'unknown', 'unavailable', 'text', 'NaN', float('inf'), float('-inf')]
        self.rows(invalid * 100)
        self.assertIsNone(self.result()['monthly_30d'])
        self.assertEqual(self.result()['coverage_30d'], 0)

    def test_invalid_bucket_breaks_coverage_without_zero_or_forward_fill(self):
        self.rows([100] * 719 + ['unavailable'])
        r = self.result()
        self.assertAlmostEqual(r['monthly_24h'], 72)
        self.assertAlmostEqual(r['coverage_24h'], 23 / 24 * 100)
        self.assertEqual(r['buckets_24h'], 23)

    def test_kw_conversion_and_unknown_units(self):
        self.rows([.1] * 720)
        self.db.execute("UPDATE statistics_meta SET unit_of_measurement='kW' WHERE id=1")
        self.assertAlmostEqual(self.result()['monthly_30d'], 72)
        for unit in ['Wh', 'VA', None]:
            self.db.execute('UPDATE statistics_meta SET unit_of_measurement=? WHERE id=1', (unit,))
            self.assertIsNone(self.result()['monthly_30d'])

    def test_zero_is_real_measurement(self):
        self.rows([0] * 720)
        self.assertEqual(self.result()['monthly_24h'], 0)
        self.assertEqual(self.result()['coverage_30d'], 100)

    def test_entry_expiry_without_new_samples_and_recovery(self):
        self.rows([100] * 720)
        self.assertAlmostEqual(self.result()['monthly_24h'], 72)
        self.assertIsNone(self.result(NOW + 3 * 3600)['monthly_24h'])
        self.rows([200] * 3, first=NOW)
        self.assertAlmostEqual(self.result(NOW + 3 * 3600)['monthly_24h'], (21 * 100 + 3 * 200) / 24 * .72)
        r = self.result(NOW + 31 * 86400)
        self.assertEqual(r['coverage_30d'], 0)
        self.assertIsNone(r['monthly_30d'])

    def test_old_future_other_source_circular_and_unaligned_excluded(self):
        self.rows([100] * 720)
        self.rows([1e9], first=-3600)
        self.rows([1e9], first=NOW + 3600)
        self.rows([1e9], first=NOW - 2 * 3600 + 1)
        self.rows([1e9] * 720, meta=2)
        self.assertAlmostEqual(self.result()['monthly_30d'], 72)
        self.db.execute('UPDATE statistics_meta SET mean_type=2 WHERE id=1')
        self.assertIsNone(self.result()['monthly_24h'])

    def test_fractional_timestamps_are_not_aligned(self):
        self.rows([100] * 720, first=.5)
        r = self.result(NOW + 1800)
        self.assertEqual(r['buckets_30d'], 0)
        self.assertIsNone(r['monthly_30d'])

    def test_timestamp_index_has_both_range_bounds(self):
        plan = [row[3] for row in self.db.execute('EXPLAIN QUERY PLAN ' + SQL)]
        self.assertTrue(any('SEARCH s ' in row and 'metadata_id=?' in row
                            and 'start_ts>?' in row and 'start_ts<?' in row for row in plan), plan)

    def test_overflow_is_excluded(self):
        self.rows([1e308] * 720)
        self.db.execute("UPDATE statistics_meta SET unit_of_measurement='kW' WHERE id=1")
        self.assertIsNone(self.result()['monthly_30d'])


class CardTests(unittest.TestCase):
    def setUp(self):
        self.attrs = {'computed_at': NOW, 'method': 'recorded_hourly_statistics_v1',
                      'unit_of_measurement': 'kWh/mês'}
        for key in ['24h', '7d', '30d']:
            self.attrs.update({'monthly_' + key: 72, 'coverage_' + key: 99.9})
        self.power, self.power_unit, self.state = '100', 'W', '72'
        env = Environment(undefined=StrictUndefined)
        env.globals.update(
            states=lambda e: self.power if e == instant.SOURCE else self.state,
            state_attr=lambda e, a: self.power_unit if e == instant.SOURCE else self.attrs.get(a),
            is_number=is_number, now=lambda: datetime.fromtimestamp(NOW, timezone.utc),
            as_timestamp=lambda dt: dt.timestamp())
        self.card = patcher.load_card()
        self.template = env.from_string(self.card['content'])

    def test_four_lines_units_labels_and_explicit_limits(self):
        r = self.template.render()
        self.assertEqual(r.count('72,0 kWh/mês'), 4)
        for text in ['Base: potência atual', 'Base: últimas 24 h', 'Base: últimos 7 dias',
                     'Base: últimos 30 dias', 'mês de 30 dias', '24 h/dia', 'Resolução de 1 h',
                     '(parcial)', 'não disponibilidade verificada', 'leituras anteriores',
                     'não consumo histórico medido nem custo financeiro']:
            self.assertIn(text, r)
        self.assertEqual(self.card['entity_id'], [instant.SOURCE, 'sensor.atorch_monthly_projections'])

    def test_missing_stale_future_unavailable_and_wrong_method(self):
        cases = [('computed_at', None), ('computed_at', NOW-121), ('computed_at', NOW+1),
                 ('method', 'other'), ('unit_of_measurement', 'W')]
        for attr, value in cases:
            with self.subTest(attr=attr, value=value):
                original = self.attrs[attr]
                self.attrs[attr] = value
                self.assertEqual(self.template.render().count('Indisponível / dados insuficientes'), 3)
                self.attrs[attr] = original
        self.state = 'unavailable'
        self.assertEqual(self.template.render().count('Indisponível / dados insuficientes'), 3)

    def test_invalid_history_not_zero_and_recovers(self):
        for value in [None, 'unknown', 'unavailable', 'NaN', float('inf'), 'text']:
            self.attrs['monthly_7d'] = value
            self.assertEqual(self.template.render().count('Indisponível / dados insuficientes'), 1)
        self.attrs['monthly_7d'] = 72
        self.assertNotIn('Indisponível', self.template.render())
        self.attrs['coverage_7d'] = 89.99
        self.assertEqual(self.template.render().count('Indisponível / dados insuficientes'), 1)

    def test_unknown_24h_state_does_not_hide_valid_7d_and_30d(self):
        self.state, self.attrs['monthly_24h'] = 'unknown', None
        self.assertEqual(self.template.render().count('72,0 kWh/mês'), 3)

    def test_instant_power_failure_independent_of_history(self):
        self.power = 'unavailable'
        self.assertEqual(self.template.render().count('72,0 kWh/mês'), 3)
        self.power, self.power_unit = '.1', 'kW'
        self.assertEqual(self.template.render().count('72,0 kWh/mês'), 4)

    def test_partial_coverage_never_rounds_to_complete(self):
        self.attrs['coverage_7d'] = 99.99999
        self.assertIn('99,9% (parcial)', self.template.render())
        self.assertNotIn('100,0%', self.template.render())


class UpgradeTests(unittest.TestCase):
    def test_only_estimate_replaced_and_idempotent(self):
        before = instant.add_estimate(fixture())
        snapshot = deepcopy(before)
        after = patcher.upgrade(before)
        self.assertEqual(before, snapshot)
        self.assertEqual(patcher.upgrade(after), after)
        cards = after['views'][0]['cards'][1]['cards']
        self.assertEqual(cards[1], patcher.load_card())
        cards[1] = instant.load_card()
        self.assertEqual(after, before)

    def test_fresh_install_and_reordered_target(self):
        source = fixture()
        source['views'][0]['cards'].reverse()
        self.assertEqual(patcher.upgrade(source)['views'][0]['cards'][0]['cards'][1], patcher.load_card())

    def test_renamed_estimate_and_unrecognized_markdown_abort(self):
        for install in [instant.add_estimate, patcher.upgrade]:
            c = install(fixture())
            cards = c['views'][0]['cards'][1]['cards']
            cards[1]['content'] = cards[1]['content'].replace('Consumo mensal estimado', 'Projeções mensais')
            with self.assertRaises(ValueError):
                patcher.upgrade(c)
        c = fixture()
        c['views'][0]['cards'][1]['cards'].append({'type': 'markdown', 'content': 'Custom note'})
        with self.assertRaises(ValueError):
            patcher.upgrade(c)

    def test_edited_moved_duplicated_estimates_abort(self):
        for new in [False, True]:
            for alteration in ['edit', 'move', 'duplicate']:
                c = patcher.upgrade(fixture()) if new else instant.add_estimate(fixture())
                cards = c['views'][0]['cards'][1]['cards']
                if alteration == 'edit': cards[1]['content'] += 'manual change'
                elif alteration == 'move': cards.append(cards.pop(1))
                else: cards.append(deepcopy(cards[1]))
                with self.subTest(new=new, alteration=alteration), self.assertRaises(ValueError):
                    patcher.upgrade(c)


if __name__ == '__main__':
    unittest.main()
