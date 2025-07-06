#!/usr/bin/env python3
"""
Example script demonstrating how to use the new refactored extract endpoint.

This script shows how to:
1. Call the /analyze endpoint to get sections
2. Call the /extract/refactored endpoint with the analyze result directly
3. The endpoint automatically adds default extraction prompts to each section
"""

import asyncio
import aiohttp
import json
from typing import List, Dict, Any

# Example configuration
API_BASE_URL = "http://localhost:8000"
FILE_ID = "your_google_drive_file_id_here"

async def analyze_document(session: aiohttp.ClientSession, file_id: str) -> Dict[str, Any]:
    """Call the /analyze endpoint to get document sections"""
    
    analyze_request = {
        "file_id": file_id,
        "prompt_text": "Identify the main sections of this document and provide their page ranges."
    }
    
    async with session.post(f"{API_BASE_URL}/analyze", json=analyze_request) as response:
        if response.status == 200:
            result = await response.json()
            if result.get("success") and result.get("result"):
                return result["result"]
            else:
                raise Exception(f"Analyze failed: {result.get('error_info', {}).get('error', 'Unknown error')}")
        else:
            raise Exception(f"Analyze request failed with status {response.status}")

async def extract_from_sections(session: aiohttp.ClientSession, analyze_result: Dict[str, Any]) -> Dict[str, Any]:
    """Call the /extract/refactored endpoint with the analyze result directly"""
    
    # The endpoint now accepts the analyze result directly
    # It will automatically add default extraction prompts to each section
    async with session.post(f"{API_BASE_URL}/extract/refactored", json=analyze_result) as response:
        if response.status == 200:
            result = await response.json()
            if result.get("success"):
                return result
            else:
                raise Exception(f"Extract failed: {result.get('error', 'Unknown error')}")
        else:
            raise Exception(f"Extract request failed with status {response.status}")

def print_results(extract_result: Dict[str, Any]):
    """Print the extraction results in a readable format"""
    
    result_data = extract_result["result"]
    print(f"\n=== Extraction Results for {result_data['originalDriveFileName']} ===\n")
    
    for section in result_data["sections"]:
        print(f"📄 Section: {section['sectionName']} (Pages: {section['pageRange']})")
        print("-" * 60)
        
        for prompt in section["prompts"]:
            print(f"\n🔍 {prompt['prompt_name']}:")
            print(f"   Prompt: {prompt['prompt_text'][:100]}...")
            print(f"   Result: {prompt['result'][:200]}..." if prompt['result'] else "   Result: None")
        
        print("\n" + "=" * 80 + "\n")

async def main():
    """Main function demonstrating the complete workflow"""
    
    print("🚀 Starting refactored extract example...")
    print(f"📁 Processing file: {FILE_ID}")
    
    async with aiohttp.ClientSession() as session:
        try:
            # Step 1: Analyze the document to get sections
            print("\n1️⃣ Analyzing document to identify sections...")
            analyze_result = await analyze_document(session, FILE_ID)
            print(f"✅ Found {len(analyze_result['sections'])} sections")
            
            # Step 2: Extract data from each section (endpoint adds prompts automatically)
            print("\n2️⃣ Extracting data from sections...")
            print("   (The endpoint automatically adds default extraction prompts to each section)")
            extract_result = await extract_from_sections(session, analyze_result)
            print("✅ Extraction completed successfully")
            
            # Step 3: Print results
            print_results(extract_result)
            
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    # Update FILE_ID with your actual Google Drive file ID
    print("⚠️  Please update FILE_ID in this script with your actual Google Drive file ID")
    print("⚠️  Make sure the API server is running on http://localhost:8000")
    
    # Uncomment the line below to run the example
    # asyncio.run(main()) 