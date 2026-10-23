"""
Web requests (BSData and Wahapedia downloads, the Play relay), checked against certifi's certificate list.

Python on Windows trusts whatever certificates the Windows store happens to hold, and that store can keep expired
copies (an old "ISRG Root X2" cross-certificate, for one) that make valid HTTPS sites fail with "certificate has
expired". certifi's list is current with each build and the same on every PC.
"""

import functools
import ssl
import urllib.request

import certifi


@functools.cache
def ssl_context():
    return ssl.create_default_context(cafile=certifi.where())


def urlopen(request, timeout):
    """urllib.request.urlopen with certifi's certificates."""
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())
