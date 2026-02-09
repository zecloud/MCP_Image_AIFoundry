"""
Test script for MCP Image Generator Function

This script demonstrates how to call the Azure Function MCP tool endpoints
to generate images using Flux Pro 2.

Note: MCP tools are typically invoked through the MCP protocol by MCP clients.
This test script simulates direct calls for testing purposes.

Usage:
    python test_function.py
"""

import requests
import json
import os

# Configuration for local testing
# The MCP trigger uses the standard Azure Functions runtime endpoints
FUNCTION_BASE_URL = "http://localhost:7071/runtime/webhooks/mcp"
# For deployed function, use:
# FUNCTION_BASE_URL = "https://your-function-app.azurewebsites.net/runtime/webhooks/mcp"

def test_image_generation():
    """Test the image generation MCP tool"""
    
    # Test request following MCP tool invocation format
    request_data = {
        "name": "generate_image",
        "arguments": {
            "prompt": "A majestic mountain landscape at sunset with vibrant colors",
            "size": "1024x1024",
            "quality": "standard",
            "n": 1
        }
    }
    
    print("Testing MCP Image Generator Tool...")
    print(f"Request: {json.dumps(request_data, indent=2)}")
    
    try:
        # Make the request
        headers = {
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            FUNCTION_BASE_URL,
            json=request_data,
            headers=headers,
            timeout=60  # Image generation may take time
        )
        
        # Check response
        print(f"\nStatus Code: {response.status_code}")
        
        try:
            response_json = response.json()
            print(f"Response: {json.dumps(response_json, indent=2)}")
            
            if response.status_code == 200:
                print("\n✓ Test passed! Image generated successfully.")
                
                # Parse the result if it's a JSON string
                if isinstance(response_json, str):
                    result = json.loads(response_json)
                else:
                    result = response_json
                    
                if result.get("images"):
                    for idx, image in enumerate(result["images"]):
                        print(f"\nImage {idx + 1}:")
                        print(f"  URL: {image.get('url', 'N/A')}")
                        print(f"  Revised Prompt: {image.get('revised_prompt', 'N/A')}")
            else:
                print("\n✗ Test failed!")
        except json.JSONDecodeError:
            print(f"Response Text: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
        print("\nNote: Make sure the Azure Function is running locally with 'func start'")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


def test_health_check():
    """Test the health check MCP tool"""
    
    request_data = {
        "name": "health_check",
        "arguments": {}
    }
    
    print("\nTesting Health Check Tool...")
    print(f"Request: {json.dumps(request_data, indent=2)}")
    
    try:
        headers = {
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            FUNCTION_BASE_URL,
            json=request_data,
            headers=headers,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        
        try:
            response_json = response.json()
            print(f"Response: {json.dumps(response_json, indent=2)}")
            
            if response.status_code == 200:
                print("\n✓ Health check passed!")
            else:
                print("\n✗ Health check failed!")
        except json.JSONDecodeError:
            print(f"Response Text: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
        print("\nNote: Make sure the Azure Function is running locally with 'func start'")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


if __name__ == "__main__":
    print("=" * 60)
    print("MCP Image Generator Function Test")
    print("=" * 60)
    print("\nNote: This test script simulates MCP tool invocations.")
    print("In production, MCP tools are invoked by MCP clients through")
    print("the Model Context Protocol.")
    print("=" * 60)
    
    # Test health check first
    test_health_check()
    
    # Test image generation
    print("\n" + "=" * 60)
    test_image_generation()
    print("=" * 60)

