import time
import threading
import pandas as pd
import streamlit as st
from pythonosc.udp_client import SimpleUDPClient

# ----------------------------
# Player Engine (runs in background thread)
# ----------------------------
class CsvOscPlayer:
    def __init__(self):
        self.client = None
        self.df = None
        self.columns = []
        self.row_count = 0

        self.base_ms = 200
        self.tempo = 1.0
        self.row_index = 0
        self.state = "stopped"  # playing | paused | stopped

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    def configure_osc(self, ip: str, port: int):
        self.client = SimpleUDPClient(ip, int(port))

    def load_df(self, df: pd.DataFrame, file_name: str = "uploaded.csv"):
        if self.client is None:
            raise RuntimeError("OSC not configured yet.")

        with self._lock:
            self.df = df
            self.columns = list(df.columns)
            self.row_count = len(df)
            self.row_index = 0
            self.state = "paused"

        # Send summary
        self.client.send_message("/player/loaded", file_name)
        self.client.send_message("/player/summary", [int(self.row_count), int(len(self.columns))])
        for c in self.columns:
            self.client.send_message("/player/columns", str(c))

        self.client.send_message("/player/state", "paused")
        self.client.send_message("/player/position", int(self.row_index))

    def set_tempo(self, tempo: float):
        with self._lock:
            self.tempo = max(0.05, float(tempo))
        if self.client:
            self.client.send_message("/player/tempo", float(self.tempo))

    def seek(self, row_index: int):
        with self._lock:
            if self.df is None or self.row_count == 0:
                return
            self.row_index = max(0, min(int(row_index), self.row_count - 1))
        if self.client:
            self.client.send_message("/player/position", int(self.row_index))

    def play(self):
        with self._lock:
            if self.df is None:
                return
            self.state = "playing"
        if self.client:
            self.client.send_message("/player/state", "playing")

        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def pause(self):
        with self._lock:
            self.state = "paused"
        if self.client:
            self.client.send_message("/player/state", "paused")

    def stop(self):
        with self._lock:
            self.state = "stopped"
            self.row_index = 0
        if self.client:
            self.client.send_message("/player/state", "stopped")
            self.client.send_message("/player/position", int(self.row_index))

    def shutdown(self):
        self._stop_event.set()

    def _send_row(self, idx: int):
        row = self.df.iloc[idx]

        self.client.send_message("/player/row/start", int(idx))
        for col in self.columns:
            val = row[col]

            # OSC typing: numeric -> float, else string
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
                state = self.state
                df_ok = (self.df is not None and self.row_count > 0)

                if not df_ok:
                    self.state = "stopped"
                    state = "stopped"

                if state != "playing":
                    pass
                else:
                    if self.row_index >= self.row_count:
                        # reached end
                        self.state = "stopped"
                        if self.client:
                            self.client.send_message("/player/state", "stopped")
                        continue

                    idx = self.row_index
                    self.row_index += 1
                    tempo = self.tempo
                    base_ms = self.base_ms

            if state != "playing":
                time.sleep(0.05)
                continue

            self._send_row(idx)
            interval = (base_ms / tempo) / 1000.0
            time.sleep(max(0.001, interval))


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="CSV → OSC Data Player", layout="wide")
st.title("CSV → OSC Data Player")

if "player" not in st.session_state:
    st.session_state.player = CsvOscPlayer()
if "loaded" not in st.session_state:
    st.session_state.loaded = False
if "file_name" not in st.session_state:
    st.session_state.file_name = ""
if "last_df" not in st.session_state:
    st.session_state.last_df = None

colA, colB = st.columns([1, 1])

with colA:
    st.subheader("OSC Settings")
    osc_ip = st.text_input("Destination IP", value="127.0.0.1")
    osc_port = st.number_input("Destination Port", min_value=1, max_value=65535, value=9000, step=1)
    base_ms = st.slider("Base interval (ms per row at tempo=1.0)", min_value=10, max_value=2000, value=200, step=10)

    if st.button("Apply OSC Settings"):
        st.session_state.player.configure_osc(osc_ip, int(osc_port))
        st.session_state.player.base_ms = int(base_ms)
        st.success(f"OSC sending to {osc_ip}:{int(osc_port)}")

with colB:
    st.subheader("Load CSV")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            st.session_state.last_df = df
            st.session_state.file_name = uploaded.name
            st.write("Preview:")
            st.dataframe(df.head(20), use_container_width=True)

            if st.button("Load into Player (send summary)"):
                st.session_state.player.load_df(df, file_name=uploaded.name)
                st.session_state.loaded = True
                st.success("Loaded + summary sent over OSC.")
        except Exception as e:
            st.error(f"Failed to read CSV: {e}")

st.divider()

st.subheader("Transport Controls")

if not st.session_state.loaded:
    st.info("1) Apply OSC Settings  2) Upload CSV  3) Load into Player")
else:
    df = st.session_state.last_df
    row_count = len(df)

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])

    with c1:
        if st.button("▶ Play"):
            st.session_state.player.play()

    with c2:
        if st.button("⏸ Pause"):
            st.session_state.player.pause()

    with c3:
        if st.button("⏹ Stop"):
            st.session_state.player.stop()

    with c4:
        tempo = st.slider("Tempo", min_value=0.25, max_value=4.0, value=1.0, step=0.05)
        st.session_state.player.set_tempo(tempo)

    seek_row = st.slider("Seek row index", min_value=0, max_value=max(0, row_count - 1), value=0, step=1)
    if st.button("Seek"):
        st.session_state.player.seek(seek_row)

st.caption("Tip: Keep Protokol listening on the same port you set here. You should see /player/* messages.")
