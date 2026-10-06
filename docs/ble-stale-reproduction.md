# AT24 BLE availability: reproduction and regression verification

## Scope and baseline

This branch fixes the pre-existing stale-availability defect in `master`
`297075d86360741ad9b3d122b23c3a20be38abfa`. Master was synchronized with
`git fetch origin` and `git pull --ff-only origin master` before the isolated
implementation worktree was created. Branch: `task/t_96111faf-ble-availability`.

The executable characterization from [PR #2](https://github.com/monrapps/atorch-at24-hass/pull/2),
commit `c0fb43f83abed625c1a137911c0ac39babe3a240`, was explicitly cherry-picked
before implementing the fix. Its 11 characterization tests and the existing
11 parser tests passed before production code was changed. The old characterization
expected the bug; this branch replaces it with assertions of the required behavior.
The historical reproduction and its documentation remain available at that SHA.

The monthly-estimate [PR #1](https://github.com/monrapps/atorch-at24-hass/pull/1)
is **not** a dependency of the production fix and is not included in the branch.
Only its frozen Jinja test fixture is reused. No dashboard, entity ID, unit,
precision, state class, parser/protocol, config flow or runtime dependency changes.
Production scope is `coordinator.py`, `sensor.py`, and `button.py` only.

## Adopted policy

- Start unavailable. A BLE connection is not a measurement.
- A sample is usable only while the current client is connected, the coordinator
  is not stopping, and a complete valid frame from this session was received less
  than 60 seconds ago. Coordinator success is also required by sensors/buttons.
- Use the existing `NOTIFICATION_TIMEOUT_S = 60` as a fixed monotonic TTL, with no
  new setting. Compare against the absolute deadline (`last_valid_at + 60`), so
  floating-point subtraction cannot rearm an already-due timer in a tight loop.
  Timestamp zero is valid; `None` means no usable observation.
- Disconnect invalidates and notifies listeners synchronously before scheduling
  reconnect. Reconnecting without a frame cannot restore cached values. First
  frame/recovery and mode changes publish immediately, regardless of throttle.
- Every accepted frame, including identical values and legitimate zero, renews
  the TTL. Partial chunks, rejected frames, ACKs, connections, publication,
  commands and manual refresh never renew it.
- A one-shot deadline publishes unavailable even without another BLE event.
  Entity availability checks the deadline as well, in case the event loop is late.
  Early callbacks rearm; superseded session/timer callbacks are inert.
- Retain `coordinator.data` only as an invalid private snapshot on loss. All 11
  sensors return `native_value=None` while unavailable. No synthetic zero, reset,
  interpolation, backfill or modification of existing recorder history.
- Mode restrictions are preserved: voltage/current/energy/temperature in 1/2/3;
  power/frequency/power-factor only in 1; charge in 2/3; USB voltages/on-time in 3.
- All three buttons require coordinator success and fresh data. The send routine
  also refuses a direct GATT write without a current mode. Command bytes remain
  unchanged; command tests use a fake client exclusively.
- Publication throttle (0–60 s) is separate from measurement TTL. A suppressed
  latest sample is flushed at the throttle deadline even if no next frame arrives,
  only while fresh. Publication never renews TTL. Expiry wins if deadlines coincide.
  An option change recalculates only the pending publication; immediate publication
  consumes it. Generation guards prevent an already-queued superseded flush from
  publishing twice. A coordinator/refresh error cannot be undone by that flush.
- Keep the 60-second transport watchdog and 5-second reconnect delay. There is one
  shared connection attempt and one scheduled reconnect; the latter rechecks its
  reason before disconnecting. Connection-start time is separate from sample time.
  TTL expiry is immediate at the deadline; physical recovery can wait for watchdog.
- Stop invalidates first, cancels timers/reconnect/watchdog/connection attempts,
  and drains them. A client returned after cancellation is closed, not installed.
  Cleanup tasks retain ownership even after a detached client's parent reconnect
  is cancelled; stop waits for actual close completion. Repeated stop is safe.
- BLE callbacks carry their session generation. Old disconnects cannot clear a new
  client; chunks cannot cross sessions. Metadata is installed before `start_notify`
  because frames may arrive during subscription. Failure invalidates and closes the
  partially installed client.

The policy uses the software's existing 60 s silence budget, not the configured
HA throttle or a claimed hardware measurement. Compatible protocol-family projects
report approximately 1 status frame/s:
[ESPHome example](https://github.com/syssi/esphome-atorch-dl24/blob/92bac948e39ce2c9aa569b88129ad9d830c1125b/esp32-ac-meter-example.yaml#L57-L63),
[Atorch protocol notes](https://github.com/tshaddack/dl24/blob/4dcab3d28a63a19268032feda86215532462f23f/README.md).
The [original reverse notes](https://github.com/devanlai/webvoltmeter/blob/44621efde8581408cdbac5f4567d7a78c61879e7/REVERSE.md)
define framing, not a cadence guarantee. No physical cadence was measured; legitimate
firmware gaps of 60 s or more would need separate evidence and policy review.
“Valid” means accepted by the existing parser, which does not verify notification
checksums; checksum/protocol changes are outside this fix.

## Reproduce the positive and negative controls

Python 3.11 or newer, from this branch's root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements-ble-stale.txt
.venv/bin/python -W error tests/test_parser.py
.venv/bin/python -W error tests/test_ble_stale.py -v
.venv/bin/python -W error tests/test_ble_stale.py --trace
.venv/bin/python -m compileall -q custom_components tests
git diff --check 297075d86360741ad9b3d122b23c3a20be38abfa HEAD

# Separate worktree: the current tests/doubles load unchanged baseline production.
git worktree add --detach ../atorch-baseline 297075d86360741ad9b3d122b23c3a20be38abfa
ATORCH_SOURCE_ROOT=../atorch-baseline .venv/bin/python -W error tests/check_baseline_regressions.py
```

Package installation needs the package index. Test execution is offline: no HA,
Bleak installation, Bluetooth adapter, household address or remote server needed.
There are no real sleeps; asyncio tasks/futures are real, and a controlled monotonic
scheduler supplies sleep completions and timer callbacks. Cancelled callbacks can
be deliberately invoked to simulate an already-queued callback. The module loader
restores `sys.modules`; harnesses must be run sequentially, not in concurrent threads.

Results executed locally: 11 parser tests and 38 availability tests passed with
warnings as errors. The negative-control runner verifies exactly seven assertion
failures and zero errors against unchanged master: P01, P04, P05, P06, P08, P20,
P34. A missing API, import failure or teardown exception is not accepted as evidence.
Its exit code 0 means the seven expected failures were demonstrated, not that the
baseline is correct. The same assertions pass on this branch. The CI workflow runs
both suites, trace, compile check and negative control on Python 3.11 and 3.14;
CI results must be read at the exact published SHA, not inferred from local tests.

## Coverage map (policy P01–P37)

Test method names carry the normative scenario IDs and expand variants via
subtests. There are 37 policy IDs and one additional cleanup-race regression P29b.

| IDs | Executed cases |
|---|---|
| P01–P03 | Initial 11 sensors/3 buttons, connect without data, first frame at t=0 with I=0/5/60 |
| P04–P07 | Immediate disconnect publication, reconnect/no frame, recovery within throttle, partial/invalid packets |
| P08–P11 | Exact 59.999/60/60.001 boundary, superseded deadline, same-session recovery, timer then watchdog |
| P12–P16 | 1 s cadence for 180 s at I=0/5/60; gaps 1/2.5/5/10/59.999 s; identical data; junk/ACKs; no first frame |
| P17–P19 | Immediate mode changes/all entities, disconnect and expiry in all modes/counter retention, valid zero and invalid projection input |
| P20–P24 | Last-sample flush, expiry precedence, cancelled timers, simultaneous frame/expiry in both orders, early and delayed callbacks |
| P25–P28 | Cross-session chunks, old client callbacks, frame during successful/failed subscription, single reconnect under overlapping triggers |
| P29, P29b | Stop drains tasks/timers, idempotence, stop during suspended stop-notify and disconnect, no connected client left behind |
| P30–P31 | Late connector return after stop, establish/subscribe failure then recovery |
| P32–P34 | Option changes including zero, buttons/direct GATT guard and unchanged command format, coordinator errors respected |
| P35–P37 | Civil-clock jumps, superseded flush in the same BLE session, actual public manual-refresh path and next-frame recovery |

Independent read-only review repeated parser/regression tests and baseline controls.
It found a cancellation race during detached-client cleanup. P29b first failed with
`stop abandoned pending BLE close`; tracked, shielded cleanup plus explicit stop
draining fixed it. P29b covers both BLE close suspension points and asserts actual
client disconnection, not merely that tasks received cancel(). A second read-only
review reran all tests and baseline controls, verified the repaired cleanup in
additional cancellation scenarios, and found no remaining blocker in that scope.
This expands the original doubles, whose BLE close methods never suspended.

The pre-existing `_update_method` method is not the HA `_async_update_data` hook;
manual refresh is not a polling acquisition path. P37 invokes `async_request_refresh`
through a boundary double of HA's default hook/error behavior: it cannot restore
invalid cache, and a valid current frame recovers immediately. This change does
not claim to fix the unrelated manual-refresh hook or add polling.

## Projection trace and fixture provenance

The executed trace feeds the real Jinja template from the state published by a
listener double, not directly from a Python sensor property:

| Stage | Published power | Projection |
|---|---|---|
| Startup / connected without data | unavailable | Indisponível |
| Valid 100 W | 100.0 | 72,0 kWh/mês |
| Disconnected | unavailable | Indisponível |
| Reconnected without frame | unavailable | Indisponível |
| New 200 W | 200.0 | 144,0 kWh/mês |
| Deadline reached | unavailable | Indisponível |
| New zero sample | 0.0 | 0,0 kWh/mês |

`tests/fixtures/monthly_estimate_pr1.jinja` is the unchanged `content` scalar from
`examples/lovelace/server_room_monthly_estimate.yaml` at PR #1 commit
`0eeaa3abbd4fbb789381afd94fc6e05436d04a08`. YAML indentation and the final newline
are stripped to match `|-`. Fixture SHA-256:
`78da073e3c94d3aa1c058579814e1fa9637eca54dcd9585f21e33fa3579f6b08`.
The fixture does not deploy or import the monthly-estimate PR.

## Limits and delivery

These are executed unit tests of real coordinator/parser/sensor/button code with
HA/BLE boundaries replaced, not physical BLE, a full HA runtime/state-machine,
recorder, frontend or firmware validation. The state publisher, refresh/error
contract, template helpers and entity resolution are doubles. Template tests supply
W units. No claim about kW conversions, HA version compatibility, recorder storage,
real-time guarantees or actual device sampling accuracy is made. BLE cleanup still
depends on the underlying transport eventually completing or raising; the doubles
test controlled suspension/cancellation, not a permanently hung Bluetooth stack.

No household values, private endpoints, production configuration or hardware were
used. No merge, auto-merge or deployment is part of this delivery. The implementation
PR is separate from PR #1; downstream task `t_c900cd22` re-verifies it in a fresh run.
