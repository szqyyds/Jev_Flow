"""Bounded retries for read/decision requests; never disables TLS validation."""
import json
import ssl
import time
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


def transient_error(exc):
    if isinstance(exc, HTTPError):
        return exc.code in {502, 503, 504}
    reason = getattr(exc, 'reason', exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return False
    return (isinstance(reason, (TimeoutError, ConnectionError, ssl.SSLEOFError)) or
            any(marker in str(reason) for marker in (
                'UNEXPECTED_EOF', 'EOF occurred', 'Tunnel connection failed: 502',
                'Tunnel connection failed: 503', 'Tunnel connection failed: 504')))


def read_json(request, timeout=15, attempts=3, max_bytes=1_000_000, records=None):
    records = records if records is not None else []
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError('服务响应超过大小限制')
            return json.loads(raw)
        except (URLError, OSError) as exc:
            retryable = transient_error(exc)
            records.append({'attempt': attempt, 'error': type(exc).__name__, 'retryable': retryable})
            if not retryable or attempt == attempts:
                raise
            if isinstance(exc, HTTPError):
                exc.close()
            time.sleep(0.5 * attempt)
