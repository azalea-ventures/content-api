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

### Extract Data

*   **Endpoint:** `POST /extract`
*   **Description:** Processes a batch of requests to extract structured data from PDF files stored in Google Drive. It uses a target PDF, a prompt document (also from Drive), and an example JSON output format to guide the extraction.
*   **Request Body:** A list of `ExtractRequestItem` objects. See [`models.py`](./models.py) for the `ExtractRequestItem` structure.
    ```json
    // Example ExtractRequestItem (within a list)
    {
        "target_file_id": "google_drive_pdf_file_id_to_extract_from",
        "prompt_document_file_id": "google_drive_text_file_id_for_prompt",
        "output_file_name_suffix": "_extracted_data"
    }
    ```
*   **Response (Success - 200 OK):** A list of `BatchExtractItemResult` objects. See [`models.py`](./models.py) for details. Each item indicates success or failure for the corresponding request.
*   **Dependencies:** `GoogleDriveService`, `PdfTextExtractorService`, `GenerativeAnalysisService`.

### Analyze Documents

*   **Endpoint:** `POST /analyze`
*   **Description:** Processes a batch of requests to perform generative analysis on sections of PDF files stored in Google Drive.
*   **Request Body:** A list of `AnalyzeRequestItem` objects. See [`models.py`](./models.py) for the `AnalyzeRequestItem` structure.
    ```json
    // Example AnalyzeRequestItem (within a list)
    {
        "file_id": "google_drive_pdf_file_id_to_analyze"
    }
    ```
*   **Response (Success - 200 OK):** A list of `BatchAnalyzeItemResult` objects (which can be `AnalyzeResponseItemSuccess` or `AnalyzeResponseItemError`). See [`models.py`](./models.py) for details.
*   **Dependencies:** `GoogleDriveService`, `GenerativeAnalysisService`.

### Split Documents

*   **Endpoint:** `POST /split`
*   **Description:** Processes a batch of requests to split PDF documents from Google Drive into multiple smaller PDF files based on specified criteria (e.g., page ranges, section titles). The split parts are saved back to Google Drive.
*   **Request Body:** `BatchSplitRequest` object containing a list of `SplitRequestItem`. See [`models.py`](./models.py) for these structures.
    ```json
    // Example BatchSplitRequest
    {
        "files_to_split": [
            {
                "file_id": "google_drive_pdf_file_id_to_split",
                "split_type": "ranges", // or "sections"
                "split_instructions": [
                    {"range_start": 1, "range_end": 2, "output_name_suffix": "Part1"},
                    {"range_start": 3, "range_end": 5, "output_name_suffix": "Part2"}
                ], // or section_titles for "sections" type
                "output_folder_id": "optional_google_drive_folder_id_for_output"
            }
        ]
    }
    ```
*   **Response (Success - 200 OK):** A list of `BatchSplitItemResult` objects. See [`models.py`](./models.py) for details.
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

## Environment Variables

The following environment variables are used by the application and should be defined in a `.env` file for local development or set in your deployment environment:

*   `GOOGLE_SERVICE_ACCOUNT_JSON`: Path to the Google Cloud service account JSON key file. This is essential for authenticating with Google Drive and other Google Cloud services.
*   `GEMINI_API_KEY`: Your API key for accessing Google's Gemini models.
*   `GEMINI_MODEL_ID`: The specific Gemini model to be used for generative tasks (e.g., `gemini-2.0-flash-latest`, `gemini-pro`).

Refer to the `main.py` and service modules for detailed initialization and usage of these variables.
