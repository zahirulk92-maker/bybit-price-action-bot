from __future__ import annotations

import os
import socket
import threading
import webbrowser

import uvicorn


def find_free_port(start: int = 8000, stop: int = 8010) -> int:
    for port in range(start, stop):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"Ports {start}-{stop - 1} are already in use")


def main() -> None:
    # These process-level values take priority over anything in .env.
    os.environ["BYBIT_DEMO"] = "true"
    os.environ["ENABLE_ORDER_PLACEMENT"] = "false"
    os.environ["RUN_ENGINE_IN_WEB"] = "true"
    os.environ["DASHBOARD_ALLOW_INSECURE_LOCAL"] = "true"

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print("\n[SAFE MODE] Bybit Demo: ON")
    print("[SAFE MODE] Order placement: OFF")
    print(f"[DASHBOARD] {url}")
    print("[STOP] Press Ctrl+C in this window.\n")
    threading.Timer(2.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("price_action_bot.web:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
