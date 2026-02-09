"""
Test script for MCP Image Generator Function

This script demonstrates how to call the Azure Function endpoint
to generate images using Flux Pro 2.

Usage:
    python test_function.py
"""

import requests
import json
import os

# Configuration
FUNCTION_URL = "http://localhost:7071/api/mcp/image/generate"
# For deployed function, use:
# FUNCTION_URL = "https://your-function-app.azurewebsites.net/api/mcp/image/generate"
# Add function key if using auth:
# FUNCTION_KEY = "your-function-key"

def test_image_generation():
    """Test the image generation endpoint"""
    
    # Test request
    request_data = {
        "prompt": "A majestic mountain landscape at sunset with vibrant colors",
        "size": "1024x1024",
        "quality": "standard",
        "n": 1
    }
    
    print("Testing MCP Image Generator...")
    print(f"Request: {json.dumps(request_data, indent=2)}")
    
    try:
        # Make the request
        headers = {
            "Content-Type": "application/json"
        }
        # If using function key authentication, uncomment:
        # headers["x-functions-key"] = FUNCTION_KEY
        
        response = requests.post(
            FUNCTION_URL,
            json=request_data,
            headers=headers,
            timeout=60  # Image generation may take time
        )
        
        # Check response
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 200:
            print("\n✓ Test passed! Image generated successfully.")
            result = response.json()
            if result.get("images"):
                for idx, image in enumerate(result["images"]):
                    print(f"\nImage {idx + 1}:")
                    print(f"  URL: {image.get('url', 'N/A')}")
                    print(f"  Revised Prompt: {image.get('revised_prompt', 'N/A')}")
        else:
            print("\n✗ Test failed!")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


def test_health_check():
    """Test the health check endpoint"""
    
    health_url = FUNCTION_URL.replace("/mcp/image/generate", "/mcp/health")
    
    print("\nTesting Health Check...")
    
    try:
        response = requests.get(health_url, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        
        if response.status_code == 200:
            print("\n✓ Health check passed!")
        else:
            print("\n✗ Health check failed!")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


if __name__ == "__main__":
    print("=" * 60)
    print("MCP Image Generator Function Test")
    print("=" * 60)
    
    # Test health check first
    test_health_check()
    
    # Test image generation
    print("\n" + "=" * 60)
    test_image_generation()
    print("=" * 60)
