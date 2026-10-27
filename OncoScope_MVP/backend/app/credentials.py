"""Credential prompts for the command-line tools (backup, restore, encrypt_migrate).

Secrets are read with getpass on a terminal, or as one line each from standard input when it is not a terminal (scripted
tests). They are never taken from command-line arguments or environment variables, which other processes and shell
histories can see, and never printed.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

from . import vault


class CredentialError(RuntimeError):
    pass


def ask_secret(prompt: str) -> str:
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(prompt)
    line = sys.stdin.readline() if sys.stdin is not None else ""
    if not line:
        raise CredentialError("no credential was given on standard input")
    return line.rstrip("\r\n")


def open_vault(vault_file: Path, *, use_recovery_key: bool = False, secret: str | None = None) -> vault.Opened:
    """Opens a vault with the passphrase (default) or recovery key, prompting when `secret` is not given."""
    try:
        doc = vault.read(vault_file)
    except vault.VaultError as exc:
        raise CredentialError(exc.message) from None
    if secret is None:
        secret = ask_secret("Recovery key: " if use_recovery_key else "Owner passphrase: ")
    try:
        return vault.open_with_recovery_key(doc, secret) if use_recovery_key else vault.open_with_passphrase(doc, secret)
    except vault.WrongCredential:
        raise CredentialError("incorrect recovery key" if use_recovery_key else "incorrect passphrase") from None
    except vault.VaultError as exc:
        raise CredentialError(exc.message) from None


def database_key_from_vault(vault_file: Path, *, use_recovery_key: bool = False, secret: str | None = None) -> bytearray:
    """The SQLCipher key of the database this vault protects. Only v2 vaults record an encrypted database."""
    opened = open_vault(vault_file, use_recovery_key=use_recovery_key, secret=secret)
    try:
        if vault.database_state(opened.doc) == "plaintext":
            raise CredentialError("this key vault does not protect an encrypted database")
        return bytearray(vault.derive(opened.dek, "database"))
    finally:
        opened.wipe()
