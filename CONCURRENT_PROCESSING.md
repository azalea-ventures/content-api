# Concurrent Processing for Extract Requests

## Overview

The Content API now uses concurrent processing by default for extract requests, allowing multiple sections to be processed simultaneously instead of sequentially. This provides significant performance improvements, especially for documents with many sections. No additional configuration is required - all extract requests automatically benefit from concurrent processing when enabled globally.

## How It Works

### Sequential vs Concurrent Processing

**Sequential Processing (Original):**
```
Section 1 → Section 2 → Section 3 → Section 4 → Section 5
Total Time = Sum of all individual section processing times
```

**Concurrent Processing (New):**
```
Section 1 ┐
Section 2 ├─ All processed simultaneously
Section 3 ├─
Section 4 ├─
Section 5 ┘
Total Time ≈ Time of slowest section + overhead
```

## Configuration

### Request-Level Configuration

Concurrent processing is enabled by default! No additional configuration is needed in your requests:

```json
{
    "originalDriveFileId": "your_file_id",
    "originalDriveFileName": "document.pdf",
    "sections": [
        {"sectionName": "Introduction", "pageRange": "1-3"},
        {"sectionName": "Background", "pageRange": "4-6"},
        {"sectionName": "Methodology", "pageRange": "7-9"}
    ],
    "prompt": {
        "prompt_name": "extract_key_points",
        "prompt_text": "Extract the key points from this section."
    }
}
```

### Global Configuration

Configure concurrent processing settings in `config.py`:

```python
# Concurrent processing configuration
max_concurrent_requests: int = 10  # Maximum concurrent API calls
concurrent_retry_cooldown_seconds: int = 30  # Shorter cooldown for concurrent mode
enable_concurrent_processing: bool = True  # Enable concurrent processing by default
```

## Performance Benefits

### Expected Improvements

- **2-5x speedup** for documents with 3-10 sections
- **5-10x speedup** for documents with 10+ sections
- **Better resource utilization** of available API capacity
- **Reduced total processing time** especially for large documents

### Real-World Example

For a document with 8 sections:
- **Sequential**: ~40 seconds (5 seconds per section)
- **Concurrent**: ~8 seconds (limited by API response time)
- **Speedup**: 5x faster

## Error Handling

### Rate Limiting

The concurrent processor handles rate limiting intelligently:

1. **Detection**: Identifies rate limit errors from individual requests
2. **Retry Logic**: Automatically retries failed requests with exponential backoff
3. **Cooldown**: Uses shorter cooldown periods (30s vs 60s) for concurrent mode
4. **Partial Success**: Continues processing successful requests while retrying failed ones

### Retry Strategy

```python
# Failed requests are retried with this logic:
if failed_tasks and not rate_limit_hit:
    # Retry failed requests
    retry_tasks = []
    for failed_task in failed_tasks:
        if failed_task["api_attempt_count"] < settings.max_api_retries:
            # Create retry task
            retry_tasks.append(retry_task)
    
    # Execute retries concurrently
    if retry_tasks:
        retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
```

## Usage Examples

### Basic Concurrent Extract

```python
import asyncio
from models import ExtractRequest, SectionWithPages, SectionExtractPrompt
from helpers.refactored_extract_helpers import process_extract_request_concurrent

async def extract_concurrently():
    request = ExtractRequest(
        originalDriveFileId="your_file_id",
        sections=[
            SectionWithPages(sectionName="Section 1", pageRange="1-3"),
            SectionWithPages(sectionName="Section 2", pageRange="4-6"),
            SectionWithPages(sectionName="Section 3", pageRange="7-9"),
        ],
        prompt=SectionExtractPrompt(
            prompt_name="extract_content",
            prompt_text="Extract the main content from this section."
        )
    )
    
    # Concurrent processing is automatic when enabled globally
    result = await process_extract_request_concurrent(request, storage_service, gemini_service)
    return result
```

### Performance Comparison

```python
import time

# Sequential processing
start_time = time.time()
sequential_result = await process_extract_request(request, storage_service, gemini_service)
sequential_time = time.time() - start_time

# Concurrent processing
start_time = time.time()
concurrent_result = await process_extract_request_concurrent(request, storage_service, gemini_service)
concurrent_time = time.time() - start_time

print(f"Sequential: {sequential_time:.2f}s")
print(f"Concurrent: {concurrent_time:.2f}s")
print(f"Speedup: {sequential_time/concurrent_time:.2f}x")
```

## Best Practices

### When to Use Concurrent Processing

✅ **Use concurrent processing when:**
- Document has 3+ sections
- API rate limits allow multiple concurrent requests
- Processing time is critical
- You have sufficient API quota

❌ **Consider sequential processing when:**
- Document has only 1-2 sections
- API rate limits are very restrictive
- You want to minimize API usage
- Debugging individual section processing

### Configuration Recommendations

```python
# For high-traffic applications
max_concurrent_requests: int = 15
concurrent_retry_cooldown_seconds: int = 20

# For conservative API usage
max_concurrent_requests: int = 5
concurrent_retry_cooldown_seconds: int = 45

# For development/testing
max_concurrent_requests: int = 3
concurrent_retry_cooldown_seconds: int = 60
```

## Troubleshooting

### Common Issues

1. **Rate Limiting**: If you hit rate limits frequently, reduce `max_concurrent_requests`
2. **Memory Usage**: Large documents with many sections may use more memory
3. **API Quota**: Monitor your API usage as concurrent requests consume quota faster

### Debugging

Enable detailed logging to see concurrent processing in action:

```python
# The system logs each concurrent API call
print(f"API Call (Concurrent): Section '{section_name}', Prompt '{prompt.prompt_name}'")
print(f"Executing {len(tasks)} concurrent API calls...")
print(f"Retrying {len(failed_tasks)} failed requests...")
```

## Migration Guide

### From Sequential to Concurrent

1. **No changes needed**: Concurrent processing is now the default
2. **Test gradually**: Start with smaller documents to verify behavior
3. **Monitor performance**: Compare processing times before and after
4. **Adjust configuration**: Tune settings based on your API limits and requirements

### Backward Compatibility

- Concurrent processing is **enabled by default** - all requests automatically benefit
- Sequential processing remains available as a fallback when disabled globally
- All existing API contracts are preserved

## Testing

Run the provided test scripts to verify concurrent processing:

```bash
# Test concurrent vs sequential performance
python test_concurrent_extract.py

# Run example with your own data
python example_concurrent_extract.py
```

## Future Enhancements

Planned improvements for concurrent processing:

- **Adaptive concurrency**: Automatically adjust based on API response times
- **Batch processing**: Group similar requests for even better efficiency
- **Priority queuing**: Process high-priority sections first
- **Resource monitoring**: Track and optimize memory/CPU usage 