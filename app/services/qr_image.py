"""Renders a `tg://login` URL as a QR code PNG.

Shared by the bot (sends the raw PNG bytes as a photo) and the Mini App API
(embeds it as a base64 data URL in the JSON response) — generating the image
server-side keeps the actual login token from ever being sent to a
third-party QR-rendering service, which would otherwise be handed the means
to log into the user's Telegram account.
"""

import base64
import io

import qrcode


def render_qr_png(url: str) -> bytes:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_qr_data_url(url: str) -> str:
    encoded = base64.b64encode(render_qr_png(url)).decode("ascii")
    return f"data:image/png;base64,{encoded}"
