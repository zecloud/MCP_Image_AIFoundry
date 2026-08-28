import base64
import json
import sys
import types
import unittest
from unittest.mock import patch

from mcp.types import ImageContent

import function_app


class FakeOutputBlob:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class FakeImageClient:
    class ImageModel:
        FLUX = "flux"

    def __init__(self, **kwargs):
        pass

    async def generate_image_async(self, **kwargs):
        return b"generated-image"

    async def flux2edit_image_async(self, **kwargs):
        return b"edited-image"


class FakeContainerClient:
    def get_blob_client(self, name):
        return self

    def download_blob(self):
        return self

    def readall(self):
        return b"reference-image"


class ImageContentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client_module = types.SimpleNamespace(GptImageClient=FakeImageClient)
        self.environment = {
            "AZURE_OPENAI_ENDPOINT": "https://example.test",
            "AZURE_OPENAI_API_KEY": "test-key",
        }

    async def test_generate_image_returns_image_content(self):
        output_blob = FakeOutputBlob()
        context = json.dumps({"arguments": {"prompt": "A mountain"}})

        with (
            patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
            patch.dict("os.environ", self.environment, clear=False),
        ):
            result = await function_app.generate_image(context, output_blob)

        self.assertIsInstance(result, ImageContent)
        self.assertEqual(result.type, "image")
        self.assertEqual(result.mimeType, "image/png")
        self.assertEqual(base64.b64decode(result.data), b"generated-image")
        self.assertEqual(output_blob.value, b"generated-image")

    async def test_edit_image_returns_image_content(self):
        output_blob = FakeOutputBlob()
        context = json.dumps(
            {
                "arguments": {
                    "filenames": ["reference.png"],
                    "prompt": "Make it brighter",
                }
            }
        )

        with (
            patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
            patch.dict("os.environ", self.environment, clear=False),
        ):
            result = await function_app.edit_image(
                context,
                FakeContainerClient(),
                output_blob,
            )

        self.assertIsInstance(result, ImageContent)
        self.assertEqual(result.type, "image")
        self.assertEqual(result.mimeType, "image/png")
        self.assertEqual(base64.b64decode(result.data), b"edited-image")
        self.assertEqual(output_blob.value, b"edited-image")

    async def test_generate_image_raises_when_credentials_are_missing(self):
        context = json.dumps({"arguments": {"prompt": "A mountain"}})

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "Azure OpenAI credentials not configured",
            ):
                await function_app.generate_image(context, FakeOutputBlob())

    async def test_edit_image_raises_for_empty_reference_list(self):
        context = json.dumps(
            {
                "arguments": {
                    "filenames": [],
                    "prompt": "Make it brighter",
                }
            }
        )

        with self.assertRaisesRegex(ValueError, "filenames list"):
            await function_app.edit_image(
                context,
                FakeContainerClient(),
                FakeOutputBlob(),
            )


if __name__ == "__main__":
    unittest.main()
