import base64
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from jsonschema import Draft202012Validator
from openai import AsyncOpenAI

import function_app
import gpt_image
from test_gpt_image import ENVIRONMENT, PNG, parse_result
from test_image_content import FakeContainerClient


ENCODED_PNG = base64.b64encode(PNG).decode("ascii")
JPEG = b"\xff\xd8\xffuploaded-jpeg"


class UploadValidationTests(unittest.TestCase):
    def test_raw_base64_and_data_urls(self):
        for image, mime_type in ((PNG, "image/png"), (JPEG, "image/jpeg")):
            encoded = base64.b64encode(image).decode("ascii")
            for payload in (encoded, f"data:{mime_type};base64,{encoded}"):
                with self.subTest(mime_type=mime_type, data_url=payload.startswith("data:")):
                    self.assertEqual(function_app._decode_uploaded_image(payload), image)

    def test_invalid_uploads(self):
        for payload in (
            "", "not base64!", "a", "é", ENCODED_PNG + "\n",
            base64.b64encode(b"not an image").decode("ascii"),
            f"data:image/gif;base64,{ENCODED_PNG}",
            f"data:image/jpeg;base64,{ENCODED_PNG}",
            f"data:image/png,{ENCODED_PNG}", "data:image/png;base64",
            "data:image/png;base64,",
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                function_app._decode_uploaded_image(payload)

    def test_upload_size_limit_before_and_after_decoding(self):
        with patch.object(function_app, "MAX_REFERENCE_BYTES", len(PNG)):
            with self.assertRaisesRegex(ValueError, "smaller than 50 MB"):
                function_app._decode_uploaded_image(ENCODED_PNG)
            with patch.object(function_app.base64, "b64decode") as decode:
                with self.assertRaisesRegex(ValueError, "smaller than 50 MB"):
                    function_app._decode_uploaded_image("A" * (len(ENCODED_PNG) + 4))
                decode.assert_not_called()
        with patch.object(function_app, "MAX_REFERENCE_BYTES", len(PNG) + 1):
            self.assertEqual(function_app._decode_uploaded_image(ENCODED_PNG), PNG)

    def test_published_schema_allows_upload_without_filenames(self):
        with patch.object(function_app.app, "functions_bindings", None):
            functions = function_app.app.get_functions()
        for function in functions:
            properties = function.get_trigger().get_dict_repr()["toolProperties"]
            if isinstance(properties, str):
                properties = json.loads(properties)
            properties = {prop["propertyName"]: prop for prop in properties}
            if "filenames" not in properties:
                self.assertNotIn("image_base64", properties)
                continue
            self.assertFalse(properties["filenames"]["isRequired"])
            self.assertEqual(properties["filenames"]["propertyType"], "string")
            self.assertTrue(properties["filenames"]["isArray"])
            self.assertFalse(properties["image_base64"]["isRequired"])
            self.assertEqual(properties["image_base64"]["propertyType"], "string")
            self.assertFalse(properties["image_base64"]["isArray"])
            self.assertTrue(properties["prompt"]["isRequired"])
            schema = {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "filenames": {"type": "array", "items": {"type": "string"}},
                    "image_base64": {"type": "string"},
                },
                "required": [name for name in ("prompt", "filenames", "image_base64") if properties[name]["isRequired"]],
            }
            validator = Draft202012Validator(schema)
            for references in (
                {"image_base64": ENCODED_PNG}, {"filenames": ["r.png"]},
                {"filenames": ["r.png"], "image_base64": ENCODED_PNG},
            ):
                self.assertTrue(validator.is_valid({"prompt": "A", **references}))
            self.assertFalse(validator.is_valid({"image_base64": ENCODED_PNG}))
            self.assertFalse(validator.is_valid({"prompt": "A", "image_base64": None}))


class UploadMcpTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, arguments):
        container = FakeContainerClient(PNG)
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(function_app, "urlstorage", "https://storage.example"),
            patch.object(function_app, "generate_gpt_image", new_callable=AsyncMock, return_value=PNG) as generate,
        ):
            result = parse_result(await function_app.edit_image(
                json.dumps({"arguments": arguments}), containerClient=container,
            ))
        return result, container, generate

    async def test_gpt_upload_only_and_mixed_references(self):
        for filenames in (None, [], ["stored.png"]):
            with self.subTest(filenames=filenames):
                arguments = {"model": "gpt-image-2.5", "prompt": "A", "image_base64": ENCODED_PNG}
                if filenames is not None:
                    arguments["filenames"] = filenames
                result, container, generate = await self.invoke(arguments)
                self.assertFalse(result.isError)
                self.assertEqual(container.value, PNG)
                images = [PNG] * (len(filenames or []) + 1)
                generate.assert_awaited_once_with(
                    ENVIRONMENT["AZURE_OPENAI_ENDPOINT"], "test-key", "A", "1024x1024", "auto", 1,
                    images=images,
                )
                self.assertEqual(json.loads(result.content[0].text)["reference_images_used"], len(images))
                self.assertEqual([call.args[0] for call in container.get_blob_client.call_args_list], [
                    *[f"agentvideo/test/{name}" for name in filenames or []],
                    "agentvideo/test/edited-test.png",
                ])

    async def test_invalid_uploads_fail_before_storage_or_api(self):
        for changes in (
            {"image_base64": ""}, {"image_base64": "invalid!"},
            {"image_base64": f"data:image/jpeg;base64,{ENCODED_PNG}"},
            {"image_base64": base64.b64encode(b"invalid image").decode("ascii")},
            {"image_base64": ["private-payload"]},
            {"filenames": ["r.png"] * 16}, {"prompt": ""},
        ):
            with self.subTest(changes=changes):
                result, container, generate = await self.invoke({
                    "model": "gpt-image-2.5", "prompt": "A", "filenames": ["r.png"],
                    "image_base64": ENCODED_PNG, **changes,
                })
                self.assertTrue(result.isError)
                container.get_blob_client.assert_not_called()
                generate.assert_not_awaited()
                self.assertNotIn("private-payload", result.content[0].text)

    async def test_upload_counts_towards_gpt_reference_limit(self):
        result, _, generate = await self.invoke({
            "model": "gpt-image-2.5", "prompt": "A", "filenames": ["r.png"] * 15,
            "image_base64": ENCODED_PNG,
        })
        self.assertFalse(result.isError)
        self.assertEqual(len(generate.await_args.kwargs["images"]), 16)

    async def test_missing_reference_still_fails_for_all_models(self):
        for model in ("flux-pro-2", "flux-kontext", "gpt-image-2.5"):
            with self.subTest(model=model):
                result, container, generate = await self.invoke({"model": model, "prompt": "A"})
                self.assertTrue(result.isError)
                container.get_blob_client.assert_not_called()
                generate.assert_not_awaited()

    async def test_flux_upload_only_and_mixed_preserve_reference_order(self):
        for filenames in (None, ["stored.png"]):
            with self.subTest(filenames=filenames):
                client = MagicMock()
                client.flux2edit_image_async = AsyncMock(return_value=PNG)
                module = types.SimpleNamespace(GptImageClient=MagicMock(return_value=client))
                arguments = {"prompt": "A", "image_base64": f"data:image/jpeg;base64,{base64.b64encode(JPEG).decode('ascii')}"}
                if filenames is not None:
                    arguments["filenames"] = filenames
                with patch.dict(sys.modules, {"FoundryImageClient": module}):
                    result, container, gpt = await self.invoke(arguments)
                self.assertFalse(result.isError)
                self.assertEqual(container.value, PNG)
                client.flux2edit_image_async.assert_awaited_once_with(
                    images=([PNG] if filenames else []) + [JPEG], prompt="A", size="1024x1024",
                )
                gpt.assert_not_awaited()

    async def test_kontext_jpeg_upload_and_temp_file_cleanup(self):
        for filenames in (None, ["stored.png"]):
            with self.subTest(filenames=filenames):
                paths = []
                async def edit(**kwargs):
                    paths.extend([kwargs["image_path"], *(kwargs["additional_images"] or [])])
                    self.assertEqual([Path(path).read_bytes() for path in paths], ([PNG] if filenames else []) + [JPEG])
                    self.assertEqual(Path(paths[-1]).suffix, ".jpg")
                    return PNG
                client = MagicMock()
                client.edit_image_async = AsyncMock(side_effect=edit)
                module = types.SimpleNamespace(GptImageClient=MagicMock(return_value=client))
                arguments = {"model": "flux-kontext", "prompt": "A", "image_base64": base64.b64encode(JPEG).decode("ascii")}
                if filenames is not None:
                    arguments["filenames"] = filenames
                with patch.dict(sys.modules, {"FoundryImageClient": module}):
                    result, _, gpt = await self.invoke(arguments)
                self.assertFalse(result.isError)
                self.assertTrue(paths)
                self.assertTrue(all(not Path(path).exists() for path in paths))
                gpt.assert_not_awaited()

    async def test_upload_payload_is_not_logged(self):
        for payload in (ENCODED_PNG, ["private-payload"]):
            with self.subTest(valid=isinstance(payload, str)), patch.object(function_app, "logging") as logging:
                await self.invoke({"model": "gpt-image-2.5", "prompt": "A", "image_base64": payload})
                logs = str(logging.mock_calls)
                self.assertNotIn(ENCODED_PNG, logs)
                self.assertNotIn("private-payload", logs)

    async def test_uploaded_jpeg_reaches_real_sdk_as_multipart(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"data": [{"b64_json": ENCODED_PNG}]})
        def make_client(**kwargs):
            return AsyncOpenAI(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)
        container = FakeContainerClient(PNG)
        with patch.dict(os.environ, ENVIRONMENT, clear=True), patch.object(gpt_image, "AsyncOpenAI", side_effect=make_client):
            result = parse_result(await function_app.edit_image(json.dumps({"arguments": {
                "model": "gpt-image-2.5", "prompt": "A", "filenames": ["stored.png"],
                "image_base64": base64.b64encode(JPEG).decode("ascii"),
            }}), containerClient=container))
        self.assertFalse(result.isError)
        self.assertEqual(container.value, PNG)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/openai/v1/images/edits")
        self.assertIn(b'filename="reference_0.png"', requests[0].content)
        self.assertIn(b'filename="reference_1.jpg"', requests[0].content)
        self.assertIn(JPEG, requests[0].content)
        self.assertEqual(requests[0].content.count(b'name="image[]"'), 2)


if __name__ == "__main__":
    unittest.main()
