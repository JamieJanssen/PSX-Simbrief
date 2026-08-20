# psx_simbrief_gui_v1_3.py
# Debug-enabled launcher/subclass for the existing PSX Simbrief GUI.
#
# Place this file next to the current psx_simbrief_gui.py and psx_simbrief.py.
# Run this file instead of psx_simbrief_gui.py.
#
# v1.3:
# - Adds a Debug toggle to the hamburger menu.
# - Enabling Debug opens a second live traffic window.
# - Logs exact TX commands sent to PSX, including Qs438/Qi220/Qs498.
# - Logs RX traffic received from PSX while Flight INIT is connected.
# - Logs relevant SimBrief-generated upload data.
# - Debug can be turned on/off without restarting the application.

from __future__ import annotations

import socket
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk

import psx_simbrief_gui as base


VERSION = "1.3"
APP_NAME = "PSX Simbrief"


class PsxSimbriefGuiDebug(base.PsxSimbriefGui):
    def __init__(self):
        self.debug_enabled = False
        self.debug_window = None
        self.debug_text = None
        self.debug_lock = threading.Lock()
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")

    # ------------------------------------------------------------------
    # Debug logging
    # ------------------------------------------------------------------

    @staticmethod
    def _debug_timestamp():
        return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "z"

    def _debug_log(self, direction, message):
        if not self.debug_enabled:
            return

        message = str(message).replace("\r", "\\r").replace("\n", "\\n")
        line = f"{self._debug_timestamp()}  {direction:<11} {message}\n"

        def append():
            if (
                not self.debug_enabled
                or self.debug_window is None
                or not self.debug_window.winfo_exists()
                or self.debug_text is None
            ):
                return
            self.debug_text.configure(state="normal")
            self.debug_text.insert("end", line)
            self.debug_text.see("end")
            self.debug_text.configure(state="disabled")

        try:
            self.after(0, append)
        except tk.TclError:
            pass

    def _open_debug_window(self):
        if self.debug_window is not None:
            try:
                if self.debug_window.winfo_exists():
                    self.debug_window.deiconify()
                    self.debug_window.lift()
                    return
            except tk.TclError:
                pass

        win = tk.Toplevel(self)
        self.debug_window = win
        win.title(f"{APP_NAME} Debug v{VERSION}")
        win.geometry("900x500")
        win.minsize(650, 300)

        body = ttk.Frame(win, padding=8)
        body.pack(fill="both", expand=True)

        text_frame = ttk.Frame(body)
        text_frame.pack(fill="both", expand=True)

        self.debug_text = tk.Text(
            text_frame,
            wrap="none",
            font=("Menlo", 10),
            state="disabled",
            undo=False,
        )
        yscroll = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.debug_text.yview
        )
        xscroll = ttk.Scrollbar(
            text_frame, orient="horizontal", command=self.debug_text.xview
        )
        self.debug_text.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )

        self.debug_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))

        ttk.Button(
            buttons,
            text="Clear",
            command=self._clear_debug_window,
        ).pack(side="left")

        ttk.Label(
            buttons,
            text="TX → PSX     RX ← PSX     SIMBRIEF = generated/fetched data",
        ).pack(side="left", padx=(12, 0))

        def close_debug():
            self.debug_enabled = False
            self.debug_var.set(False)
            try:
                win.destroy()
            except tk.TclError:
                pass
            self.debug_window = None
            self.debug_text = None

        win.protocol("WM_DELETE_WINDOW", close_debug)
        self._debug_log("DEBUG", "Debug logging enabled")

    def _clear_debug_window(self):
        if self.debug_text is None:
            return
        try:
            self.debug_text.configure(state="normal")
            self.debug_text.delete("1.0", "end")
            self.debug_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _toggle_debug(self):
        self.debug_enabled = bool(self.debug_var.get())
        if self.debug_enabled:
            self._open_debug_window()
        else:
            if self.debug_window is not None:
                try:
                    self.debug_window.destroy()
                except tk.TclError:
                    pass
            self.debug_window = None
            self.debug_text = None

    # ------------------------------------------------------------------
    # Hamburger menu
    # ------------------------------------------------------------------

    def show_menu(self):
        menu = tk.Menu(self, tearoff=False)

        if not hasattr(self, "debug_var"):
            self.debug_var = tk.BooleanVar(
                master=self, value=self.debug_enabled
            )
        else:
            self.debug_var.set(self.debug_enabled)

        menu.add_checkbutton(
            label="Debug",
            variable=self.debug_var,
            command=self._toggle_debug,
        )
        menu.add_separator()
        menu.add_command(label="Purge Routes…", command=self.purge_routes)
        menu.add_command(label="Settings…", command=self.open_settings)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.destroy)

        x = self.menu_button.winfo_rootx()
        y = self.menu_button.winfo_rooty() + self.menu_button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # SimBrief visibility in debug window
    # ------------------------------------------------------------------

    def fetch_simbrief(self):
        self._debug_log(
            "SIMBRIEF",
            f"Fetch requested for username "
            f"{self.config_values.get('username', '').strip()!r}",
        )
        super().fetch_simbrief()

    def _apply_fetched_data(self, data, from_cache=False):
        super()._apply_fetched_data(data, from_cache=from_cache)

        source = "CACHE" if from_cache else "SIMBRIEF"
        self._debug_log(
            source,
            f"Flight {data.get('callsign', '-')} "
            f"{data.get('flight', '-')} "
            f"CO ROUTE {data.get('coroute', '-')}",
        )

        for key in ("qi123", "qs438", "qs498"):
            if key in data:
                self._debug_log(
                    "GENERATED",
                    f"{key.upper()} = {data[key]}",
                )

    # ------------------------------------------------------------------
    # PSX live traffic
    # ------------------------------------------------------------------

    def _send_debug_command(self, sock, command, pause=None):
        self._debug_log("TX -> PSX", command)

        if pause is None:
            base.backend.send_command(sock, command)
        else:
            base.backend.send_command(sock, command, pause=pause)

    def _psx_receive_worker(self, sock, stop_event):
        buffer = b""

        while not stop_event.is_set():
            try:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buffer += chunk

                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.rstrip(b"\r").decode(
                        "utf-8", errors="replace"
                    )
                    self._debug_log("RX <- PSX", line)

            except socket.timeout:
                continue
            except OSError as exc:
                if not stop_event.is_set():
                    self._debug_log("RX ERROR", repr(exc))
                break

        if buffer:
            line = buffer.decode("utf-8", errors="replace")
            self._debug_log("RX <- PSX", line)

    def _upload_worker(self):
        receiver_stop = threading.Event()
        receiver_thread = None

        try:
            coroute_name, route_path = self._restore_route_file_for_upload()

            psx_host = self.config_values["host"]
            psx_port = int(self.config_values["port"])
            callsign = self.current_data["callsign"].strip()

            self._debug_log(
                "CONNECT",
                f"Opening PSX connection {psx_host}:{psx_port}",
            )

            with socket.create_connection(
                (psx_host, psx_port), timeout=10
            ) as sock:
                self._debug_log("CONNECT", "PSX connected")
                sock.settimeout(0.2)

                if self.debug_enabled:
                    receiver_thread = threading.Thread(
                        target=self._psx_receive_worker,
                        args=(sock, receiver_stop),
                        daemon=True,
                    )
                    receiver_thread.start()

                time.sleep(base.backend.WAIT_AFTER_CONNECT_SECONDS)

                self._send_debug_command(sock, "Qh401=58\r\n")
                self._send_debug_command(sock, f"Qs401={callsign}\r\n")
                self._send_debug_command(
                    sock,
                    f"Qs075={coroute_name}\r\n",
                    pause=0.1,
                )
                self._send_debug_command(sock, "Qh401=53\r\n")

                self._send_debug_command(sock, self.current_data["qi123"])
                self._send_debug_command(sock, self.current_data["qs438"])

                self._send_debug_command(sock, "Qi220=1\r\n")
                self._send_debug_command(sock, "Qi220=0\r\n")

                time.sleep(base.backend.AFTER_FUELING_PAUSE_SECONDS)

                self._send_debug_command(sock, "Qs497=201\r\n")
                self._send_debug_command(sock, self.current_data["qs498"])
                self._send_debug_command(sock, "exit\r\n", pause=0)

                if self.debug_enabled:
                    time.sleep(0.15)

            receiver_stop.set()
            if receiver_thread is not None:
                receiver_thread.join(timeout=0.5)

            self._debug_log("CONNECT", "PSX disconnected")
            self.after(
                0, self._upload_complete, coroute_name, str(route_path)
            )

        except Exception as exc:
            receiver_stop.set()
            self._debug_log("ERROR", repr(exc))
            self.after(0, self._operation_failed, "PSX", str(exc))


if __name__ == "__main__":
    app = PsxSimbriefGuiDebug()
    app.mainloop()
