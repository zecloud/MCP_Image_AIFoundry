"""
Test script for MCP Image Editor Function with Multiple Reference Images

This script demonstrates how to call the edit_image Azure Function MCP tool endpoint
to edit images using Flux Pro 2 with multiple reference images.

Note: MCP tools are typically invoked through the MCP protocol by MCP clients.
This test script simulates direct calls for testing purposes.

Usage:
    python test_edit_multiple_images.py
"""

import requests
import json

# Configuration for local testing
# The MCP trigger uses the standard Azure Functions runtime endpoints
FUNCTION_BASE_URL = "http://localhost:7071/runtime/webhooks/mcp"
# For deployed function, use:
# FUNCTION_BASE_URL = "https://your-function-app.azurewebsites.net/runtime/webhooks/mcp"

def test_edit_image_single_reference():
    """Test the image editing MCP tool with a single reference image"""
    
    # Test request following MCP tool invocation format with single image in list
    request_data = {
        "name": "edit_image",
        "arguments": {
            "filenames": ["img-test-scene0-talk0.png"],
            "prompt": "Add dramatic sunset colors and enhance the lighting",
            "size": "1024x1024",
            "quality": "standard",
            "n": 1,
            "video_id": "test",
            "scene_number": 0,
            "talk_number": 0,
            "prefix": "edited"
        }
    }
    
    print("Testing MCP Image Editor Tool with Single Reference Image...")
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
            timeout=120  # Image editing may take time
        )
        
        # Check response
        print(f"\nStatus Code: {response.status_code}")
        
        try:
            response_json = response.json()
            print(f"Response: {json.dumps(response_json, indent=2)}")
            
            if response.status_code == 200:
                print("\n✓ Test passed! Image edited successfully with 1 reference image.")
                
                # Parse the result if it's a JSON string
                if isinstance(response_json, str):
                    result = json.loads(response_json)
                else:
                    result = response_json
                    
                if result.get("image"):
                    print(f"\nEdited Image URL: {result.get('image')}")
                    print(f"Reference Images Used: {result.get('reference_images_used', 'N/A')}")
            else:
                print("\n✗ Test failed!")
        except json.JSONDecodeError:
            print(f"Response Text: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
        print("\nNote: Make sure the Azure Function is running locally with 'func start'")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


def test_edit_image_multiple_references():
    """Test the image editing MCP tool with multiple reference images"""
    
    # Test request following MCP tool invocation format with multiple images
    request_data = {
        "name": "edit_image",
        "arguments": {
            "filenames": [
                "img-test-scene0-talk0.png",
                "img-test-scene1-talk0.png",
                "img-test-scene2-talk0.png"
            ],
            "prompt": "Blend these scenes together with a cohesive art style and color palette",
            "size": "1024x1024",
            "quality": "standard",
            "n": 1,
            "video_id": "test",
            "scene_number": 0,
            "talk_number": 1,
            "prefix": "blended"
        }
    }
    
    print("\nTesting MCP Image Editor Tool with Multiple Reference Images...")
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
            timeout=120  # Image editing may take time
        )
        
        # Check response
        print(f"\nStatus Code: {response.status_code}")
        
        try:
            response_json = response.json()
            print(f"Response: {json.dumps(response_json, indent=2)}")
            
            if response.status_code == 200:
                print("\n✓ Test passed! Image edited successfully with multiple reference images.")
                
                # Parse the result if it's a JSON string
                if isinstance(response_json, str):
                    result = json.loads(response_json)
                else:
                    result = response_json
                    
                if result.get("image"):
                    print(f"\nEdited Image URL: {result.get('image')}")
                    print(f"Reference Images Used: {result.get('reference_images_used', 'N/A')}")
            else:
                print("\n✗ Test failed!")
        except json.JSONDecodeError:
            print(f"Response Text: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
        print("\nNote: Make sure the Azure Function is running locally with 'func start'")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


def test_edit_image_validation():
    """Test the image editing MCP tool with invalid inputs"""
    
    # Test with empty filenames list
    request_data = {
        "name": "edit_image",
        "arguments": {
            "filenames": [],
            "prompt": "This should fail validation",
            "video_id": "test"
        }
    }
    
    print("\nTesting MCP Image Editor Tool with Invalid Input (empty filenames list)...")
    print(f"Request: {json.dumps(request_data, indent=2)}")
    
    try:
        headers = {
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            FUNCTION_BASE_URL,
            json=request_data,
            headers=headers,
            timeout=30
        )
        
        print(f"\nStatus Code: {response.status_code}")
        
        try:
            response_json = response.json()
            print(f"Response: {json.dumps(response_json, indent=2)}")
            
            # We expect this to fail with an error
            if isinstance(response_json, str):
                result = json.loads(response_json)
            else:
                result = response_json
                
            if result.get("error"):
                print("\n✓ Validation test passed! Empty filenames list properly rejected.")
            else:
                print("\n✗ Validation test failed! Should have rejected empty filenames list.")
        except json.JSONDecodeError:
            print(f"Response Text: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error making request: {str(e)}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {str(e)}")


if __name__ == "__main__":
    print("=" * 80)
    print("MCP Image Editor Function Test - Multiple Reference Images")
    print("=" * 80)
    print("\nNote: This test script simulates MCP tool invocations.")
    print("In production, MCP tools are invoked by MCP clients through")
    print("the Model Context Protocol.")
    print("=" * 80)
    
    # Test single reference image
    test_edit_image_single_reference()
    
    # Test multiple reference images
    print("\n" + "=" * 80)
    test_edit_image_multiple_references()
    
    # Test validation
    print("\n" + "=" * 80)
    test_edit_image_validation()
    print("=" * 80)
