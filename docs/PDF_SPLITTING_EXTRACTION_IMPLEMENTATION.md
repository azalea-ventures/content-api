# PDF Splitting Extraction Implementation

## Overview

This document describes the implementation of PDF splitting functionality for the extraction system to increase accuracy by processing each section with its own dedicated file.

## Key Changes Made

### 1. Updated `RefactoredExtractionContext`

Added new fields to support section-based extraction:

```python
class RefactoredExtractionContext(BaseModel):
    # ... existing fields ...
    section_gemini_files: Dict[str, genai_types_google.File] = Field(default_factory=dict)
    section_pdf_streams: Dict[str, io.BytesIO] = Field(default_factory=dict)
    use_section_splitting: bool = True  # Default to using section splitting
```

### 2. New Functions Added

#### `_split_and_upload_sections()`
- Downloads the original PDF from storage
- Splits it into separate PDF streams based on section page ranges
- Uploads each section as a separate file to Gemini AI with unique names
- Stores section files and streams in the extraction context

#### `_cleanup_section_files()`
- Deletes section files from Gemini AI storage
- Closes PDF streams to free memory
- Clears the context dictionaries

### 3. Updated API Call Functions

#### `_execute_section_extraction_api_call()`
- Now uses section-specific Gemini AI files instead of page range context
- Falls back to original single file approach if section splitting is disabled
- Removed page range from prompt instructions since each file contains only the relevant section

#### `_execute_section_extraction_api_call_concurrent()`
- Updated concurrent version with same section-specific file approach
- Maintains all existing retry and error handling logic

### 4. Updated Processing Functions

All three main processing functions now use section splitting:

- `process_extract_request()` - Sequential processing
- `process_extract_request_concurrent()` - Concurrent processing  
- `process_refactored_extract_request()` - Refactored processing

Key changes:
- Added `pdf_splitter_service` parameter
- Call `_split_and_upload_sections()` instead of `_get_or_upload_genai_file()`
- Added cleanup calls in both success and error paths
- Set `genai_file_name=None` in responses (no single file when using section splitting)

### 5. Updated Main Endpoint

#### `/extract` endpoint
- Added PDF splitter service dependency check
- Pass `pdf_splitter_service` to extraction functions
- Updated error message to include PDF Splitter service

## How It Works

### 1. PDF Splitting Process
```
Original PDF → Download → Split by sections → Upload each section to Gemini AI
```

### 2. Section Processing
```
For each section:
  - Get section-specific Gemini AI file
  - Process with focused prompt (no page range needed)
  - Store results in section prompts
```

### 3. Cleanup Process
```
After processing:
  - Delete all section files from Gemini AI
  - Close all PDF streams
  - Clear context dictionaries
```

## Benefits

### Accuracy Improvements
- **Focused Content**: Each section is processed with only its relevant content
- **No Confusion**: Eliminates interference from other sections
- **Better Context**: Gemini AI receives clean, section-specific files

### Performance Benefits
- **Smaller Files**: Each section is smaller than the full document
- **Faster Processing**: Smaller files process faster and are less likely to hit API limits
- **Better Memory Management**: Streams are properly closed after use

### Reliability Improvements
- **Error Isolation**: Issues with one section don't affect others
- **Resource Management**: Automatic cleanup prevents memory leaks
- **Graceful Degradation**: Failed sections don't prevent successful ones from completing

## API Usage

### Request Format (Unchanged)
```json
{
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-3"
    },
    {
      "section_name": "Main Content", 
      "page_range": "4-6"
    }
  ],
  "prompt": {
    "prompt_name": "extract_key_points",
    "prompt_text": "Extract the key points from this section."
  }
}
```

### Response Format (Updated)
```json
{
  "success": true,
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-3",
      "prompts": [
        {
          "prompt_name": "extract_key_points",
          "prompt_text": "Extract the key points from this section.",
          "result": "Key points extracted from Introduction section..."
        }
      ]
    }
  ],
  "prompt": {
    "prompt_name": "extract_key_points",
    "prompt_text": "Extract the key points from this section.",
    "result": null
  },
  "genai_file_name": null  // No single file when using section splitting
}
```

## Error Handling

### Section-Level Errors
- Individual section failures don't affect other sections
- Failed sections get error messages in their prompt results
- Successful sections complete normally

### Resource Cleanup
- Section files are always cleaned up, even on errors
- PDF streams are properly closed to prevent memory leaks
- Context dictionaries are cleared after processing

### Fallback Support
- If section splitting fails, the system can fall back to the original approach
- Controlled by `use_section_splitting` flag in context

## Configuration

### Dependencies
- `PdfSplitterService` - Required for PDF splitting
- `StorageService` - Required for file download
- `GenerativeAnalysisService` - Required for Gemini AI operations

### Settings
- All existing retry and rate limiting settings apply
- No new configuration required

## Migration Notes

### Breaking Changes
- `genai_file_name` is now `null` in responses (no single file)
- PDF splitter service is now required for extraction endpoints

### Backward Compatibility
- Request format remains unchanged
- Response format is compatible (just `genai_file_name` is `null`)
- All existing error handling and retry logic preserved

## Testing

### Syntax Validation
- All files compile without errors
- Import statements are correct
- Function signatures are properly updated

### Integration Points
- Main endpoint properly passes PDF splitter service
- All extraction functions receive required parameters
- Cleanup functions are called in all code paths

## Future Enhancements

### Potential Improvements
- **Parallel Upload**: Upload sections concurrently for faster processing
- **Caching**: Cache section files for repeated extractions
- **Compression**: Compress section files before upload
- **Validation**: Add validation for section page ranges

### Monitoring
- Add metrics for section processing times
- Track section file upload/download performance
- Monitor cleanup success rates 