# Examples

Each script optionally takes the serial port path as its first
argument. If omitted, the script auto-detects an FTDI device using
`find_readers()`.

| Script | What it does |
|---|---|
| `01_read_tags_active.py` | Listens in active mode for 30 s, prints every tag broadcast |
| `02_inventory_once.py` | Single command-mode scan, prints found tags |
| `03_write_epc.py` | Re-programs a tag's 96-bit EPC ID (with safety checks) |
| `04_configure_reader.py` | First-time setup: region, power, mode, beep |

Run from the project root after `pip install -e .`:

```bash
python3 examples/01_read_tags_active.py
python3 examples/02_inventory_once.py /dev/ttyUSB0
python3 examples/03_write_epc.py /dev/ttyUSB0 E200123456789ABCDEF01122
python3 examples/04_configure_reader.py
```

Or with the CLI installed:

```bash
swrfid info
swrfid listen --start-read --seconds 30
swrfid inventory
swrfid write-epc E200123456789ABCDEF01122
```
