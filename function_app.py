from typing import Optional
import re

import azure.functions as func
import azurefunctions.extensions.bindings.blob as blob
import logging
import json
import os
import base64
from pydantic import BaseModel, Field, field_validator
from AzureFunctionsMCPPydanticTool import pydantic_to_tool_properties

# Regex to detect a blob filename (short string ending with an image extension)
_FILENAME_PATTERN = re.compile(r'^[\w\-./\\]+\.\w{2,5}$')

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
urlstorage=os.environ.get('AgentVideoStorage__blobServiceUri', '')

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

# Convert Pydantic model to tool properties JSON
tool_properties_json = pydantic_to_tool_properties(ImageGenerationRequest)

# Pydantic model for image editing request
class ImageEditRequest(BaseModel):
    """Request model for image editing using Flux Pro 2"""
    images: list[str] = Field(..., description="List of reference images for editing. Each entry can be a blob storage filename (e.g., 'img-test-scene0-talk0.png') or a base64-encoded image string (optionally prefixed with a data URL like 'data:image/png;base64,...'). The type is auto-detected.", min_length=1)
    prompt: str = Field(..., description="The text description of how to edit the image")
    size: Optional[str] = Field(default="1024x1024", description="The size of the edited image (e.g., '1024x1024')")
    quality: Optional[str] = Field(default="standard", description="The quality of the edited image")
    n: Optional[int] = Field(default=1, description="The number of images to generate")
    video_id: Optional[str] = Field(default="test", description="video ID for associating edited images with a video")
    scene_number: Optional[int] = Field(default=0, description="Scene number for associating edited images with a specific scene in a video")
    talk_number: Optional[int] = Field(default=0, description="Talk number for associating edited images with a specific talk in a video")
    prefix: Optional[str] = Field(default="edited", description="Prefix for the edited image filenames")

    @field_validator('images')
    @classmethod
    def validate_images(cls, v):
        for i, item in enumerate(v):
            if not item or not item.strip():
                raise ValueError(f"images[{i}] must be a non-empty string")
        return v


def _is_filename(value: str) -> bool:
    """Detect whether a string looks like a blob storage filename."""
    return bool(_FILENAME_PATTERN.match(value.strip()))


def _decode_base64_image(b64_image: str) -> bytes:
    """Decode a base64 string, stripping data URL prefix if present."""
    normalized = b64_image
    if normalized.startswith("data:"):
        header, separator, encoded_data = normalized.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("Invalid data URL for base64 image")
        normalized = encoded_data
    return base64.b64decode(normalized, validate=True)

# Convert Pydantic model for image editing to tool properties JSON
edit_tool_properties_json = pydantic_to_tool_properties(ImageEditRequest)


@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="generate_image",
    description="Generate images using Flux Pro 2 model via Azure AI Foundry. Provide a text prompt describing the image you want to create.",
    toolProperties=tool_properties_json,
)
@app.blob_output(
    arg_name="outputBlob",
    path="fluxjob/agentvideo/{arguments.video_id}/{arguments.prefix}-{arguments.video_id}-scene{arguments.scene_number}-talk{arguments.talk_number}.png",
    connection="AgentVideoStorage"
)
async def generate_image(context,outputBlob: func.Out[bytes]) -> str:
    """
    Azure Function with MCP trigger that generates images using Flux Pro 2
    via Azure AI Foundry.
    
    Args:
        context: The MCP tool invocation context containing the request arguments
        
    Returns:
        str: JSON string with the generated image URLs and metadata
    """
    logging.info('MCP Image Generator function received a request.')
    
    try:
        # Parse the context to extract arguments
        content = json.loads(context)
        arguments = content.get("arguments", {})
        
        logging.info(f"Request arguments: {json.dumps(arguments)}")
        try:
            validated_input = ImageGenerationRequest(**arguments)
        except Exception as e:
            error_response = {"success": False, "error": f"Validation échouée: {str(e)}"}
            logging.error(f"Erreur dans generate_image: {str(error_response)}")
            return json.dumps(error_response)
        # Extract parameters from arguments
        prompt = validated_input.prompt
        size = validated_input.size
        quality = validated_input.quality
        n = validated_input.n
        video_id = validated_input.video_id
        scene_number = validated_input.scene_number
        talk_number = validated_input.talk_number
        prefix = validated_input.prefix
        # Validate required parameters
        if not prompt:
            error_msg = "Missing required parameter: prompt"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
        # Get Azure OpenAI credentials from environment variables
        endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
        api_key = os.environ.get('AZURE_OPENAI_API_KEY')
        deployment_name = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'flux-pro-2')
        
        if not endpoint or not api_key:
            error_msg = "Azure OpenAI credentials not configured"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
        # Import the image generation client
        try:
            from FoundryImageClient import GptImageClient
        except ImportError as e:
            error_msg = f"Image client library not available: {str(e)}"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
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
        blob_url = f"{urlstorage}/fluxjob/agentvideo/{video_id}/{prefix}-{video_id}-scene{scene_number}-talk{talk_number}.png"
        # Format response
        response = {
            "status": "success",
            "image": blob_url,
        }
        
        return json.dumps(response)
        
    except ValueError as e:
        error_msg = f"Invalid request: {str(e)}"
        logging.error(error_msg)
        return json.dumps({"error": error_msg})
    except Exception as e:
        error_msg = f"Error generating image: {str(e)}"
        logging.error(error_msg, exc_info=True)
        return json.dumps({"error": error_msg})


@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="edit_image",
    description="Edit images using Flux Pro 2 model via Azure AI Foundry. Provide reference images as blob filenames or base64-encoded strings (auto-detected), along with a text prompt describing the edits.",
    toolProperties=edit_tool_properties_json,
)
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
async def edit_image(context, containerClient: blob.ContainerClient, outputBlob: func.Out[bytes]) -> str:
    """
    Azure Function with MCP trigger that edits images using Flux Pro 2
    via Azure AI Foundry with multiple reference images.
    
    Args:
        context: The MCP tool invocation context containing the request arguments
        containerClient: ContainerClient to access multiple blobs in the container
        outputBlob: The output blob for the edited image
        
    Returns:
        str: JSON string with the edited image URL and metadata
    """
    logging.info('MCP Image Editor function received a request.')
    
    try:
        # Parse the context to extract arguments
        content = json.loads(context)
        arguments = content.get("arguments", {})
        
        safe_args = {k: (f"[{len(v)} image(s), base64 data redacted]" if k == "images" and isinstance(v, list) else v) for k, v in arguments.items()}
        logging.info(f"Request arguments: {json.dumps(safe_args)}")
        try:
            validated_input = ImageEditRequest(**arguments)
        except Exception as e:
            error_response = {"success": False, "error": f"Validation échouée: {str(e)}"}
            logging.error(f"Erreur dans edit_image: {str(error_response)}")
            return json.dumps(error_response)
            
        # Extract parameters from arguments
        images_input = validated_input.images
        prompt = validated_input.prompt
        size = validated_input.size
        quality = validated_input.quality
        n = validated_input.n
        video_id = validated_input.video_id
        scene_number = validated_input.scene_number
        talk_number = validated_input.talk_number
        prefix = validated_input.prefix
        
        # Validate required parameters
        if not prompt:
            error_msg = "Missing required parameter: prompt"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
        # Collect reference images, auto-detecting filenames vs base64
        reference_images = []

        for idx, image_entry in enumerate(images_input):
            if _is_filename(image_entry):
                # Treat as a blob storage filename
                try:
                    blob_client = containerClient.get_blob_client(f"agentvideo/{video_id}/{image_entry}")
                    download_stream = blob_client.download_blob()
                    image_data = download_stream.readall()
                    reference_images.append(image_data)
                    logging.info(f"Downloaded reference image: {image_entry}")
                except Exception as e:
                    error_msg = f"Failed to download image {image_entry}: {str(e)}"
                    logging.error(error_msg)
                    return json.dumps({"error": error_msg})
            else:
                # Treat as base64-encoded image data
                try:
                    image_data = _decode_base64_image(image_entry)
                    reference_images.append(image_data)
                    logging.info(f"Decoded base64 reference image {idx}")
                except Exception as e:
                    error_msg = f"Failed to decode base64 image at index {idx}: {str(e)}"
                    logging.error(error_msg)
                    return json.dumps({"error": error_msg})
        
        # Get Azure OpenAI credentials from environment variables
        endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
        api_key = os.environ.get('AZURE_OPENAI_API_KEY')
        deployment_name = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'flux-pro-2')
        
        if not endpoint or not api_key:
            error_msg = "Azure OpenAI credentials not configured"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
        # Import the image generation client
        try:
            from FoundryImageClient import GptImageClient
        except ImportError as e:
            error_msg = f"Image client library not available: {str(e)}"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
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
        result = await client.flux2edit_image_async(
            images=reference_images,  # Pass list of images
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
        blob_url = f"{urlstorage}/fluxjob/agentvideo/{video_id}/{prefix}-{video_id}-scene{scene_number}-talk{talk_number}.png"
        
        # Format response
        response = {
            "status": "success",
            "image": blob_url,
            "reference_images_used": len(reference_images)
        }
        
        return json.dumps(response)
        
    except ValueError as e:
        error_msg = f"Invalid request: {str(e)}"
        logging.error(error_msg)
        return json.dumps({"error": error_msg})
    except Exception as e:
        error_msg = f"Error editing image: {str(e)}"
        logging.error(error_msg, exc_info=True)
        return json.dumps({"error": error_msg})




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

