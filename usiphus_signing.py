
#!/usr/bin/env python3
"""Asymmetric (Ed25519) signing for Usiphus audit events and anchor files.

Why: an HMAC key is shared, so anyone who can verify can also forge. With Ed25519 the
verifier holds only PUBLIC keys (a "trusted keys" file); only the signer holds the private key.

Backends (chosen automatically):
  1. the optional `cryptography` package  (pip install cryptography)
  2. the `openssl` command line tool, version 3.x  (no Python dependency)

Signature string format:  ed25519:<key_id>:<base64 signature>
key_id = first 16 hex chars of SHA-256 over the public key (DER SubjectPublicKeyInfo).

Private keys never leave the machine that signs. For a KMS/HSM/agent, implement a class
with the same `sign(message: str) -> str`, `actor`-free interface as Ed25519Signer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import atomic_write_text  # noqa: E402

VERSION = "1.0.0"
ALGORITHM = "ed25519"
PASSPHRASE_ENV = "USIPHUS_AUDIT_KEY_PASSPHRASE"


class SigningError(ValueError):
    pass


try:  # optional dependency
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    HAVE_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - exercised when the package is absent
    HAVE_CRYPTOGRAPHY = False

_FORCE_OPENSSL = False  # tests flip this to exercise the openssl backend


def _use_cryptography() -> bool:
    return HAVE_CRYPTOGRAPHY and not _FORCE_OPENSSL


def _openssl() -> str:
    exe = shutil.which("openssl")
    if not exe:
        raise SigningError("Ed25519 signing needs either `pip install cryptography` or the openssl command line tool (3.x)")
    return exe


def _run_openssl(args: list[str], *, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run([_openssl(), *args], input=stdin, capture_output=True, check=False)
    except OSError as exc:
        raise SigningError(f"openssl could not be run: {exc}") from exc
    if proc.returncode != 0:
        raise SigningError("openssl failed: " + proc.stderr.decode("utf-8", "replace").strip()[:300])
    return proc.stdout


def key_id_from_spki(spki_der: bytes) -> str:
    return hashlib.sha256(spki_der).hexdigest()[:16]


def _pem_to_der(pem: str) -> bytes:
    lines = [ln.strip() for ln in pem.strip().splitlines() if ln.strip() and not ln.startswith("-----")]
    try:
        return base64.b64decode("".join(lines), validate=True)
    except ValueError as exc:
        raise SigningError("Public key is not valid PEM") from exc


def public_key_id(public_pem: str) -> str:
    return key_id_from_spki(_pem_to_der(public_pem))


def _check_private_key_permissions(path: Path) -> None:
    if os.name == "posix" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SigningError(f"Private key {path} is readable by other users; run: chmod 600 {path}")


def passphrase_from_env(name: str = PASSPHRASE_ENV) -> bytes | None:
    raw = os.environ.get(name, "")
    return raw.encode("utf-8") if raw else None


# --------------------------------------------------------------------------
# Key generation
# --------------------------------------------------------------------------
def generate_keypair(private_path: Path, public_path: Path, passphrase: bytes | None = None) -> str:
    """Create an Ed25519 key pair. Returns the key_id. Refuses to overwrite existing files."""
    private_path, public_path = Path(private_path), Path(public_path)
    for path in (private_path, public_path):
        if path.exists():
            raise SigningError(f"Refusing to overwrite existing file: {path}")
    if _use_cryptography():
        key = ed25519.Ed25519PrivateKey.generate()
        enc = (serialization.BestAvailableEncryption(passphrase) if passphrase else serialization.NoEncryption())
        private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
        public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    else:
        with tempfile.TemporaryDirectory(prefix="usiphus-key-", ignore_cleanup_errors=True) as tmp:
            priv = Path(tmp) / "k.pem"
            cmd = ["genpkey", "-algorithm", "ED25519", "-out", str(priv)]
            if passphrase:
                cmd += ["-aes-256-cbc", "-pass", "env:USIPHUS_TMP_PASS"]
                os.environ["USIPHUS_TMP_PASS"] = passphrase.decode("utf-8")
            try:
                _run_openssl(cmd)
            finally:
                os.environ.pop("USIPHUS_TMP_PASS", None)
            private_pem = priv.read_bytes()
            pub_cmd = ["pkey", "-in", str(priv), "-pubout"]
            if passphrase:
                pub_cmd += ["-passin", "env:USIPHUS_TMP_PASS"]
                os.environ["USIPHUS_TMP_PASS"] = passphrase.decode("utf-8")
            try:
                public_pem = _run_openssl(pub_cmd)
            finally:
                os.environ.pop("USIPHUS_TMP_PASS", None)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(private_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(private_pem)
    public_path.write_bytes(public_pem)
    return public_key_id(public_pem.decode("ascii"))


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------
class Ed25519Signer:
    def __init__(self, private_key_path: str | Path, passphrase: bytes | None = None) -> None:
        self.path = Path(private_key_path)
        if not self.path.is_file():
            raise SigningError(f"Private key not found: {self.path}")
        _check_private_key_permissions(self.path)
        self._pem = self.path.read_bytes()
        self._passphrase = passphrase
        if _use_cryptography():
            try:
                self._key = serialization.load_pem_private_key(self._pem, password=passphrase)
            except (ValueError, TypeError) as exc:
                raise SigningError(f"Could not load private key (wrong passphrase or not an Ed25519 PEM?): {exc}") from exc
            if not isinstance(self._key, ed25519.Ed25519PrivateKey):
                raise SigningError("Private key is not an Ed25519 key")
            spki = self._key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        else:
            self._key = None
            spki = self._openssl_spki()
        self.key_id = key_id_from_spki(spki)

    def _pass_args(self, flag: str) -> tuple[list[str], dict[str, str]]:
        if self._passphrase:
            return [flag, "env:USIPHUS_TMP_PASS"], {"USIPHUS_TMP_PASS": self._passphrase.decode("utf-8")}
        return [], {}

    def _openssl_spki(self) -> bytes:
        extra, env = self._pass_args("-passin")
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return _run_openssl(["pkey", "-in", str(self.path), "-pubout", "-outform", "DER", *extra])
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def sign(self, message: str) -> str:
        data = message.encode("utf-8")
        if self._key is not None:
            raw = self._key.sign(data)
        else:
            extra, env = self._pass_args("-passin")
            with tempfile.TemporaryDirectory(prefix="usiphus-sign-", ignore_cleanup_errors=True) as tmp:
                msg = Path(tmp) / "m"
                msg.write_bytes(data)
                old = {k: os.environ.get(k) for k in env}
                os.environ.update(env)
                try:
                    raw = _run_openssl(["pkeyutl", "-sign", "-inkey", str(self.path), "-rawin", "-in", str(msg), *extra])
                finally:
                    for k, v in old.items():
                        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return f"{ALGORITHM}:{self.key_id}:{base64.b64encode(raw).decode('ascii')}"


# --------------------------------------------------------------------------
# Verification (public keys only)
# --------------------------------------------------------------------------
def _verify_raw(public_pem: str, message: bytes, raw_sig: bytes) -> bool:
    if _use_cryptography():
        try:
            key = serialization.load_pem_public_key(public_pem.encode("ascii"))
            if not isinstance(key, ed25519.Ed25519PublicKey):
                return False
            key.verify(raw_sig, message)
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False
    with tempfile.TemporaryDirectory(prefix="usiphus-verify-", ignore_cleanup_errors=True) as tmp:
        t = Path(tmp)
        (t / "pub.pem").write_text(public_pem, encoding="ascii")
        (t / "m").write_bytes(message)
        (t / "s").write_bytes(raw_sig)
        try:
            _run_openssl(["pkeyutl", "-verify", "-pubin", "-inkey", str(t / "pub.pem"), "-rawin",
                          "-in", str(t / "m"), "-sigfile", str(t / "s")])
            return True
        except SigningError:
            return False


class TrustedKeys:
    """Public keys allowed to sign, per actor. JSON: {"keys":[{"actor":"alice","public_key_pem":"..."}]}"""

    def __init__(self, entries: list[tuple[str, str]]) -> None:
        self.by_actor: dict[str, dict[str, str]] = {}
        for actor, pem in entries:
            self.by_actor.setdefault(actor, {})[public_key_id(pem)] = pem

    @classmethod
    def load(cls, path: str | Path) -> "TrustedKeys":
        p = Path(path)
        if not p.is_file():
            raise SigningError(f"Trusted keys file not found: {p}")
        try:
            payload = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SigningError(f"Invalid trusted keys file: {exc}") from exc
        rows = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise SigningError("Trusted keys file must be an object with a non-empty 'keys' list")
        entries = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get("actor"), str) or not row["actor"].strip() \
                    or not isinstance(row.get("public_key_pem"), str):
                raise SigningError(f"Trusted key {i} requires 'actor' and 'public_key_pem'")
            entries.append((row["actor"].strip(), row["public_key_pem"]))
        return cls(entries)

    def verify(self, message: str, signature: str, actor: str) -> bool:
        parts = signature.split(":", 2) if isinstance(signature, str) else []
        if len(parts) != 3 or parts[0] != ALGORITHM:
            return False
        keys = self.by_actor.get(actor, {})
        pem = keys.get(parts[1])
        if pem is None:
            return False
        try:
            raw = base64.b64decode(parts[2], validate=True)
        except ValueError:
            return False
        return _verify_raw(pem, message.encode("utf-8"), raw)

    def verify_any_actor(self, message: str, signature: str) -> str | None:
        """Return the actor whose trusted key verifies the signature, if any."""
        for actor in self.by_actor:
            if self.verify(message, signature, actor):
                return actor
        return None


def trusted_entry(actor: str, public_path: str | Path) -> dict[str, str]:
    return {"actor": actor, "public_key_pem": Path(public_path).read_text(encoding="ascii")}


# --------------------------------------------------------------------------
# Signed anchor documents (heads of audit chains, kept OUTSIDE the claims file)
# --------------------------------------------------------------------------
def _anchor_message(doc: dict[str, Any]) -> str:
    body = {k: doc[k] for k in ("schema_version", "generated_at", "signed_by", "anchors")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def build_anchor_document(anchors: dict[str, str], signer: Ed25519Signer, actor: str, generated_at: str) -> dict[str, Any]:
    doc: dict[str, Any] = {"schema_version": 1, "generated_at": generated_at, "signed_by": actor, "anchors": dict(sorted(anchors.items()))}
    doc["signature"] = signer.sign(_anchor_message(doc))
    return doc


def verify_anchor_document(doc: Any, trusted: TrustedKeys) -> dict[str, str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("anchors"), dict) or not isinstance(doc.get("signature"), str):
        raise SigningError("Anchor file must be a signed object with 'anchors' and 'signature'")
    if doc.get("schema_version") != 1 or not isinstance(doc.get("signed_by"), str) or not isinstance(doc.get("generated_at"), str):
        raise SigningError("Anchor file has unsupported schema or missing signed_by/generated_at")
    if not trusted.verify(_anchor_message(doc), doc["signature"], doc["signed_by"]):
        raise SigningError("Anchor file signature is invalid or the signer is not trusted")
    for claim_id, head in doc["anchors"].items():
        if not isinstance(claim_id, str) or not isinstance(head, str) or len(head) != 64:
            raise SigningError("Anchor entries must map claim_id to a 64-character hash")
    return dict(doc["anchors"])


def load_anchor_file(path: str | Path, trusted: TrustedKeys) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        raise SigningError(f"Anchor file not found: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SigningError(f"Invalid anchor file: {exc}") from exc
    return verify_anchor_document(doc, trusted)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", path)
