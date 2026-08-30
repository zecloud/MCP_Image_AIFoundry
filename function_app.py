import base64
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

import azure.functions as func
import azurefunctions.extensions.bindings.blob as blob
import logging
import json
import os
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    generate_blob_sas,
)
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel, Field

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


# Pydantic model for image generation request
class ImageGenerationRequest(BaseModel):
    """Request model for image generation using Flux Pro 2"""
    prompt: str = Field(..., description="The text description of the image to generate")
    size: Optional[str] = Field(default="1024x1024", description="The size of the generated image (e.g., '1024x1024')")
    quality: Optional[str] = Field(default="standard", description="The quality of the generated image")
    n: Optional[int] = Field(default=1, description="The number of images to generate")
    video_id: Optional[str] = Field(default="test", description="video ID for associating generated images with a video")
    scene_number: Optional[int] = Field(default=0, description="Scene number for associating generated images with a specific scene in a video")
    talk_number: Optional[int] = Field(default=0, description="Talk number for associating generated images with a specific talk in a video")
    prefix: Optional[str] = Field(default="img", description="Prefix for the generated image filenames")
    sas: bool = Field(default=False, description="If true, return a read-only SAS URL for the generated image")

# Pydantic model for image editing request
class ImageEditRequest(BaseModel):
    """Request model for image editing using Flux Pro 2 or Flux Kontext"""
    filenames: list[str] = Field(..., description="List of filenames of reference images to use for editing (e.g., ['img-test-scene0-talk0.png', 'img-test-scene1-talk0.png'])")
    prompt: str = Field(..., description="The text description of how to edit the image")
    use_flux_kontext: Optional[bool] = Field(default=False, description="If true, use Flux Kontext model for editing instead of Flux Pro 2")
    size: Optional[str] = Field(default="1024x1024", description="The size of the edited image (e.g., '1024x1024')")
    quality: Optional[str] = Field(default="standard", description="The quality of the edited image")
    n: Optional[int] = Field(default=1, description="The number of images to generate")
    video_id: Optional[str] = Field(default="test", description="video ID for associating edited images with a video")
    scene_number: Optional[int] = Field(default=0, description="Scene number for associating edited images with a specific scene in a video")
    talk_number: Optional[int] = Field(default=0, description="Talk number for associating edited images with a specific talk in a video")
    prefix: Optional[str] = Field(default="edited", description="Prefix for the edited image filenames")
    sas: bool = Field(default=False, description="If true, return a read-only SAS URL for the edited image")

@app.mcp_tool(use_result_schema=True)
@app.mcp_tool_property(arg_name="prompt", description="The text description of the image to generate")
@app.mcp_tool_property(arg_name="size", description="The size of the generated image", is_required=False)
@app.mcp_tool_property(arg_name="quality", description="The quality of the generated image", is_required=False)
@app.mcp_tool_property(arg_name="n", description="The number of images to generate", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="video_id", description="Video ID for associating generated images with a video", is_required=False)
@app.mcp_tool_property(arg_name="scene_number", description="Scene number for associating generated images with a scene", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="talk_number", description="Talk number for associating generated images with a talk", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="prefix", description="Prefix for the generated image filename", is_required=False)
@app.mcp_tool_property(arg_name="sas", description="Return a read-only SAS URL for the generated image", property_type=func.McpPropertyType.BOOLEAN, is_required=False)
@app.blob_output(
    arg_name="outputBlob",
    path="fluxjob/agentvideo/{arguments.video_id}/{arguments.prefix}-{arguments.video_id}-scene{arguments.scene_number}-talk{arguments.talk_number}.png",
    connection="AgentVideoStorage"
)
async def generate_image(
    context: func.MCPToolContext,
    outputBlob: func.Out[bytes],
    prompt: str,
    size: Optional[str] = "1024x1024",
    quality: Optional[str] = "standard",
    n: Optional[int] = 1,
    video_id: Optional[str] = "test",
    scene_number: Optional[int] = 0,
    talk_number: Optional[int] = 0,
    prefix: Optional[str] = "img",
    sas: bool = False,
) -> CallToolResult:
    """
    Azure Function with MCP trigger that generates images using Flux Pro 2
    via Azure AI Foundry.
    
    Args:
        context: The MCP tool invocation context containing the request arguments
        
    Returns:
        CallToolResult: The image URL and generated PNG content
    """
    logging.info('MCP Image Generator function received a request.')
    
    try:
        try:
            validated_input = ImageGenerationRequest(
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
        video_id = validated_input.video_id
        scene_number = validated_input.scene_number
        talk_number = validated_input.talk_number
        prefix = validated_input.prefix
        sas = validated_input.sas
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
        
        # Import the image generation client
        try:
            from FoundryImageClient import GptImageClient
        except ImportError as e:
            error_msg = f"Image client library not available: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e
        
        # Initialize the image client
        logging.info(f"Initializing Azure OpenAI Image Client for deployment: {deployment_name}")
        client = GptImageClient(
            endpoint=endpoint,
            api_key=api_key,
            deployment_name=deployment_name,
            model=GptImageClient.ImageModel.FLUX,
            output_format="png"
        )
        
        # Generate images asynchronously
        logging.info(f"Generating {n} image(s) with prompt: {prompt}")
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
        outputBlob.set(image_bytes)

        logging.info(f"Image generation completed successfully")
        blob_name = (
            f"agentvideo/{video_id}/"
            f"{prefix}-{video_id}-scene{scene_number}-talk{talk_number}.png"
        )
        blob_url = f"{urlstorage}/{blob_container_name}/{blob_name}"
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
@app.mcp_tool_property(arg_name="filenames", description="List of reference image filenames", property_type=func.McpPropertyType.STRING, as_array=True)
@app.mcp_tool_property(arg_name="prompt", description="The text description of how to edit the image")
@app.mcp_tool_property(arg_name="use_flux_kontext", description="Use Flux Kontext instead of Flux Pro 2", property_type=func.McpPropertyType.BOOLEAN, is_required=False)
@app.mcp_tool_property(arg_name="size", description="The size of the edited image", is_required=False)
@app.mcp_tool_property(arg_name="quality", description="The quality of the edited image", is_required=False)
@app.mcp_tool_property(arg_name="n", description="The number of images to generate", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="video_id", description="Video ID for associating edited images with a video", is_required=False)
@app.mcp_tool_property(arg_name="scene_number", description="Scene number for associating edited images with a scene", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="talk_number", description="Talk number for associating edited images with a talk", property_type=func.McpPropertyType.INTEGER, is_required=False)
@app.mcp_tool_property(arg_name="prefix", description="Prefix for the edited image filename", is_required=False)
@app.mcp_tool_property(arg_name="sas", description="Return a read-only SAS URL for the edited image", property_type=func.McpPropertyType.BOOLEAN, is_required=False)
@app.blob_input(
    arg_name="containerClient",
    path="fluxjob",
    connection="AgentVideoStorage"
)
@app.blob_output(
    arg_name="outputBlob",
    path="fluxjob/agentvideo/{arguments.video_id}/{arguments.prefix}-{arguments.video_id}-scene{arguments.scene_number}-talk{arguments.talk_number}.png",
    connection="AgentVideoStorage"
)
async def edit_image(
    context: func.MCPToolContext,
    containerClient: blob.ContainerClient,
    outputBlob: func.Out[bytes],
    filenames: list[str],
    prompt: str,
    use_flux_kontext: Optional[bool] = False,
    size: Optional[str] = "1024x1024",
    quality: Optional[str] = "standard",
    n: Optional[int] = 1,
    video_id: Optional[str] = "test",
    scene_number: Optional[int] = 0,
    talk_number: Optional[int] = 0,
    prefix: Optional[str] = "edited",
    sas: bool = False,
) -> CallToolResult:
    """
    Azure Function with MCP trigger that edits images using Flux Pro 2
    via Azure AI Foundry with multiple reference images.
    
    Args:
        context: The MCP tool invocation context containing the request arguments
        containerClient: ContainerClient to access multiple blobs in the container
        outputBlob: The output blob for the edited image
        
    Returns:
        CallToolResult: The image URL and edited PNG content
    """
    logging.info('MCP Image Editor function received a request.')
    
    try:
        try:
            validated_input = ImageEditRequest(
                filenames=filenames,
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
        logging.info(f"Request arguments: {validated_input.model_dump_json()}")
            
        # Extract parameters from arguments
        filenames = validated_input.filenames
        prompt = validated_input.prompt
        use_flux_kontext = validated_input.use_flux_kontext
        size = validated_input.size
        quality = validated_input.quality
        n = validated_input.n
        video_id = validated_input.video_id
        scene_number = validated_input.scene_number
        talk_number = validated_input.talk_number
        prefix = validated_input.prefix
        sas = validated_input.sas
        
        # Validate required parameters
        missing_params = []
        if not prompt:
            missing_params.append("prompt")
        if not filenames:
            missing_params.append("filenames list")
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
        
        # Import the image generation client
        try:
            from FoundryImageClient import GptImageClient
        except ImportError as e:
            error_msg = f"Image client library not available: {str(e)}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e
        
        # Initialize the image client
        logging.info(f"Initializing Azure OpenAI Image Client for editing with deployment: {deployment_name}")
        client = GptImageClient(
            endpoint=endpoint,
            api_key=api_key,
            deployment_name=deployment_name,
            model=GptImageClient.ImageModel.FLUX,
            output_format="png"
        )
        
        # Edit image asynchronously with multiple reference images
        logging.info(f"Editing with {len(reference_images)} reference images and prompt: {prompt}")
        if use_flux_kontext:
            # edit_image_async expects file paths, so write bytes to files in a temp directory
            temp_dir = tempfile.TemporaryDirectory()
            temp_files = []
            try:
                for idx,img_data in enumerate(reference_images):
                    temp_path = os.path.join(temp_dir.name, f"reference_{idx}.png")
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
            
        outputBlob.set(image_bytes)

        logging.info(f"Image editing completed successfully")
        blob_name = (
            f"agentvideo/{video_id}/"
            f"{prefix}-{video_id}-scene{scene_number}-talk{talk_number}.png"
        )
        blob_url = f"{urlstorage}/{blob_container_name}/{blob_name}"
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
