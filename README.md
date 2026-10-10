# MCP Image AI Foundry

MCP (Model Context Protocol) remote server on Azure Functions for generating and editing images with Azure AI Foundry using Flux Pro 2, Flux Kontext and GPT Image 2.5.

## Overview

This Azure Function application provides MCP tools for image generation and multi-reference editing using Flux Pro 2, Flux Kontext or an existing GPT Image 2.5 deployment via Azure AI Foundry. It uses the Python V2 programming model for Azure Functions with MCP binding and Pydantic for strong typing of tool properties.

## Features

- **Azure Functions Python V2**: Modern programming model with async support
- **MCP Tool Trigger**: Native MCP binding for Model Context Protocol integration
- **Pydantic Strong Typing**: Type-safe tool properties using Pydantic models
- **FLUX Integration**: Uses Azure OpenAI Image Client for Flux Pro 2 and Flux Kontext
- **GPT Image 2.5 Integration**: Uses the asynchronous OpenAI SDK with the same Foundry resource endpoint and API key; selectable per call without changing existing FLUX calls
- **Optional Reference Upload**: Five-minute, blob-scoped, create-only SAS URLs for uploading PNG/JPEG references before editing
- **Async Image Generation**: Non-blocking asynchronous image generation
- **Error Handling**: Comprehensive error handling and logging
- **Health Check**: Dedicated health check MCP tool

## Prerequisites

- Python 3.10 or higher (use a version supported by your Azure Functions runtime)
- Azure Functions Core Tools v4
- Azure subscription with Azure AI Foundry access
- Azure OpenAI resource with Flux Pro 2 deployment

## Installation

1. Clone this repository:
```bash
git clone https://github.com/zecloud/MCP_Image_AIFoundry.git
cd MCP_Image_AIFoundry
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure local settings:
   - Create a `local.settings.json` file in the project root with the following content:
     ```json
     {
       "IsEncrypted": false,
       "Values": {
         "AzureWebJobsStorage": "UseDevelopmentStorage=true",
         "FUNCTIONS_WORKER_RUNTIME": "python",
         "AZURE_OPENAI_ENDPOINT": "<your Azure OpenAI endpoint URL>",
         "AZURE_OPENAI_API_KEY": "<your Azure OpenAI API key>",
         "AZURE_OPENAI_DEPLOYMENT_NAME": "flux-pro-2",
         "AZURE_FLUX_KONTEXT_DEPLOYMENT_NAME": "flux-kontext",
         "AZURE_GPT_IMAGE_DEPLOYMENT_NAME": "<exact name of your existing GPT Image 2.5 deployment>"
       }
     }
     ```

## Local Development

Run the function locally:
```bash
func start
```

The MCP server will expose the following tools:
- `generate_image` - Generate images using Flux Pro 2 (default) or GPT Image 2.5
- `edit_image` - Edit images with multiple references using Flux Pro 2 (default), Flux Kontext or GPT Image 2.5
- `get_image_upload_sas` - Get a five-minute SAS URL to upload one new reference image into an existing video's folder

## Testing

Run the unit tests without Azure credentials or a running Functions host:

```bash
python -m unittest discover -s test -p "test_*.py" -q
```

The tests cover the actual OpenAI SDK using a mocked HTTP transport (JSON generation,
multipart multi-reference editing, response decoding, rate-limit behavior and client
cleanup), MCP model selection, validation, Blob/SAS output, upload SAS scope,
permissions, expiration, client cleanup, uploaded-reference editing and FLUX compatibility.

For a live smoke test, configure `local.settings.json`, start the Functions host,
and use an MCP client to invoke the examples below. These calls use your existing
paid deployment. Check both generation and editing, including a returned SAS URL.
A live Foundry request is not part of the unit tests.

## MCP Tool Usage

### Generate Image Tool

**Tool Name:** `generate_image`

**Description:** Generate images using Flux Pro 2 (default) or GPT Image 2.5 via Azure AI Foundry. Provide a text prompt describing the image you want to create.

**Tool Properties:**
- `prompt` (required, string): Text description of the image to generate
- `model` (optional, string): `flux-pro-2` (default) or `gpt-image-2.5`; this is a routing selector, not the Azure deployment name
- `size` (optional, string): Image size, default is "1024x1024"
- `quality` (optional, string): Image quality, default is "standard"
- `n` (optional, number): Default is 1. GPT Image calls accept only `n=1` in this version because the MCP tool writes and returns a single image; other values are rejected before generation. Existing FLUX behavior is unchanged.
- `video_id` (optional, string): Storage folder and filename identifier; defaults to `test`
- `scene_number` (optional, integer): Adds `-scene<number>` only when provided; omit the argument to exclude this component
- `talk_number` (optional, integer): Adds `-talk<number>` only when provided; omit the argument to exclude this component
- `prefix` (optional, string): Filename prefix; defaults to `img` for generation and `edited` for editing when omitted. Use `""` to omit the prefix entirely.
- `sas` (optional, boolean): When `true`, return a read-only SAS URL valid for 60 minutes; default is `false`

### Image filenames

Both tools store PNG files under `fluxjob/agentvideo/{video_id}/`. Only supplied
scene/talk numbers appear in the name, independently; explicit `0` is preserved.
Existing calls that supply both numbers keep their current filenames.

These naming arguments are optional, not nullable, in the published MCP input
schema. Omit unused arguments instead of sending JSON `null`; schema-validating
clients reject `null` before the function runs. Internal Python `None` defaults
do not make the public MCP fields nullable.

Examples with `video_id="clip"`:

| Naming arguments | Generation filename | Editing filename |
| --- | --- | --- |
| All naming arguments omitted | `img-clip.png` | `edited-clip.png` |
| `prefix="cover"` | `cover-clip.png` | `cover-clip.png` |
| `scene_number=0` | `img-clip-scene0.png` | `edited-clip-scene0.png` |
| `talk_number=2` | `img-clip-talk2.png` | `edited-clip-talk2.png` |
| `scene_number=0, talk_number=0` | `img-clip-scene0-talk0.png` | `edited-clip-scene0-talk0.png` |
| `prefix=""` | `clip.png` | `clip.png` |

Without naming arguments, generation now writes `img-test.png` and editing writes
`edited-test.png`, rather than adding `-scene0-talk0`. To retain the old default
name, explicitly supply both numbers as `0`. Repeated calls with the same naming
arguments overwrite the same blob; use distinct prefixes or numbers to keep
multiple outputs. The stored blob, returned URL and optional SAS all use the same
computed name.

**Example MCP Tool Call:**
```json
{
  "name": "generate_image",
  "arguments": {
    "prompt": "A beautiful sunset over mountains",
    "size": "1024x1024",
    "quality": "standard",
    "n": 1,
    "sas": true
  }
}
```

**Response:**
```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"status\":\"success\",\"image\":\"https://.../image.png\"}"
    },
    {
      "type": "image",
      "data": "<base64-encoded PNG>",
      "mimeType": "image/png"
    }
  ],
  "isError": false
}
```

Errors use the same result shape and expose a client-readable message:

```json
{
  "content": [
    {
      "type": "text",
      "text": "Invalid request: ..."
    }
  ],
  "isError": true
}
```

### GPT Image 2.5 configuration and usage

Set `AZURE_GPT_IMAGE_DEPLOYMENT_NAME` to the **exact deployment name** shown in Foundry,
not an assumed model identifier. It can point to your existing Sunburst or Flare
deployment; the MCP selector remains `gpt-image-2.5` for either variant. GPT calls
reuse `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`. FLUX still uses
`AZURE_OPENAI_DEPLOYMENT_NAME`; do not replace it with the GPT deployment name.

The endpoint must be the HTTPS Foundry resource URL (for example,
`https://your-resource.openai.azure.com/` or `https://your-resource.services.ai.azure.com/`),
or its `/openai/v1/` base URL. Project endpoints such as `/api/projects/...` are
not supported by the image API. The adapter uses
`/openai/v1/images/generations?api-version=preview` and
`/openai/v1/images/edits?api-version=preview` and explicitly requests PNG output.
The preview query is configured on the GPT client's `default_query`; no query
needs to be added to `AZURE_OPENAI_ENDPOINT`.

GPT parameters:
- `quality`: `auto`, `low`, `medium`, `high`, `xhigh` or `max`. The existing
  `standard` default, or `null`, is translated to `auto` only for GPT.
- `size`: `auto` (also used for `null`) or `WIDTHxHEIGHT`. Both edges must be
  multiples of 16 and at most 3840 pixels, the aspect ratio must be between
  1:3 and 3:1, and the total must be 655360–8294400 pixels. For example,
  `1024x1024`, `1536x864`, `704x1280` and `3840x2160` are valid.
  Resolutions above `2560x1440` are experimental according to
  [Microsoft's image generation guide](https://learn.microsoft.com/azure/foundry/openai/how-to/dall-e).
- `n`: only `1` is supported by the GPT route in this version.

```json
{
  "name": "generate_image",
  "arguments": {
    "model": "gpt-image-2.5",
    "prompt": "A cinematic mountain landscape at sunrise",
    "size": "1536x864",
    "quality": "high",
    "n": 1,
    "video_id": "test",
    "scene_number": 0,
    "talk_number": 0,
    "prefix": "img",
    "sas": true
  }
}
```

### Upload a reference image (optional)

**Tool Name:** `get_image_upload_sas`

This optional step lets a client upload a local image before calling `edit_image`.
Existing editing calls using images already in storage are unchanged. Uploads go
to the existing `fluxjob` container, under `agentvideo/{video_id}/{filename}`;
no container named after the video is created.

**Tool Properties:**
- `video_id` (required, string): Video folder identifier, 1–128 characters
- `filename` (required, string): New reference filename, 1–255 characters, ending
  in `.png`, `.jpg` or `.jpeg` (case-insensitive)

Both identifiers must be single path components: no `/`, `\`, URL syntax
(`?`, `#`, `%`), control characters, surrounding whitespace or trailing dots.
Use a unique filename when uploading multiple versions of a reference.

**Example MCP Tool Call:**
```json
{
  "name": "get_image_upload_sas",
  "arguments": {
    "video_id": "clip",
    "filename": "my-reference.jpg"
  }
}
```

The result contains one MCP text block with JSON metadata, not image content.
Example decoded metadata (the SAS token is illustrative):
```json
{
  "status": "success",
  "video_id": "clip",
  "filename": "my-reference.jpg",
  "blob_name": "agentvideo/clip/my-reference.jpg",
  "blob_url": "https://your-storage.blob.core.windows.net/fluxjob/agentvideo/clip/my-reference.jpg",
  "upload_url": "https://your-storage.blob.core.windows.net/fluxjob/agentvideo/clip/my-reference.jpg?<sas-token>",
  "expires_at": "2026-10-10T12:05:00+00:00",
  "method": "PUT",
  "headers": {
    "x-ms-blob-type": "BlockBlob",
    "Content-Type": "image/jpeg"
  }
}
```

The user delegation SAS expires **five minutes after issuance**, uses HTTPS only,
and grants **create (`c`) access to this single blob**. It cannot read, list,
delete or overwrite existing blobs. Its start time is backdated five minutes to
allow for clock skew. If an upload fails because the filename already exists,
request another SAS using a new filename. If it expires before upload, request
a fresh SAS. The unsigned `blob_url` does not grant public read access.

Upload the raw file bytes in a **single PUT** using the returned method and
headers; do not send multipart/form-data, JSON or base64. For example, in PowerShell:
```powershell
$uploadUrl = '<upload_url returned by the tool>'
curl.exe --fail-with-body --request PUT --upload-file '.\my-reference.jpg' `
  --header 'x-ms-blob-type: BlockBlob' `
  --header 'Content-Type: image/jpeg' "$uploadUrl"
```

Wait for Blob Storage's **201 Created** response before editing. The SAS does
not upload or validate the file content itself; clients should supply actual
PNG/JPEG data compatible with their selected editing model. GPT validates
reference content and requires each image to be smaller than 50 MB.

Then use the returned `filename` and the same `video_id`:
```json
{
  "name": "edit_image",
  "arguments": {
    "video_id": "clip",
    "filenames": ["my-reference.jpg"],
    "prompt": "Keep the subject and replace the background with a sunset"
  }
}
```

Uploaded references can be combined with existing references in `filenames`.
For browser uploads, configure Blob service **CORS** to allow the client origin,
`PUT`, and the returned request headers (and `OPTIONS` preflight). A SAS does not
bypass storage firewall/private endpoint rules or the need to authenticate to
the MCP endpoint. Treat `upload_url` as a secret; do not log or persist its token.

### Edit Image Tool

**Tool Name:** `edit_image`

Use `filenames` (required, nonempty list of reference image filenames) and `prompt`
(required). Files are read from `fluxjob/agentvideo/{video_id}/{filename}`. The
remaining size, quality, naming and SAS parameters are shared with generation.

`model` accepts `flux-pro-2`, `flux-kontext` or `gpt-image-2.5`. If omitted,
existing calls still choose Flux Pro 2, or Flux Kontext when
`use_flux_kontext=true`. An explicit non-Kontext model combined with
`use_flux_kontext=true` is rejected rather than silently choosing a provider.
GPT editing accepts 1–16 PNG/JPEG references, each nonempty and smaller than
50 MB. All references are submitted in a single multipart edit request.

```json
{
  "name": "edit_image",
  "arguments": {
    "model": "gpt-image-2.5",
    "filenames": ["img-test-scene0-talk0.png"],
    "prompt": "Keep the mountains unchanged and add a dramatic sunset",
    "size": "1536x864",
    "quality": "high",
    "n": 1,
    "video_id": "test",
    "scene_number": 0,
    "talk_number": 0,
    "prefix": "edited",
    "sas": true
  }
}
```

Both providers retain the same PNG Blob naming and MCP text/image result.
Editing also returns `reference_images_used` in the text metadata. GPT's HTTP
client is closed after each call and uses a 300-second timeout with automatic
retries disabled to avoid unintentionally submitting duplicate paid generations.
If a timeout or rate limit occurs, the caller decides whether to retry. The
Functions host and MCP client timeouts must accommodate the request duration.
No GPT failure automatically falls back to FLUX.

### Health Check Tool

**Tool Name:** `health_check`

**Description:** Check the health status of the MCP Image Generator service.

**Response:**
```json
{
  "status": "healthy",
  "service": "MCP Image Generator",
  "version": "1.0.0"
}
```

## Deployment

Deploy to Azure Functions:

```bash
func azure functionapp publish <YOUR_FUNCTION_APP_NAME>
```

Make sure to configure the application settings in Azure:
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT_NAME` (FLUX)
- `AZURE_FLUX_KONTEXT_DEPLOYMENT_NAME` (optional, defaults to `flux-kontext`)
- `AZURE_GPT_IMAGE_DEPLOYMENT_NAME` (required only when selecting GPT Image)
- `AgentVideoStorage__blobServiceUri`

Generating read or upload SAS URLs requires permission to obtain a user
delegation key at **storage-account scope** (for example **Storage Blob Delegator**)
and the corresponding blob data access. The Function App identity needs read/write
access to `fluxjob` (for example **Storage Blob Data Contributor**). That role at
storage-account scope also includes the delegation-key permission; when data access
is scoped only to the container, add **Storage Blob Delegator** at account scope.
The `AgentVideoStorage` container binding supplies the SDK client used to read
references and upload images with dynamic filenames. The `fluxjob` container must
already exist. Upload SAS generation does not create containers or access account keys.

`AgentVideoStorage__blobServiceUri` must be the HTTPS Blob service endpoint,
for example `https://your-storage.blob.core.windows.net`. For a user-assigned
identity, set `AgentVideoStorage__clientId` (or `AZURE_CLIENT_ID`). Local development
uses `DefaultAzureCredential` when `AZURE_FUNCTIONS_ENVIRONMENT=Development`;
production uses `ManagedIdentityCredential`. Local credentials need the same
storage permissions as the managed identity.

## Project Structure

```
MCP_Image_AIFoundry/
├── function_app.py          # Main function app with MCP tool triggers
├── gpt_image.py             # Async GPT Image adapter and provider validation
├── test/                    # Unit tests and manual request examples
├── host.json                # Function app host configuration
├── local.settings.json      # Local development settings (gitignored)
├── requirements.txt         # Python dependencies
├── test_function.py         # Test script for validation
├── .env.example            # Example environment configuration
├── .funcignore             # Deployment filtering
├── .gitignore              # Git ignore rules
└── README.md               # This file
```

## Dependencies

- `azure-functions>=1.26.0b3`: Azure Functions Python worker
- `azure-identity`: Passwordless authentication with the Function App managed identity
- `azure-storage-blob`: Blob access and read-only/create-only user delegation SAS generation
- `azureopenaigptimageclient`: Azure OpenAI Image Client for FLUX
- `openai>=2.32.0,<3`: Async GPT Image generation and multipart editing
- `pydantic>=2.0.0`: Data validation and settings management
- `requests>=2.31.0`: HTTP library for testing

## Technical Details

### MCP Tool Binding

This project uses the native MCP tool trigger binding for Azure Functions, which provides:
- Automatic tool registration in the MCP protocol
- Strong typing through Pydantic models
- Seamless integration with MCP clients and agents

### Pydantic Models

Tool properties are exposed with `mcp_tool_property` decorators and validated with Pydantic request models. Provider-specific GPT validation runs before submitting a generation or downloading edit references.

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
