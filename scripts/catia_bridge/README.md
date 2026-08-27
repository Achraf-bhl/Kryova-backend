# Kryova CATIA COM Automation Bridge

The Kryova CATIA COM Automation Bridge enables bidirectional synchronization between Dassault Systèmes CATIA (V5-6R / 3DEXPERIENCE) and the Kryova Cloud FEA Platform.

## Features

- **Parametric Read / Write**: Inspect and modify CATPart/CATProduct parameters (`Part.Parameters`) dynamically.
- **Design Table Automation**: Retrieve and activate design table configurations (`DesignTable`).
- **Native CAD Export**: Automatically export active CATIA geometry to STEP (`.stp`), IGES (`.igs`), or STL (`.stl`).
- **Live Push Daemon**: Background service that watches CATIA design updates and posts new geometry versions to Kryova API endpoints.
- **Cross-Platform Mock Mode**: Allows Linux/macOS and CI test runs without requiring a physical CATIA installation.

---

## Prerequisites (Windows Workstation)

1. **CATIA V5-6R (R2019-R2024)** or **3DEXPERIENCE CATIA**.
2. **Python 3.10+** (64-bit).
3. `pywin32` package:
   ```bash
   pip install pywin32 comtypes requests
   ```

---

## CLI Usage

### 1. Read CATIA Parameters
```bash
python catia_bridge.py params --read
```

### 2. Update Parameters and Regenerate Geometry
```bash
python catia_bridge.py params --set '{"Length": 200.0, "Width": 80.0}'
# Or key-value pairs:
python catia_bridge.py params --set "Length=200.0,Width=80.0"
```

### 3. Export Geometry to STEP
```bash
python catia_bridge.py export --format step --output ./bracket_v2.stp
```

### 4. Run Cross-Platform Mock Daemon (for Testing/Linux)
```bash
python catia_bridge.py --mock daemon --project-id PRJ-001 --interval 5
```

---

## Integration with Kryova API

The bridge automatically syncs newly exported `.stp` files to Kryova's `/api/v1/projects/{projectId}/geometry` endpoint via multi-part upload, preserving version history and parameter provenance.
