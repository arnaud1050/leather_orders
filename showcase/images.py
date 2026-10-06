"""
Turning an uploaded photo into the copy the showcase keeps.

Every photo is decoded and re-encoded, never stored as uploaded, for one
reason above the others: **metadata**. A phone photo's EXIF block carries
GPS coordinates, and a piece photographed at a client's home would
otherwise publish their address on the studio's website (SC11). Pillow
writes no EXIF unless it's handed some, so re-encoding is what strips it —
after `exif_transpose` has applied the orientation tag, which would
otherwise be lost with it and leave portrait photos lying on their side.

The ICC colour profile is kept: it carries no personal data, and dropping
it shifts colours on wide-gamut phone photos.
"""

from dataclasses import dataclass
from io import BytesIO

from showcase import config


class ImageError(Exception):
    """A refused upload, with a reason safe to show the studio."""


@dataclass
class Processed:
    full: bytes
    thumbnail: bytes
    width: int
    height: int


def _encode(image, edge: int, icc_profile) -> bytes:
    copy = image.copy()
    copy.thumbnail((edge, edge))
    buffer = BytesIO()
    options = {"format": "JPEG", "quality": config.JPEG_QUALITY,
               "optimize": True, "progressive": True}
    if icc_profile:
        options["icc_profile"] = icc_profile
    copy.save(buffer, **options)
    return buffer.getvalue()


def process(data: bytes, name: str = "That file") -> Processed:
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ImageError(
            f"{name} is {len(data) / 1024 / 1024:.1f}MB, over the "
            f"{config.MAX_UPLOAD_BYTES / 1024 / 1024:.0f}MB limit for a photo.")
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover — Pillow is in requirements.txt
        raise ImageError("Photos can't be processed on this server.") from exc

    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in config.ACCEPTED_FORMATS:
                raise ImageError(f"{name} isn't a JPEG, PNG or WebP photo.")
            if image.width * image.height > config.MAX_PIXELS:
                raise ImageError(f"{name} is too large to be a photo of a piece.")
            image.load()
            icc_profile = image.info.get("icc_profile")
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                # A transparent PNG goes onto white rather than black, which
                # is what a plain convert("RGB") would give it.
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            full = _encode(image, config.FULL_EDGE, icc_profile)
            thumbnail = _encode(image, config.THUMB_EDGE, icc_profile)
            with Image.open(BytesIO(full)) as stored:
                width, height = stored.size
    except ImageError:
        raise
    except Exception as exc:
        raise ImageError(f"{name} doesn't look like a photo that can be opened.") from exc
    return Processed(full=full, thumbnail=thumbnail, width=width, height=height)
