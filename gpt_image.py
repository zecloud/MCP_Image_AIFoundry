import base64
import binascii
import os
import re
from urllib.parse import urlsplit, urlunsplit

from openai import AsyncOpenAI


GPT_IMAGE_MODEL = "gpt-image-2.5"
GPT_QUALITIES = {"auto", "low", "medium", "high", "xhigh", "max"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_REFERENCE_IMAGES = 16
MAX_REFERENCE_BYTES = 50 * 1024 * 1024


def validate_gpt_parameters(size, quality, n):
    if n != 1:
        raise ValueError("GPT Image currently supports only n=1 in this MCP")
    quality = "auto" if quality in (None, "standard") else quality
    if quality not in GPT_QUALITIES:
        raise ValueError("GPT Image quality must be auto, low, medium, high, xhigh or max")
    size = "auto" if size is None else size
    if size != "auto":
        if not re.fullmatch(r"[0-9]{1,4}x[0-9]{1,4}", size):
            raise ValueError("GPT Image size must be auto or WIDTHxHEIGHT")
        width, height = map(int, size.split("x"))
        if (
            width <= 0 or height <= 0
            or width % 16 or height % 16
            or max(width, height) > 3840
            or max(width, height) > 3 * min(width, height)
            or not 655360 <= width * height <= 8294400
        ):
            raise ValueError(
                "GPT Image dimensions must be multiples of 16, at most 3840 pixels "
                "per edge, with aspect ratio 1:3 to 3:1 and 655360 to 8294400 pixels"
            )
    return size, quality


def validate_reference_count(filenames):
    if not 1 <= len(filenames) <= MAX_REFERENCE_IMAGES:
        raise ValueError("GPT Image editing requires 1 to 16 reference images")


def _reference_files(images):
    validate_reference_count(images)
    files = []
    for index, image in enumerate(images):
        if not image or len(image) >= MAX_REFERENCE_BYTES:
            raise ValueError("Each GPT Image reference must be nonempty and smaller than 50 MB")
        if image.startswith(PNG_SIGNATURE):
            extension, mime_type = "png", "image/png"
        elif image.startswith(b"\xff\xd8\xff"):
            extension, mime_type = "jpg", "image/jpeg"
        else:
            raise ValueError("GPT Image references must be PNG or JPEG images")
        files.append((f"reference_{index}.{extension}", image, mime_type))
    return files


def _base_url(endpoint):
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https" or not parsed.netloc
        or parsed.query or parsed.fragment or parsed.username or parsed.password
        or parsed.path.rstrip("/") not in ("", "/openai/v1")
    ):
        raise ValueError(
            "GPT Image requires an HTTPS Foundry resource endpoint or its /openai/v1/ URL"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/", "", ""))


def _decode_image(response):
    if not response.data or len(response.data) != 1 or not response.data[0].b64_json:
        raise RuntimeError("GPT Image did not return exactly one base64 image")
    try:
        image = base64.b64decode(response.data[0].b64_json, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RuntimeError("GPT Image returned invalid base64 image data") from error
    if not image.startswith(PNG_SIGNATURE):
        raise RuntimeError("GPT Image did not return a PNG image")
    return image


async def generate_gpt_image(endpoint, api_key, prompt, size, quality, n, images=None):
    size, quality = validate_gpt_parameters(size, quality, n)
    deployment = os.environ.get("AZURE_GPT_IMAGE_DEPLOYMENT_NAME", "").strip()
    if not deployment:
        raise RuntimeError("AZURE_GPT_IMAGE_DEPLOYMENT_NAME is required for GPT Image")
    files = _reference_files(images) if images is not None else None
    # Do not automatically resubmit a paid generation after a timeout or server error.
    async with AsyncOpenAI(
        base_url=_base_url(endpoint),
        api_key=api_key,
        timeout=300.0,
        max_retries=0,
    ) as client:
        arguments = dict(
            model=deployment,
            prompt=prompt,
            size=size,
            quality=quality,
            n=1,
            output_format="png",
        )
        if files is None:
            response = await client.images.generate(**arguments)
        else:
            response = await client.images.edit(image=files, **arguments)
    return _decode_image(response)
