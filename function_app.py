from typing import Optional

import azure.functions as func
import logging
import json
import os
from pydantic import BaseModel, Field
from AzureFunctionsMCPPydanticTool import pydantic_to_tool_properties

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
            model=GptImageClient.ImageModel.FLUX
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
        blob_url = f"{urlstorage}/fluxjob/agentvideo/{video_id}/img-{video_id}-scene{scene_number}-talk{talk_number}.png"
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

