# Split Batching Implementation

## Overview

The split process has been enhanced with batching functionality to address memory constraints. The system now processes sections in configurable batches (default: 5 sections per batch) with garbage collection between batches to prevent memory exhaustion.

## Problem Solved

**Memory Issues**: When processing large PDFs with many sections, the original implementation would:
- Create multiple PDF streams simultaneously
- Keep all streams in memory until all sections were processed
- Cause memory peaks that could lead to worker timeouts and crashes

**Solution**: Implement batched processing with:
- Maximum of 5 streams open at any time
- Immediate cleanup after each batch
- Garbage collection between batches
- Configurable batch sizes and delays

## Implementation Details

### 1. Configuration Options

New configuration settings in `config.py`:

```python
# Split batching configuration
split_batch_size: int = 5  # Number of sections to process in each batch
split_batch_delay_seconds: float = 0.5  # Delay between batches for memory cleanup
```

### 2. PDF Splitter Service Enhancements

#### New Method: `split_pdf_by_sections_batched()`
- Processes sections in configurable batches
- Performs garbage collection after each batch
- Maintains the same interface as the original method
- Returns the same data structure

#### New Helper Method: `_process_section_batch()`
- Handles the processing of individual batches
- Manages PDF document creation and cleanup
- Validates page ranges and handles errors

### 3. Split Helpers Enhancements

#### New Function: `process_single_split_request_batched()`
- Uses the new batched PDF splitting method
- Processes uploads in batches as well
- Implements memory management with garbage collection
- Maintains the same response format

#### New Helper Function: `_process_upload_batch()`
- Handles uploading a batch of sections to Gemini AI
- Manages stream cleanup after each upload
- Returns batch results for aggregation

### 4. Main Endpoint Update

The `/split` endpoint now uses the batched processing by default:

```python
@app.post("/split", response_model=BatchSplitItemResult, status_code=status.HTTP_200_OK)
async def split_documents_endpoint(request: SplitRequest):
    # ... validation ...
    
    # Use batched processing for memory efficiency
    from helpers.split_helpers import process_single_split_request_batched
    result = await process_single_split_request_batched(request, storage_service, pdf_splitter_service, gemini_analysis_service)
    
    return result
```

## How It Works

### 1. PDF Splitting Process
```
Original PDF → Download → Split in batches → Process each batch → Cleanup → Next batch
```

### 2. Batch Processing Flow
```
For each batch of 5 sections:
  - Split PDF into section streams
  - Upload each section to Gemini AI
  - Close section streams
  - Force garbage collection
  - Delay before next batch (if configured)
```

### 3. Memory Management
```
Memory Usage Pattern:
- Batch 1: Original PDF + 5 section PDFs + 5 Gemini files
- Cleanup: Close all streams, garbage collection
- Batch 2: Original PDF + 5 section PDFs + 5 Gemini files
- Cleanup: Close all streams, garbage collection
- ... continues for all batches
```

## Benefits

### Memory Efficiency
- **Controlled memory usage**: Maximum of 5 streams open at any time
- **Immediate cleanup**: Resources released after each batch
- **Garbage collection**: Forced cleanup prevents memory leaks
- **Predictable peaks**: Memory usage is bounded and predictable

### Reliability
- **No worker crashes**: Eliminates SIGKILL due to memory exhaustion
- **Graceful degradation**: Failed batches don't prevent successful ones
- **Error isolation**: Issues in one batch don't affect others
- **Better resource management**: Automatic cleanup prevents resource exhaustion

### Performance
- **Configurable batching**: Adjust batch size based on available memory
- **Controlled delays**: Prevent system overload between batches
- **Efficient processing**: Maintains performance while managing memory
- **Scalable**: Works with any number of sections

## Configuration

### Environment Variables
```bash
# Optional: Override default batch size
SPLIT_BATCH_SIZE=3

# Optional: Override default batch delay
SPLIT_BATCH_DELAY_SECONDS=1.0
```

### Default Values
- **Batch Size**: 5 sections per batch
- **Batch Delay**: 0.5 seconds between batches
- **Garbage Collection**: Enabled by default

## Usage

The batching is transparent to API consumers. The same request and response format is maintained:

### Request (Unchanged)
```json
{
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "storage_parent_folder_id": "your_folder_id",
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

### Response (Unchanged)
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

## Monitoring

The system provides detailed logging for monitoring batch processing:

```
PdfSplitter: Opened original PDF with 50 pages for batched processing.
PdfSplitter: Processing batch 1-5 of 20 sections.
PdfSplitter: Completed batch 1-5, garbage collection performed.
Processing upload batch 1-5 of 20 sections.
Completed upload batch 1-5, garbage collection performed.
PdfSplitter: Processing batch 6-10 of 20 sections.
...
```

## Troubleshooting

### Memory Issues
- **Reduce batch size**: Set `SPLIT_BATCH_SIZE=3` or lower
- **Increase delays**: Set `SPLIT_BATCH_DELAY_SECONDS=1.0` or higher
- **Monitor logs**: Check for garbage collection messages

### Performance Issues
- **Increase batch size**: Set `SPLIT_BATCH_SIZE=10` for faster processing
- **Reduce delays**: Set `SPLIT_BATCH_DELAY_SECONDS=0.1` for minimal delays
- **Monitor system resources**: Ensure adequate memory is available

### Error Handling
- **Batch failures**: Individual batch failures don't affect other batches
- **Partial success**: Successfully processed sections are returned even if some fail
- **Error reporting**: Detailed error messages for debugging 