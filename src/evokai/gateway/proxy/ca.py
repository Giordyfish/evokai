"""The proxy's certificate authority: a root to distribute and a constrained intermediate.

``evokai proxy init-ca`` makes both. The root (ECDSA P-256, ten years) is what PCs trust; its key
is needed only to issue a new intermediate and is kept offline. The intermediate (three years,
path length 0) carries **X.509 Name Constraints**: it may sign only for the catalogue's domains and
the check host (``evokai.gateway.proxy.catalogue.NAME_CONSTRAINTS``), and for no IP address. A
stolen intermediate key then cannot impersonate any other site: TLS clients reject a certificate
for ``example.com`` under it.

The proxy reads the intermediate's key (``EVOKAI_PROXY_CA_KEY``) and its certificate followed by
the root's (``EVOKAI_PROXY_CA_CERT``), as PEM or as file paths. The API reads only the certificate,
to serve the root at ``/proxy/ca.crt``. Leaf certificates are minted per host and kept in memory.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from evokai.gateway.proxy.catalogue import NAME_CONSTRAINTS

EcKey = ec.EllipticCurvePrivateKey
ROOT_YEARS = 10
INTERMEDIATE_YEARS = 3


class CaError(ValueError):
    """The CA is missing, unreadable or unfit for the proxy."""


@dataclass(frozen=True)
class Authority:
    """The intermediate the proxy signs with, and the chain it presents."""

    key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey
    cert: x509.Certificate
    chain: list[x509.Certificate]
    """The intermediate first, then the root when the file holds it."""
    permitted: list[str]
    """The DNS subtrees of its Name Constraints."""


def _name(cn: str, org: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


def make(
    org: str = "Evokai", domains: tuple[str, ...] = NAME_CONSTRAINTS
) -> tuple[EcKey, x509.Certificate, EcKey, x509.Certificate]:
    """A new root and an intermediate constrained to the domains.

    Returns:
        (root key, root certificate, intermediate key, intermediate certificate).
    """
    now = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = _name(f"{org} gateway proxy root CA", org)
    root_ski = x509.SubjectKeyIdentifier.from_public_key(root_key.public_key())
    root = (
        x509
        .CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=365 * ROOT_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(_ca_usage(), critical=True)
        .add_extension(root_ski, critical=False)
        .sign(root_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName(d) for d in domains],
        excluded_subtrees=[
            x509.IPAddress(ipaddress.ip_network("0.0.0.0/0")),
            x509.IPAddress(ipaddress.ip_network("::/0")),
        ],
    )
    cert = (
        x509
        .CertificateBuilder()
        .subject_name(_name(f"{org} gateway proxy intermediate CA", org))
        .issuer_name(root_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=365 * INTERMEDIATE_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_ca_usage(), critical=True)
        .add_extension(constraints, critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(root_ski),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    return root_key, root, key, cert


def _ca_usage() -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False,
    )


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def init(out: Path, org: str = "Evokai") -> dict[str, Path]:
    """Write a new root and intermediate to a directory (``evokai proxy init-ca``).

    Files: ``root.crt`` (to distribute), ``root.key`` (keep offline), ``intermediate.crt`` (the
    intermediate and the root: ``EVOKAI_PROXY_CA_CERT``) and ``intermediate.key``
    (``EVOKAI_PROXY_CA_KEY``). Keys are written readable by their owner only.

    Returns:
        Each file's role to its path.

    Raises:
        CaError: A file is already there (nothing is overwritten).
    """
    files = {
        "root_cert": out / "root.crt",
        "root_key": out / "root.key",
        "cert": out / "intermediate.crt",
        "key": out / "intermediate.key",
    }
    if taken := [str(p) for p in files.values() if p.exists()]:
        raise CaError(f"already there, not overwritten: {', '.join(taken)}")
    out.mkdir(parents=True, exist_ok=True)
    root_key, root, key, cert = make(org)
    _write(files["root_key"], _pem_key(root_key), secret=True)
    _write(files["key"], _pem_key(key), secret=True)
    _write(files["root_cert"], _pem(root))
    _write(files["cert"], _pem(cert) + _pem(root))
    return files


def _write(path: Path, data: bytes, *, secret: bool = False) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if secret else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def material(value: str | None, what: str) -> bytes:
    """A PEM given inline (as a Key Vault reference delivers it) or as a file path.

    Returns:
        The PEM bytes.

    Raises:
        CaError: Not set, or not readable.
    """
    if not value or not value.strip():
        raise CaError(f"{what} is not set")
    text = value.strip()
    if text.startswith("-----BEGIN"):
        return text.replace("\\n", "\n").encode()
    try:
        return Path(text).expanduser().read_bytes()
    except OSError as e:
        raise CaError(f"{what}: cannot read {text} ({type(e).__name__})") from e


def certificates(pem: bytes) -> list[x509.Certificate]:
    """The certificates of a PEM bundle, in order.

    Returns:
        The certificates.

    Raises:
        CaError: None could be read.
    """
    try:
        certs = x509.load_pem_x509_certificates(pem)
    except ValueError as e:
        raise CaError("the certificate is not PEM") from e
    if not certs:
        raise CaError("no certificate found")
    return certs


def root_of(pem: bytes) -> x509.Certificate:
    """The root to distribute: the bundle's self-signed certificate (else its last).

    Returns:
        The certificate.
    """
    certs = certificates(pem)
    return next((c for c in certs if c.issuer == c.subject), certs[-1])


def permitted(cert: x509.Certificate) -> list[str] | None:
    """The DNS names a CA certificate's Name Constraints permit.

    Returns:
        The names, or None when it has no Name Constraints.
    """
    try:
        nc = cert.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return None
    return [str(n.value) for n in nc.permitted_subtrees or [] if isinstance(n, x509.DNSName)]


def covers(names: list[str], host: str) -> bool:
    """Whether a Name Constraints list permits a host (a name covers itself and its subdomains).

    Returns:
        True when it does.
    """
    host = host.lower().rstrip(".")
    return any(
        host == n.lower().lstrip(".") or host.endswith("." + n.lower().lstrip(".")) for n in names
    )


def load(key_value: str | None, cert_value: str | None) -> Authority:
    """The proxy's intermediate, checked: key and certificate match, it is a CA, it is constrained.

    Returns:
        The authority.

    Raises:
        CaError: What is wrong, in words an operator can act on.
    """
    key_pem = material(key_value, "EVOKAI_PROXY_CA_KEY")
    chain = certificates(material(cert_value, "EVOKAI_PROXY_CA_CERT"))
    cert = chain[0]
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except (ValueError, TypeError) as e:
        raise CaError("EVOKAI_PROXY_CA_KEY is not an unencrypted PEM private key") from e
    if not isinstance(key, (ec.EllipticCurvePrivateKey, rsa.RSAPrivateKey)):
        raise CaError("EVOKAI_PROXY_CA_KEY must be an EC or RSA key")
    pub = serialization.PublicFormat.SubjectPublicKeyInfo
    enc = serialization.Encoding.DER
    if key.public_key().public_bytes(enc, pub) != cert.public_key().public_bytes(enc, pub):
        raise CaError(
            "EVOKAI_PROXY_CA_KEY does not belong to the first certificate of EVOKAI_PROXY_CA_CERT"
        )
    try:
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        basic = None
    if basic is None or not basic.ca:
        raise CaError("EVOKAI_PROXY_CA_CERT is not a CA certificate")
    names = permitted(cert)
    if not names:
        raise CaError(
            "the intermediate has no X.509 Name Constraints: create one with "
            "`evokai proxy init-ca` (a stolen unconstrained key could impersonate any site)"
        )
    if cert.not_valid_after_utc < dt.datetime.now(dt.UTC):
        raise CaError("the intermediate CA has expired")
    return Authority(key=key, cert=cert, chain=chain, permitted=names)
