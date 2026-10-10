import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, quote, urlsplit

from azure.storage.blob import UserDelegationKey
from mcp.types import CallToolResult

import function_app


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
STORAGE_URL = "https://storage.blob.core.windows.net"


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


class ImageUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.credential = MagicMock()
        self.service = MagicMock()
        self.service.account_name = "storage"
        key = UserDelegationKey()
        key.signed_oid = "11111111-1111-1111-1111-111111111111"
        key.signed_tid = "22222222-2222-2222-2222-222222222222"
        key.signed_start = "2026-10-10T11:55:00Z"
        key.signed_expiry = "2026-10-10T12:05:00Z"
        key.signed_service = "b"
        key.signed_version = "2025-11-05"
        key.value = base64.b64encode(b"unit-test-delegation-key").decode()
        self.service.get_user_delegation_key.return_value = key
        self.service_context = MagicMock()
        self.service_context.__enter__.return_value = self.service
        for name, value in (
            ("urlstorage", STORAGE_URL),
            ("_get_storage_credential", MagicMock(return_value=self.credential)),
            ("BlobServiceClient", MagicMock(return_value=self.service_context)),
        ):
            patcher = patch.object(function_app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock_patcher = patch.object(function_app, "datetime", wraps=datetime)
        self.clock = clock_patcher.start()
        self.addCleanup(clock_patcher.stop)
        self.clock.now.return_value = NOW

    async def invoke(self, arguments):
        return parse_result(await function_app.get_image_upload_sas(
            json.dumps({"arguments": arguments})
        ))

    async def test_real_sdk_sas_is_blob_scoped_create_only_https_and_five_minutes(self):
        for filename, content_type in (
            ("reference.png", "image/png"), ("photo.JPG", "image/jpeg"),
            ("photo.jpeg", "image/jpeg"), ("référence été.png", "image/png"),
        ):
            with self.subTest(filename=filename):
                result = await self.invoke({"video_id": "clip", "filename": filename})
                self.assertFalse(result.isError)
                self.assertEqual(len(result.content), 1)
                metadata = json.loads(result.content[0].text)
                blob_name = f"agentvideo/clip/{filename}"
                blob_url = f"{STORAGE_URL}/fluxjob/{quote(blob_name, safe='/')}"
                self.assertEqual(metadata["status"], "success")
                self.assertEqual(metadata["video_id"], "clip")
                self.assertEqual(metadata["filename"], filename)
                self.assertEqual(metadata["blob_name"], blob_name)
                self.assertEqual(metadata["blob_url"], blob_url)
                self.assertEqual(metadata["upload_url"].split("?")[0], blob_url)
                self.assertEqual(metadata["method"], "PUT")
                self.assertEqual(metadata["headers"], {
                    "x-ms-blob-type": "BlockBlob", "Content-Type": content_type,
                })
                token = parse_qs(urlsplit(metadata["upload_url"]).query)
                self.assertEqual(token["sp"], ["c"])
                self.assertEqual(token["sr"], ["b"])
                self.assertEqual(token["spr"], ["https"])
                self.assertTrue(token["sig"][0])
                expiry = datetime.fromisoformat(token["se"][0].replace("Z", "+00:00"))
                start = datetime.fromisoformat(token["st"][0].replace("Z", "+00:00"))
                self.assertEqual(expiry, NOW + timedelta(minutes=5))
                self.assertEqual(start, NOW - timedelta(minutes=5))
                self.assertEqual(datetime.fromisoformat(metadata["expires_at"]), expiry)
                self.service.get_user_delegation_key.assert_called_with(
                    key_start_time=start, key_expiry_time=expiry,
                )
        function_app.BlobServiceClient.assert_called_with(
            account_url=STORAGE_URL, credential=self.credential,
        )
        self.assertEqual(self.credential.close.call_count, 4)
        self.assertEqual(self.service_context.__exit__.call_count, 4)

    async def test_expiration_metadata_matches_sdk_second_precision(self):
        self.clock.now.return_value = NOW.replace(microsecond=987654)
        result = await self.invoke({"video_id": "clip", "filename": "reference.png"})
        self.assertFalse(result.isError)
        metadata = json.loads(result.content[0].text)
        token = parse_qs(urlsplit(metadata["upload_url"]).query)
        expiry = datetime.fromisoformat(token["se"][0].replace("Z", "+00:00"))
        self.assertEqual(datetime.fromisoformat(metadata["expires_at"]), expiry)
        self.assertEqual(expiry, NOW + timedelta(minutes=5))

    def test_shared_signer_preserves_read_only_one_hour_sas(self):
        signed_url = function_app._append_read_sas(
            f"{STORAGE_URL}/fluxjob/agentvideo/clip/output.png", "agentvideo/clip/output.png"
        )
        token = parse_qs(urlsplit(signed_url).query)
        self.assertEqual(token["sp"], ["r"])
        expiry = datetime.fromisoformat(token["se"][0].replace("Z", "+00:00"))
        self.assertEqual(expiry, NOW + timedelta(hours=1))
        self.credential.close.assert_called_once_with()

    async def test_invalid_components_do_not_request_a_delegation_key(self):
        cases = [
            {"video_id": value, "filename": "reference.png"}
            for value in ("", ".", "..", "../other", "nested/video", "nested\\video",
                          "%2e%2e", "video?sig=x", "video#x", "clip\n", "clip.",
                          " clip", "clip ", None, 123, "a" * 129)
        ] + [
            {"video_id": "clip", "filename": value}
            for value in ("", "../reference.png", "folder/photo.png", "folder\\photo.png",
                          "%2e%2e.png", "photo.png?sig=x", "photo#x.png", "photo\x00.png",
                          "photo\x7f.png", "photo.gif", "photo.webp", "photo.svg", "photo",
                          " photo.png", "photo.png ", None, 123, "a" * 252 + ".png")
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = await self.invoke(arguments)
                self.assertTrue(result.isError)
                self.assertIn("Invalid request:", result.content[0].text)
        self.service.get_user_delegation_key.assert_not_called()
        function_app.BlobServiceClient.assert_not_called()
        function_app._get_storage_credential.assert_not_called()

    async def test_unsafe_or_missing_endpoint_does_not_issue_a_sas(self):
        for endpoint in (
            "", "http://storage.example", "invalid", "https://storage.example/container",
            "https://storage.example?sig=secret", "https://storage.example#fragment",
            "https://user:secret@storage.example",
        ):
            with self.subTest(endpoint=endpoint), patch.object(function_app, "urlstorage", endpoint):
                result = await self.invoke({"video_id": "clip", "filename": "reference.png"})
                self.assertTrue(result.isError)
                self.assertIn("AgentVideoStorage__blobServiceUri", result.content[0].text)
        self.service.get_user_delegation_key.assert_not_called()

    async def test_storage_failure_closes_clients_and_does_not_expose_secrets(self):
        for error_type in (RuntimeError, ValueError):
            with self.subTest(error_type=error_type):
                self.service.get_user_delegation_key.side_effect = error_type("sig=private-token")
                self.credential.close.reset_mock()
                self.service_context.__exit__.reset_mock()
                with self.assertLogs(level="ERROR") as captured:
                    result = await self.invoke({"video_id": "clip", "filename": "reference.png"})
                self.assertTrue(result.isError)
                self.assertNotIn("private-token", result.content[0].text)
                self.assertNotIn("private-token", " ".join(captured.output))
                self.credential.close.assert_called_once_with()
                self.service_context.__exit__.assert_called_once()

    async def test_signing_failure_closes_clients(self):
        with patch.object(function_app, "generate_blob_sas", side_effect=RuntimeError("cannot sign")):
            result = await self.invoke({"video_id": "clip", "filename": "reference.png"})
        self.assertTrue(result.isError)
        self.credential.close.assert_called_once_with()
        self.service_context.__exit__.assert_called_once()

    async def test_client_construction_failure_closes_credential(self):
        function_app.BlobServiceClient.side_effect = RuntimeError("cannot connect")
        result = await self.invoke({"video_id": "clip", "filename": "reference.png"})
        self.assertTrue(result.isError)
        self.credential.close.assert_called_once_with()

    async def test_uploaded_blob_can_be_used_as_edit_reference(self):
        upload = await self.invoke({"video_id": "clip", "filename": "my-upload.jpg"})
        metadata = json.loads(upload.content[0].text)
        reference = b"\xff\xd8\xffuploaded-reference"
        generated = b"edited-image"
        blobs = {metadata["blob_name"]: reference}
        container = MagicMock()

        def get_blob_client(name):
            client = MagicMock()
            client.download_blob.return_value.readall.side_effect = lambda: blobs[name]
            client.upload_blob.side_effect = lambda data, **kwargs: blobs.update({name: data})
            return client

        container.get_blob_client.side_effect = get_blob_client
        with (
            patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": "https://foundry.example",
                                    "AZURE_OPENAI_API_KEY": "test-key"}),
            patch.object(function_app, "generate_gpt_image", AsyncMock(return_value=generated)) as edit,
        ):
            result = parse_result(await function_app.edit_image(
                json.dumps({"arguments": {
                    "model": "gpt-image-2.5", "video_id": metadata["video_id"],
                    "filenames": [metadata["filename"]], "prompt": "Make it brighter",
                }}), containerClient=container,
            ))
        self.assertFalse(result.isError)
        self.assertEqual(edit.call_args.kwargs["images"], [reference])
        self.assertEqual(blobs["agentvideo/clip/edited-clip.png"], generated)
        self.assertEqual(json.loads(result.content[0].text)["reference_images_used"], 1)

    def test_published_mcp_schema_has_required_identifiers_and_no_blob_binding(self):
        with patch.object(function_app.app, "functions_bindings", {}):
            functions = {function.get_function_name(): function for function in function_app.app.get_functions()}
        self.assertIn("get_image_upload_sas", functions)
        function = functions["get_image_upload_sas"]
        properties = function.get_trigger().get_dict_repr()["toolProperties"]
        if isinstance(properties, str):
            properties = json.loads(properties)
        self.assertEqual({prop["propertyName"] for prop in properties}, {"video_id", "filename"})
        for prop in properties:
            self.assertTrue(prop["isRequired"])
            self.assertEqual(prop["propertyType"], "string")
        self.assertEqual(len(function.get_bindings()), 1)


if __name__ == "__main__":
    unittest.main()
