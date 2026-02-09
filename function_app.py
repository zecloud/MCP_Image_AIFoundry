import azure.functions as func
import logging
import json
import os
from typing import Optional
import asyncio

app = func.FunctionApp()

@app.function_name(name="MCPImageGenerator")
@app.route(route="mcp/image/generate", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
async def mcp_image_generator(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Function with MCP trigger that generates images using Flux Pro 2
    via Azure AI Foundry.
    
    Expected request body:
    {
        "prompt": "A description of the image to generate",
        "size": "1024x1024",  # Optional, defaults to "1024x1024"
        "quality": "standard",  # Optional, defaults to "standard"
        "n": 1  # Optional, number of images to generate, defaults to 1
    }
    
    Returns:
    {
        "status": "success",
        "images": [
            {
                "url": "https://...",
                "revised_prompt": "..."
            }
        ]
    }
    """
    logging.info('MCP Image Generator function received a request.')
    
    try:
        # Parse request body
        req_body = req.get_json()
        logging.info(f"Request body: {json.dumps(req_body)}")
        
        # Extract parameters
        prompt = req_body.get('prompt')
        size = req_body.get('size', '1024x1024')
        quality = req_body.get('quality', 'standard')
        n = req_body.get('n', 1)
        
        # Validate required parameters
        if not prompt:
            return func.HttpResponse(
                json.dumps({"error": "Missing required parameter: prompt"}),
                status_code=400,
                mimetype="application/json"
            )
        
        # Get Azure OpenAI credentials from environment variables
        endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT')
        api_key = os.environ.get('AZURE_OPENAI_API_KEY')
        deployment_name = os.environ.get('AZURE_OPENAI_DEPLOYMENT_NAME', 'flux-pro-2')
        
        if not endpoint or not api_key:
            logging.error("Azure OpenAI credentials not configured")
            return func.HttpResponse(
                json.dumps({"error": "Azure OpenAI credentials not configured"}),
                status_code=500,
                mimetype="application/json"
            )
        
        # Import the image generation client
        try:
            from azureopenaigptimageclient import AzureOpenAIImageClient
        except ImportError as e:
            logging.error(f"Failed to import AzureOpenAIImageClient: {str(e)}")
            return func.HttpResponse(
                json.dumps({"error": f"Image client library not available: {str(e)}"}),
                status_code=500,
                mimetype="application/json"
            )
        
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
        
        return func.HttpResponse(
            json.dumps(response),
            status_code=200,
            mimetype="application/json"
        )
        
    except ValueError as e:
        logging.error(f"Invalid request body: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": f"Invalid request body: {str(e)}"}),
            status_code=400,
            mimetype="application/json"
        )
    except Exception as e:
        logging.error(f"Error generating image: {str(e)}", exc_info=True)
        return func.HttpResponse(
            json.dumps({"error": f"Internal server error: {str(e)}"}),
            status_code=500,
            mimetype="application/json"
        )


@app.function_name(name="MCPHealthCheck")
@app.route(route="mcp/health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def mcp_health_check(req: func.HttpRequest) -> func.HttpResponse:
    """
    Health check endpoint for MCP service
    """
    logging.info('MCP Health Check endpoint called.')
    
    return func.HttpResponse(
        json.dumps({
            "status": "healthy",
            "service": "MCP Image Generator",
            "version": "1.0.0"
        }),
        status_code=200,
        mimetype="application/json"
    )
