"""
Load build configuration from build-config.yml at the project root.
"""

import os
import sys

try:
    import yaml
except ImportError:
    print("[!] ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

_CONFIG_CACHE = None


def get_config_path():
    """Return the absolute path to build-config.yml in the project root."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build-config.yml")


def load_config():
    """Load and cache the build configuration from build-config.yml."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path = get_config_path()
    if not os.path.exists(config_path):
        print(f"[!] ERROR: Build config not found at {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        _CONFIG_CACHE = yaml.safe_load(f)

    print(f"[*] Loaded build config from: {config_path}")
    return _CONFIG_CACHE


def get(key_path, default=None):
    """Get a config value by dot-separated key path, e.g. get('webrtc.default_branch')."""
    config = load_config()
    keys = key_path.split(".")
    value = config
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k)
        else:
            return default
        if value is None:
            return default
    return value
