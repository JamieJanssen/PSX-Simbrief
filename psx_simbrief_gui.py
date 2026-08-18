# psx_simbrief_gui.py
# GUI entry point for PSX Simbrief

from __future__ import annotations

import configparser
import socket
import threading
import time
import tkinter as tk

import psx_simbrief as backend
import psx_simbrief_gui_core as core


VERSION = "1.1f"
APP_NAME = core.APP_NAME
INI_PATH = core.INI_PATH


class PsxSimbriefGui(core.PsxSimbriefGui):
    def __init__(self):
        self._destroying = False
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self._restore_window_position()

    def _build_ui(self):
        super()._build_ui()
        self.upload_button.configure(text="Flight INIT")

    def show_menu(self):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Fetch SimBrief", command=self.fetch_simbrief)
        menu.add_command(label="Flight INIT", command=self.upload_current_to_psx)
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

    def save_config(self, values):
        super().save_config(values)
        self._save_window_position()

    def _restore_window_position(self):
        if not INI_PATH.exists():
            return

        config = configparser.ConfigParser()
        config.read(INI_PATH, encoding="utf-8")

        if not config.has_section("WINDOW"):
            return

        try:
            x = config.getint("WINDOW", "x")
            y = config.getint("WINDOW", "y")
        except (ValueError, configparser.Error):
            return

        self.geometry(f"+{x}+{y}")

    def _save_window_position(self):
        try:
            self.update_idletasks()
            x = self.winfo_x()
            y = self.winfo_y()

            config = configparser.ConfigParser()
            if INI_PATH.exists():
                config.read(INI_PATH, encoding="utf-8")

            config["WINDOW"] = {
                "x": str(x),
                "y": str(y),
            }

            core.SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with INI_PATH.open("w", encoding="utf-8") as handle:
                config.write(handle)
        except Exception as exc:
            print(f"[WINDOW] Could not save window position: {exc}")

    def destroy(self):
        if not self._destroying:
            self._destroying = True
            self._save_window_position()
        super().destroy()

    def upload_current_to_psx(self):
        if not self.current_data:
            return

        self.upload_button.configure(state="disabled")
        self.fetch_button.configure(state="disabled")
        self.status_var.set("Flight INIT…")
        threading.Thread(target=self._upload_worker, daemon=True).start()

    def _upload_worker(self):
        try:
            coroute_name, route_path = self._restore_route_file_for_upload()

            psx_host = self.config_values["host"]
            psx_port = int(self.config_values["port"])
            callsign = self.current_data["callsign"].strip()

            print(f"[PSX] Connecting to {psx_host}:{psx_port}...")
            with socket.create_connection((psx_host, psx_port), timeout=10) as sock:
                print("[PSX] Connected successfully.")
                time.sleep(backend.WAIT_AFTER_CONNECT_SECONDS)

                print("[PSX] Initializing flight from SimBrief data...")

                # Put the restored/re-numbered route name in the CDU CO ROUTE
                # field and execute LOAD. Qs075 uses the route name without
                # the trailing underscore or .route extension.
                backend.send_command(
                    sock, f"Qs075={coroute_name}\r\n", pause=0.1
                )
                backend.send_command(sock, "Qh401=53\r\n")

                backend.send_command(sock, f"Qs401={callsign}\r\n")
                backend.send_command(sock, self.current_data["qi123"])
                backend.send_command(sock, self.current_data["qs438"])

                backend.send_command(sock, "Qi220=1\r\n")
                backend.send_command(sock, "Qi220=0\r\n")

                time.sleep(backend.AFTER_FUELING_PAUSE_SECONDS)

                backend.send_command(sock, "Qs497=201\r\n")
                backend.send_command(sock, self.current_data["qs498"])
                backend.send_command(sock, "exit\r\n", pause=0)

            print("[PSX] Flight INIT complete. Disconnected.")
            self.after(0, self._upload_complete, coroute_name, str(route_path))
        except Exception as exc:
            self.after(0, self._operation_failed, "PSX", str(exc))

    def _upload_complete(self, coroute_name, route_path):
        self.current_data["coroute"] = coroute_name
        self.current_data["route_path"] = route_path
        self.coroute_var.set(coroute_name)
        self.fetch_button.configure(state="normal")
        self.upload_button.configure(state="normal")
        self.status_var.set("Flight INIT complete")


if __name__ == "__main__":
    app = PsxSimbriefGui()
    app.mainloop()
