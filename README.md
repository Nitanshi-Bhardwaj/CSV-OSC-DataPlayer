# Data Player

## Prerequisites

- Python 3.7 or higher
- pip (Python package manager)

## Installation & Setup

### Windows

```powershell
# Create virtual environment
py -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### macOS/Linux

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running the Application

```bash
streamlit run app.py
```

The application will open in your default web browser at `http://localhost:8501`

## Usage

1. **Load Data**: Upload a CSV or Excel file using the file uploader
2. **Configure OSC**: Set the target IP address and port (default: `127.0.0.1:7000`)
3. **Adjust Tempo**: Use the slider to control playback speed (rows per second)
4. **Control Playback**: Use Play, Pause, and Stop buttons to control data streaming

## OSC Message Contract

The application sends OSC messages to the configured IP and port. All messages follow this protocol:

### Summary (sent on load)

Sent when a file is first loaded:

- `/player/loaded` → `(string fileName)` - Name of the loaded file
- `/player/summary` → `(int rows, int cols)` - Dataset dimensions
- `/player/columns` → `(string colName)` - Repeated for each column in the dataset

### Transport Control

Sent when transport state changes:

- `/player/state` → `(string state)` - Current state: `"playing"`, `"paused"`, or `"stopped"`
- `/player/position` → `(int rowIndex)` - Current row position (0-indexed)
- `/player/tempo` → `(float tempo)` - Current playback tempo in rows/second

### Row Streaming (during playback)

Sent for each row during playback:

- `/player/row/start` → `(int rowIndex)` - Marks the beginning of a row
- `/player/row/value` → `(int rowIndex, string colName, value)` - One message per column
- `/player/row/end` → `(int rowIndex)` - Marks the end of a row

### Value Type Rules

- **Numeric cells** → sent as `float`
- **Text cells** → sent as `string`
- **Missing/NaN values** → sent as empty string `""`

## Troubleshooting

**Virtual environment activation fails**: On Windows, you may need to enable script execution:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**OSC messages not received**: Ensure your receiver is listening on the same IP/port configured in the app

**File loading errors**: Verify your file is a valid CSV or Excel format with proper encoding

