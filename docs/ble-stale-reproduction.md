# AT24 stale availability: executable characterization

## Scope and baseline

This reproduces an existing defect, **not a fix**. Passing characterization tests
mean the stale behavior is still present; they must be replaced/adapted to the
approved availability policy when implementing the fix. No production integration
code is changed by this work.

Baseline: `master` at `297075d86360741ad9b3d122b23c3a20be38abfa`, verified after
`git pull --ff-only origin master`, before creating isolated branch
`task/t_76f7a9a0-ble-stale-repro` (task `t_76f7a9a0`). The monthly-estimate
PR #1 is not in this baseline. The coordinator/sensor behavior predates that PR;
this is not a regression caused by the estimate.

The tests import the actual `const.py`, `parser.py`, `coordinator.py`, and
`sensor.py`. Only HA/BLE boundaries, the clock, task scheduling and a tiny state
publisher are doubles. Synthetic 36-byte packets are delivered as 20+16 byte
notifications through the registered callback, with the real parser and sensor
properties. No household address, endpoint or configuration is used.

## Run locally

From this branch's repository root, with Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements-ble-stale.txt
.venv/bin/python tests/test_parser.py
.venv/bin/python -W error tests/test_ble_stale.py -v
.venv/bin/python tests/test_ble_stale.py --trace
```

Package installation requires the package index; **test execution is offline**.
There is no need to install HA, Bleak, access Bluetooth, connect to an HA server,
or wait for real timers. The isolated loader restores `sys.modules` after each
scenario and closes unconsumed reconnect coroutines. The synthetic clock advances
by the sleep duration; its budget cancels the watchdog deterministically.

Executed on Python 3.11.15, Jinja2 3.1.6: existing parser suite **11 passed**;
new characterization suite **11 passed**, with warnings treated as errors.
The dedicated `BLE stale-data characterization` GitHub workflow runs both suites
and the trace. The JSON trace is generated from execution, not a captured device
session.

## Observed default behavior (`update_interval=0`)

| Stage | Connected | Power available | Native W | Published state (double) | Listener publications | Monthly rendering |
| --- | --- | --- | --- | --- | --- | --- |
| Startup | false | false | null | unavailable | 0 | Indisponível |
| Connected, no sample | true | false | null | unavailable | 0 | Indisponível |
| Valid synthetic 100 W | true | true | 100 | 100.0 | 1 | 72,0 kWh/mês |
| Unexpected disconnect | false | **true** | **100** | **100.0** | **1** | **72,0 kWh/mês** |
| Reconnected, no new sample | true | **true** | **100** | **100.0** | **1** | **72,0 kWh/mês** |
| New valid synthetic 200 W | true | true | 200 | 200.0 | 2 | 144,0 kWh/mês |

The disconnect test also compares availability/value pairs for **every sensor
description** before/after the disconnect in AC Full mode. Previously available
sensors remain available; mode-incompatible sensors remain unavailable.

Additional executable controls:

- Invalid notification type after reconnection leaves the old data, timestamp,
  availability and publication count unchanged.
- New valid DC-mode data makes the power entity unavailable (`native_value=None`),
  and the template displays `Indisponível`, not zero.
- Setting the coordinator's `last_update_success=False` does not affect the real
  sensor's `available` override.
- Watchdog at exactly 60 seconds does not reconnect (`elapsed > 60`, not `>=`).
  At a simulated 61 seconds it reconnects, but does not invalidate retained power
  or publish an unavailable transition.
- With a 60-second publication throttle, the first new 200 W sample after
  reconnection replaces `coordinator.data`/`native_value`, but the published double
  state stays 100 W (72,0 kWh/mês). A subsequent 300 W sample at the throttle
  boundary publishes and renders 216,0 kWh/mês. Merely reaching the boundary does
  not itself publish: an incoming valid packet triggers the check.
- `unknown`, `unavailable`, arbitrary text and nonfinite values render unavailable;
  a genuine numeric zero renders 0,0. The template is not confusing unavailable
  with zero; the problem is that its upstream input remains a valid-looking number.

## Availability and publication flow

Line references below refer to the baseline, not a future fix:

1. `coordinator.py:153–185`: BLE chunks reassemble into a complete packet. A
   successfully parsed packet refreshes `_last_data_time` and assigns `self.data`.
   Only the throttle condition calls `async_set_updated_data(parsed)`, the
   coordinator's push/listener path.
2. `sensor.py:202–207`: availability only checks `coordinator.data is not None`
   and whether the retained mode is supported. It neither calls the inherited
   coordinator availability property nor consults `connected` or sample age.
   `sensor.py:210–215` reads the value directly from the retained data.
3. `coordinator.py:139–145`: `_on_disconnect` clears `_client` and schedules
   `_reconnect` unless the disconnect is expected. It does **not** clear data,
   change a success flag, or notify listeners. Thus both the property and an
   already published state can stay available/numeric.
4. `coordinator.py:147–151,90–124`: `_reconnect` waits 5 seconds, then `_connect`
   subscribes and clears the partial buffer. `_connect` refreshes
   `_last_data_time` even without a valid new sample. It neither invalidates old
   data nor publishes a state transition. This timestamp is therefore not a
   reliable last-valid-sample timestamp across reconnections.
5. `coordinator.py:205–225`: the watchdog sleeps 60 seconds between checks and
   reconnects when the elapsed time is **strictly greater than** 60 seconds. It
   acts on the connection, not sensor availability; the silent-connection test
   confirms retained data after the reconnect. The BLE double calls the real
   disconnect callback during this path, which also queues `_reconnect`; queued
   tasks are closed at teardown, not raced in the test.
6. The next valid sample replaces the retained value. A nonzero throttle can
   still defer the listener publication, so a property read and the last published
   state can temporarily differ. This distinction must survive a future fix.

`const.py:77–83` defines a 60-second notification watchdog, 5-second reconnect
delay and zero/default publication throttle. These are **software constants**,
not evidence of a measured physical notification cadence. The protocol says
20+16 bytes per packet but does not establish a timing guarantee. No physical
cadence, packet loss distribution or timeout policy is established by this task;
the dependent policy task must make/justify that decision before a production fix.

## Projection provenance and limits

`tests/fixtures/monthly_estimate_pr1.jinja` is a frozen copy of the `content` scalar
from `examples/lovelace/server_room_monthly_estimate.yaml` in
[PR #1](https://github.com/monrapps/atorch-at24-hass/pull/1), commit
`0eeaa3abbd4fbb789381afd94fc6e05436d04a08`. Two-space YAML indentation and the final
newline are stripped to match the YAML `|-` scalar. SHA-256 of the UTF-8 fixture:

`78da073e3c94d3aa1c058579814e1fa9637eca54dcd9585f21e33fa3579f6b08`

The actual Jinja template is rendered, not a rewritten projection formula. Its
`states`, `state_attr` and finite-number guard are offline doubles, modeled on
that PR's own offline tests. The input is the last state published by the tiny
listener double. Re-rendering after disconnect still sees `100.0` W and returns
72,0 kWh/mês. In the actual frontend, absence of a state event can simply leave
the already rendered text unchanged as well.

This is a Markdown projection, not a newly available/unavailable HA sensor.
The template checks numeric validity and W/kW units; it has no independent BLE
connection/freshness information. It cannot distinguish the retained numeric
reading from a fresh one. Do not fix this by converting stale readings to zero.

Limitations: this is an executed unit-level reproduction plus code-flow tracing,
**not** a physical disconnect test, full HA state-machine/recorder integration
test, frontend rendering test or proof of real notification timing. The doubles
only implement the boundary contracts needed here, not HA internals, concurrent
reconnect races, event scheduling or hardware behavior. The loader mutates the
process-global module table: run harnesses sequentially, not in concurrent threads.
The projection double always supplies W; this suite does not validate kW/invalid
units or HA entity resolution. No hardware, production configuration or production
entity state was modified.

An independent read-only review repeated both suites (11 + 11 passing tests) and
the six-stage trace, confirmed execution of the real methods and exact fixture
provenance, and reported no blocking finding. This does not expand the limits above.

## Continuation

Policy work belongs to `t_a4cb2def`; the production fix and regression expectations
belong to its downstream implementation task. Preserve the disconnect/reconnect,
invalid-data, mode, watchdog boundary and throttle distinctions when replacing
these characterization assertions with the approved policy. The implementation
must be a separate PR; this branch only provides reproduction, documentation and
CI. Do not merge/deploy automatically.
