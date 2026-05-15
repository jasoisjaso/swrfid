"""
swrfid.gui — minimal cross-platform tkinter GUI for the SW UHF RFID
reader family.

Launch via the ``swrfid-gui`` console script (installed by pip), or via
``python3 -m swrfid.gui``. No external GUI dependencies — uses tkinter
which ships with Python on Windows and macOS, and is one apt-install
away on Debian / Ubuntu (``sudo apt install python3-tk``).

Tools menu:
  - Diagnose Connection  — runs the swrfid.diagnose checklist
  - Calibrate Power      — sweeps RF power against a reference tag
  - Decode EPC...        — paste an EPC, see scheme + fields
  - MQTT Bridge...       — forward tag reads to an MQTT broker (HA / Node-RED)

Threading: the active-mode listener runs in its own daemon thread via
:meth:`RFIDReader.start_active_listener`. Tag events are put on a
queue and drained by a tk ``after`` timer on the main thread — the
standard pattern for keeping the UI responsive without touching tk
widgets from a worker thread.
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Optional

from . import __version__
from . import epc as epc_mod
from .protocol import (
    REGION_FREQ, RF_POWER_MAX, MemBank, ParamAddr, WorkMode,
)
from .reader import RFIDReader, ReaderError
from .ports import find_readers, list_serial_ports


def _require_tk():
    try:
        import tkinter  # noqa: F401
        from tkinter import ttk, messagebox, simpledialog  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "tkinter is not available.\n"
            "  Linux:   sudo apt install python3-tk\n"
            "  Windows: tkinter ships with python.org installers — "
            "reinstall Python and tick 'tcl/tk and IDLE' in the wizard\n"
            "  macOS:   install Python from https://python.org "
            "(the system Python on macOS sometimes lacks tk)\n"
        )
        sys.exit(1)


def _paho_available() -> bool:
    try:
        import paho.mqtt.client  # noqa: F401
        return True
    except ImportError:
        return False


class SWRFIDApp:
    """Single-window tkinter app for SW UHF RFID readers."""

    POLL_MS = 50          # how often the main loop drains tag events

    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title('swrfid %s — UHF RFID reader' % __version__)
        self.root.geometry('860x640')
        self.root.minsize(720, 520)

        self.reader: Optional[RFIDReader] = None
        self.tag_queue: "queue.Queue" = queue.Queue()
        self.tag_counts: dict = {}   # epc_hex -> {ant, rssi, count, scheme}
        self.listening = False

        self._build_menu()
        self._build_ui()
        self._refresh_ports()
        self._set_connected(False)

        self.root.after(self.POLL_MS, self._drain_tag_queue)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu(self):
        tk = self.tk
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label='Quit', command=self._on_close)
        menubar.add_cascade(label='File', menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label='Diagnose Connection',
                               command=self._on_diagnose)
        tools_menu.add_command(label='Calibrate Power...',
                               command=self._on_calibrate)
        tools_menu.add_separator()
        tools_menu.add_command(label='Decode EPC...',
                               command=self._on_decode_epc)
        tools_menu.add_separator()
        mqtt_label = 'MQTT Bridge...' if _paho_available() else 'MQTT Bridge...  (install paho-mqtt)'
        tools_menu.add_command(label=mqtt_label, command=self._on_mqtt_bridge)
        menubar.add_cascade(label='Tools', menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label='About', command=self._on_about)
        menubar.add_cascade(label='Help', menu=help_menu)

    # ------------------------------------------------------------------
    # Main UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        # Port row
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='x')

        ttk.Label(top, text='Port:').pack(side='left')
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=22)
        self.port_combo.pack(side='left', padx=4)
        ttk.Button(top, text='Refresh', command=self._refresh_ports).pack(side='left')

        ttk.Label(top, text='Baud:').pack(side='left', padx=(8, 2))
        self.baud_var = tk.StringVar(value='115200')
        ttk.Combobox(
            top, textvariable=self.baud_var,
            values=['9600', '19200', '38400', '57600', '115200'],
            width=8, state='readonly',
        ).pack(side='left')

        self.connect_btn = ttk.Button(
            top, text='Connect', command=self._toggle_connect,
        )
        self.connect_btn.pack(side='left', padx=8)
        self.info_label = ttk.Label(top, text='', foreground='#555')
        self.info_label.pack(side='left', padx=8)

        # Action row
        action = ttk.Frame(self.root, padding=(8, 0))
        action.pack(fill='x')

        self.inv_btn = ttk.Button(
            action, text='Inventory (one-shot)', command=self._on_inventory,
        )
        self.inv_btn.pack(side='left')
        self.listen_btn = ttk.Button(
            action, text='Start Listening', command=self._toggle_listen,
        )
        self.listen_btn.pack(side='left', padx=4)
        ttk.Button(action, text='Clear table', command=self._on_clear).pack(side='left', padx=4)
        ttk.Button(action, text='Write EPC...', command=self._on_write_epc).pack(side='left', padx=4)
        self.stats_label = ttk.Label(action, text='0 unique tags', foreground='#555')
        self.stats_label.pack(side='right')

        # Tag table
        table_frame = ttk.Frame(self.root, padding=(8, 4))
        table_frame.pack(fill='both', expand=True)

        cols = ('epc', 'fmt', 'ant', 'rssi', 'count')
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show='headings', selectmode='browse',
        )
        self.tree.heading('epc', text='EPC')
        self.tree.heading('fmt', text='Format')
        self.tree.heading('ant', text='Ant')
        self.tree.heading('rssi', text='RSSI')
        self.tree.heading('count', text='Reads')
        self.tree.column('epc', width=320, anchor='w')
        self.tree.column('fmt', width=90, anchor='w')
        self.tree.column('ant', width=50, anchor='center')
        self.tree.column('rssi', width=70, anchor='center')
        self.tree.column('count', width=70, anchor='e')
        self.tree.bind('<Double-1>', self._on_tag_double_click)

        ysb = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y')

        # Settings panel
        settings = ttk.LabelFrame(self.root, text='Reader settings', padding=8)
        settings.pack(fill='x', padx=8, pady=4)

        # Row 1: Region + Mode
        ttk.Label(settings, text='Region:').grid(row=0, column=0, sticky='w', padx=2)
        self.region_var = tk.StringVar(value='US')
        ttk.Combobox(
            settings, textvariable=self.region_var,
            values=sorted(REGION_FREQ.keys()),
            width=8, state='readonly',
        ).grid(row=0, column=1, sticky='w', padx=2)

        ttk.Label(settings, text='Work mode:').grid(row=0, column=2, sticky='w', padx=8)
        self.mode_var = tk.StringVar(value='ANSWER')
        ttk.Combobox(
            settings, textvariable=self.mode_var,
            values=[m.name for m in WorkMode],
            width=10, state='readonly',
        ).grid(row=0, column=3, sticky='w', padx=2)

        # Row 2: Power
        ttk.Label(settings, text='RF power:').grid(row=1, column=0, sticky='w', padx=2, pady=4)
        self.power_var = tk.IntVar(value=0x14)
        ttk.Scale(
            settings, from_=0, to=0x1E, variable=self.power_var,
            orient='horizontal', length=300,
            command=lambda v: self.power_label.config(
                text='0x%02X (%d)' % (int(float(v)), int(float(v)))
            ),
        ).grid(row=1, column=1, columnspan=2, sticky='ew', padx=2)
        self.power_label = ttk.Label(settings, text='0x14 (20)', width=12)
        self.power_label.grid(row=1, column=3, sticky='w')

        # Row 3: Dedup window
        ttk.Label(settings, text='Dedup window:').grid(row=2, column=0, sticky='w', padx=2, pady=2)
        self.dedup_var = tk.DoubleVar(value=1.0)
        ttk.Scale(
            settings, from_=0.0, to=5.0, variable=self.dedup_var,
            orient='horizontal', length=300,
            command=lambda v: self.dedup_label.config(
                text=('off' if float(v) < 0.05 else '%.1f s' % float(v))
            ),
        ).grid(row=2, column=1, columnspan=2, sticky='ew', padx=2)
        self.dedup_label = ttk.Label(settings, text='1.0 s', width=12)
        self.dedup_label.grid(row=2, column=3, sticky='w')

        # Row 4: RSSI min
        ttk.Label(settings, text='RSSI minimum:').grid(row=3, column=0, sticky='w', padx=2, pady=2)
        self.rssi_min_var = tk.IntVar(value=0)
        ttk.Scale(
            settings, from_=0, to=0xFF, variable=self.rssi_min_var,
            orient='horizontal', length=300,
            command=lambda v: self.rssi_min_label.config(
                text='0x%02X' % int(float(v))
                if int(float(v)) > 0 else 'off'
            ),
        ).grid(row=3, column=1, columnspan=2, sticky='ew', padx=2)
        self.rssi_min_label = ttk.Label(settings, text='off', width=12)
        self.rssi_min_label.grid(row=3, column=3, sticky='w')

        # Apply
        self.apply_btn = ttk.Button(
            settings, text='Apply settings', command=self._on_apply,
        )
        self.apply_btn.grid(row=4, column=0, columnspan=4, pady=6)

        settings.columnconfigure(1, weight=1)

        # Status bar
        self.status_var = tk.StringVar(value='Disconnected.')
        ttk.Label(
            self.root, textvariable=self.status_var, anchor='w',
            relief='sunken', padding=4,
        ).pack(fill='x', side='bottom')

    # ------------------------------------------------------------------
    # Port discovery
    # ------------------------------------------------------------------

    def _refresh_ports(self):
        ports = list_serial_ports()
        ftdi = [p.device for p in ports if p.is_ftdi]
        other = [p.device for p in ports if not p.is_ftdi]
        values = ftdi + other
        self.port_combo['values'] = values
        if values and not self.port_var.get():
            self.port_var.set(values[0])
        if not values:
            self._status('No serial ports found. Plug in the reader and click Refresh.')

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _toggle_connect(self):
        if self.reader is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        port = self.port_var.get().strip()
        if not port:
            self._error('Pick a port first (or click Refresh).')
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            self._error('Invalid baud rate: %r' % self.baud_var.get())
            return

        # Open the port. Port-level failures (permission denied, doesn't
        # exist, held by another process) are real errors and abort.
        r = RFIDReader(port, baud=baud, timeout=2.0)
        try:
            r.open()
        except ReaderError as exc:
            self._error('Could not open port:\n\n%s' % exc)
            return
        except Exception as exc:
            self._error('Unexpected error opening port:\n\n%s' % exc)
            return

        # If the reader is in active-broadcast mode (the documented default
        # on RU5305), it can be slow to acknowledge a command while emitting
        # tag frames. Send STOP_READ first — best-effort, ignore failure —
        # so any subsequent probe lands in a quiet bus.
        try:
            r.stop_read()
        except Exception:
            pass

        # system_info is INFORMATIONAL. If the reader is silent (wrong baud,
        # wrong addr, firmware quirk) we still keep the port open and let
        # the user drive the GUI — Inventory / Listen / settings will all
        # still work; the SN/version label just stays empty.
        info = None
        try:
            info = r.system_info()
        except Exception:
            pass

        self.reader = r
        self._set_connected(True)
        if info is not None:
            self.info_label.config(
                text='SN %s · sw=%s · hw=%s'
                % (info.serial_hex, info.soft_version, info.hard_version)
            )
            self._status('Connected to %s at %d baud.' % (port, baud))
        else:
            self.info_label.config(
                text='(reader silent — try other baud, or use anyway)',
                foreground='#b45309',
            )
            self._status(
                'Port open at %d baud, but the reader did not respond to '
                'system info. Inventory / Listen may still work. If not, '
                'try a different baud rate or Tools → Diagnose.' % baud,
            )

        # Try to sync settings — silent failure is fine, the sliders just
        # stay at their defaults.
        self._sync_settings_from_reader()

    def _disconnect(self):
        if self.listening:
            self._toggle_listen()
        try:
            if self.reader is not None:
                self.reader.close()
        except Exception:
            pass
        self.reader = None
        # Reset the info label to its default (grey, blank) so a previous
        # warning colour doesn't bleed into the next session.
        self.info_label.config(text='', foreground='#555')
        self._set_connected(False)
        self._status('Disconnected.')

    def _set_connected(self, ok: bool):
        self.connect_btn.config(text='Disconnect' if ok else 'Connect')
        state = 'normal' if ok else 'disabled'
        for w in (self.inv_btn, self.listen_btn, self.apply_btn):
            w.config(state=state)

    def _sync_settings_from_reader(self):
        if self.reader is None:
            return
        try:
            mode = self.reader.read_one_param(ParamAddr.WORK_MODE)
            power = self.reader.read_one_param(ParamAddr.RF_POWER)
            n1, n2 = self.reader.read_freq_bytes()
        except Exception:
            return
        try:
            self.mode_var.set(WorkMode(mode).name)
        except ValueError:
            pass
        self.power_var.set(power)
        self.power_label.config(text='0x%02X (%d)' % (power, power))
        for region, key in REGION_FREQ.items():
            if key == (n1, n2):
                self.region_var.set(region)
                break

    # ------------------------------------------------------------------
    # Inventory + listen
    # ------------------------------------------------------------------

    def _on_inventory(self):
        if self.reader is None:
            return
        try:
            tags = self.reader.inventory()
        except ReaderError as exc:
            self._error('Inventory failed: %s' % exc)
            return
        for t in tags:
            self.tag_queue.put(t)
        self._status('Inventory: found %d tag(s).' % len(tags))

    def _toggle_listen(self):
        if self.reader is None:
            return
        if not self.listening:
            try:
                self.reader.start_active_listener(
                    self._listener_cb,
                    dedup_window=float(self.dedup_var.get()),
                    rssi_min=int(self.rssi_min_var.get()),
                )
                self.reader.start_read()
            except ReaderError as exc:
                self._error('Start listening failed: %s' % exc)
                return
            self.listening = True
            self.listen_btn.config(text='Stop Listening')
            self.inv_btn.config(state='disabled')
            dedup = float(self.dedup_var.get())
            rssi = int(self.rssi_min_var.get())
            filters = []
            if dedup > 0:
                filters.append('dedup %.1fs' % dedup)
            if rssi > 0:
                filters.append('RSSI >= 0x%02X' % rssi)
            extra = (' [' + ', '.join(filters) + ']') if filters else ''
            self._status('Listening in active mode%s.' % extra)
        else:
            try:
                self.reader.stop_read()
            except Exception:
                pass
            self.reader.stop_active_listener()
            self.listening = False
            self.listen_btn.config(text='Start Listening')
            self.inv_btn.config(state='normal')
            self._status('Stopped listening.')

    def _listener_cb(self, dev_sn, tags):
        # Called from the listener thread — only push to the queue here.
        for t in tags:
            self.tag_queue.put(t)

    # ------------------------------------------------------------------
    # Tag-queue drain (runs on main thread)
    # ------------------------------------------------------------------

    def _drain_tag_queue(self):
        try:
            drained = 0
            while True:
                try:
                    t = self.tag_queue.get_nowait()
                except queue.Empty:
                    break
                self._upsert_tag(t)
                drained += 1
                if drained >= 200:
                    break
            if drained:
                self._update_stats()
        finally:
            self.root.after(self.POLL_MS, self._drain_tag_queue)

    def _upsert_tag(self, t):
        key = t.epc_hex
        entry = self.tag_counts.get(key)
        if entry is None:
            scheme = epc_mod.identify(t.epc)
            entry = {
                'ant': t.antenna, 'rssi': t.rssi, 'count': 0,
                'scheme': scheme,
            }
            self.tag_counts[key] = entry
        entry['ant'] = t.antenna
        entry['rssi'] = t.rssi
        entry['count'] += 1

        scheme = entry['scheme']
        fmt = scheme.upper() if scheme != 'unknown' else '—'

        if self.tree.exists(key):
            self.tree.item(key, values=(key, fmt, entry['ant'],
                                        '0x%02X' % entry['rssi'],
                                        entry['count']))
        else:
            self.tree.insert(
                '', 'end', iid=key,
                values=(key, fmt, entry['ant'],
                        '0x%02X' % entry['rssi'], entry['count']),
            )

    def _update_stats(self):
        self.stats_label.config(text='%d unique tags' % len(self.tag_counts))

    def _on_clear(self):
        self.tag_counts.clear()
        for child in self.tree.get_children():
            self.tree.delete(child)
        self._update_stats()

    # ------------------------------------------------------------------
    # Settings apply
    # ------------------------------------------------------------------

    def _on_apply(self):
        if self.reader is None:
            return
        try:
            self.reader.set_region(self.region_var.get())
            self.reader.set_rf_power(int(self.power_var.get()))
            self.reader.set_work_mode(WorkMode[self.mode_var.get()])
        except ReaderError as exc:
            self._error('Apply failed: %s' % exc)
            return
        except Exception as exc:
            self._error('Apply error: %s' % exc)
            return
        self._status(
            'Applied: region=%s, power=0x%02X, mode=%s.'
            % (self.region_var.get(), int(self.power_var.get()),
               self.mode_var.get())
        )

    # ------------------------------------------------------------------
    # Tag detail (double-click)
    # ------------------------------------------------------------------

    def _on_tag_double_click(self, event):
        from tkinter import messagebox
        sel = self.tree.selection()
        if not sel:
            return
        epc_hex = sel[0]
        try:
            epc_bytes = bytes.fromhex(epc_hex)
        except ValueError:
            return
        desc = epc_mod.describe(epc_bytes)
        lines = [
            'EPC (hex): %s' % desc.hex,
            'Scheme:    %s' % desc.scheme,
        ]
        if desc.sgtin is not None:
            s = desc.sgtin
            lines += [
                '',
                '--- SGTIN-96 ---',
                'Filter:    %d' % s.filter,
                'Partition: %d' % s.partition,
                'Company prefix: %s' % s.company_prefix,
                'Item ref:       %s' % s.item_reference,
                'Serial:         %d' % s.serial,
                'GTIN-14:        %s' % s.gtin14,
                'Pure URI:       %s' % s.pure_identity_uri,
            ]
        elif desc.scheme == 'unknown':
            lines += ['', '(unknown / non-GS1 EPC — bytes are an opaque identifier)']
        else:
            lines += ['', 'Full parsing for %s requires `pip install epcpy`.' % desc.scheme]
        messagebox.showinfo('EPC details — ' + epc_hex, '\n'.join(lines))

    # ------------------------------------------------------------------
    # Write EPC dialog
    # ------------------------------------------------------------------

    def _on_write_epc(self):
        from tkinter import messagebox, simpledialog
        if self.reader is None:
            self._error('Connect first.')
            return
        if self.listening:
            self._error('Stop listening before writing.')
            return
        if not messagebox.askyesno(
            'Write EPC',
            'This rewrites a tag\'s 96-bit EPC ID.\n\n'
            'Put ONE tag in front of the antenna and remove all others.\n'
            'If multiple tags are present, the write may land on any of them.\n\n'
            'Continue?',
        ):
            return
        epc_hex = simpledialog.askstring(
            'New EPC', 'Enter the new 96-bit EPC as 24 hex characters:',
            parent=self.root,
        )
        if not epc_hex:
            return
        try:
            epc = bytes.fromhex(epc_hex.replace(' ', ''))
        except ValueError as exc:
            self._error('Invalid hex: %s' % exc)
            return
        if len(epc) != 12:
            self._error('EPC must be exactly 12 bytes (24 hex chars). Got %d.' % len(epc))
            return
        try:
            tags_before = self.reader.inventory()
        except ReaderError as exc:
            self._error('Pre-check inventory failed: %s' % exc)
            return
        if len(tags_before) != 1:
            self._error(
                'Expected exactly 1 tag in field, found %d. '
                'Remove other tags and try again.' % len(tags_before)
            )
            return
        old = tags_before[0].epc_hex
        if not messagebox.askokcancel(
            'Confirm write',
            'About to overwrite tag\n  %s\nwith\n  %s\n\nProceed?'
            % (old, epc.hex().upper()),
        ):
            return
        try:
            self.reader.write_tag(MemBank.EPC, 2, epc)
            readback = self.reader.read_tag(MemBank.EPC, 2, 6)
        except ReaderError as exc:
            self._error('Write failed: %s' % exc)
            return
        if readback == epc:
            messagebox.showinfo(
                'Success',
                'Wrote and verified.\nTag now reports EPC = %s'
                % readback.hex().upper(),
            )
            self._status('Wrote EPC %s -> %s' % (old, epc.hex().upper()))
        else:
            self._error(
                'Verification failed.\nWrote %s but reading back %s'
                % (epc.hex().upper(), readback.hex().upper())
            )

    # ------------------------------------------------------------------
    # Tools menu: Diagnose
    # ------------------------------------------------------------------

    def _on_diagnose(self):
        from tkinter import messagebox
        from . import diagnose as dx
        was_connected = (self.reader is not None)
        if was_connected:
            if not messagebox.askokcancel(
                'Diagnose',
                'Diagnostics need to open the port directly.\n'
                'Disconnect the current session, run checks, and leave disconnected?',
            ):
                return
            self._disconnect()
        port = self.port_var.get().strip() or None
        win = self._open_log_window('Diagnose %s' % (port or '(auto-detect)'))

        def run():
            try:
                checks = dx.diagnose(port=port)
            except Exception as exc:
                win.append('Error: %s\n' % exc)
                return
            win.append('swrfid diagnostic report\n')
            win.append('=' * 32 + '\n')
            for c in checks:
                # Lightweight color: only the symbol coloured
                tag = c.status
                win.append_tagged(dx.STATUS_ICONS.get(c.status, '?') + '  ', tag)
                win.append(c.name + '  ' + c.message + '\n')
                if c.suggestion:
                    win.append('        -> ' + c.suggestion + '\n')
            fails = sum(1 for c in checks if c.status == dx.STATUS_FAIL)
            warns = sum(1 for c in checks if c.status == dx.STATUS_WARN)
            win.append('\n')
            if fails:
                win.append('%d failure(s), %d warning(s).\n' % (fails, warns))
            elif warns:
                win.append('No failures. %d warning(s) — see suggestions above.\n' % warns)
            else:
                win.append('All checks passed.\n')

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------
    # Tools menu: Calibrate
    # ------------------------------------------------------------------

    def _on_calibrate(self):
        from tkinter import messagebox, simpledialog
        from . import calibrate as cal
        if self.reader is None:
            self._error('Connect first.')
            return
        if self.listening:
            self._error('Stop listening before calibrating.')
            return
        # Get reference tag — auto-detect a single tag in field, or ask.
        try:
            tags = self.reader.inventory()
        except ReaderError as exc:
            self._error('Pre-check inventory failed: %s' % exc)
            return
        if len(tags) == 1:
            target_hex = tags[0].epc_hex
            if not messagebox.askokcancel(
                'Calibrate',
                'Use tag\n  %s\nas the reference?\n\n'
                'It will stay still on the antenna during the sweep.' % target_hex,
            ):
                return
        else:
            target_hex = simpledialog.askstring(
                'Reference tag',
                'Enter the 24-hex-char EPC of the reference tag:',
                parent=self.root,
            )
            if not target_hex:
                return
        try:
            target_epc = bytes.fromhex(target_hex.replace(' ', ''))
        except ValueError as exc:
            self._error('Invalid hex: %s' % exc)
            return
        if len(target_epc) != 12:
            self._error('Reference EPC must be 12 bytes.')
            return

        win = self._open_log_window('Calibrate power vs ' + target_epc.hex().upper())
        win.append('Sweeping power 0x00..0x1E, 5 samples per step...\n\n')

        def progress(i, total, sample):
            bar = '#' * sample.successes + '-' * (sample.attempts - sample.successes)
            line = '  [%2d/%2d] power=0x%02X [%s] %d/%d  rssi=%s\n' % (
                i, total, sample.power_byte, bar,
                sample.successes, sample.attempts,
                ('%.1f' % sample.avg_rssi) if sample.avg_rssi is not None else '-',
            )
            self.root.after(0, win.append, line)

        def run():
            try:
                result = cal.sweep(
                    self.reader, target_epc,
                    samples_per_power=5, progress=progress,
                )
            except Exception as exc:
                self.root.after(0, win.append, 'Error: %s\n' % exc)
                return
            rec = cal.recommended_power(result)
            first = cal.first_detectable_power(result)
            tail = ['\n', 'Reader SN: %s\n' % result.reader_serial,
                    'Tag EPC:   %s\n' % result.tag_epc]
            if first is not None:
                tail.append('First detectable power:               0x%02X\n' % first)
            if rec is not None:
                tail.append('Recommended power (100%% reliability): 0x%02X\n' % rec)
            else:
                tail.append('No power level achieved 100%% reliability.\n')
            for line in tail:
                self.root.after(0, win.append, line)

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------
    # Tools menu: Decode EPC
    # ------------------------------------------------------------------

    def _on_decode_epc(self):
        from tkinter import simpledialog
        epc_hex = simpledialog.askstring(
            'Decode EPC',
            'Paste the EPC (24 hex chars for a 96-bit tag):',
            parent=self.root,
        )
        if not epc_hex:
            return
        try:
            epc_bytes = bytes.fromhex(epc_hex.replace(' ', ''))
        except ValueError as exc:
            self._error('Invalid hex: %s' % exc)
            return
        desc = epc_mod.describe(epc_bytes)
        win = self._open_log_window('Decoded EPC ' + desc.hex)
        win.append('Hex:     %s\n' % desc.hex)
        win.append('Scheme:  %s\n' % desc.scheme)
        if desc.sgtin is not None:
            s = desc.sgtin
            win.append('\n--- SGTIN-96 ---\n')
            win.append('Filter:          %d\n' % s.filter)
            win.append('Partition:       %d\n' % s.partition)
            win.append('Company prefix:  %s\n' % s.company_prefix)
            win.append('Item reference:  %s\n' % s.item_reference)
            win.append('Serial:          %d\n' % s.serial)
            win.append('GTIN-14:         %s\n' % s.gtin14)
            win.append('Pure URI:        %s\n' % s.pure_identity_uri)
            win.append('Tag URI:         %s\n' % s.tag_uri)
        elif desc.scheme == 'unknown':
            win.append('\n(Unknown header byte. Not a standard GS1 EPC scheme.)\n')
        else:
            win.append('\nFull parsing for %s isn\'t built in.\n' % desc.scheme)
            win.append('Run: pip install epcpy   for complete GS1 TDS coverage.\n')

    # ------------------------------------------------------------------
    # Tools menu: MQTT Bridge
    # ------------------------------------------------------------------

    def _on_mqtt_bridge(self):
        from tkinter import messagebox
        if not _paho_available():
            messagebox.showinfo(
                'MQTT not installed',
                'The MQTT bridge needs the paho-mqtt library.\n\n'
                'Install it with:\n'
                '    pip install swrfid[mqtt]\n'
                'or: pip install paho-mqtt',
            )
            return
        if self.reader is None:
            self._error('Connect to a reader first.')
            return
        if self.listening:
            self._error('Stop listening before starting the bridge.')
            return
        MqttDialog(self.root, self)

    # ------------------------------------------------------------------
    # Help: About
    # ------------------------------------------------------------------

    def _on_about(self):
        from tkinter import messagebox
        messagebox.showinfo(
            'About swrfid',
            'swrfid %s\n\n'
            'Pure-Python driver for SW UHF RFID readers\n'
            '(YanPoDo RU5100 / RU5300 / RU5500).\n\n'
            'MIT licensed.\n'
            'https://github.com/jasoisjaso/swrfid' % __version__,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _open_log_window(self, title):
        return _LogWindow(self.root, title)

    def _status(self, msg):
        self.status_var.set(msg)

    def _error(self, msg):
        from tkinter import messagebox
        self.status_var.set(msg)
        messagebox.showerror('swrfid', msg)

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


class _LogWindow:
    """A simple modeless Toplevel with a scrolling read-only text widget,
    used for the streaming output of diagnose and calibrate."""

    def __init__(self, parent, title):
        import tkinter as tk
        from tkinter import ttk
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.geometry('640x480')

        frame = ttk.Frame(self.top, padding=4)
        frame.pack(fill='both', expand=True)
        self.text = tk.Text(frame, wrap='none', font=('Courier', 10), state='disabled')
        ysb = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=ysb.set)
        self.text.pack(side='left', fill='both', expand=True)
        ysb.pack(side='right', fill='y')

        # Status tags
        self.text.tag_config('ok', foreground='#15803d')
        self.text.tag_config('warn', foreground='#b45309')
        self.text.tag_config('fail', foreground='#b91c1c')
        self.text.tag_config('info', foreground='#0e7490')

        ttk.Button(self.top, text='Close', command=self.top.destroy).pack(pady=4)

    def append(self, s):
        self.text.configure(state='normal')
        self.text.insert('end', s)
        self.text.see('end')
        self.text.configure(state='disabled')

    def append_tagged(self, s, tag):
        self.text.configure(state='normal')
        self.text.insert('end', s, tag)
        self.text.see('end')
        self.text.configure(state='disabled')


class MqttDialog:
    """Modal-ish dialog for configuring + running the MQTT bridge.

    Disconnects the GUI's reader first (the bridge opens its own), then
    runs MqttBridge in a daemon thread. Status updates flow back via a
    queue drained by an ``after`` timer.
    """

    def __init__(self, parent, app):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.top = tk.Toplevel(parent)
        self.top.title('MQTT Bridge')
        self.top.geometry('480x320')
        self.top.transient(parent)

        frm = ttk.Frame(self.top, padding=10)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text='Broker URL:').grid(row=0, column=0, sticky='w', pady=2)
        self.broker_var = tk.StringVar(value='mqtt://localhost:1883')
        ttk.Entry(frm, textvariable=self.broker_var, width=40).grid(row=0, column=1, sticky='ew', padx=4)

        ttk.Label(frm, text='Topic prefix:').grid(row=1, column=0, sticky='w', pady=2)
        self.topic_var = tk.StringVar(value='rfid/swrfid')
        ttk.Entry(frm, textvariable=self.topic_var, width=40).grid(row=1, column=1, sticky='ew', padx=4)

        ttk.Label(frm, text='Dedup window:').grid(row=2, column=0, sticky='w', pady=2)
        self.dedup_var = tk.DoubleVar(value=2.0)
        ttk.Spinbox(frm, from_=0.0, to=30.0, increment=0.5,
                    textvariable=self.dedup_var, width=10).grid(row=2, column=1, sticky='w')

        ttk.Label(frm, text='RSSI minimum:').grid(row=3, column=0, sticky='w', pady=2)
        self.rssi_var = tk.IntVar(value=0)
        ttk.Spinbox(frm, from_=0, to=0xFF, textvariable=self.rssi_var,
                    width=10).grid(row=3, column=1, sticky='w')

        self.ha_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text='Publish Home Assistant MQTT discovery',
                        variable=self.ha_var).grid(row=4, column=0, columnspan=2,
                                                    sticky='w', pady=4)

        self.status_var = tk.StringVar(value='Idle.')
        ttk.Label(frm, textvariable=self.status_var, foreground='#555',
                  wraplength=440).grid(row=5, column=0, columnspan=2, sticky='w', pady=8)

        btnf = ttk.Frame(frm)
        btnf.grid(row=6, column=0, columnspan=2, pady=4)
        self.start_btn = ttk.Button(btnf, text='Start bridge', command=self._start)
        self.start_btn.pack(side='left', padx=4)
        self.stop_btn = ttk.Button(btnf, text='Stop bridge', command=self._stop,
                                   state='disabled')
        self.stop_btn.pack(side='left', padx=4)
        ttk.Button(btnf, text='Close', command=self._close).pack(side='left', padx=4)

        frm.columnconfigure(1, weight=1)

        self.bridge = None
        self.thread = None

    def _start(self):
        from . import mqtt as mqtt_mod
        port = self.app.port_var.get().strip()
        if not port:
            self.status_var.set('No port selected in main window.')
            return
        if self.app.reader is not None:
            self.app._disconnect()
        self.status_var.set('Starting bridge...')
        try:
            self.bridge = mqtt_mod.MqttBridge(
                port=port,
                broker_url=self.broker_var.get().strip(),
                topic_prefix=self.topic_var.get().strip(),
                dedup_window=float(self.dedup_var.get()),
                rssi_min=int(self.rssi_var.get()),
                ha_discovery=bool(self.ha_var.get()),
            )
        except Exception as exc:
            self.status_var.set('Failed to construct bridge: %s' % exc)
            return

        def run():
            try:
                self.bridge.run()
                self.app.root.after(0, lambda: self.status_var.set('Bridge stopped.'))
            except Exception as exc:
                self.app.root.after(
                    0, lambda: self.status_var.set('Bridge error: %s' % exc),
                )
            finally:
                self.app.root.after(0, self._on_stopped)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.status_var.set(
            'Bridge running. Publishing to %s/tags.'
            % self.topic_var.get().strip()
        )

    def _stop(self):
        if self.bridge is not None:
            self.bridge.stop()
        self.status_var.set('Stopping...')

    def _on_stopped(self):
        self.start_btn.config(state='normal')
        self.stop_btn.config(state='disabled')

    def _close(self):
        if self.bridge is not None and self.thread is not None and self.thread.is_alive():
            self.bridge.stop()
            self.thread.join(timeout=2.0)
        self.top.destroy()


def main():
    _require_tk()
    import tkinter as tk
    root = tk.Tk()
    SWRFIDApp(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
