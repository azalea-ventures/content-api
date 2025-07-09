# Content API

**Version:** 1.2.0

This API provides services to analyze, extract, and enhance structured data from, split, and enhance documents using Google's Gemini AI and PyMuPDF for PDF processing.

## Table of Contents

- [Features](#features)
- [Local Development Setup](#local-development-setup)
- [API Endpoints](#api-endpoints)
  - [Health Check](#health-check)
  - [Extract Data](#extract-data)
  - [Analyze Documents](#analyze-documents)
  - [Split Documents](#split-documents)
  - [Enhance Lessons](#enhance-lessons)
- [Environment Variables](#environment-variables)

## Features

*   **Document Analysis**: Leverages multimodal generative AI to analyze content within PDF documents.
*   **Structured Data Extraction**: Extracts specific information from PDFs based on a provided prompt and JSON schema.
*   **PDF Splitting**: Splits PDF documents based on specified page ranges or criteria (handled by `PdfSplitterService`).
*   **Content Enhancement**: Augments lesson data using generative AI based on a series of prompts.
*   **Batch Processing**: Supports batch operations for most endpoints to efficiently process multiple documents or requests.
*   **Asynchronous Operations**: Utilizes `asyncio` for non-blocking I/O operations, improving performance for concurrent requests.
*   **File Upload Caching**: Intelligently caches uploaded files to avoid re-uploading the same content multiple times, reducing API calls and improving performance.

## Local Development Setup

Follow these steps to set up and run the API locally:

1.  **Clone the Repository (if you haven't already):**
    ```bash
    git clone <your-repository-url>
    cd content-api
    ```

2.  **Create and Activate a Virtual Environment:**
    It's highly recommended to use a virtual environment to manage project dependencies.
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
    (On Windows, use `venv\Scripts\activate`)

3.  **Install Dependencies:**
    The required Python packages are listed in `requirements.txt`.
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set Up Environment Variables:**
    This API requires Google Cloud credentials for accessing Google Drive and Google AI services.
    Create a `.env` file in the root of the project directory (`content-api/`) and add the following:
    ```env
    # filepath: .env
    GOOGLE_SERVICE_ACCOUNT_JSON='<path_to_your_google_service_account_key.json>'
    GEMINI_API_KEY='<your_google_gemini_api_key>'
    GEMINI_MODEL_ID='gemini-1.5-flash-latest' # Or your preferred model
    ```
    Replace `<path_to_your_google_service_account_key.json>` with the absolute or relative path to your Google Cloud service account JSON key file.
    Replace `<your_google_gemini_api_key>` with your actual Gemini API key.

5.  **Run the API:**
    The application uses Uvicorn as the ASGI server.
    ```bash
    uvicorn main:app --reload
    ```
    The `--reload` flag enables auto-reloading when code changes, which is useful for development.
    The API will typically be available at `http://127.0.0.1:8000`.

## API Endpoints

The API exposes the following endpoints:

### Health Check

*   **Endpoint:** `GET /health`
*   **Description:** Checks the operational status of the API and its dependent services (Google Credentials, Drive Service, PDF Splitter Service, Gemini Analysis Service, PDF Text Extractor Service).
*   **Response (Success - 200 OK):**
    ```json
    {
        "status": "ok"
    }
    ```
*   **Response (Error - 503 Service Unavailable):**
    ```json
    {
        "status": "Credentials not loaded. Check GOOGLE_SERVICE_ACCOUNT_JSON."
    }
    ```
    or
    ```json
    {
        "status": "Required services not initialized. Check configuration and logs."
    }
    ```

### Cache Management

*   **Endpoint:** `GET /cache/status`
*   **Description:** Get the current status of the file upload cache, including the number of cached files and their names.
*   **Response (Success - 200 OK):**
    ```json
    {
        "cache_enabled": true,
        "cache_stats": {
            "cached_files_count": 2,
            "cached_file_names": ["file1.pdf", "file2.pdf"]
        }
    }
    ```

*   **Endpoint:** `POST /cache/cleanup`
*   **Description:** Manually trigger cleanup of expired or inactive files from the cache.
*   **Response (Success - 200 OK):**
    ```json
    {
        "status": "cleanup_completed",
        "cache_stats": {
            "cached_files_count": 1,
            "cached_file_names": ["file1.pdf"]
        }
    }
    ```

### Extract Data

*   **Endpoint:** `POST /extract`
*   **Description:** Extracts structured data from a single section of a PDF file stored in Google Drive. Designed for n8n workflows, it processes multiple prompts for a single section and returns results with prompts as a sibling property.
*   **Request Body:** `ExtractRequest` object. See [`models.py`](./models.py) for the `ExtractRequest` structure.
    ```json
    {
        "storage_file_id": "google_drive_pdf_file_id_to_extract_from",
        "file_name": "document.pdf",
        "storage_parent_folder_id": "parent_folder_id",
        "section": {
            "section_name": "Introduction",
            "page_range": "1-3",
            "pages": [
                {"pageNumber": 1, "pageLabel": "1"},
                {"pageNumber": 2, "pageLabel": "2"},
                {"pageNumber": 3, "pageLabel": "3"}
            ]
        },
        "prompts": [
            {
                "prompt_name": "extract_content",
                "prompt_text": "Extract all relevant content from this section, including any key information, data, or important details."
            },
            {
                "prompt_name": "extract_key_points",
                "prompt_text": "Identify the key points and main ideas from this section."
            }
        ],
        "genai_file_name": "optional_existing_gemini_file_name"
    }
    ```
*   **Concurrent Processing Configuration:**
    - **`enable_concurrent_processing`**: Global setting in `config.py` to enable/disable concurrent processing (default: `true`)
    - **`max_concurrent_requests`**: Maximum number of concurrent API calls (default: 10)
    - **`concurrent_retry_cooldown_seconds`**: Cooldown period for concurrent retries (default: 30s)
*   **Response (Success - 200 OK):** `ExtractResponse` object with prompts as a sibling property. See [`models.py`](./models.py) for details.
    ```json
    {
        "success": true,
        "storage_file_id": "your_file_id",
        "file_name": "document.pdf",
        "storage_parent_folder_id": "parent_folder_id",
        "section": {
            "section_name": "Introduction",
            "page_range": "1-3",
            "pages": [
                {"pageNumber": 1, "pageLabel": "1"},
                {"pageNumber": 2, "pageLabel": "2"},
                {"pageNumber": 3, "pageLabel": "3"}
            ]
        },
        "prompts": [
            {
                "prompt_name": "extract_content",
                "prompt_text": "Extract all relevant content from this section...",
                "result": "Content extracted from the introduction section..."
            },
            {
                "prompt_name": "extract_key_points",
                "prompt_text": "Identify the key points and main ideas...",
                "result": "Key points identified: 1. Main concept... 2. Supporting details..."
            }
        ],
        "genai_file_name": "uploaded_file_name"
    }
    ```
*   **Dependencies:** `GoogleDriveService`, `GenerativeAnalysisService`.
*   **Key Features:**
    - **n8n Workflow Optimized**: Single section processing with sibling prompts array
    - **Multiple Prompts**: Processes multiple prompts for the same section
    - **Section Context**: Automatically adds section name and page range to prompts
    - **File Reuse**: Optionally reuse existing Gemini AI files to avoid re-uploading
    - **Rate Limiting**: Includes comprehensive retry logic and rate limiting
    - **Concurrent Processing**: Process multiple sections simultaneously for improved performance
*   **Performance Benefits:**
    - **Faster Processing**: All sections processed simultaneously instead of sequentially
    - **Better Resource Utilization**: Makes full use of available API capacity
    - **Reduced Total Time**: Especially beneficial for documents with many sections
    - **Smart Retry Logic**: Handles rate limiting and retries efficiently in concurrent mode



### Analyze Documents

*   **Endpoint:** `POST /analyze`
*   **Description:** Processes requests to perform generative analysis on sections of PDF files stored in Google Drive. Can optionally reuse existing files in Gemini AI storage to avoid duplicate uploads.
*   **Request Body:** `AnalyzeRequestItem` object. See [`models.py`](./models.py) for the `AnalyzeRequestItem` structure.
    ```json
    // Example AnalyzeRequestItem
    {
        "storage_file_id": "google_drive_pdf_file_id_to_analyze",
        "prompt_text": "Identify the main sections of this document and their page ranges.",
        "genai_file_name": "optional_existing_gemini_file_name"
    }
    ```
*   **Response (Success - 200 OK):** `BatchAnalyzeItemResult` object (which can be `AnalyzeResponseItemSuccess` or `AnalyzeResponseItemError`). See [`models.py`](./models.py) for details.
*   **Dependencies:** `GoogleDriveService`, `GenerativeAnalysisService`.
*   **New Feature - genai_file_name:**
    - If `genai_file_name` is provided, the system will first check if that file exists in Gemini AI storage
    - If the file exists and is active, it will be reused instead of uploading a new file
    - If the file doesn't exist, the system will fall back to normal upload behavior
    - This feature helps optimize performance by avoiding duplicate uploads when the same file needs to be analyzed multiple times

### Split Documents

*   **Endpoint:** `POST /split`
*   **Description:** Splits a PDF document from Google Drive into multiple smaller PDF files based on specified page ranges and section names. The split parts are saved back to Google Drive.
*   **Request Body:** `SplitRequest` object. See [`models.py`](./models.py) for the `SplitRequest` structure.
    ```json
    // Example SplitRequest
    {
        "storage_file_id": "google_drive_pdf_file_id_to_split",
        "file_name": "optional_original_file_name",
        "storage_parent_folder_id": "google_drive_folder_id_for_output",
        "sections": [
            {
                "section_name": "Introduction",
                "page_range": "1-3"
            },
            {
                "section_name": "Main Content",
                "page_range": "4-10"
            },
            {
                "section_name": "Conclusion",
                "page_range": "11-12"
            }
        ]
    }
    ```
*   **Response (Success - 200 OK):** A `BatchSplitItemResult` object. See [`models.py`](./models.py) for details.
*   **Dependencies:** `GoogleDriveService`, `PdfSplitterService`.

### Enhance Lessons

*   **Endpoint:** `POST /enhance`
*   **Description:** Enhances a list of lesson data structures by applying a series of generative AI prompts to each lesson. It handles data dependencies between prompts and includes retry logic for API calls, including rate limiting.
*   **Request Body:** `EnhanceRequest` object. See [`models.py`](./models.py) for `EnhanceRequest`, `LessonDataEnhance`, and `PromptItem` structures.
    ```json
    // Example EnhanceRequest
    {
        "lesson_data": [
            {
                "lesson_id": "lesson1",
                "input_data": {"title": "Introduction to AI", "content_gdoc_id": "drive_file_id_for_content"},
                "generated_content": []
            }
        ],
        "prompts": [
            {
                "prompt_name": "generate_summary",
                "prompt_template": "Summarize the following text: {input_data.content_gdoc_id}",
                "output_json_schema": null, // or a JSON schema string
                "depends_on_prompt": null,
                "use_multimodal_input": false
            },
            {
                "prompt_name": "extract_keywords",
                "prompt_template": "Extract keywords from this summary: {generated_content.generate_summary.output}",
                "output_json_schema": "{\"type\": \"object\", \"properties\": {\"keywords\": {\"type\": \"array\", \"items\": {\"type\": \"string\"}}}}",
                "depends_on_prompt": "generate_summary",
                "use_multimodal_input": false
            }
        ]
    }
    ```
*   **Response (Success - 200 OK):** `EnhanceResponse` object containing the list of `LessonDataEnhance` with populated `generated_content`. See [`models.py`](./models.py).
*   **Dependencies:** `GenerativeAnalysisService`.
*   **Key Logic:**
    *   Manages a data dependency queue and an API retry queue.
    *   Implements cooldowns for rate-limited API calls (`RETRY_COOLDOWN_SECONDS`).
    *   Retries prompts if their data dependencies are not yet met (`MAX_DATA_DEPENDENCY_RETRIES`).
    *   Retries API calls on failure up to `MAX_API_RETRIES_PER_TASK` (defined in `helpers/enhance_helpers.py`).

### Extract Documents (Legacy Refactored Endpoint)

This endpoint accepts the `result` property from the `/analyze` response directly, making it easy to map in n8n workflows. It automatically adds default extraction prompts to each section and processes them. **Note: This is the legacy refactored endpoint. For new n8n workflows, use the `/extract` endpoint above.**

**Request Body:** The `result` property from the `/analyze` response (type: `AnalyzeResponseItemSuccess`)
```json
{
  "storage_file_id": "your_file_id",
  "file_name": "document.pdf",
  "storage_parent_folder_id": "parent_folder_id",
  "sections": [
    {
      "section_name": "Introduction",
      "page_range": "1-3",
      "pages": [
        {"pageNumber": 1, "pageLabel": "1"},
        {"pageNumber": 2, "pageLabel": "2"},
        {"pageNumber": 3, "pageLabel": "3"}
      ]
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
    "storage_parent_folder_id": "parent_folder_id",
    "sections": [
      {
        "section_name": "Introduction",
        "page_range": "1-3",
        "pages": [
          {"pageNumber": 1, "pageLabel": "1"},
          {"pageNumber": 2, "pageLabel": "2"},
          {"pageNumber": 3, "pageLabel": "3"}
        ],
        "prompts": [
          {
            "prompt_name": "extract_content",
            "prompt_text": "Extract all relevant content from this section, including any key information, data, or important details.",
            "result": "Content extracted from the introduction section..."
          }
        ]
      }
    ]
  },
  "error": null
}
```

**Key Features:**
- **Easy n8n Integration**: Accepts the `result` property from `/analyze` response directly
- **Automatic Prompt Generation**: Adds default extraction prompts to each section automatically
- **Reuses Uploaded File**: Uses the same file from the analyze process to save resources
- **Parallel Processing**: Processes each section's prompts in parallel with rate limiting
- **Section Context**: Automatically adds section context and page range to prompts for better AI focus
- **Error Handling**: Includes comprehensive retry logic and error handling

## Environment Variables

The following environment variables are used by the application and should be defined in a `.env` file for local development or set in your deployment environment:

*   `GOOGLE_SERVICE_ACCOUNT_JSON`: Path to the Google Cloud service account JSON key file. This is essential for authenticating with Google Drive and other Google Cloud services.
*   `GEMINI_API_KEY`: Your API key for accessing Google's Gemini models.
*   `GEMINI_MODEL_ID`: The specific Gemini model to be used for generative tasks (e.g., `gemini-2.0-flash-latest`, `gemini-pro`).


