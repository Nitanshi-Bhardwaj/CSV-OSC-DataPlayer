## Run (Windows)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py

## OSC Message Contract

### Summary (sent on load)
- `/player/loaded` → `(string fileName)`
- `/player/summary` → `(int rows, int cols)`
- `/player/columns` → `(string colName)` repeated for each column

### Transport
- `/player/state` → `(string "playing" | "paused" | "stopped")`
- `/player/position` → `(int rowIndex)`
- `/player/tempo` → `(float tempo)`

### Row streaming (during play)
- `/player/row/start` → `(int rowIndex)`
- `/player/row/value` → `(int rowIndex, string colName, value)`
- `/player/row/end` → `(int rowIndex)`

**Value typing rule**
- If a cell is numeric → sent as `float`
- Otherwise → sent as `string`
- Missing/NaN → sent as empty string `""`