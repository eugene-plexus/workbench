"""One TLS context, including private CAs installed by the node's operator."""

from __future__ import annotations

import os
import ssl
from functools import cache

import truststore


@cache
def ssl_context() -> ssl.SSLContext:
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cafile, capath = os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR")
    if cafile or capath:
        context.load_verify_locations(cafile=cafile, capath=capath)
    return context
