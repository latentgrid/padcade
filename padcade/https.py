"""The board's own certificate, so that it can also answer on a secure address. No authority vouches for a device in
someone's home, so a browser asks once whether to trust it."""
import os
import socket
import subprocess
import time
from pathlib import Path

LIFE = 800  # days: phones turn down a certificate that is valid for much longer


def certificate(home):
    """The certificate and its key, made on first use and again before they run out: (certificate, key).
    None where openssl is missing or does not make them."""
    name = socket.gethostname()
    folder = Path(home) / 'https'
    cert, key = folder / f'{name}.pem', folder / f'{name}.key'
    if cert.is_file() and key.is_file() and time.time() - cert.stat().st_mtime < (LIFE - 30) * 86400:
        return cert, key
    new_cert, new_key = folder / 'new.pem', folder / 'new.key'
    names = ','.join(f'DNS:{n}' for n in dict.fromkeys([name, name + '.local', 'localhost']))
    try:
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes',
                        '-days', str(LIFE), '-keyout', str(new_key), '-out', str(new_cert), '-subj', f'/CN={name}.local',
                        '-addext', f'subjectAltName={names},IP:127.0.0.1'],
                       check=True, capture_output=True, timeout=60, preexec_fn=lambda: os.umask(0o077))  # (the key is the owner's alone)
        new_key.replace(key)
        new_cert.replace(cert)
    except (OSError, subprocess.SubprocessError):
        return None
    return cert, key
