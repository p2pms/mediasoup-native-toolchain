# mediasoup-native-toolchain

Pre-built C++ build toolchain for [mediasoup](https://mediasoup.org/) native client development — includes **libwebrtc**, **libmediasoupclient**, and **sdptransform**, pre-compiled for Windows x64.

## Purpose

Building mediasoup-based native clients requires compiling **WebRTC** from source (~2 hours on a modern machine) and then **libmediasoupclient** on top. This repository provides ready-to-use pre-built binaries via GitHub Releases, so developers can skip the lengthy WebRTC compilation.

## Produced Artifacts

```
mediasoup-native-toolchain-windows-x64-Release.zip
├── webrtc/
│   ├── include/          # WebRTC C++ headers
│   └── lib/
│       └── webrtc.lib
├── mediasoupclient/
│   ├── include/          # mediasoupclient + sdptransform headers
│   └── lib/
│       ├── mediasoupclient.lib
│       └── sdptransform.lib
└── VERSIONS.json         # Build metadata and SHA256 checksums
```

## Releases

Pre-built packages are published as [GitHub Releases](https://github.com/p2pms/mediasoup-native-toolchain/releases).

Each release includes:

| File | Description |
|------|-------------|
| `mediasoup-native-toolchain-windows-x64-Release.zip` | Combined toolchain archive |
| `VERSIONS.json` | Version manifest with component versions and SHA256 |

## Usage in Downstream Projects

### Download via Script

```bash
# Download the latest Release and extract to ./deps/
python tools/download_deps.py
```

### Manual Download

1. Go to the [Releases page](https://github.com/p2pms/mediasoup-native-toolchain/releases)
2. Download the latest `mediasoup-native-toolchain-windows-x64-Release.zip`
3. Extract to a directory, then pass paths to CMake:

```cmake
cmake -B build -S . \
  -DLIBWEBRTC_INCLUDE_PATH=./deps/webrtc/include \
  -DLIBWEBRTC_BINARY_PATH=./deps/webrtc/lib/webrtc.lib \
  -DLIBMEDIASOUPCLIENT_INCLUDE_PATH=./deps/mediasoupclient/include \
  -DLIBMEDIASOUPCLIENT_BINARY_PATH=./deps/mediasoupclient/lib/mediasoupclient.lib
```

## Building from Source

If you prefer to build from source instead of using pre-built artifacts:

### Prerequisites

- Windows 10/11 with **Visual Studio 2022** (C++ desktop development workload)
- **Python 3.6+**
- **Git**

### Build Commands

```bash
# Step 1: Build WebRTC (takes ~2 hours)
python scripts/build_webrtc.py --branch branch-heads/6099 --config Release --arch x64 --build

# Step 2: Build mediasoupclient
python scripts/build_mediasoupclient.py --version v3.4.3 --config Release --arch x64 --build

# Step 3: Package combined toolchain
python scripts/package_toolchain.py --webrtc-branch branch-heads/6099 --mediasoupclient-version v3.4.3

# Output will be at: output/archive/mediasoup-native-toolchain-windows-x64-Release.zip
```

### Build Options

| Option | Default | Description |
|--------|---------|-------------|
| `--branch` | `branch-heads/6099` | WebRTC branch to build |
| `--version` | `v3.4.3` | libmediasoupclient version tag |
| `--config` | `Release` | Build configuration (Release/Debug) |
| `--arch` | `x64` | Target architecture |
| `--jobs` | `8` | Parallel build jobs (mediasoupclient only) |
| `--clean` | — | Clean output before building |

## Version Compatibility

| Toolchain Release | WebRTC Branch | libmediasoupclient | Notes |
|-------------------|---------------|-------------------|-------|
| v1.0.0 | branch-heads/6099 (M119/M120) | v3.4.3 | Initial release |

> **Note:** Not all WebRTC branches are compatible with all libmediasoupclient versions. Check the [mediasoup compatibility matrix](https://github.com/versatica/libmediasoupclient#compatibility) before upgrading.

## License

The build scripts in this repository are licensed under MIT.

**Note:** The produced artifacts (libwebrtc, libmediasoupclient, sdptransform) are governed by their respective upstream licenses:
- WebRTC: BSD-style license
- libmediasoupclient: MIT
- sdptransform: MIT
