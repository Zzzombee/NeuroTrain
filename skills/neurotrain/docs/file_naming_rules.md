# File Naming Rules

## Light Files

```text
sorted_01_200light25_1,5,9.pl2
```

Parsed fields:

- `file_index`: `01`
- `file_id`: `test01` by default
- `light_on_s`: `200`
- `duration_s`: `25`
- `light_off_s`: `225`
- `sorted_channels`: `1,5,9`

## No-Light Files

```text
sorted_02_nolight_1,5,9.pl2
```

Parsed fields:

- `file_index`: `02`
- `file_id`: `test02` by default
- `has_light`: `no`
- `sorted_channels`: `1,5,9`

No-light files must not generate fake light events. They can keep full-session outputs and should skip light-aligned outputs.

## Channel List

The `sorted_channels` segment is saved in notes/metadata only. It is not used to select units or calculate firing rates.

