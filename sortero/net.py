"""HTTPS with a CA bundle that survives being frozen into an app.

A PyInstaller build ships its own Python, and that Python has no access to the
system's CA certificates - OpenSSL looks in paths that don't exist inside the
bundle. Every HTTPS request then dies with:

    CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate

So we verify against certifi's bundle, which is packaged with the app. Running
from source, this is equally correct: certifi is a normal dependency.

Never fall back to unverified TLS. A failed certificate check on an OAuth token
exchange is exactly the case where silently continuing would be dangerous.
"""
import ssl
import urllib.request

_CONTEXT = None


def context():
    """A verifying SSL context, using certifi's CA bundle when available."""
    global _CONTEXT
    if _CONTEXT is None:
        try:
            import certifi
            _CONTEXT = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            # No certifi: fall back to the platform store. Still verifying.
            _CONTEXT = ssl.create_default_context()
    return _CONTEXT


def urlopen(req, timeout=30):
    return urllib.request.urlopen(req, timeout=timeout, context=context())


def describe_ssl_error(exc):
    """Turn a certificate failure into something a user can act on."""
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "SSLCertVerification" in text:
        return ("Couldn't verify GitHub's certificate. This usually means the "
                "app is missing its certificate bundle, or a corporate network "
                "or VPN is intercepting HTTPS.\n\n"
                "If you're on a work network or VPN, try again off it.")
    return None
