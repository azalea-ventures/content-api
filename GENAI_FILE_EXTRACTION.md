# GenAI File Name Extraction Approach

## Overview

The extraction system has been updated to use a more memory-efficient approach that leverages previously uploaded files to Gemini AI using the `genai_file_name` parameter. This replaces the previous PDF splitting approach that was causing memory issues in production.

## Key Changes

### 1. Removed PDF Splitting
- **No more PDF splitting**: The system no longer splits PDFs into separate section files
- **Single file upload**: Only one file is uploaded to Gemini AI per extraction request
- **Memory efficient**: Eliminates the memory overhead of multiple PDF streams

### 2. GenAI File Name Reuse
- **File reuse**: Uses the `genai_file_name` parameter to reuse previously uploaded files
- **No duplicate uploads**: If a file is already uploaded, it's reused instead of uploading again
- **Fallback upload**: If the specified file doesn't exist, a new file is uploaded

### 3. Enhanced Prompt Context
- **Page range**: Page range is appended to the prompt for better context
- **Section number**: Section numbers are extracted and included in the prompt
- **Section-specific focus**: Each section is processed with specific page range context

## How It Works

### 1. File Handling
```python
# Check if genai_file_name is provided and try to get existing file
if genai_file_name:
    existing_file = await gemini_service.get_file_by_name(genai_file_name)
    if existing_file:
        # Reuse existing file
        extraction_ctx.genai_file = existing_file
        return True

# If no existing file found, upload new one
uploaded_file = await gemini_service.upload_pdf_for_analysis_by_file_id(
    file_id, display_name, storage_service
)
```

### 2. Enhanced Prompt Construction
```python
# Add section context, page range, and section number to the prompt
section_context = f"Focus on the section '{section_name}' when extracting information."
page_range_context = f"This extraction applies to pages: {page_range}"

# Extract section number from section name if it contains a number
section_number = ""
section_number_match = re.search(r'(\d+)', section_name)
if section_number_match:
    section_number = f"Section number: {section_number_match.group(1)}. "

final_instructions = f"{section_context}\n{page_range_context}\n{section_number}\n\n{prompt.prompt_text}"
final_instructions += f"\n\nEnsure the output is ONLY the requested information for this specific section (pages {page_range})."
```

### 3. Section Processing
```python
# Each section is processed with the same file but different prompts
for section in sections:
    multimodal_prompt_parts = [
        genai_file,  # Same file for all sections
        final_instructions  # Different instructions per section
    ]
    
    response = await gemini_service.model.generate_content_async(multimodal_prompt_parts)
```

## API Usage

### Extract Request
```json
{
  "originalDriveFileId": "your_file_id",
  "originalDriveFileName": "document.pdf",
  "originalDriveParentFolderId": "parent_folder_id",
  "genai_file_name": "files/abc123def456",  // Optional: reuse existing file
  "sections": [
    {
      "sectionName": "Introduction",
      "pageRange": "1-3"
    },
    {
      "sectionName": "Main Content", 
      "pageRange": "4-6"
    }
  ],
  "prompt": {
    "prompt_name": "extract_key_points",
    "prompt_text": "Extract the key points from this section."
  }
}
```

### Extract Response
```json
{
  "success": true,
  "originalDriveFileId": "your_file_id",
  "originalDriveFileName": "document.pdf",
  "originalDriveParentFolderId": "parent_folder_id",
  "sections": [
    {
      "sectionName": "Introduction",
      "pageRange": "1-3",
      "prompts": [
        {
          "prompt_name": "extract_key_points",
          "prompt_text": "Extract the key points from this section.",
          "result": "Key points extracted from Introduction section (pages 1-3)..."
        }
      ]
    },
    {
      "sectionName": "Main Content",
      "pageRange": "4-6", 
      "prompts": [
        {
          "prompt_name": "extract_key_points",
          "prompt_text": "Extract the key points from this section.",
          "result": "Key points extracted from Main Content section (pages 4-6)..."
        }
      ]
    }
  ],
  "prompt": {
    "prompt_name": "extract_key_points",
    "prompt_text": "Extract the key points from this section.",
    "result": null
  },
  "genai_file_name": "files/abc123def456"  // File name for reuse
}
```

## Benefits

### Performance
- **Reduced memory usage**: No PDF splitting means fewer file streams in memory
- **Faster processing**: Single file upload instead of multiple section uploads
- **Better scalability**: More efficient for large documents and high concurrency

### Reliability
- **File reuse**: Avoids duplicate uploads and reduces API calls
- **Consistent results**: Same file used across all sections ensures consistency
- **Better error handling**: Simpler file management with fewer failure points

### Production Benefits
- **Memory efficient**: Eliminates the memory issues that were occurring in production
- **Cost effective**: Fewer API calls due to file reuse
- **Simpler architecture**: Less complex file management and cleanup

## Migration Notes

### Breaking Changes
- **No PDF splitting**: The system no longer creates separate section files
- **Single file approach**: All sections use the same uploaded file
- **Enhanced prompts**: Page ranges and section numbers are now included in prompts

### Backward Compatibility
- **API compatibility**: The request/response structure remains the same
- **Optional genai_file_name**: The parameter is optional and will upload new files if not provided
- **Same functionality**: All extraction features work the same way

## Implementation Details

### Key Components

1. **RefactoredExtractionContext**: Simplified to manage single file instead of multiple section files
2. **_get_or_upload_genai_file()**: Handles file reuse and upload logic
3. **_execute_section_extraction_api_call()**: Processes sections with enhanced prompts
4. **process_extract_request()**: Main extraction logic using genai_file_name approach

### Error Handling

- **File not found**: Gracefully falls back to uploading new file
- **Upload failures**: Proper error handling and retry logic
- **API failures**: Section-level retries with rate limiting
- **Memory management**: No cleanup needed since no temporary files are created

### Performance Considerations

- **Single file upload**: Only one file is uploaded per extraction request
- **File reuse**: Subsequent requests can reuse the same file
- **Memory efficiency**: No multiple PDF streams in memory
- **API optimization**: Fewer API calls due to file reuse

## Testing

Use the `test_genai_file_extract.py` script to test the new implementation:

```bash
python test_genai_file_extract.py
```

This script tests:
- Initial file upload and extraction
- File reuse with genai_file_name
- Enhanced prompt context with page ranges and section numbers
- Response structure validation 