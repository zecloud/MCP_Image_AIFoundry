import base64
import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from mcp.types import CallToolResult, ImageContent, TextContent

import function_app


class FakeContainerClient:
    def __init__(self, reference_image=b"reference-image"):
        self.value = None
        self.reference_image = reference_image
        self.blobs = {}
        self.get_blob_client = MagicMock(side_effect=self._get_blob_client)

    def _get_blob_client(self, name):
        client = MagicMock()
        client.download_blob.return_value.readall.return_value = self.reference_image
        client.upload_blob.side_effect = self._upload
        self.blobs[name] = client
        return client

    def _upload(self, value, **kwargs):
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


class ImageContentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client_module = types.SimpleNamespace(GptImageClient=FakeImageClient)
        self.environment = {
            "AZURE_OPENAI_ENDPOINT": "https://example.test",
            "AZURE_OPENAI_API_KEY": "test-key",
        }

    def parse_result(self, result):
        if isinstance(result, CallToolResult):
            return result
        envelope = json.loads(result)
        payload = envelope.get("content", envelope)
        return CallToolResult.model_validate_json(payload) if isinstance(payload, str) else CallToolResult.model_validate(payload)

    def test_append_read_sas_generates_read_only_blob_token(self):
        credential = MagicMock()
        service_client = MagicMock()
        service_client.account_name = "storage"
        service_client.get_user_delegation_key.return_value = "delegation-key"
        service_context = MagicMock()
        service_context.__enter__.return_value = service_client

        with (
            patch.object(function_app, "urlstorage", "https://storage.example"),
            patch.object(
                function_app,
                "_get_storage_credential",
                return_value=credential,
            ),
            patch.object(
                function_app,
                "BlobServiceClient",
                return_value=service_context,
            ),
            patch.object(
                function_app,
                "generate_blob_sas",
                return_value="sp=r&sig=test",
            ) as generate_blob_sas,
        ):
            result = function_app._append_read_sas(
                "https://storage.example/fluxjob/image.png",
                "image.png",
            )

        self.assertEqual(
            result,
            "https://storage.example/fluxjob/image.png?sp=r&sig=test",
        )
        sas_arguments = generate_blob_sas.call_args.kwargs
        self.assertEqual(sas_arguments["account_name"], "storage")
        self.assertEqual(sas_arguments["container_name"], "fluxjob")
        self.assertEqual(sas_arguments["blob_name"], "image.png")
        self.assertEqual(str(sas_arguments["permission"]), "r")
        credential.close.assert_called_once_with()

    async def test_generate_image_returns_url_and_image_content(self):
        output_blob = FakeContainerClient()
        context = json.dumps({"arguments": {"prompt": "A mountain"}})

        with (
            patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
            patch.dict("os.environ", self.environment, clear=False),
            patch.object(function_app, "urlstorage", "https://storage.example"),
        ):
            serialized_result = await function_app.generate_image(
                context,
                containerClient=output_blob,
            )

        result = self.parse_result(serialized_result)
        self.assertFalse(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        metadata = json.loads(result.content[0].text)
        self.assertEqual(metadata["status"], "success")
        self.assertEqual(
            metadata["image"],
            "https://storage.example/fluxjob/agentvideo/test/img-test.png",
        )
        output_blob.get_blob_client.assert_called_once_with("agentvideo/test/img-test.png")
        upload = output_blob.blobs["agentvideo/test/img-test.png"].upload_blob
        upload.assert_called_once()
        self.assertTrue(upload.call_args.kwargs["overwrite"])
        self.assertEqual(upload.call_args.kwargs["content_settings"].content_type, "image/png")
        self.assertIsInstance(result.content[1], ImageContent)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertEqual(
            base64.b64decode(result.content[1].data),
            b"generated-image",
        )
        self.assertEqual(output_blob.value, b"generated-image")

    async def test_generate_image_returns_read_sas_url_when_requested(self):
        output_blob = FakeContainerClient()
        context = json.dumps(
            {"arguments": {"prompt": "A mountain", "sas": True}}
        )
        signed_url = (
            "https://storage.example/fluxjob/agentvideo/test/"
            "img-test.png?sp=r&sig=test"
        )

        with (
            patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
            patch.dict("os.environ", self.environment, clear=False),
            patch.object(function_app, "urlstorage", "https://storage.example"),
            patch.object(
                function_app,
                "_append_read_sas",
                return_value=signed_url,
            ) as append_read_sas,
        ):
            serialized_result = await function_app.generate_image(
                context,
                containerClient=output_blob,
            )

        result = self.parse_result(serialized_result)
        metadata = json.loads(result.content[0].text)
        self.assertEqual(metadata["image"], signed_url)
        append_read_sas.assert_called_once_with(
            "https://storage.example/fluxjob/agentvideo/test/img-test.png",
            "agentvideo/test/img-test.png",
        )

    async def test_edit_image_returns_url_and_image_content(self):
        output_blob = FakeContainerClient()
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
                containerClient=output_blob,
            )

        result = self.parse_result(serialized_result)
        self.assertFalse(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        metadata = json.loads(result.content[0].text)
        self.assertEqual(metadata["status"], "success")
        self.assertEqual(metadata["reference_images_used"], 1)
        self.assertEqual(
            metadata["image"],
            "https://storage.example/fluxjob/agentvideo/test/edited-test.png",
        )
        self.assertEqual(
            [call.args[0] for call in output_blob.get_blob_client.call_args_list],
            ["agentvideo/test/reference.png", "agentvideo/test/edited-test.png"],
        )
        self.assertIsInstance(result.content[1], ImageContent)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertEqual(
            base64.b64decode(result.content[1].data),
            b"edited-image",
        )
        self.assertEqual(output_blob.value, b"edited-image")

    async def test_edit_image_returns_read_sas_url_when_requested(self):
        output_blob = FakeContainerClient()
        context = json.dumps(
            {
                "arguments": {
                    "filenames": ["reference.png"],
                    "prompt": "Make it brighter",
                    "sas": True,
                }
            }
        )
        signed_url = (
            "https://storage.example/fluxjob/agentvideo/test/"
            "edited-test.png?sp=r&sig=test"
        )

        with (
            patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
            patch.dict("os.environ", self.environment, clear=False),
            patch.object(function_app, "urlstorage", "https://storage.example"),
            patch.object(
                function_app,
                "_append_read_sas",
                return_value=signed_url,
            ) as append_read_sas,
        ):
            serialized_result = await function_app.edit_image(
                context,
                containerClient=output_blob,
            )

        result = self.parse_result(serialized_result)
        metadata = json.loads(result.content[0].text)
        self.assertEqual(metadata["image"], signed_url)
        append_read_sas.assert_called_once_with(
            "https://storage.example/fluxjob/agentvideo/test/edited-test.png",
            "agentvideo/test/edited-test.png",
        )

    async def test_optional_naming_matches_blob_url_and_sas_for_both_tools(self):
        cases = [
            ({}, "{prefix}-clip.png"),
            ({"scene_number": None, "talk_number": None, "prefix": None}, "{prefix}-clip.png"),
            ({"scene_number": 0, "talk_number": 0}, "{prefix}-clip-scene0-talk0.png"),
            ({"scene_number": 3}, "{prefix}-clip-scene3.png"),
            ({"talk_number": 0}, "{prefix}-clip-talk0.png"),
            ({"scene_number": 0, "talk_number": None}, "{prefix}-clip-scene0.png"),
            ({"prefix": "cover"}, "cover-clip.png"),
            ({"prefix": "cover", "scene_number": 1, "talk_number": 2}, "cover-clip-scene1-talk2.png"),
            ({"prefix": ""}, "clip.png"),
            ({"prefix": "", "talk_number": 2}, "clip-talk2.png"),
        ]
        for edit in (False, True):
            for naming, filename in cases:
                with self.subTest(edit=edit, naming=naming):
                    filename = filename.format(prefix="edited" if edit else "img")
                    blob_name = f"agentvideo/clip/{filename}"
                    blob_url = f"https://storage.example/fluxjob/{blob_name}"
                    container = FakeContainerClient()
                    arguments = {"prompt": "A mountain", "video_id": "clip", "sas": True, **naming}
                    if edit:
                        arguments["filenames"] = ["reference.png"]
                    with (
                        patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
                        patch.dict("os.environ", self.environment, clear=False),
                        patch.object(function_app, "urlstorage", "https://storage.example"),
                        patch.object(function_app, "_append_read_sas", side_effect=lambda url, name: url + "?sp=r") as sas,
                    ):
                        tool = function_app.edit_image if edit else function_app.generate_image
                        response = await tool(json.dumps({"arguments": arguments}), containerClient=container)
                    result = self.parse_result(response)
                    self.assertFalse(result.isError)
                    self.assertEqual(json.loads(result.content[0].text)["image"], blob_url + "?sp=r")
                    sas.assert_called_once_with(blob_url, blob_name)
                    image_bytes = b"edited-image" if edit else b"generated-image"
                    self.assertEqual(container.value, image_bytes)
                    upload = container.blobs[blob_name].upload_blob
                    upload.assert_called_once()
                    self.assertEqual(upload.call_args.args, (image_bytes,))
                    self.assertTrue(upload.call_args.kwargs["overwrite"])
                    self.assertEqual(upload.call_args.kwargs["content_settings"].content_type, "image/png")

    async def test_url_encodes_custom_prefix_but_sas_uses_raw_blob_name(self):
        for edit in (False, True):
            with self.subTest(edit=edit):
                arguments = {"prompt": "A mountain", "prefix": "été #1?", "sas": True}
                if edit:
                    arguments["filenames"] = ["reference.png"]
                container = FakeContainerClient()
                with (
                    patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
                    patch.dict("os.environ", self.environment, clear=False),
                    patch.object(function_app, "urlstorage", "https://storage.example"),
                    patch.object(function_app, "_append_read_sas", side_effect=lambda url, name: url + "?sp=r") as sas,
                ):
                    tool = function_app.edit_image if edit else function_app.generate_image
                    response = await tool(json.dumps({"arguments": arguments}), containerClient=container)
                self.assertFalse(self.parse_result(response).isError)
                sas.assert_called_once_with(
                    "https://storage.example/fluxjob/agentvideo/test/%C3%A9t%C3%A9%20%231%3F-test.png",
                    "agentvideo/test/été #1?-test.png",
                )
                self.assertIn("agentvideo/test/été #1?-test.png", container.blobs)

    async def test_upload_failure_returns_error_without_signing_url(self):
        for edit in (False, True):
            with self.subTest(edit=edit):
                container = MagicMock()
                container.get_blob_client.return_value.download_blob.return_value.readall.return_value = b"reference-image"
                container.get_blob_client.return_value.upload_blob.side_effect = RuntimeError("Upload failed")
                arguments = {"prompt": "A mountain", "sas": True}
                if edit:
                    arguments["filenames"] = ["reference.png"]
                with (
                    patch.dict(sys.modules, {"FoundryImageClient": self.client_module}),
                    patch.dict("os.environ", self.environment, clear=False),
                    patch.object(function_app, "_append_read_sas") as sas,
                    self.assertLogs(level="ERROR"),
                ):
                    tool = function_app.edit_image if edit else function_app.generate_image
                    response = await tool(json.dumps({"arguments": arguments}), containerClient=container)
                result = self.parse_result(response)
                self.assertTrue(result.isError)
                self.assertIn("Upload failed", result.content[0].text)
                sas.assert_not_called()

    def test_models_default_to_optional_naming(self):
        for model, required, prefix in (
            (function_app.ImageGenerationRequest, {"prompt": "A"}, "img"),
            (function_app.ImageEditRequest, {"prompt": "A", "filenames": ["r.png"]}, "edited"),
        ):
            request = model(**required)
            self.assertIsNone(request.scene_number)
            self.assertIsNone(request.talk_number)
            self.assertEqual(request.prefix, prefix)
            self.assertEqual(model(**required, scene_number=0, talk_number=0).scene_number, 0)

    async def test_generate_image_returns_detailed_error(self):
        context = json.dumps({"arguments": {"prompt": "A mountain"}})

        with patch.dict("os.environ", {}, clear=True):
            serialized_result = await function_app.generate_image(
                context,
                containerClient=FakeContainerClient(),
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
        )

        result = self.parse_result(serialized_result)
        self.assertTrue(result.isError)
        self.assertIsInstance(result.content[0], TextContent)
        self.assertIn("filenames list", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
