"""
swrfid.gui — minimal cross-platform tkinter GUI for the SW UHF RFID
reader family.

Launch via the ``swrfid-gui`` console script (installed by pip), or via
``python3 -m swrfid.gui``. No external GUI dependencies — uses tkinter
which ships with Python on Windows and macOS, and is one apt-install
away on Debian / Ubuntu (``sudo apt install python3-tk``).

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


class SWRFIDApp:
    """Single-window tkinter app for SW UHF RFID readers."""

    POLL_MS = 50          # how often the main loop drains tag events
    REFRESH_MS = 3000     # how often the port list auto-refreshes

    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title('swrfid %s — UHF RFID reader' % __version__)
        self.root.geometry('820x600')
        self.root.minsize(640, 480)

        self.reader: Optional[RFIDReader] = None
        self.tag_queue: "queue.Queue" = queue.Queue()
        self.tag_counts = {}        # epc_hex -> {'ant':..., 'rssi':..., 'count':...}
        self.listening = False

        self._build_ui()
        self._refresh_ports()
        self._set_connected(False)

        self.root.after(self.POLL_MS, self._drain_tag_queue)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        tk = self.tk
        ttk = self.ttk

        # Port row
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='x')

        ttk.Label(top, text='Port:').pack(side='left')

        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=28)
        self.port_combo.pack(side='left', padx=4)

        ttk.Button(top, text='Refresh', command=self._refresh_ports).pack(side='left')
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

        ttk.Button(
            action, text='Clear table', command=self._on_clear,
        ).pack(side='left', padx=4)

        ttk.Button(
            action, text='Write EPC...', command=self._on_write_epc,
        ).pack(side='left', padx=4)

        self.stats_label = ttk.Label(action, text='0 unique tags', foreground='#555')
        self.stats_label.pack(side='right')

        # Tag table
        table_frame = ttk.Frame(self.root, padding=(8, 4))
        table_frame.pack(fill='both', expand=True)

        cols = ('epc', 'ant', 'rssi', 'count')
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show='headings', selectmode='browse',
        )
        self.tree.heading('epc', text='EPC')
        self.tree.heading('ant', text='Ant')
        self.tree.heading('rssi', text='RSSI')
        self.tree.heading('count', text='Reads')
        self.tree.column('epc', width=380, anchor='w')
        self.tree.column('ant', width=50, anchor='center')
        self.tree.column('rssi', width=70, anchor='center')
        self.tree.column('count', width=70, anchor='e')

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
        self.region_combo = ttk.Combobox(
            settings, textvariable=self.region_var,
            values=sorted(REGION_FREQ.keys()),
            width=8, state='readonly',
        )
        self.region_combo.grid(row=0, column=1, sticky='w', padx=2)

        ttk.Label(settings, text='Work mode:').grid(row=0, column=2, sticky='w', padx=8)
        self.mode_var = tk.StringVar(value='ANSWER')
        self.mode_combo = ttk.Combobox(
            settings, textvariable=self.mode_var,
            values=[m.name for m in WorkMode],
            width=10, state='readonly',
        )
        self.mode_combo.grid(row=0, column=3, sticky='w', padx=2)

        # Row 2: Power
        ttk.Label(settings, text='RF power byte:').grid(row=1, column=0, sticky='w', padx=2, pady=4)
        self.power_var = tk.IntVar(value=0x14)
        self.power_scale = ttk.Scale(
            settings, from_=0, to=0x1E, variable=self.power_var,
            orient='horizontal', length=300,
            command=lambda v: self.power_label.config(
                text='0x%02X (%d)' % (int(float(v)), int(float(v)))
            ),
        )
        self.power_scale.grid(row=1, column=1, columnspan=2, sticky='ew', padx=2)
        self.power_label = ttk.Label(settings, text='0x14 (20)', width=12)
        self.power_label.grid(row=1, column=3, sticky='w')

        # Apply button
        self.apply_btn = ttk.Button(
            settings, text='Apply settings', command=self._on_apply,
        )
        self.apply_btn.grid(row=2, column=0, columnspan=4, pady=6)

        settings.columnconfigure(1, weight=1)

        # Status bar
        self.status_var = tk.StringVar(value='Disconnected.')
        status = ttk.Label(
            self.root, textvariable=self.status_var, anchor='w',
            relief='sunken', padding=4,
        )
        status.pack(fill='x', side='bottom')

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
            r = RFIDReader(port)
            r.open()
            info = r.system_info()
        except ReaderError as exc:
            self._error('Connect failed: %s' % exc)
            return
        except Exception as exc:
            self._error('Unexpected error: %s' % exc)
            return
        self.reader = r
        self._set_connected(True)
        self.info_label.config(
            text='SN %s · sw=%s · hw=%s'
            % (info.serial_hex, info.soft_version, info.hard_version)
        )
        self._status('Connected to %s. Click Inventory to scan, or Start Listening for live mode.' % port)
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
        self.info_label.config(text='')
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
                self.reader.start_active_listener(self._listener_cb)
                self.reader.start_read()
            except ReaderError as exc:
                self._error('Start listening failed: %s' % exc)
                return
            self.listening = True
            self.listen_btn.config(text='Stop Listening')
            self.inv_btn.config(state='disabled')
            self._status('Listening in active mode — tags will stream in.')
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
        # Called from the listener thread — only push to the queue here,
        # never touch tkinter widgets directly.
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
            entry = {'ant': t.antenna, 'rssi': t.rssi, 'count': 0}
            self.tag_counts[key] = entry
        entry['ant'] = t.antenna
        entry['rssi'] = t.rssi
        entry['count'] += 1

        if self.tree.exists(key):
            self.tree.item(key, values=(key, entry['ant'],
                                        '0x%02X' % entry['rssi'],
                                        entry['count']))
        else:
            self.tree.insert(
                '', 'end', iid=key,
                values=(key, entry['ant'],
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
    # Helpers
    # ------------------------------------------------------------------

    def _status(self, msg):
        self.status_var.set(msg)

    def _error(self, msg):
        from tkinter import messagebox
        self.status_var.set(msg)
        messagebox.showerror('swrfid', msg)

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


def main():
    _require_tk()
    import tkinter as tk
    root = tk.Tk()
    SWRFIDApp(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
