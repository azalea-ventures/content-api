# Storage Clear Endpoint Documentation

## Overview

The `/storage/clear` endpoint allows you to clear all files from Google AI storage. This is useful for cleaning up temporary files and managing storage space.

## Endpoint Details

- **URL**: `DELETE /storage/clear`
- **Method**: DELETE
- **Authentication**: None required (uses service account credentials)
- **Response**: JSON

## Usage

### Request
```bash
curl -X DELETE http://localhost:8000/storage/clear
```

### Response Format

#### Success Response (200 OK)
```json
{
  "success": true,
  "message": "Cleared 5 out of 5 files from Google AI storage",
  "total_files": 5,
  "deleted_files": 5,
  "failed_deletions": 0,
  "deleted_file_names": [
    "document1.pdf",
    "document2.pdf",
    "section_1.pdf",
    "section_2.pdf",
    "analysis_result.pdf"
  ]
}
```

#### No Files Found (200 OK)
```json
{
  "success": true,
  "message": "No files found in Google AI storage",
  "total_files": 0,
  "deleted_files": 0,
  "failed_deletions": 0,
  "deleted_file_names": []
}
```

#### Error Response (500 Internal Server Error)
```json
{
  "success": false,
  "message": "Error during file clearing operation: [error details]",
  "total_files": 0,
  "deleted_files": 0,
  "failed_deletions": 0,
  "deleted_file_names": []
}
```

#### Service Unavailable (503 Service Unavailable)
```json
{
  "status": "Generative Analysis service not initialized."
}
```

## Response Fields

- `success`: Boolean indicating if the operation completed successfully
- `message`: Human-readable description of the operation result
- `total_files`: Total number of files found in storage before clearing
- `deleted_files`: Number of files successfully deleted
- `failed_deletions`: Number of files that failed to delete
- `deleted_file_names`: Array of display names of successfully deleted files

## Implementation Details

The endpoint uses the `GenerativeAnalysisService.clear_all_files()` method which:

1. Lists all files in Google AI storage using `genai.list_files()`
2. Iterates through each file and attempts to delete it using `genai.delete_file()`
3. Tracks successful and failed deletions
4. Returns detailed statistics about the operation

## Error Handling

- **Service Not Initialized**: Returns 503 if the Generative Analysis service is not available
- **Individual File Deletion Failures**: Continues processing other files even if some deletions fail
- **General Errors**: Returns 500 with error details if the entire operation fails

## Security Considerations

- This endpoint deletes ALL files in Google AI storage
- Use with caution in production environments
- Consider adding authentication/authorization if needed
- The operation is irreversible - deleted files cannot be recovered

## Related Endpoints

- `GET /debug/files` - List all files in Google AI storage
- `DELETE /storage/clear` - Clear all files from Google AI storage 