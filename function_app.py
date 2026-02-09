import azure.functions as func
import logging
import json
import os
from pydantic import BaseModel, Field
from AzureFunctionsMCPPydanticTool import pydantic_to_tool_properties

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


# Pydantic model for image generation request
class ImageGenerationRequest(BaseModel):
    """Request model for image generation using Flux Pro 2"""
    prompt: str = Field(..., description="The text description of the image to generate")
    size: str = Field(default="1024x1024", description="The size of the generated image (e.g., '1024x1024')")
    quality: str = Field(default="standard", description="The quality of the generated image")
    n: int = Field(default=1, description="The number of images to generate")


# Convert Pydantic model to tool properties JSON
tool_properties_json = pydantic_to_tool_properties(ImageGenerationRequest)


@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="generate_image",
    description="Generate images using Flux Pro 2 model via Azure AI Foundry. Provide a text prompt describing the image you want to create.",
    toolProperties=tool_properties_json,
)
async def generate_image(context) -> str:
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
            from azureopenaigptimageclient import AzureOpenAIImageClient
        except ImportError as e:
            error_msg = f"Image client library not available: {str(e)}"
            logging.error(error_msg)
            return json.dumps({"error": error_msg})
        
        # Initialize the image client
        logging.info(f"Initializing Azure OpenAI Image Client for deployment: {deployment_name}")
        client = AzureOpenAIImageClient(
            endpoint=endpoint,
            api_key=api_key,
            deployment_name=deployment_name
        )
        
        # Generate images asynchronously
        logging.info(f"Generating {n} image(s) with prompt: {prompt}")
        result = await client.generate_images_async(
            prompt=prompt,
            size=size,
            quality=quality,
            n=n
        )
        
        logging.info(f"Image generation completed successfully")
        
        # Format response
        response = {
            "status": "success",
            "images": result.get('data', []),
            "created": result.get('created'),
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
    toolName="health_check",
    description="Check the health status of the MCP Image Generator service.",
    toolProperties="[]",
)
async def health_check(context) -> str:
    """
    Health check endpoint for MCP service
    
    Args:
        context: The MCP tool invocation context
        
    Returns:
        str: JSON string with service health status
    """
    logging.info('MCP Health Check tool called.')
    
    response = {
        "status": "healthy",
        "service": "MCP Image Generator",
        "version": "1.0.0"
    }
    
    return json.dumps(response)

