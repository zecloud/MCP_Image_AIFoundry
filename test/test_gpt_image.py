import base64
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import AsyncOpenAI, RateLimitError
from mcp.types import CallToolResult

import function_app
import gpt_image
from test_image_content import FakeContainerClient


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWioAAAAASUVORK5CYII="
)
ENVIRONMENT = {
    "AZURE_OPENAI_ENDPOINT": "https://foundry.example.test/",
    "AZURE_OPENAI_API_KEY": "test-key",
    "AZURE_GPT_IMAGE_DEPLOYMENT_NAME": "my-image-deployment",
}


def parse_result(result):
    if isinstance(result, CallToolResult):
        return result
    envelope = json.loads(result)
    payload = envelope.get("content", envelope)
    return (
        CallToolResult.model_validate_json(payload)
        if isinstance(payload, str)
        else CallToolResult.model_validate(payload)
    )


class GptValidationTests(unittest.TestCase):
    def test_sizes_and_qualities(self):
        for size in ("auto", "1024x1024", "1536x864", "704x1280", "3840x2160"):
            for quality in gpt_image.GPT_QUALITIES:
                with self.subTest(size=size, quality=quality):
                    self.assertEqual(
                        gpt_image.validate_gpt_parameters(size, quality, 1),
                        (size, quality),
                    )
        self.assertEqual(
            gpt_image.validate_gpt_parameters(None, "standard", 1), ("auto", "auto")
        )

    def test_invalid_parameters(self):
        cases = [
            ("512x512", "auto", 1), ("1025x1024", "auto", 1),
            ("4096x2048", "auto", 1), ("3840x3840", "auto", 1),
            ("3840x512", "auto", 1), ("0x1024", "auto", 1),
            ("-1024x1024", "auto", 1), ("1024X1024", "auto", 1),
            ("1024x1024", "standard", 2), ("auto", "ultra", 1),
            ("auto", "auto", 0), ("auto", "auto", None),
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                gpt_image.validate_gpt_parameters(*args)

    def test_endpoint_normalization(self):
        for endpoint in (
            "https://foundry.example.test", "https://foundry.example.test/",
            "https://foundry.example.test/openai/v1",
            "https://foundry.example.test/openai/v1/",
        ):
            self.assertEqual(
                gpt_image._base_url(endpoint), "https://foundry.example.test/openai/v1/"
            )
        for endpoint in (
            "http://foundry.example.test", "https://foundry.example.test/api/projects/p",
            "https://foundry.example.test/?key=secret", "https://user:pass@foundry.example.test",
            "https://foundry.example.test/#fragment", "invalid",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                gpt_image._base_url(endpoint)

    def test_invalid_references(self):
        for images in ([], [PNG] * 17, [b""], [b"not an image"]):
            with self.subTest(count=len(images)), self.assertRaises(ValueError):
                gpt_image._reference_files(images)
        with patch.object(gpt_image, "MAX_REFERENCE_BYTES", len(PNG)):
            with self.assertRaisesRegex(ValueError, "smaller than"):
                gpt_image._reference_files([PNG])
        self.assertEqual(len(gpt_image._reference_files([PNG] * 16)), 16)

    def test_invalid_responses(self):
        for data in (
            [], [types.SimpleNamespace(b64_json=None)],
            [types.SimpleNamespace(b64_json="not base64!")],
            [types.SimpleNamespace(b64_json=base64.b64encode(b"not PNG").decode())],
            [types.SimpleNamespace(b64_json="a")] * 2,
        ):
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                gpt_image._decode_image(types.SimpleNamespace(data=data))


class GptSdkTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, images=None, endpoint=None):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200, json={"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]}
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        def make_client(**kwargs):
            self.assertEqual(kwargs["timeout"], 300.0)
            self.assertEqual(kwargs["max_retries"], 0)
            return AsyncOpenAI(http_client=http_client, **kwargs)

        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(gpt_image, "AsyncOpenAI", side_effect=make_client),
        ):
            try:
                result = await gpt_image.generate_gpt_image(
                    endpoint or ENVIRONMENT["AZURE_OPENAI_ENDPOINT"],
                    ENVIRONMENT["AZURE_OPENAI_API_KEY"],
                    "A landscape", "1536x864", "xhigh", 1, images=images,
                )
            finally:
                self.assertTrue(http_client.is_closed)
        return result, requests

    async def test_generation_uses_configured_deployment_and_v1(self):
        result, requests = await self.run_request(endpoint="https://foundry.example.test/openai/v1/")
        self.assertEqual(result, PNG)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), "https://foundry.example.test/openai/v1/images/generations?api-version=preview")
        self.assertEqual(request.headers["authorization"], "Bearer test-key")
        self.assertEqual(json.loads(request.content), {
            "model": "my-image-deployment", "prompt": "A landscape",
            "size": "1536x864", "quality": "xhigh", "n": 1, "output_format": "png",
        })

    async def test_edit_sends_all_references_as_multipart(self):
        jpeg = b"\xff\xd8\xffreference"
        result, requests = await self.run_request(images=[PNG, jpeg])
        self.assertEqual(result, PNG)
        request = requests[0]
        self.assertEqual(str(request.url), "https://foundry.example.test/openai/v1/images/edits?api-version=preview")
        self.assertIn("multipart/form-data", request.headers["content-type"])
        self.assertIn(b'filename="reference_0.png"', request.content)
        self.assertIn(b'filename="reference_1.jpg"', request.content)
        self.assertEqual(request.content.count(b'name="image[]"'), 2)
        self.assertIn(b"image/png", request.content)
        self.assertIn(b"image/jpeg", request.content)
        self.assertIn(PNG, request.content)
        self.assertIn(jpeg, request.content)
        self.assertIn(b"my-image-deployment", request.content)

    async def test_missing_deployment_does_not_call_api(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(gpt_image, "AsyncOpenAI") as client:
            with self.assertRaisesRegex(RuntimeError, "AZURE_GPT_IMAGE_DEPLOYMENT_NAME"):
                await gpt_image.generate_gpt_image("https://foundry.example.test", "test-key", "A", "auto", "auto", 1)
        client.assert_not_called()

    async def test_rate_limit_is_not_retried(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(429, json={"error": {"message": "rate limited"}})

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(gpt_image, "AsyncOpenAI", side_effect=lambda **kw: AsyncOpenAI(http_client=http_client, **kw)),
        ):
            with self.assertRaises(RateLimitError):
                await gpt_image.generate_gpt_image("https://foundry.example.test", "test-key", "A", "auto", "auto", 1)
        self.assertEqual(len(requests), 1)
        self.assertTrue(http_client.is_closed)


class GptMcpTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, arguments, edit=False, image=PNG):
        output = container = FakeContainerClient(image)
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(function_app, "urlstorage", "https://storage.example"),
            patch.object(function_app, "generate_gpt_image", new_callable=AsyncMock, return_value=PNG) as generate,
            patch.object(function_app, "_append_read_sas", return_value="https://storage.example/image.png?sp=r") as sas,
        ):
            context = json.dumps({"arguments": arguments})
            if edit:
                response = await function_app.edit_image(context, containerClient=container)
            else:
                response = await function_app.generate_image(context, containerClient=container)
        return parse_result(response), output, container, generate, sas

    async def test_generation_routes_gpt_and_preserves_response(self):
        result, output, _, generate, sas = await self.invoke({
            "model": "gpt-image-2.5", "prompt": "A", "sas": True,
        })
        self.assertFalse(result.isError)
        self.assertEqual(output.value, PNG)
        self.assertEqual(base64.b64decode(result.content[1].data), PNG)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertEqual(json.loads(result.content[0].text)["image"], "https://storage.example/image.png?sp=r")
        generate.assert_awaited_once_with(ENVIRONMENT["AZURE_OPENAI_ENDPOINT"], "test-key", "A", "1024x1024", "auto", 1)
        sas.assert_called_once_with(
            "https://storage.example/fluxjob/agentvideo/test/img-test.png",
            "agentvideo/test/img-test.png",
        )

    async def test_edit_routes_gpt_with_references_and_metadata(self):
        result, output, container, generate, _ = await self.invoke({
            "model": "gpt-image-2.5", "prompt": "A", "filenames": ["one.png", "two.png"],
            "video_id": "clip", "size": "1536x864", "quality": "max",
        }, edit=True)
        self.assertFalse(result.isError)
        self.assertEqual(output.value, PNG)
        self.assertEqual(json.loads(result.content[0].text)["reference_images_used"], 2)
        self.assertEqual([call.args[0] for call in container.get_blob_client.call_args_list], [
            "agentvideo/clip/one.png", "agentvideo/clip/two.png", "agentvideo/clip/edited-clip.png",
        ])
        generate.assert_awaited_once_with(ENVIRONMENT["AZURE_OPENAI_ENDPOINT"], "test-key", "A", "1536x864", "max", 1, images=[PNG, PNG])

    async def test_validation_precedes_api_and_storage(self):
        for changes in ({"n": 2}, {"quality": "bad"}, {"size": "512x512"}, {"model": "unknown"}):
            with self.subTest(changes=changes):
                result, output, _, generate, _ = await self.invoke({"model": "gpt-image-2.5", "prompt": "A", **changes})
                self.assertTrue(result.isError)
                self.assertIsNone(output.value)
                generate.assert_not_awaited()
        for changes in (
            {"use_flux_kontext": True}, {"filenames": []}, {"filenames": ["r.png"] * 17}, {"n": 2},
        ):
            with self.subTest(changes=changes):
                result, output, container, generate, _ = await self.invoke({
                    "model": "gpt-image-2.5", "prompt": "A", "filenames": ["r.png"], **changes,
                }, edit=True)
                self.assertTrue(result.isError)
                self.assertIsNone(output.value)
                container.get_blob_client.assert_not_called()
                generate.assert_not_awaited()

    async def test_adapter_error_returns_mcp_error_without_blob_write(self):
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(function_app, "generate_gpt_image", new_callable=AsyncMock, side_effect=RuntimeError("GPT Image did not return a PNG image")),
        ):
            output = FakeContainerClient()
            result = parse_result(await function_app.generate_image(
                json.dumps({"arguments": {"model": "gpt-image-2.5", "prompt": "A"}}), containerClient=output,
            ))
        self.assertTrue(result.isError)
        self.assertIn("did not return a PNG", result.content[0].text)
        self.assertIsNone(output.value)

    async def test_explicit_flux_generation_and_edit_still_use_flux(self):
        client = MagicMock()
        client.generate_image_async = AsyncMock(return_value=b"flux-generated")
        client.flux2edit_image_async = AsyncMock(return_value=b"flux-edited")
        module = types.SimpleNamespace(GptImageClient=MagicMock(return_value=client))
        output = container = FakeContainerClient(b"reference")
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.dict(sys.modules, {"FoundryImageClient": module}),
            patch.object(function_app, "generate_gpt_image", new_callable=AsyncMock) as gpt,
        ):
            result = parse_result(await function_app.generate_image(
                json.dumps({"arguments": {"prompt": "A", "model": "flux-pro-2"}}), containerClient=output,
            ))
            self.assertFalse(result.isError)
            self.assertEqual(output.value, b"flux-generated")
            client.generate_image_async.assert_awaited_once_with(prompt="A", size="1024x1024", quality="standard", n=1)
            result = parse_result(await function_app.edit_image(
                json.dumps({"arguments": {"prompt": "A", "filenames": ["r.png"], "model": "flux-pro-2"}}),
                containerClient=container,
            ))
            self.assertFalse(result.isError)
            self.assertEqual(output.value, b"flux-edited")
            client.flux2edit_image_async.assert_awaited_once_with(images=[b"reference"], prompt="A", size="1024x1024")
            gpt.assert_not_awaited()

    async def test_kontext_flag_and_model_use_same_path_and_cleanup(self):
        for selection in ({"use_flux_kontext": True}, {"model": "flux-kontext"}, {"model": "flux-kontext", "use_flux_kontext": True}):
            with self.subTest(selection=selection):
                paths = []
                async def edit(**kwargs):
                    paths.extend([kwargs["image_path"], *kwargs["additional_images"]])
                    self.assertEqual([Path(path).read_bytes() for path in paths], [PNG, PNG])
                    return PNG
                client = MagicMock()
                client.edit_image_async = AsyncMock(side_effect=edit)
                module = types.SimpleNamespace(GptImageClient=MagicMock(return_value=client))
                container = MagicMock()
                container.get_blob_client.return_value.download_blob.return_value.readall.return_value = PNG
                with patch.dict(os.environ, ENVIRONMENT, clear=True), patch.dict(sys.modules, {"FoundryImageClient": module}):
                    result = parse_result(await function_app.edit_image(
                        json.dumps({"arguments": {"prompt": "A", "filenames": ["one.png", "two.png"], **selection}}),
                        containerClient=container,
                    ))
                self.assertFalse(result.isError)
                self.assertEqual(module.GptImageClient.call_args.kwargs["deployment_name"], "flux-kontext")
                self.assertTrue(paths)
                self.assertTrue(all(not Path(path).exists() for path in paths))

    async def test_gpt_mcp_to_sdk_generation_and_edit(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.params.get("api-version") != "preview":
                return httpx.Response(400, json={
                    "error": {"message": "Missing or invalid api-version"},
                })
            return httpx.Response(200, json={
                "created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}],
            })

        def make_client(**kwargs):
            return AsyncOpenAI(
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                **kwargs,
            )

        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(gpt_image, "AsyncOpenAI", side_effect=make_client),
            patch.object(function_app, "urlstorage", "https://storage.example"),
        ):
            for edit in (False, True):
                with self.subTest(edit=edit):
                    arguments = {"model": "gpt-image-2.5", "prompt": "A"}
                    output = FakeContainerClient(PNG)
                    if edit:
                        arguments["filenames"] = ["r.png"]
                        response = await function_app.edit_image(
                            json.dumps({"arguments": arguments}), containerClient=output,
                        )
                    else:
                        response = await function_app.generate_image(
                            json.dumps({"arguments": arguments}), containerClient=output,
                        )
                    result = parse_result(response)
                    self.assertFalse(result.isError)
                    self.assertEqual(output.value, PNG)
                    self.assertEqual(base64.b64decode(result.content[1].data), PNG)
        self.assertEqual([request.url.path for request in requests], [
            "/openai/v1/images/generations", "/openai/v1/images/edits",
        ])
        self.assertEqual([dict(request.url.params) for request in requests], [
            {"api-version": "preview"}, {"api-version": "preview"},
        ])

    async def test_invalid_reference_does_not_submit_or_write(self):
        output = container = FakeContainerClient(b"bad reference")
        with (
            patch.dict(os.environ, ENVIRONMENT, clear=True),
            patch.object(gpt_image, "AsyncOpenAI") as client,
        ):
            result = parse_result(await function_app.edit_image(
                json.dumps({"arguments": {
                    "model": "gpt-image-2.5", "prompt": "A", "filenames": ["bad.png"],
                }}), containerClient=container,
            ))
        self.assertTrue(result.isError)
        self.assertIn("PNG or JPEG", result.content[0].text)
        self.assertIsNone(output.value)
        client.assert_not_called()

    def test_mcp_schema_exposes_model_and_optional_naming(self):
        for function in function_app.app.get_functions():
            trigger = function.get_trigger().get_dict_repr()
            properties = trigger["toolProperties"]
            if isinstance(properties, str):
                properties = json.loads(properties)
            model_property = next(prop for prop in properties if prop["propertyName"] == "model")
            self.assertEqual(model_property["propertyType"], "string")
            self.assertFalse(model_property["isRequired"])
            properties = {prop["propertyName"]: prop for prop in properties}
            for name, property_type in (("scene_number", "integer"), ("talk_number", "integer"), ("prefix", "string")):
                self.assertFalse(properties[name]["isRequired"])
                self.assertEqual(properties[name]["propertyType"], property_type)
            bindings = [binding.get_dict_repr() for binding in function.get_bindings()]
            blob_bindings = [binding for binding in bindings if binding["type"] == "blob"]
            self.assertEqual(len(blob_bindings), 1)
            self.assertEqual(blob_bindings[0]["name"], "containerClient")
            self.assertEqual(blob_bindings[0]["path"], "fluxjob")
            self.assertEqual(blob_bindings[0]["direction"].name, "IN")
            self.assertEqual(blob_bindings[0]["connection"], "AgentVideoStorage")


if __name__ == "__main__":
    unittest.main()
