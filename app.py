import time
import threading
import pandas as pd
import streamlit as st
import numpy as np
from pythonosc.udp_client import SimpleUDPClient


def robust_flags_numeric(df: pd.DataFrame, z_thresh: float = 3.5):
    flags = {}
    flag_times_rows = set()

    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    stats = {}
    for col in num_cols:
        x = df[col].astype(float)
        med = np.nanmedian(x)
        mad = np.nanmedian(np.abs(x - med))
        denom = 1.4826 * mad if mad > 0 else None
        stats[col] = (med, denom)

    for i in range(len(df)):
        for col in num_cols:
            val = df.at[i, col]
            if pd.isna(val):
                flags[(i, col)] = 0
                continue
            med, denom = stats[col]
            if denom is None:
                flags[(i, col)] = 0
                continue
            rz = abs((float(val) - med) / denom)
            f = 1 if rz >= z_thresh else 0
            flags[(i, col)] = f
            if f == 1:
                flag_times_rows.add(i)

    return flags, sorted(flag_times_rows)

    

# ----------------------------
# Player Engine (runs in background thread)
# ----------------------------
class CsvOscPlayer:
    def __init__(self):

        self.client = None
        self.df = None
        self.columns = []
        self.row_count = 0

        #self.base_ms = 200
        #self.tempo = 1.0
        self.row_index = 0
        self.state = "stopped"  # playing | paused | stopped

        self.bpm = 120.0
        self.rows_per_beat = 1.0
        self.time_col = None            # name of CSV column used as timestamp (optional)
        self.time_values = None         # numpy/pandas series for timestamps per row

        self.flags = None               # dict: (rowIndex, colName) -> 0/1
        self.flag_times = []            # list of timestamps (float) where any flag occurred

        # segment looping
        self.segment_enabled = False
        self.segment_start = 0
        self.segment_end = 0

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    def configure_osc(self, ip: str, port: int):
        self.client = SimpleUDPClient(ip, int(port))
    
    #import numpy as np

    def load_df(self, df: pd.DataFrame, file_name: str = "uploaded.csv"):
        if self.client is None:
            raise RuntimeError("OSC not configured yet.")

        with self._lock:
            self.df = df
            self.columns = list(df.columns)
            self.row_count = len(df)
            self.row_index = 0
            self.state = "paused"
            self.segment_start = 0
            self.segment_end = max(0, self.row_count - 1)
            self.segment_enabled = False

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
            if self.row_index >= self.row_count:
                self.row_index = 0
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

            # value message
            self.client.send_message("/player/row/value", [int(idx), str(col), payload])

            # 0/1 flag message (unusual)
            flag = 0
            if self.flags is not None:
                # flags dict keys should be (rowIndex, colName)
                flag = int(self.flags.get((idx, col), 0))
            self.client.send_message("/player/row/flag", [int(idx), str(col), flag])

        self.client.send_message("/player/row/end", int(idx))

    def _run(self):
        while not self._stop_event.is_set():
        # snapshot state under lock
            with self._lock:
                state = self.state
                df_ok = (self.df is not None and self.row_count > 0)

                if not df_ok:
                    self.state = "stopped"
                    state = "stopped"

                if state == "playing":
                    if self.row_index >= self.row_count:
                        # reached end
                        self.state = "stopped"
                        if self.client:
                            self.client.send_message("/player/state", "stopped")
                        state = "stopped"
                    else:
                        idx = self.row_index
                        self.row_index += 1

                        # segment looping
                        if self.segment_enabled and self.row_index > self.segment_end:
                            self.row_index = self.segment_start

                        bpm = float(self.bpm)
                        rpb = float(self.rows_per_beat)

            # if not playing, just idle
            if state != "playing":
                time.sleep(0.05)
                continue

            # send row
            self._send_row(idx)

            # BPM-based sleep (ms per beat / rows per beat)
            interval_ms = 60000.0 / max(1e-6, bpm) / max(1e-6, rpb)
            time.sleep(max(0.001, interval_ms / 1000.0))

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

            missing_per_col = df.isna().sum()
            total_missing = int(missing_per_col.sum())
            total_cells = int(df.shape[0] * df.shape[1])
            pct_missing = (total_missing / total_cells * 100.0) if total_cells else 0.0

            if total_missing > 0:
                st.warning(f"Missing data detected: {total_missing} missing cells ({pct_missing:.2f}%).")
                miss_tbl = (
                    missing_per_col[missing_per_col > 0]
                    .sort_values(ascending=False)
                    .reset_index()
                )
                miss_tbl.columns = ["column", "missing_count"]
                miss_tbl["missing_pct"] = miss_tbl["missing_count"] / len(df) * 100.0
                st.dataframe(miss_tbl, use_container_width=True)
            else:
                st.success("No missing data detected.")

            if st.button("Load into Player (send summary)"):
                # 1) compute unusual flags (numeric robust z-score)
                flags, flagged_rows = robust_flags_numeric(df, z_thresh=3.5)

                # 2) store them on the player (so _send_row can use them)
                st.session_state.player.flags = flags
                st.session_state.player.flagged_rows = flagged_rows

                # 3) load + send summary as usual
                st.session_state.player.load_df(df, file_name=uploaded.name)
                st.session_state.loaded = True

                st.success(f"Loaded + summary sent over OSC. Flagged rows: {len(flagged_rows)}")
            
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
        #tempo = st.slider("Tempo", min_value=0.25, max_value=4.0, value=1.0, step=0.05)
        #st.session_state.player.set_tempo(tempo)
        bpm_val = st.number_input("Tempo (BPM)", min_value=1.0, max_value=400.0, value=float(st.session_state.player.bpm), step=1.0)
        rpb_val = st.number_input("Rows per beat", min_value=0.25, max_value=64.0, value=float(st.session_state.player.rows_per_beat), step=0.25)

        with st.session_state.player._lock:
            st.session_state.player.bpm = float(bpm_val)
            st.session_state.player.rows_per_beat = float(rpb_val)

        # optional OSC notify
        if st.session_state.player.client:
            st.session_state.player.client.send_message("/player/bpm", float(bpm_val))
            st.session_state.player.client.send_message("/player/rows_per_beat", float(rpb_val))

    seek_row = st.slider("Seek row index", min_value=0, max_value=max(0, row_count - 1), value=0, step=1)
    if st.button("Seek"):
        st.session_state.player.seek(seek_row)

st.caption("Tip: Keep Protokol listening on the same port you set here. You should see /player/* messages.")
