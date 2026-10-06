# Server Room: consumo mensal estimado

This optional Lovelace example projects the **current power** over a fixed
30-day month. It is not historical energy, an Energy Dashboard total, or a bill.
The integration and its BLE measurements are unchanged.

## Source and calculation

Confirm the entity in Developer Tools → States before installing. The target
installation uses `sensor.atorch_at24_power`, unit `W`, device class `power`,
state class `measurement`. The integration's `sensor.py` exposes `d.power` in
watts; this is not the accumulated `energy` sensor.

```
W / 1000 × 24 hours/day × 30 days = kWh/month
100 W → 72.0 kWh/month
```

The card also accepts `kW` (no division by 1000). Other/missing units,
`unknown`, `unavailable`, nonnumeric and nonfinite values display
`Indisponível`, not zero. Real zero watts displays `0,0 kWh/mês`.
A single decimal and Portuguese decimal separator keep the result readable.

## Add to the existing block

Overview is a storage dashboard. Its Server Room block is a native
`vertical-stack`: an `entities` card headed `Server Room`, followed by voltage
and power statistics graphs. Insert the contents of
[`server_room_monthly_estimate.yaml`](server_room_monthly_estimate.yaml) as a
new card **immediately after the entities card, within that same stack**.
Keep the existing six measurements, title, options and graphs unchanged.
Do not paste this fragment over the entire Overview configuration.

The result is a native Markdown card inside the existing Server Room block,
not a new sensor or a custom entities row. It displays:

> **Consumo mensal estimado:** 72,0 kWh/mês
>
> Potência atual constante, 24 h/dia, mês de 30 dias.
> Projeção, não consumo histórico medido nem custo financeiro.

The card's explicit `entity_id` subscription rerenders when power or its unit
changes. No custom frontend resources, HACS update, template-sensor reload,
Core restart or recorder/energy-statistics changes are necessary. Save the
storage dashboard; reload the browser if an already-open view is stale.

For another meter, replace **both** template references and the `entity_id`
subscription. The patch utility intentionally targets only the confirmed
entity/title above, not arbitrary devices.

### Optional offline patch utility

Python 3.10+ and PyYAML are required. The repository has no prior dashboard
configuration convention, so this example stays separate from integration code.

```
python -m pip install -r tests/requirements-monthly-estimate.txt
python scripts/add_monthly_estimate.py /private/overview-before.json /private/overview-candidate.json
```

Supply the JSON **configuration object** returned by `lovelace/config`, not
its WebSocket envelope or `.storage` file wrapper. The utility is offline:
it does not authenticate, fetch or save anything to Home Assistant. It
locates a unique Server Room entities card by title plus power entity,
requires its direct parent to be a vertical stack, and inserts one card.
An identical existing insertion is a no-op; ambiguous targets or a conflicting
estimate abort. Conflict detection recognizes the Portuguese label
`Consumo mensal estimado`; if that label was manually renamed, inspect/remove
the previous estimate before rerunning to avoid a duplicate. The source is
never modified, and the output is create-only,
mode `0600`. Keep exports and backups outside this public repository.

Before any live save, back up the current config, compare the candidate with
the backup, and re-fetch Overview to ensure nobody edited it in the meantime.
Lovelace's API has no atomic compare-and-swap: perform the save without other
concurrent dashboard editors. Read back `lovelace/config` with `force: true`
after saving and compare it against the candidate. To undo, remove only this
Markdown child; do not restore a stale full backup over somebody else's edits.

## Validation

```
python tests/test_parser.py
python -m unittest discover -s tests -p 'test_monthly_estimate.py' -v
```

Offline tests cover conversion, precision, invalid/unavailable inputs, unit
changes, value changes/recovery, overflow, unchanged unrelated configuration,
idempotency, ambiguous targets, placement, output permissions and no-overwrite.
Jinja tests emulate HA's `is_number` finite guard; they are not a full HA runtime.

For runtime validation, render the exact `content` in HA Developer Tools →
Template or through `render_template` on its WebSocket API. A subscribed
`render_template` should emit a new result when the source changes naturally;
do not overwrite the real sensor state merely to test this card. Check the
card in both desktop and mobile Overview and, when the source genuinely loses
availability, confirm `Indisponível` is displayed. Record any access/testing
limitations in the PR instead of claiming a visual inspection occurred.

References:
- https://www.home-assistant.io/dashboards/markdown/
- https://www.home-assistant.io/docs/configuration/templating/
