"""Tkinter dashboard. Tk is touched ONLY from the main thread; the engine thread talks to it via a queue."""
from __future__ import annotations

import asyncio
import queue
import threading
import time

from arbx.hub import Hub
from arbx.util import loop_factory


def run_dashboard(cfg) -> None:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    root = tk.Tk()
    root.title(f"ARBX | {','.join(x.id for x in cfg.exchanges)} | {cfg.mode.upper()}")
    root.geometry("900x560")
    root.configure(bg="#0A0A0C")
    if cfg.mode == "live" and not messagebox.askyesno(
            "LIVE MODE", "REAL orders with REAL funds.\nHave you completed the paper-trading checklist in the README?"):
        root.destroy()
        return
    tk.Label(root, text=f"MODE: {cfg.mode.upper()}", bg="#FF1744" if cfg.mode == "live" else "#29B6F6",
             fg="white", font=("Consolas", 11, "bold")).pack(fill="x")
    labels = {}
    for k in ("status", "equity", "counts", "rejects", "rtt"):
        labels[k] = tk.Label(root, bg="#0A0A0C", fg="#EAEAEA", font=("Consolas", 10), anchor="w")
        labels[k].pack(fill="x", padx=12)
    box = scrolledtext.ScrolledText(root, bg="#101012", fg="#EAEAEA", font=("Consolas", 9), relief="flat")
    box.pack(fill="both", expand=True, padx=12, pady=8)

    out: queue.Queue = queue.Queue()
    hub = Hub(cfg, out)
    threading.Thread(target=lambda: asyncio.run(hub.run(), loop_factory=loop_factory()), daemon=True).start()

    def poll():
        try:
            while True:
                item = out.get_nowait()
                if item[0] == "log":
                    box.insert("end", f"[{time.strftime('%H:%M:%S')}] [{item[1].upper()}] {item[2]}\n")
                    box.see("end")
                else:
                    s = item[1]
                    labels["status"].config(text=f"Status  : {s['status']}")
                    labels["equity"].config(text=f"Equity  : {s['equity']:.4f} USD   PnL: {s['pnl']:+.4f}")
                    labels["counts"].config(text=f"Scans {s['scans']} | Signals {s['signals']} | "
                                                 f"Trades {s['trades']} | Unfilled {s['failed']}")
                    labels["rejects"].config(text=f"Gate rejections: {s['rejects']}")
                    labels["rtt"].config(text=f"REST RTT p50 (ms): {s['rtt']}")
        except queue.Empty:
            pass
        root.after(100, poll)

    def close():
        hub.request_stop()
        root.after(300, root.destroy)

    root.protocol("WM_DELETE_WINDOW", close)
    poll()
    root.mainloop()
