# MCP Image AI Foundry

MCP (Model Context Protocol) remote server on Azure Functions for generating images with Azure AI Foundry using Flux Pro 2.

## Overview

This Azure Function application provides an HTTP-triggered endpoint that generates images using the Flux Pro 2 model via Azure AI Foundry. It uses the Python V2 programming model for Azure Functions and performs asynchronous image generation.

## Features

- **Azure Functions Python V2**: Modern programming model with async support
- **MCP HTTP Trigger**: RESTful API endpoint for image generation requests
- **Flux Pro 2 Integration**: Uses Azure OpenAI Image Client for Flux Pro 2 model
- **Async Image Generation**: Non-blocking asynchronous image generation
- **Error Handling**: Comprehensive error handling and logging
- **Health Check**: Dedicated health check endpoint

## Prerequisites

- Python 3.8 or higher
- Azure Functions Core Tools
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
   - Copy `local.settings.json` and update the following values:
     - `AZURE_OPENAI_ENDPOINT`: Your Azure OpenAI endpoint URL
     - `AZURE_OPENAI_API_KEY`: Your Azure OpenAI API key
     - `AZURE_OPENAI_DEPLOYMENT_NAME`: Your Flux Pro 2 deployment name (default: "flux-pro-2")

## Local Development

Run the function locally:
```bash
func start
```

The function will be available at:
- Image Generation: `http://localhost:7071/api/mcp/image/generate`
- Health Check: `http://localhost:7071/api/mcp/health`

## API Usage

### Generate Image

**Endpoint:** `POST /api/mcp/image/generate`

**Request Body:**
```json
{
  "prompt": "A beautiful sunset over mountains",
  "size": "1024x1024",
  "quality": "standard",
  "n": 1
}
```

**Parameters:**
- `prompt` (required): Text description of the image to generate
- `size` (optional): Image size, default is "1024x1024"
- `quality` (optional): Image quality, default is "standard"
- `n` (optional): Number of images to generate, default is 1

**Response:**
```json
{
  "status": "success",
  "images": [
    {
      "url": "https://...",
      "revised_prompt": "..."
    }
  ],
  "created": 1234567890
}
```

### Health Check

**Endpoint:** `GET /api/mcp/health`

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
- `AZURE_OPENAI_DEPLOYMENT_NAME`

## Project Structure

```
MCP_Image_AIFoundry/
├── function_app.py          # Main function app with MCP trigger
├── host.json                # Function app host configuration
├── local.settings.json      # Local development settings
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Dependencies

- `azure-functions`: Azure Functions Python worker
- `azureopenaigptimageclient`: Azure OpenAI Image Client for Flux Pro 2

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
