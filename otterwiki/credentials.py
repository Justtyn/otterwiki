"""Authenticated encryption for repository credentials."""

import base64
import hashlib


class CredentialError(ValueError):
    pass


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise CredentialError(
            "缺少 cryptography 依赖，无法安全处理仓库凭据。"
        ) from error
    from otterwiki.server import app

    material = (
        "otterwiki-space-git-v1\0" + str(app.config["SECRET_KEY"])
    ).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def encrypt_secret(value):
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception as error:
        raise CredentialError("仓库凭据无法解密，请重新录入。") from error
