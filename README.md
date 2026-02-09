# MCP Image AI Foundry

MCP (Model Context Protocol) remote server on Azure Functions for generating images with Azure AI Foundry using Flux Pro 2.

## Overview

This Azure Function application provides an MCP tool trigger endpoint that generates images using the Flux Pro 2 model via Azure AI Foundry. It uses the Python V2 programming model for Azure Functions with MCP binding and Pydantic for strong typing of tool properties.

## Features

- **Azure Functions Python V2**: Modern programming model with async support
- **MCP Tool Trigger**: Native MCP binding for Model Context Protocol integration
- **Pydantic Strong Typing**: Type-safe tool properties using Pydantic models
- **Flux Pro 2 Integration**: Uses Azure OpenAI Image Client for Flux Pro 2 model
- **Async Image Generation**: Non-blocking asynchronous image generation
- **Error Handling**: Comprehensive error handling and logging
- **Health Check**: Dedicated health check MCP tool

## Prerequisites

- Python 3.8 or higher
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
   - Copy `local.settings.json` and update the following values:
     - `AZURE_OPENAI_ENDPOINT`: Your Azure OpenAI endpoint URL
     - `AZURE_OPENAI_API_KEY`: Your Azure OpenAI API key
     - `AZURE_OPENAI_DEPLOYMENT_NAME`: Your Flux Pro 2 deployment name (default: "flux-pro-2")

## Local Development

Run the function locally:
```bash
func start
```

The MCP server will expose the following tools:
- `generate_image` - Generate images using Flux Pro 2
- `health_check` - Check service health status

## Testing

A test script is provided to verify the function works correctly:

```bash
# Make sure the function is running locally
func start

# In another terminal, run the test script
python test_function.py
```

The test script will:
1. Check the health endpoint
2. Send a test image generation request
3. Display the results

## MCP Tool Usage

### Generate Image Tool

**Tool Name:** `generate_image`

**Description:** Generate images using Flux Pro 2 model via Azure AI Foundry. Provide a text prompt describing the image you want to create.

**Tool Properties:**
- `prompt` (required, string): Text description of the image to generate
- `size` (optional, string): Image size, default is "1024x1024"
- `quality` (optional, string): Image quality, default is "standard"
- `n` (optional, number): Number of images to generate, default is 1

**Example MCP Tool Call:**
```json
{
  "name": "generate_image",
  "arguments": {
    "prompt": "A beautiful sunset over mountains",
    "size": "1024x1024",
    "quality": "standard",
    "n": 1
  }
}
```

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
- `AZURE_OPENAI_DEPLOYMENT_NAME`

## Project Structure

```
MCP_Image_AIFoundry/
├── function_app.py          # Main function app with MCP tool triggers
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

- `azure-functions>=1.18.0`: Azure Functions Python worker
- `azureopenaigptimageclient`: Azure OpenAI Image Client for Flux Pro 2
- `azurefunctionsmcpydantic`: Pydantic to MCP tool properties converter
- `pydantic>=2.0.0`: Data validation and settings management
- `requests>=2.31.0`: HTTP library for testing

## Technical Details

### MCP Tool Binding

This project uses the native MCP tool trigger binding for Azure Functions, which provides:
- Automatic tool registration in the MCP protocol
- Strong typing through Pydantic models
- Seamless integration with MCP clients and agents

### Pydantic Models

Tool properties are defined using Pydantic models and automatically converted to MCP tool properties format using the `azurefunctionsmcpydantic` package. This ensures type safety and better developer experience.

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
