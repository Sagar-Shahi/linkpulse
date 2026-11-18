"""QR codes for short links, drawn on the server.

The code is generated here with the `segno` package, so no address is ever sent to a third-party
QR service. It encodes the short link, so every scan is counted like any other click.
"""
import io

import segno


def qr_svg(url):
    """An SVG image (bytes) of a QR code for `url`, with a quiet zone so phones can read it."""
    code = segno.make(url, error='m')
    buffer = io.BytesIO()
    code.save(buffer, kind='svg', scale=8, border=3, dark='#000000', light='#ffffff', xmldecl=False, svgns=True, nl=False)
    return buffer.getvalue()
