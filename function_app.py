import asyncio
import base64
import binascii
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from urllib.parse import quote

import azure.functions as func
import azurefunctions.extensions.bindings.blob as blob
import logging
import json
import os
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel, ConfigDict, Field

from gpt_image import (
    GPT_IMAGE_MODEL,
    MAX_REFERENCE_BYTES,
    PNG_SIGNATURE,
    generate_gpt_image,
    validate_gpt_parameters,
    validate_reference_count,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
urlstorage = os.environ.get("AgentVideoStorage__blobServiceUri", "").rstrip("/")
blob_container_name = "fluxjob"
read_sas_lifetime = timedelta(hours=1)


def _get_storage_credential():
    client_id = (
        os.environ.get("AgentVideoStorage__clientId")
        or os.environ.get("AZURE_CLIENT_ID")
    )
    if os.environ.get("AZURE_FUNCTIONS_ENVIRONMENT") == "Development":
        return DefaultAzureCredential(managed_identity_client_id=client_id)
    return ManagedIdentityCredential(client_id=client_id)


def _append_read_sas(blob_url: str, blob_name: str) -> str:
    if not urlstorage:
        raise RuntimeError(
            "AgentVideoStorage__blobServiceUri is required to generate a SAS URL"
        )

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(minutes=5)
    expiry_time = now + read_sas_lifetime
    credential = _get_storage_credential()
    try:
        with BlobServiceClient(
            account_url=urlstorage,
            credential=credential,
        ) as service_client:
            delegation_key = service_client.get_user_delegation_key(
                key_start_time=start_time,
                key_expiry_time=expiry_time,
            )
            sas_token = generate_blob_sas(
                account_name=service_client.account_name,
                container_name=blob_container_name,
                blob_name=blob_name,
                user_delegation_key=delegation_key,
                permission=BlobSasPermissions(read=True),
                start=start_time,
                expiry=expiry_time,
            )
    finally:
        credential.close()

    return f"{blob_url}?{sas_token}"


def _success_result(
    image_bytes: bytes,
    image_url: str,
    **metadata,
) -> CallToolResult:
    response = {"status": "success", "image": image_url, **metadata}
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(response)),
            ImageContent(
                type="image",
                data=base64.b64encode(image_bytes).decode('utf-8'),
                mimeType="image/png",
            )
        ]
    )


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,
    )


def _image_blob_name(
    video_id: str,
    prefix: str,
    scene_number: Optional[int],
    talk_number: Optional[int],
) -> str:
    parts = [prefix, video_id] if prefix else [video_id]
    if scene_number is not None:
        parts.append(f"scene{scene_number}")
    if talk_number is not None:
        parts.append(f"talk{talk_number}")
    return f"agentvideo/{video_id}/{'-'.join(parts)}.png"


async def _upload_image(
    container_client: blob.ContainerClient,
    blob_name: str,
    image_bytes: bytes,
) -> None:
    # A static output binding cannot omit individual filename components.
    blob_client = container_client.get_blob_client(blob_name)
    await asyncio.to_thread(
        blob_client.upload_blob,
        image_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type="image/png"),
    )


def _decode_uploaded_image(image_base64: str) -> bytes:
    declared_mime = None
    encoded = image_base64
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        supported_headers = {
            "data:image/png;base64": "image/png",
            "data:image/jpeg;base64": "image/jpeg",
        }
        if not separator or header not in supported_headers:
            raise ValueError("image_base64 data URL must contain a base64 PNG or JPEG image")
        declared_mime = supported_headers[header]
    if len(encoded) > 4 * ((MAX_REFERENCE_BYTES + 2) // 3):
        raise ValueError("Uploaded image must be nonempty and smaller than 50 MB")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("image_base64 must contain valid base64 image data") from error
    if not image or len(image) >= MAX_REFERENCE_BYTES:
        raise ValueError("Uploaded image must be nonempty and smaller than 50 MB")
    if image.startswith(PNG_SIGNATURE):
        mime_type = "image/png"
    elif image.startswith(b"\xff\xd8\xff"):
        mime_type = "image/jpeg"
    else:
        raise ValueError("Uploaded image must be a PNG or JPEG image")
    if declared_mime is not None and declared_mime != mime_type:
        raise ValueError("image_base64 data URL MIME type does not match the image")
    return image


# Pydantic model for image generation request
class ImageGenerationRequest(BaseModel):
    """Request model for image generation using Flux Pro 2 or GPT Image 2.5."""
    model: Optional[Literal["flux-pro-2", "gpt-image-2.5"]] = Field(default=None, description="Image model; defaults to flux-pro-2")
    prompt: str = Field(..., description="The text description of the image to generate")
    size: Optional[str] = Field(default="1024x1024", description="The size of the generated image (e.g., '1024x1024')")
    quality: Optional[str] = Field(default="standard", description="The quality of the generated image")
    n: Optional[int] = Field(default=1, description="The number of images to generate")
    video_id: Optional[str] = Field(default="test", description="video ID for associating generated images with a video")
    scene_number: Optional[int] = Field(default=None, description="Optional scene number; omit the argument to exclude it from the filename")
    talk_number: Optional[int] = Field(default=None, description="Optional talk number; omit the argument to exclude it from the filename")
    prefix: Optional[str] = Field(default="img", description="Optional filename prefix; defaults to img when omitted")
    sas: bool = Field(default=False, description="If true, return a read-only SAS URL for the generated image")

# Pydantic model for image editing request
class ImageEditRequest(BaseModel):
    """Request model for image editing using Flux Pro 2, Flux Kontext or GPT Image 2.5."""
    model_config = ConfigDict(hide_input_in_errors=True)

    model: Optional[Literal["flux-pro-2", "flux-kontext", "gpt-image-2.5"]] = Field(default=None, description="Image model; defaults to the existing Flux selection")
    filenames: list[str] = Field(default_factory=list, description="Optional reference image filenames; provide filenames and/or image_base64")
    image_base64: Optional[str] = Field(default=None, repr=False, description="Optional uploaded PNG/JPEG as raw base64 or a data URL; smaller than 50 MB")
    prompt: str = Field(..., description="The text description of how to edit the image")
    use_flux_kontext: Optional[bool] = Field(default=False, description="If true, use Flux Kontext model for editing instead of Flux Pro 2")
    size: Optional[str] = Field(default="1024x1024", description="The size of the edited image (e.g., '1024x1024')")
    quality: Optional[str] = Field(default="standard", description="The quality of the edited image")
    n: Optional[int] = Field(default=1, description="The number of images to generate")
    video_id: Optional[str] = Field(default="test", description="video ID for associating edited images with a video")
    scene_number: Optional[int] = Field(default=None, description="Optional scene number; omit the argument to exclude it from the filename")
    talk_number: Optional[int] = Field(default=None, description="Optional talk number; omit the argument to exclude it from the filename")
    prefix: Optional[str] = Field(default="edited", description="Optional filename prefix; defaults to edited when omitted")
    sas: bool = Field(default=False, description="If true, return a read-only SAS URL for the edited image")

@app.mcp_tool(use_result_schema=True)
@app.mcp_tool_property(arg_name="prompt", description="The text description of the image to generate")
@app.mcp_tool_property(arg_name="model", description="flux-pro-2 (default) or gpt-image-2.5; GPT uses the configured Foundry deployment", is_required=False)
@app.mcp_tool_property(arg_name="size", description="Image size (default 1024x1024); GPT accepts auto or dimensions divisible by 16 within its resolution limits", is_required=False)
@app.mcp_tool_property(arg_name="quality", description="Image quality (default standard); GPT maps standard to auto and accepts low, medium, high, xhigh, max or auto", is_required=False)
@app.mcp_tool_property(arg_name="n", description="Number of images (default 1); GPT Image currently requires n=1", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="video_id", description="Video ID for associating generated images with a video", is_required=False)
@app.mcp_tool_property(arg_name="scene_number", description="Optional scene number; omit the argument to exclude it from the filename", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="talk_number", description="Optional talk number; omit the argument to exclude it from the filename", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="prefix", description="Optional filename prefix (default img when omitted); use an empty string for no prefix", is_required=False)
@app.mcp_tool_property(arg_name="sas", description="Return a read-only SAS URL for the generated image", property_type=func.McpPropertyType.BOOLEAN, is_required=False)
@app.blob_input(
    arg_name="containerClient",
    path=blob_container_name,
    connection="AgentVideoStorage"
)
async def generate_image(
    context: func.MCPToolContext,
    containerClient: blob.ContainerClient,
    prompt: str,
    size: Optional[str] = "1024x1024",
    quality: Optional[str] = "standard",
    n: Optional[int] = 1,
    video_id: Optional[str] = "test",
    scene_number: Optional[int] = None,
    talk_number: Optional[int] = None,
    prefix: Optional[str] = "img",
    sas: bool = False,
    model: Optional[str] = None,
) -> CallToolResult:
    """
    Azure Function with MCP trigger that generates images using Flux Pro 2 or GPT Image 2.5
    via Azure AI Foundry.
    
    Args:
        context: The MCP tool invocation context containing the request arguments
        containerClient: ContainerClient to upload the generated image
        
    Returns:
        CallToolResult: The image URL and generated PNG content
    """
    logging.info('MCP Image Generator function received a request.')
    
    try:
        try:
            validated_input = ImageGenerationRequest(
                model=model,
                prompt=prompt,
                size=size,
                quality=quality,
                n=n,
                video_id=video_id,
                scene_number=scene_number,
                talk_number=talk_number,
                prefix=prefix,
                sas=sas,
            )
        except Exception as e:
            error_msg = f"Image generation validation failed: {str(e)}"
            logging.error(error_msg)
            raise ValueError(error_msg) from e
        logging.info(f"Request arguments: {validated_input.model_dump_json()}")
        # Extract parameters from arguments
        prompt = validated_input.prompt
        size = validated_input.size
        quality = validated_input.quality
        n = validated_input.n
        video_id = validated_input.video_id if validated_input.video_id is not None else "test"
        scene_number = validated_input.scene_number
        talk_number = validated_input.talk_number
        prefix = validated_input.prefix if validated_input.prefix is not None else "img"
        sas = validated_input.sas
        model = validated_input.model or "flux-pro-2"
        if model == GPT_IMAGE_MODEL:
            size, quality = validate_gpt_parameters(size, quality, n)
        # Validate required parameters
        if not prompt:
            error_msg = "Missing required parameter: prompt"
            logging.error(error_msg)
            raise ValueError(error_msg)
        
        # Get Azure OpenAI credentials from environment variables
        endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
        api_key = os.environ.get('AZURE_OPENAI_API_KEY')
        deployment_name = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'flux-pro-2')
        
        if not endpoint or not api_key:
            error_msg = "Azure OpenAI credentials not configured"
            logging.error(error_msg)
            raise RuntimeError(error_msg)
        
        if model == GPT_IMAGE_MODEL:
            result = await generate_gpt_image(endpoint, api_key, prompt, size, quality, n)
        else:
            try:
                from FoundryImageClient import GptImageClient
            except ImportError as e:
                raise RuntimeError(f"Image client library not available: {e}") from e

            client = GptImageClient(
                endpoint=endpoint,
                api_key=api_key,
                deployment_name=deployment_name,
                model=GptImageClient.ImageModel.FLUX,
                output_format="png"
            )
            result = await client.generate_image_async(
                prompt=prompt,
                size=size,
                quality=quality,
                n=n
            )
        if isinstance(result, str):
            # Si c'est un chemin de fichier, lire le fichier
            with open(result, "rb") as file:
                image_bytes = file.read()
        else:
            # Sinon, c'est déjà des bytes
            image_bytes = result
        blob_name = _image_blob_name(video_id, prefix, scene_number, talk_number)
        await _upload_image(containerClient, blob_name, image_bytes)

        logging.info("Image generation completed successfully")
        blob_url = f"{urlstorage}/{blob_container_name}/{quote(blob_name, safe='/')}"
        if sas:
            blob_url = _append_read_sas(blob_url, blob_name)
        return _success_result(image_bytes, blob_url)
        
    except ValueError as e:
        error_msg = f"Invalid request: {str(e)}"
        logging.error(error_msg)
        return _error_result(error_msg)
    except Exception as e:
        error_msg = f"Error generating image: {str(e)}"
        logging.error(error_msg, exc_info=True)
        return _error_result(error_msg)


@app.mcp_tool(use_result_schema=True)
@app.mcp_tool_property(arg_name="filenames", description="Optional reference image filenames; provide filenames and/or image_base64", property_type=func.McpPropertyType.STRING, as_array=True, is_required=False)
@app.mcp_tool_property(arg_name="image_base64", description="Optional uploaded PNG/JPEG as raw base64 or a data:image/png;base64,... or data:image/jpeg;base64,... URL; smaller than 50 MB. Appended after filenames when both are supplied", is_required=False)
@app.mcp_tool_property(arg_name="prompt", description="The text description of how to edit the image")
@app.mcp_tool_property(arg_name="model", description="flux-pro-2, flux-kontext or gpt-image-2.5; omit to preserve the existing Flux selection", is_required=False)
@app.mcp_tool_property(arg_name="use_flux_kontext", description="Use Flux Kontext instead of Flux Pro 2", property_type=func.McpPropertyType.BOOLEAN, is_required=False)
@app.mcp_tool_property(arg_name="size", description="Image size (default 1024x1024); GPT accepts auto or dimensions divisible by 16 within its resolution limits", is_required=False)
@app.mcp_tool_property(arg_name="quality", description="Image quality (default standard); GPT maps standard to auto and accepts low, medium, high, xhigh, max or auto", is_required=False)
@app.mcp_tool_property(arg_name="n", description="Number of images (default 1); GPT Image currently requires n=1", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="video_id", description="Video ID for associating edited images with a video", is_required=False)
@app.mcp_tool_property(arg_name="scene_number", description="Optional scene number; omit the argument to exclude it from the filename", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="talk_number", description="Optional talk number; omit the argument to exclude it from the filename", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="prefix", description="Optional filename prefix (default edited when omitted); use an empty string for no prefix", is_required=False)
@app.mcp_tool_property(arg_name="sas", description="Return a read-only SAS URL for the edited image", property_type=func.McpPropertyType.BOOLEAN, is_required=False)
@app.blob_input(
    arg_name="containerClient",
    path=blob_container_name,
    connection="AgentVideoStorage"
)
async def edit_image(
    context: func.MCPToolContext,
    containerClient: blob.ContainerClient,
    filenames: Optional[list[str]] = None,
    prompt: str = "",
    use_flux_kontext: Optional[bool] = False,
    size: Optional[str] = "1024x1024",
    quality: Optional[str] = "standard",
    n: Optional[int] = 1,
    video_id: Optional[str] = "test",
    scene_number: Optional[int] = None,
    talk_number: Optional[int] = None,
    prefix: Optional[str] = "edited",
    sas: bool = False,
    model: Optional[str] = None,
    image_base64: Optional[str] = None,
) -> CallToolResult:
    """
    Azure Function with MCP trigger that edits images using Flux Pro 2, Flux Kontext or GPT Image 2.5
    via Azure AI Foundry with multiple reference images.
    
    Args:
        context: The MCP tool invocation context containing the request arguments
        containerClient: ContainerClient to read references and upload the edited image
        
    Returns:
        CallToolResult: The image URL and edited PNG content
    """
    logging.info('MCP Image Editor function received a request.')
    
    try:
        try:
            validated_input = ImageEditRequest(
                model=model,
                filenames=filenames if filenames is not None else [],
                image_base64=image_base64,
                prompt=prompt,
                use_flux_kontext=use_flux_kontext,
                size=size,
                quality=quality,
                n=n,
                video_id=video_id,
                scene_number=scene_number,
                talk_number=talk_number,
                prefix=prefix,
                sas=sas,
            )
        except Exception as e:
            error_msg = f"Image editing validation failed: {str(e)}"
            logging.error(error_msg)
            raise ValueError(error_msg) from e
        logging.info(
            "Request arguments: %s",
            validated_input.model_dump_json(exclude={"image_base64"}),
        )

        # Extract parameters from arguments
        filenames = validated_input.filenames
        image_base64 = validated_input.image_base64
        prompt = validated_input.prompt
        use_flux_kontext = validated_input.use_flux_kontext
        size = validated_input.size
        quality = validated_input.quality
        n = validated_input.n
        video_id = validated_input.video_id if validated_input.video_id is not None else "test"
        scene_number = validated_input.scene_number
        talk_number = validated_input.talk_number
        prefix = validated_input.prefix if validated_input.prefix is not None else "edited"
        sas = validated_input.sas
        model = validated_input.model
        if model is not None and use_flux_kontext and model != "flux-kontext":
            raise ValueError("use_flux_kontext=true conflicts with the selected model")
        model = model or ("flux-kontext" if use_flux_kontext else "flux-pro-2")
        use_flux_kontext = model == "flux-kontext"
        uploaded_image = (
            _decode_uploaded_image(image_base64) if image_base64 is not None else None
        )
        if model == GPT_IMAGE_MODEL:
            size, quality = validate_gpt_parameters(size, quality, n)
            validate_reference_count(
                filenames + (["uploaded image"] if uploaded_image is not None else [])
            )

        # Validate required parameters
        missing_params = []
        if not prompt:
            missing_params.append("prompt")
        if not filenames and uploaded_image is None:
            missing_params.append("filenames list or image_base64")
        if missing_params:
            error_msg = f"Missing required parameter(s): {', '.join(missing_params)}"
            logging.error(error_msg)
            raise ValueError(error_msg)
        
        # Download all reference images using ContainerClient
        reference_images = []
        for filename in filenames:
            try:
                # Get blob client for each file from the container
                blob_client = containerClient.get_blob_client(f"agentvideo/{video_id}/{filename}")
                
                # Download the blob content
                download_stream = blob_client.download_blob()
                image_data = download_stream.readall()
                
                reference_images.append(image_data)
                logging.info(f"Downloaded reference image: {filename}")
            except Exception as e:
                error_msg = f"Failed to download image {filename}: {str(e)}"
                logging.error(error_msg)
                raise RuntimeError(error_msg) from e
        if uploaded_image is not None:
            reference_images.append(uploaded_image)

        # Get Azure OpenAI credentials from environment variables
        endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
        api_key = os.environ.get('AZURE_OPENAI_API_KEY')
        if use_flux_kontext:
            deployment_name = os.environ.get('AZURE_FLUX_KONTEXT_DEPLOYMENT_NAME', 'flux-kontext')
        else:
            deployment_name = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'flux-pro-2')
        
        if not endpoint or not api_key:
            error_msg = "Azure OpenAI credentials not configured"
            logging.error(error_msg)
            raise RuntimeError(error_msg)
        
        if model == GPT_IMAGE_MODEL:
            result = await generate_gpt_image(
                endpoint, api_key, prompt, size, quality, n, images=reference_images
            )
        else:
            try:
                from FoundryImageClient import GptImageClient
            except ImportError as e:
                raise RuntimeError(f"Image client library not available: {e}") from e

            client = GptImageClient(
                endpoint=endpoint,
                api_key=api_key,
                deployment_name=deployment_name,
                model=GptImageClient.ImageModel.FLUX,
                output_format="png"
            )
            if use_flux_kontext:
                temp_dir = tempfile.TemporaryDirectory()
                temp_files = []
                try:
                    for idx, img_data in enumerate(reference_images):
                        extension = "jpg" if img_data.startswith(b"\xff\xd8\xff") else "png"
                        temp_path = os.path.join(temp_dir.name, f"reference_{idx}.{extension}")
                        with open(temp_path, "wb") as tmp:
                            tmp.write(img_data)
                        temp_files.append(temp_path)

                    result = await client.edit_image_async(
                        image_path=temp_files[0],
                        prompt=prompt,
                        additional_images=temp_files[1:] if len(temp_files) > 1 else None,
                        size=size
                    )
                finally:
                    try:
                        temp_dir.cleanup()
                    except OSError as cleanup_error:
                        logging.warning(
                            "Failed to clean up temporary directory %s: %s",
                            temp_dir.name,
                            cleanup_error,
                        )
            else:
                result = await client.flux2edit_image_async(
                    images=reference_images,
                    prompt=prompt,
                    size=size
                )
        
        if isinstance(result, str):
            # Si c'est un chemin de fichier, lire le fichier
            with open(result, "rb") as file:
                image_bytes = file.read()
        else:
            # Sinon, c'est déjà des bytes
            image_bytes = result
            
        blob_name = _image_blob_name(video_id, prefix, scene_number, talk_number)
        await _upload_image(containerClient, blob_name, image_bytes)

        logging.info("Image editing completed successfully")
        blob_url = f"{urlstorage}/{blob_container_name}/{quote(blob_name, safe='/')}"
        if sas:
            blob_url = _append_read_sas(blob_url, blob_name)
        return _success_result(
            image_bytes,
            blob_url,
            reference_images_used=len(reference_images),
        )
        
    except ValueError as e:
        error_msg = f"Invalid request: {str(e)}"
        logging.error(error_msg)
        return _error_result(error_msg)
    except Exception as e:
        error_msg = f"Error editing image: {str(e)}"
        logging.error(error_msg, exc_info=True)
        return _error_result(error_msg)




# @app.generic_trigger(
#     arg_name="context",
#     type="mcpToolTrigger",
#     toolName="health_check",
#     description="Check the health status of the MCP Image Generator service.",
#     toolProperties="[]",
# )
# async def health_check(context) -> str:
#     """
#     Health check endpoint for MCP service
    
#     Args:
#         context: The MCP tool invocation context
        
#     Returns:
#         str: JSON string with service health status
#     """
#     logging.info('MCP Health Check tool called.')
    
#     response = {
#         "status": "healthy",
#         "service": "MCP Image Generator",
#         "version": "1.0.0"
#     }
    
#     return json.dumps(response)
