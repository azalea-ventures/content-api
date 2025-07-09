# Memory Management Improvements

## Overview

This document describes the memory management improvements implemented to prevent worker timeout and memory exhaustion issues in production, particularly when processing large files with multiple sections.

## Problem Statement

The original implementation was experiencing critical memory issues in production:

```
[CRITICAL] WORKER TIMEOUT (pid:1040)
[ERROR] Worker (pid:1040) was sent SIGKILL! Perhaps out of memory?
```

This occurred when processing 40 lessons with multiple sections each, causing the application to crash and restart.

## Root Cause Analysis

### Memory Issues in Original Implementation

1. **Multiple PDF Streams in Memory**: The PDF splitting approach created multiple PDF streams simultaneously
2. **No Immediate Cleanup**: Resources were only cleaned up after all sections were processed
3. **Concurrent Processing**: Multiple sections were processed concurrently, multiplying memory usage
4. **Insufficient Worker Timeout**: Default timeout was too short for large processing jobs

### Memory Usage Pattern

```
Original Approach:
- Download original PDF → Split into N sections → Upload N files → Process N sections → Cleanup
- Memory usage: Original PDF + N section PDFs + N Gemini files = High memory peak
```

## Solution: Memory-Efficient Sequential Processing

### New Approach

```
Memory-Efficient Approach:
For each section:
  - Download original PDF → Split single section → Upload single file → Process → Immediate cleanup
  - Memory usage: Original PDF + 1 section PDF + 1 Gemini file = Low, consistent memory usage
```

### Key Improvements

#### 1. Sequential Section Processing

- **One section at a time**: Process sections sequentially instead of concurrently
- **Immediate cleanup**: Clean up resources immediately after each section
- **Garbage collection**: Force garbage collection after each section
- **Controlled delays**: Small delays between sections to prevent system overload

#### 2. Resource Management

```python
async def _process_section_memory_efficient(section, ...):
    try:
        # Download original PDF
        original_pdf_stream = storage_service.download_file_content(...)
        try:
            # Split single section
            split_results = pdf_splitter_service.split_pdf_by_sections(...)
            section_pdf_stream = split_results[0]["fileContent"]
            try:
                # Upload to Gemini AI
                gemini_file = await gemini_service.upload_pdf_for_analysis(...)
                try:
                    # Process section
                    response = await gemini_service.model.generate_content_async(...)
                    return True, response.text
                finally:
                    # Clean up Gemini AI file immediately
                    await gemini_service.delete_file(gemini_file)
            finally:
                # Clean up section PDF stream immediately
                section_pdf_stream.close()
        finally:
            # Clean up original PDF stream
            original_pdf_stream.close()
    finally:
        # Force garbage collection
        gc.collect()
```

#### 3. Configuration Options

New configuration settings in `config.py`:

```python
# Memory management configuration
enable_memory_efficient_processing: bool = True  # Use memory-efficient processing by default
section_processing_delay_seconds: float = 0.1  # Delay between section processing
force_garbage_collection: bool = True  # Force garbage collection after each section
```

#### 4. Increased Worker Timeout

Updated `startup.sh`:

```bash
export WORKER_TIMEOUT=${WORKER_TIMEOUT:-1800}  # Increased from 600 to 1800 seconds
```

## Implementation Details

### New Functions

#### `_process_section_memory_efficient()`
- Processes a single section with complete resource management
- Downloads, splits, uploads, processes, and cleans up for one section only
- Returns success status and result/error message

#### `process_extract_request_memory_efficient()`
- Main processing function using sequential section processing
- Creates processed sections with results
- Returns standard `ExtractResponse` format

### Memory Usage Comparison

| Approach | Peak Memory Usage | Memory Duration | Reliability |
|----------|------------------|-----------------|-------------|
| Original Concurrent | High (N × section size) | Long (entire processing) | Low (memory crashes) |
| Memory Efficient | Low (1 × section size) | Short (per section) | High (stable) |

### Performance Impact

#### Pros
- **Stable memory usage**: Consistent, predictable memory consumption
- **No worker crashes**: Eliminates SIGKILL due to memory exhaustion
- **Better reliability**: Graceful handling of large files
- **Configurable**: Can be enabled/disabled via configuration

#### Cons
- **Slower processing**: Sequential instead of concurrent processing
- **More API calls**: Each section requires separate upload/delete cycle
- **Longer total time**: Trade-off between speed and stability

## Configuration

### Environment Variables

```bash
# Memory management
ENABLE_MEMORY_EFFICIENT_PROCESSING=true
SECTION_PROCESSING_DELAY_SECONDS=0.1
FORCE_GARBAGE_COLLECTION=true

# Worker configuration
WORKER_TIMEOUT=1800
```

### Runtime Configuration

The system automatically uses memory-efficient processing by default, but can be configured:

```python
# In config.py or environment
settings.enable_memory_efficient_processing = True  # Default: True
settings.section_processing_delay_seconds = 0.1     # Default: 0.1
settings.force_garbage_collection = True            # Default: True
```

## Migration

### Automatic Migration
- Memory-efficient processing is enabled by default
- No changes required to existing API calls
- Backward compatible with existing request/response formats

### Manual Override
To use the original processing methods:

```python
# Set in environment or config
ENABLE_MEMORY_EFFICIENT_PROCESSING=false
ENABLE_CONCURRENT_PROCESSING=true  # For concurrent processing
```

## Monitoring and Debugging

### Memory Usage Monitoring
- Monitor worker memory usage in production
- Watch for garbage collection frequency
- Track section processing times

### Logging
The new implementation includes detailed logging:

```
Processing section 1/5: Introduction
Processing section 'Introduction' with memory-efficient approach
Uploading section 'Introduction' as 'document_Introduction_abc123.pdf' to Gemini AI
Successfully processed section 'Introduction'
Deleted section file 'Introduction' from Gemini AI
Closed PDF stream for section 'Introduction'
Closed original PDF stream for section 'Introduction'
```

### Error Handling
- Each section is processed independently
- Section failures don't affect other sections
- Detailed error messages for debugging

## Testing

### Memory Usage Testing
Test with large files containing many sections:

```bash
# Test with 40 lessons, 7 sections each
curl -X POST /extract \
  -H "Content-Type: application/json" \
  -d '{"storage_file_id": "large_file_id", "sections": [...], "prompt": {...}}'
```

### Performance Testing
Compare processing times between approaches:

```python
# Memory efficient (slower but stable)
start_time = time.time()
result = await process_extract_request_memory_efficient(...)
memory_efficient_time = time.time() - start_time

# Original concurrent (faster but unstable)
start_time = time.time()
result = await process_extract_request_concurrent(...)
concurrent_time = time.time() - start_time
```

## Future Improvements

### Potential Enhancements
1. **Hybrid approach**: Use memory-efficient for large files, concurrent for small files
2. **Batch processing**: Process sections in small batches
3. **Memory monitoring**: Dynamic adjustment based on available memory
4. **Streaming**: Process sections as streams without full PDF loading

### Configuration Tuning
- Adjust `section_processing_delay_seconds` based on system performance
- Tune `force_garbage_collection` based on memory pressure
- Configure worker timeout based on expected file sizes

## Conclusion

The memory-efficient processing approach successfully addresses the production memory issues by:

1. **Eliminating memory peaks**: Processing one section at a time
2. **Immediate cleanup**: Releasing resources after each section
3. **Garbage collection**: Forcing memory cleanup
4. **Increased timeouts**: Allowing longer processing times

This solution prioritizes stability and reliability over raw performance, ensuring the application can handle large files without crashing. 