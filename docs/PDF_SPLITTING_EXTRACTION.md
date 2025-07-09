# PDF Splitting Extraction Approach

## Overview

The extraction system has been refactored to use a more efficient PDF splitting approach. Instead of uploading the entire PDF to Gemini AI and processing all sections together, the system now:

1. **Splits the PDF into page ranges** based on the provided sections
2. **Uploads each section as a separate file** to Gemini AI
3. **Processes each section independently** with the extraction prompts
4. **Cleans up section files** after processing

## Benefits

### Performance
- **Efficient for large files**: Processes smaller chunks instead of entire documents
- **Better memory management**: Streams are disposed after use
- **Parallel processing potential**: Each section can be processed independently

### Reliability
- **Error isolation**: One section failure doesn't affect others
- **Better resource management**: Automatic cleanup of temporary files
- **Improved retry logic**: Section-specific retries and error handling

### Scalability
- **Handles larger documents**: No single file size limitations
- **Reduced API timeouts**: Smaller files process faster
- **Better rate limiting**: More granular control over API calls

## How It Works

### 1. PDF Splitting
```python
# The system splits the PDF based on section page ranges
sections_for_splitting = [
    {"section_name": "Introduction", "page_range": "1-3"},
    {"section_name": "Main Content", "page_range": "4-6"},
    {"section_name": "Conclusion", "page_range": "7-8"}
]

split_results = pdf_splitter_service.split_pdf_by_sections(
    original_pdf_stream, 
    sections_for_splitting
)
```

### 2. Section Upload
```python
# Each section is uploaded as a separate file to Gemini AI
for split_result in split_results:
    section_name = split_result["section_name"]
    pdf_stream = split_result["fileContent"]
    display_name = f"{filename}_{section_name}_{unique_id}"
    
    gemini_file = await gemini_service.upload_pdf_for_analysis(
        pdf_stream, 
        display_name
    )
```

### 3. Independent Processing
```python
# Each section is processed independently with the extraction prompt
for section in sections:
    section_gemini_file = extraction_ctx.section_gemini_files[section.section_name]
    
    multimodal_prompt_parts = [
        section_gemini_file,
        f"Focus on section '{section.section_name}': {prompt_text}"
    ]
    
    response = await gemini_service.model.generate_content_async(
        multimodal_prompt_parts
    )
```

### 4. Cleanup
```python
# Section files are cleaned up after processing
for section_name, gemini_file in extraction_ctx.section_gemini_files.items():
    await gemini_service.delete_file(gemini_file)
    
for section_name, pdf_stream in extraction_ctx.section_pdf_streams.items():
    pdf_stream.close()
```

## API Usage

### Extract Request
```json
{
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-3",
      # pages field removed - no longer needed
    },
    {
      "section_name": "Main Content",
      "page_range": "4-6",
      # pages field removed - no longer needed
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
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-3",
      # pages field removed - no longer needed
      "prompts": [
        {
          "prompt_name": "extract_key_points",
          "prompt_text": "Extract the key points from this section.",
          "result": "Key points extracted from Introduction section..."
        }
      ]
    },
    {
      "section_name": "Main Content",
      "page_range": "4-6",
      # pages field removed - no longer needed
      "prompts": [
        {
          "prompt_name": "extract_key_points",
          "prompt_text": "Extract the key points from this section.",
          "result": "Key points extracted from Main Content section..."
        }
      ]
    }
  ],
  "prompt": {
    "prompt_name": "extract_key_points",
    "prompt_text": "Extract the key points from this section.",
    "result": null
  },
  "genai_file_name": null
}
```

## Implementation Details

### Key Components

1. **RefactoredExtractionContext**: Manages section files and streams
2. **_split_and_upload_sections()**: Handles PDF splitting and upload
3. **_execute_section_extraction_api_call()**: Processes individual sections
4. **_cleanup_section_files()**: Manages resource cleanup

### Error Handling

- **Section-level retries**: Each section can be retried independently
- **Graceful degradation**: Failed sections don't affect successful ones
- **Resource cleanup**: Ensures temporary files are always cleaned up
- **Detailed logging**: Comprehensive error tracking and debugging

### Performance Considerations

- **Stream management**: PDF streams are properly closed after use
- **Memory efficiency**: Only one section is loaded at a time
- **API optimization**: Smaller files reduce upload and processing time
- **Parallel potential**: Future enhancement for concurrent processing

## Migration Notes

### Breaking Changes
- `genai_file_name` in responses is now `null` (no single file)
- Section results are stored in the `prompts` array within each section
- File cleanup happens automatically after processing

### Backward Compatibility
- API request/response structure remains the same
- Existing client code should continue to work
- Only internal processing has changed

## Testing

Run the test script to verify the new approach:
```bash
python test_extract_endpoint.py
```

Or run the example to see the process in action:
```bash
python example_pdf_splitting_extract.py
```

## Future Enhancements

1. **Parallel Processing**: Process multiple sections concurrently
2. **Caching**: Cache section files for repeated processing
3. **Streaming**: Stream results as they become available
4. **Batch Operations**: Process multiple documents efficiently 