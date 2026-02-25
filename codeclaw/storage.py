"""Storage helpers for encrypted-at-rest local artifacts."""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

from .config import CodeClawConfig, load_config, save_config

_ENC_PREFIX = "CODECLAW_ENCRYPTED_V1:"
_LOCAL_KEY_FILE = Path.home() / ".codeclaw" / "encryption.key"
_KEYRING_SERVICE = "codeclaw"


def _load_crypto():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    return Fernet


def _load_keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def _to_fernet_key(raw_key: str) -> bytes:
    return base64.urlsafe_b64encode(raw_key.encode("utf-8")[:32].ljust(32, b"0"))


def _read_local_fallback_key() -> str | None:
    if not _LOCAL_KEY_FILE.exists():
        return None
    try:
        return _LOCAL_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_local_fallback_key(key: str) -> None:
    _LOCAL_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCAL_KEY_FILE.write_text(key, encoding="utf-8")
    with contextlib_suppress_oserror():
        _LOCAL_KEY_FILE.chmod(0o600)


class contextlib_suppress_oserror:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, _tb):
        return exc_type is not None and issubclass(exc_type, OSError)


def ensure_encryption_key(config: CodeClawConfig | None = None) -> tuple[bool, str | None, str | None]:
    """Ensure encryption key reference exists and return status.

    Returns:
      (available, key_ref, backend) where backend is "keyring" | "file" | None.
    """
    cfg = config if config is not None else load_config()
    key_ref = cfg.get("encryption_key_ref")
    keyring_mod = _load_keyring()

    if key_ref and keyring_mod is not None:
        try:
            value = keyring_mod.get_password(_KEYRING_SERVICE, key_ref)
            if value:
                return True, key_ref, "keyring"
        except Exception:
            pass

    fallback = _read_local_fallback_key()
    if fallback:
        if cfg.get("encryption_key_ref") != "file:default":
            cfg["encryption_key_ref"] = "file:default"
            save_config(cfg)
        return True, "file:default", "file"

    raw_key = secrets.token_urlsafe(32)
    if keyring_mod is not None:
        key_ref = f"key-{secrets.token_hex(8)}"
        try:
            keyring_mod.set_password(_KEYRING_SERVICE, key_ref, raw_key)
            cfg["encryption_key_ref"] = key_ref
            save_config(cfg)
            return True, key_ref, "keyring"
        except Exception:
            pass

    _write_local_fallback_key(raw_key)
    cfg["encryption_key_ref"] = "file:default"
    save_config(cfg)
    return True, "file:default", "file"


def _resolve_raw_key(config: CodeClawConfig | None = None) -> str | None:
    cfg = config if config is not None else load_config()
    key_ref = cfg.get("encryption_key_ref")
    keyring_mod = _load_keyring()
    if key_ref and keyring_mod is not None and key_ref != "file:default":
        try:
            value = keyring_mod.get_password(_KEYRING_SERVICE, key_ref)
            if value:
                return value
        except Exception:
            return None
    return _read_local_fallback_key()


def encryption_status(config: CodeClawConfig | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_config()
    keyring_mod = _load_keyring()
    crypto = _load_crypto()
    raw_key = _resolve_raw_key(cfg)
    return {
        "enabled": bool(cfg.get("encryption_enabled", True)),
        "key_ref": cfg.get("encryption_key_ref"),
        "key_present": bool(raw_key),
        "keyring_available": keyring_mod is not None,
        "crypto_available": crypto is not None,
    }


def is_encrypted_text(text: str) -> bool:
    return text.startswith(_ENC_PREFIX)


def encrypt_text(plain: str, config: CodeClawConfig | None = None) -> str:
    crypto = _load_crypto()
    if crypto is None:
        return plain
    raw_key = _resolve_raw_key(config)
    if not raw_key:
        return plain
    fernet = crypto(_to_fernet_key(raw_key))
    token = fernet.encrypt(plain.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_text(payload: str, config: CodeClawConfig | None = None) -> str:
    if not is_encrypted_text(payload):
        return payload
    crypto = _load_crypto()
    if crypto is None:
        return payload
    raw_key = _resolve_raw_key(config)
    if not raw_key:
        return payload
    token = payload[len(_ENC_PREFIX):]
    try:
        fernet = crypto(_to_fernet_key(raw_key))
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return payload


def maybe_encrypt_file(path: Path, config: CodeClawConfig | None = None) -> bool:
    cfg = config if config is not None else load_config()
    if "encryption_enabled" not in cfg:
        return False
    if not cfg.get("encryption_enabled", True):
        return False
    if not cfg.get("encryption_key_ref"):
        return False
    payload = read_text(path, config=cfg)
    if is_encrypted_text(payload):
        return True
    encrypted = encrypt_text(payload, config=cfg)
    if encrypted == payload:
        return False
    write_text(path, encrypted)
    return True


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if os.name != "nt":
        with contextlib_suppress_oserror():
            path.chmod(0o600)


def read_text(path: Path, config: CodeClawConfig | None = None) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return decrypt_text(raw, config=config)


def read_jsonl(path: Path, config: CodeClawConfig | None = None) -> list[dict[str, Any]]:
    text = read_text(path, config=config)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
