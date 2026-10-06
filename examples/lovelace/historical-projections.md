# Monthly projections from recorded power statistics

This optional companion replaces the instantaneous-only Markdown card with four
lines in the same Server Room stack. It does not modify the AT24 integration,
BLE connection, the six existing measurements, or the voltage/power graphs.
The original example remains usable without a helper.

## Meaning and limitations

Every line projects **30 days × 24 hours**, not a different billing period:
`mean_W / 1000 * 24 * 30`. A constant 100 W means **72.0 kWh/month** in all
three historical windows. The instantaneous line retains its original W/kW
conversion and invalid-value handling, with the explicit label
“Base: potência atual”. The other labels describe the preceding rolling
24 hours, 7 days and 30 days; all boundaries are elapsed UTC seconds, not
calendar days or local DST boundaries.

The historical inputs are the recorder's **long-term hourly power means**.
They are not raw readings, measured energy, money, or the current power reused
as history. The query treats each recorded hourly mean as constant throughout
its hour, clips the first bucket to the requested window, and weights by that
overlap's duration. It divides by the duration of valid covered buckets, not
by the whole window. Absent, null, textual, non-finite, unsupported-unit,
non-arithmetic, future, misaligned and unfinished buckets do not contribute
zero or covered duration. The window ends at evaluation time: it is not shifted
back to hide the unfinished current hour.

This is exact for that **piecewise-constant reconstruction of hourly means**,
but an approximation to the original irregular readings. HA 2026.9.4 computes
the numeric five-minute means using elapsed-time/last-observation weighting,
then averages available five-minute means into hourly statistics. These stored
means do not retain the duration of valid sensor availability within a bucket.
The recorder can carry an earlier numeric reading across internal gaps; a
partially observed hour cannot be distinguished from a fully observed hour.
`mean_weight` is for circular statistics, **not availability duration**.

Consequently the displayed percentage means **coverage of recorded buckets**,
never verified device availability. The card says so explicitly, including
possible carried readings. Partial coverage is always labeled “parcial” and its
percentage is truncated rather than rounded up to 100%. An explicit conservative
policy requires at least **90% bucket coverage per window** to show a number;
otherwise it shows “Indisponível / dados insuficientes”. This threshold is a
quality guard, not a confidence interval or an accuracy guarantee. It cannot
repair internal gaps, a biased subset of history, or stale numeric readings
already recorded by a device integration.

If you require strict exclusion of every unavailable interval or a true
availability-weighted raw-data mean, **do not use these estimates for that
purpose**. An availability-aware recording scheme must collect that information
prospectively; purged raw data cannot be recovered from hourly means.

## Why not native Statistics helpers / repeated raw SQL scans?

The Statistics helper's `average_step` uses its raw-state buffer, not long-term
statistics, and raw recorder retention may not span 30 days after restart.
`mean` would also incorrectly give irregular samples equal weight. Reading the
raw history repeatedly can be prohibitively expensive for a BLE sensor that
reports multiple times per second. This query reads at most 721 aligned hourly
records for one indexed statistic. It does not scan the raw `states` table or
change recorder retention. Long-term statistics normally persist while raw
states and five-minute statistics normally have a shorter retention.

## Supported setup (tested on HA 2026.9.4 / SQLite)

Prerequisites:
- Recorder with SQLite supporting materialized CTEs (SQLite >= 3.35).
- Long-term arithmetic statistics for `sensor.atorch_at24_power`, recorded in
  W or kW. This SQL is deliberately SQLite-specific, not MariaDB/PostgreSQL.
- Native SQL integration; no custom frontend resources, scripts inside HA,
  additional packages, or new custom integration.

1. Settings → Devices & services → Add integration → SQL.
2. Name: `Atorch monthly projections`. Leave Database URL blank to use the
   configured recorder database, without copying its credentials.
3. Query: paste the full contents of [`monthly_projections.sql`](monthly_projections.sql).
4. Column: `monthly_24h`. Additional options → unit: `kWh/mês`.
   Do not select a device class or state class: this is a projection, not an
   energy meter for the Energy dashboard.
5. Verify the actual entity ID is `sensor.atorch_monthly_projections`. A name
   collision can produce a suffix; resolve that deliberately or adapt both
   references in the card. Do not blindly create duplicate helpers on retries.
6. Leave polling enabled (native default **30 seconds**). SQL runs on a DB
   executor, not the HA event loop. One sensor provides the three values and
   coverage attributes; the sensor's own state is the 24-hour projection.
   An `unknown` state because 24-hour coverage is insufficient does not suppress
   otherwise valid seven-/30-day attributes.
7. Verify `method=recorded_hourly_statistics_v1`, recent `computed_at`, all
   `monthly_*`/`coverage_*` attributes, `resolution_seconds=3600`, and
   `minimum_coverage=90`. Missing history yields null values, not zeros.
8. Export Overview privately using `lovelace/config` with `force=true`.
   Generate a candidate outside Git:

   ```sh
   python scripts/add_historical_projections.py /private/before.json /private/candidate.json
   ```

   The patcher finds the unique Server Room title + power entity, reuses the
   existing structural guards, and replaces only the exact versioned old card
   (or inserts on a fresh dashboard). Exact new-card reapplication is idempotent.
   It refuses edited, moved, duplicated or ambiguous cards, including renamed
   estimates. Conservatively, any unrecognized Markdown in the target Server
   Room stack requires manual inspection; unrelated cards elsewhere are kept.
   JSON output is
   create-only with mode 0600; dashboard exports must never enter this repo.
9. Check that replacing the new Markdown with the previous Markdown reproduces
   the complete prior dashboard. Immediately before `lovelace/config/save`,
   force-read again and require equality with the original snapshot. Abort on
   concurrent edits. Lovelace has no atomic compare-and-swap; avoid concurrent
   editors during this brief read/save interval. Read back the exact saved target.
10. Check all four lines and the existing measurements/graphs in Overview.

Config-flow creation and option edits load/reload **only SQL**. No Core restart,
template reload, BLE disconnection, or AT24 reload is necessary. A manual
`homeassistant.update_entity` targeting this helper can request an update;
normal polling also handles bucket entry, changing boundary overlap and expiry,
even when the power entity does not change. Backend aggregates arrive hourly;
polling cannot create more recent information than the recorder has compiled.

The Markdown listens to the source and helper, and `now()` also causes a minute
refresh. It rejects missing/wrong metadata, unavailable helpers and results older
than 120 seconds, so a failed SQL query retaining an old numeric state does not
silently keep a historical estimate visible forever. Instantaneous power remains
independent. Initial installation can temporarily show insufficient data until
the first helper update. No fake source-state injection is necessary for testing.

## Verification and rollback

Run:

```sh
python tests/test_parser.py
python -m unittest discover -s tests -p 'test_monthly_estimate.py' -v
python -m unittest discover -s tests -p 'test_historical_projections.py' -v
```

The SQLite tests execute the actual query with a controlled clock and synthetic
hourly tables; they cover distinct windows, known time-weighted means, partial
boundaries, missing/invalid data, insufficient coverage, expiry without samples,
recovery, true zero, unit conversion and overflow. Strict Jinja tests cover the
four labels, decimals, freshness, independent instantaneous availability and
coverage disclosure. These do not claim recovery of missing raw durations.

Rollback: restore only the exact former Markdown card after a fresh comparison,
then remove this SQL helper if unused elsewhere. Do not restore an entire old
dashboard over later edits. Preserve private snapshots and the created config
entry ID outside the public repo. This generic example contains no household
export, address, device MAC, token, or private configuration. No separate config
repository is required merely to install this fully reproducible generic helper.

References (implementation semantics pinned to the version above):
- https://www.home-assistant.io/integrations/sql/
- https://www.home-assistant.io/integrations/statistics/
- https://www.home-assistant.io/integrations/recorder/
- https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/sensor/recorder.py
- https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/recorder/statistics.py
