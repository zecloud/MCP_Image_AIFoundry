import base64
import json
import sys
import types
import unittest
from unittest.mock import patch

from mcp.types import CallToolResult, ImageContent, TextContent

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

    def parse_result(self, serialized_result):
        envelope = json.loads(serialized_result)
        self.assertEqual(envelope["type"], "call_tool_result")
        return CallToolResult.model_validate_json(envelope["content"])

    async def test_generate_image_returns_url_and_image_content(self):
        output_blob = FakeOutputBlob()
        context = json.dumps({"arguments": {"prompt": "A mountain"}})

        with (
            patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
            patch.dict("os.environ", self.environment, clear=False),
            patch.object(function_app, "urlstorage", "https://storage.example"),
        ):
            serialized_result = await function_app.generate_image(
                context,
                outputBlob=output_blob,
            )

        result = self.parse_result(serialized_result)
        self.assertFalse(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        metadata = json.loads(result.content[0].text)
        self.assertEqual(metadata["status"], "success")
        self.assertEqual(
            metadata["image"],
            "https://storage.example/fluxjob/agentvideo/test/"
            "img-test-scene0-talk0.png",
        )
        self.assertIsInstance(result.content[1], ImageContent)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertEqual(
            base64.b64decode(result.content[1].data),
            b"generated-image",
        )
        self.assertEqual(output_blob.value, b"generated-image")

    async def test_edit_image_returns_url_and_image_content(self):
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
            patch.object(function_app, "urlstorage", "https://storage.example"),
        ):
            serialized_result = await function_app.edit_image(
                context,
                containerClient=FakeContainerClient(),
                outputBlob=output_blob,
            )

        result = self.parse_result(serialized_result)
        self.assertFalse(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        metadata = json.loads(result.content[0].text)
        self.assertEqual(metadata["status"], "success")
        self.assertEqual(metadata["reference_images_used"], 1)
        self.assertEqual(
            metadata["image"],
            "https://storage.example/fluxjob/agentvideo/test/"
            "edited-test-scene0-talk0.png",
        )
        self.assertIsInstance(result.content[1], ImageContent)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertEqual(
            base64.b64decode(result.content[1].data),
            b"edited-image",
        )
        self.assertEqual(output_blob.value, b"edited-image")

    async def test_generate_image_returns_detailed_error(self):
        context = json.dumps({"arguments": {"prompt": "A mountain"}})

        with patch.dict("os.environ", {}, clear=True):
            serialized_result = await function_app.generate_image(
                context,
                outputBlob=FakeOutputBlob(),
            )

        result = self.parse_result(serialized_result)
        self.assertTrue(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        self.assertIn(
            "Azure OpenAI credentials not configured",
            result.content[0].text,
        )

    async def test_edit_image_returns_detailed_validation_error(self):
        context = json.dumps(
            {
                "arguments": {
                    "filenames": [],
                    "prompt": "Make it brighter",
                }
            }
        )

        serialized_result = await function_app.edit_image(
            context,
            containerClient=FakeContainerClient(),
            outputBlob=FakeOutputBlob(),
        )

        result = self.parse_result(serialized_result)
        self.assertTrue(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        self.assertIn("filenames list", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
