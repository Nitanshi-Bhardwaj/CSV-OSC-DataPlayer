import threading
import time
import pandas as pd
from pythonosc.udp_client import SimpleUDPClient

OSC_IP = "127.0.0.1"
OSC_PORT = 9000
BASE_MS = 200  # 200ms per row at tempo=1.0

class CsvOscPlayer:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.client = SimpleUDPClient(OSC_IP, OSC_PORT)

        self.df = None
        self.columns = []
        self.row_count = 0

        self.state = "stopped"  # playing | paused | stopped
        self.tempo = 1.0
        self.row_index = 0

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def load(self):
        self.df = pd.read_csv(self.csv_path)
        self.columns = list(self.df.columns)
        self.row_count = len(self.df)

        # Send summary
        self.client.send_message("/player/loaded", self.csv_path)
        self.client.send_message("/player/summary", [int(self.row_count), int(len(self.columns))])
        for c in self.columns:
            self.client.send_message("/player/columns", c)

        self.state = "paused"
        self.row_index = 0
        self.client.send_message("/player/state", self.state)
        self.client.send_message("/player/position", int(self.row_index))

    def set_tempo(self, tempo: float):
        with self._lock:
            self.tempo = max(0.05, float(tempo))
        self.client.send_message("/player/tempo", float(self.tempo))

    def seek(self, row_index: int):
        with self._lock:
            self.row_index = max(0, min(int(row_index), self.row_count - 1))
        self.client.send_message("/player/position", int(self.row_index))

    def play(self):
        with self._lock:
            self.state = "playing"
        self.client.send_message("/player/state", "playing")

        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def pause(self):
        with self._lock:
            self.state = "paused"
        self.client.send_message("/player/state", "paused")

    def stop(self):
        with self._lock:
            self.state = "stopped"
            self.row_index = 0
        self.client.send_message("/player/state", "stopped")
        self.client.send_message("/player/position", int(self.row_index))

    def shutdown(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _send_row(self, idx: int):
        row = self.df.iloc[idx]

        self.client.send_message("/player/row/start", int(idx))
        for col in self.columns:
            val = row[col]

            if pd.isna(val):
                payload = ""
            elif isinstance(val, (int, float)):
                payload = float(val)
            else:
                try:
                    payload = float(val)
                except Exception:
                    payload = str(val)

            self.client.send_message("/player/row/value", [int(idx), str(col), payload])
        self.client.send_message("/player/row/end", int(idx))

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                if self.state != "playing":
                    pass
                else:
                    if self.row_index >= self.row_count:
                        self.state = "stopped"
                        self.client.send_message("/player/state", "stopped")
                        break

                    idx = self.row_index
                    self.row_index += 1
                    tempo = self.tempo

                # if paused/stopped, just wait a bit and loop
                if self.state != "playing":
                    time.sleep(0.05)
                    continue

            self._send_row(idx)
            interval = (BASE_MS / tempo) / 1000.0
            time.sleep(max(0.001, interval))

def main():
    p = CsvOscPlayer("stats_wl.csv")
    p.load()

    print("Commands:")
    print("  play | pause | stop")
    print("  seek <rowIndex>")
    print("  tempo <value>   (e.g., 0.5, 1, 2)")
    print("  quit")

    while True:
        cmd = input("> ").strip().split()
        if not cmd:
            continue

        if cmd[0] == "play":
            p.play()
        elif cmd[0] == "pause":
            p.pause()
        elif cmd[0] == "stop":
            p.stop()
        elif cmd[0] == "seek" and len(cmd) == 2:
            p.seek(int(cmd[1]))
        elif cmd[0] == "tempo" and len(cmd) == 2:
            p.set_tempo(float(cmd[1]))
        elif cmd[0] == "quit":
            p.shutdown()
            break
        else:
            print("Unknown command.")

if __name__ == "__main__":
    main()
