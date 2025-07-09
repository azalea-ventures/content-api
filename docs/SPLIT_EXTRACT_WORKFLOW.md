# Split/Extract Workflow Documentation

## Overview

The new split/extract workflow separates the concerns of file splitting and content extraction to eliminate timeout issues and improve performance. This approach allows you to split files once and extract content multiple times using the pre-loaded files.

## Problem Solved

Previously, the `/extract` endpoint would:
1. Split the PDF into sections
2. Upload each section to Gemini AI
3. Process each section with prompts
4. Clean up files

This combined approach often led to timeout issues, especially with large files or many sections.

## New Workflow

### Step 1: Split and Upload (`/split` endpoint)

The `/split` endpoint now:
- Splits the PDF into sections
- **NEW**: Uploads each section to Gemini AI only
- **NEW**: Returns `genai_file_name` for each section

**Request:**
```json
{
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "storage_parent_folder_id": "your_folder_id",  // Optional - only needed for original file access
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-5"
    },
    {
      "section_name": "Main Content", 
      "page_range": "6-15"
    }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "result": {
    "storage_file_id": "your_file_id",
    "file_name": "document.pdf",
    "storage_parent_folder_id": "your_folder_id",
    "sections": [
      {
        "section_name": "Introduction",
        "page_range": "1-5",
        "genai_file_name": "files/abc123def456"
      },
      {
        "section_name": "Main Content",
        "page_range": "6-15",
        "genai_file_name": "files/ghi789jkl012"
      }
    ]
  }
}
```

### Step 2: Extract with Pre-loaded Files (`/extract` endpoint)

The `/extract` endpoint now:
- Uses pre-loaded files from the split operation
- **NO PDF splitting** - files are already loaded in Gemini AI
- Processes each section with prompts
- **NO file cleanup** - files remain available for future extractions

**Request:**
```json
{
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "storage_parent_folder_id": "your_folder_id",
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-5",
      "genai_file_name": "files/abc123def456"
    },
    {
      "section_name": "Main Content",
      "page_range": "6-15", 
      "genai_file_name": "files/ghi789jkl012"
    }
  ],
  "prompt": {
    "prompt_name": "extract_key_points",
    "prompt_text": "Extract the key points and main ideas from this section."
  }
}
```

## Benefits

1. **Eliminates Timeout Issues**: No more combining split + extract operations
2. **Separates Concerns**: Split once, extract multiple times
3. **Reuses Files**: Pre-loaded files can be used for multiple extraction prompts
4. **Better Performance**: Faster processing without file upload overhead
5. **More Efficient**: Reduces resource usage and API calls

## Usage Pattern

```python
# Step 1: Split and upload
split_response = await client.post("/split", json=split_request)
split_data = split_response.json()

# Step 2: Extract using pre-loaded files
extract_request = {
    "storage_file_id": split_request["storage_file_id"],
    "file_name": split_request["file_name"],
    "storage_parent_folder_id": split_request["storage_parent_folder_id"],
    "sections": [
        {
            "section_name": section["section_name"],
            "page_range": section["page_range"],  # Now available from split response
            "genai_file_name": section["genai_file_name"]
        }
        for section in split_data["result"]["sections"]
    ],
    "prompt": {
        "prompt_name": "your_prompt_name",
        "prompt_text": "your_prompt_text"
    }  # Prompt details gathered separately through n8n workflow
}

extract_response = await client.post("/extract", json=extract_request)
```

## Migration Notes

- The `/extract` endpoint no longer requires the PDF splitter service
- Sections must include `genai_file_name` from a previous split operation
- If `genai_file_name` is missing, the endpoint will return an error asking to run `/split` first
- The old extract functionality is still available but not recommended for new implementations

## Error Handling

- If no sections have `genai_file_name`, the extract endpoint returns an error
- If a `genai_file_name` cannot be retrieved from Gemini AI, that section will have an error result
- Individual section failures don't fail the entire request

## File Management

- Files uploaded to Gemini AI during split operations remain available for future extractions
- Files are not automatically cleaned up after extraction
- Manual cleanup may be needed for long-running applications 