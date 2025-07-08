import io
import json
import re
import os
import time
import traceback
import asyncio
from typing import List, Dict, Any, Optional, Union, Tuple

import google.generativeai as genai
# Import types for type hinting File object
from google.generativeai import types
# Import protos to access the File.State enum
from google.generativeai import protos # <-- ADD THIS IMPORT
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError # For specific error catching
from config import settings

# Import StorageService for type hinting
from services.google_drive_service import StorageService

# No longer need Credentials here, genai is configured with API key
# from google.oauth2.service_account import Credentials

class GenerativeAnalysisService:
    def __init__(self, api_key: str, model_id: str):
        """
        Initializes the GenerativeAnalysisService.

        Args:
            api_key: The API key string for Generative AI operations.
            model_id: The ID of the Gemini model to use (e.g., 'gemini-1.5-flash-latest').
        """
        if not api_key:
             raise ValueError("API key not provided for GenAI.")

        if not model_id:
             raise ValueError("Gemini Model ID is empty.")

        try:
            # Configure the genai library with the API key for ALL calls
            genai.configure(api_key=api_key)
            print("Google Generative AI configured with API key.")

            self.model = genai.GenerativeModel(model_id)
            self.model_id = model_id
            print(f"GenerativeAnalysisService initialized successfully with model: {model_id}")

        except Exception as e:
            print(f"Error initializing GenerativeAnalysisService with model {model_id}: {e}")
            raise RuntimeError(f"Failed to initialize Generative Model {model_id}. Check API key and model ID.") from e

    async def generate_text(self, prompt_text: str) -> Tuple[str, Optional[str]]:
        """
        Generates text using the Gemini model.

        Args:
            prompt_text: The text prompt to send to the model.

        Returns:
            A tuple containing (generated_text, error_message). If successful, error_message is None.
        """
        if not prompt_text or not prompt_text.strip():
            return "ERROR_INPUT", "Empty or invalid prompt text provided."

        try:
            print(f"Generating text with model: {self.model_id}")
            print(f"Prompt length: {len(prompt_text)} characters")

            # Generate content using the model
            response = await asyncio.to_thread(
                self.model.generate_content,
                prompt_text
            )

            if response and response.text:
                print(f"Successfully generated text. Response length: {len(response.text)} characters")
                return response.text, None
            else:
                print("No text generated in response")
                return "ERROR_NO_RESPONSE", "No text was generated in the response."

        except ResourceExhausted as e:
            print(f"RESOURCE EXHAUSTED: {e}")
            return "ERROR_RESOURCE_EXHAUSTED", f"Resource exhausted: {str(e)}"

        except GoogleAPIError as e:
            print(f"GOOGLE API ERROR: {e}")
            return "ERROR_GOOGLE_API", f"Google API error: {str(e)}"

        except Exception as e:
            print(f"UNEXPECTED ERROR: Error during Gemini text generation: {e}")
            traceback.print_exc()
            return "ERROR_API", f"Unexpected error during API call: {str(e)}"

    async def find_file_by_display_name(self, display_name: str) -> Optional[types.File]:
        """
        Search for an active file in Gemini AI storage by display name.
        
        Args:
            display_name: The display name to search for
            
        Returns:
            An active types.File object if found, None otherwise
        """
        try:
            print(f"DEBUG: Searching all files in Gemini AI storage for display name: '{display_name}'")
            files = list(genai.list_files())  # Convert generator to list
            print(f"DEBUG: Found {len(files)} total files in Gemini AI storage to search through")
            
            for i, file in enumerate(files, 1):
                print(f"DEBUG: [{i}/{len(files)}] Checking file - Name: '{file.name}', Display: '{file.display_name}'")
                
                # Try multiple comparison methods for robustness
                exact_match = file.display_name == display_name
                case_insensitive_match = file.display_name.lower() == display_name.lower()
                stripped_match = file.display_name.strip() == display_name.strip()
                
                if exact_match or case_insensitive_match or stripped_match:
                    print(f"DEBUG: ✓ MATCH FOUND! - Name: '{file.name}' (length: {len(file.name)}), State: {file.state}")
                    if not exact_match:
                        print(f"DEBUG: Match type - Exact: {exact_match}, Case-insensitive: {case_insensitive_match}, Stripped: {stripped_match}")
                    
                    if file.state == protos.File.State.ACTIVE:
                        print(f"DEBUG: ✓ File is ACTIVE and ready for use")
                        return file
                    else:
                        print(f"DEBUG: ✗ File is not ACTIVE (state: {file.state})")
                        return None
            
            print(f"DEBUG: No matching file found with display name: '{display_name}'")
            return None
            
        except Exception as e:
            print(f"DEBUG: Error searching for file with display name '{display_name}': {e}")
            return None

    async def list_all_uploaded_files(self) -> List[Dict[str, Any]]:
        """
        List all files currently uploaded to Google AI storage.
        
        Returns:
            List of dictionaries containing file information
        """
        try:
            files = list(genai.list_files())  # Convert generator to list
            
            file_list = []
            for file in files:
                file_info = {
                    "name": file.name,
                    "display_name": file.display_name,
                    "state": file.state.name if hasattr(file.state, 'name') else str(file.state),
                    "uri": file.uri,
                    "size_bytes": getattr(file, 'size_bytes', None),
                    "expiration_time": getattr(file, 'expiration_time', None)
                }
                file_list.append(file_info)
            
            return file_list
            
        except Exception as e:
            print(f"Error listing uploaded files: {e}")
            traceback.print_exc()
            return []

    async def upload_pdf_for_analysis(self, pdf_stream: io.BytesIO, display_name: str) -> Optional[types.File]:
        """
        Uploads a PDF stream to Google AI's temporary storage for analysis.
        Always uploads a new file - no duplicate checking.
        """
        if not pdf_stream or pdf_stream.getbuffer().nbytes == 0:
            print("PDF stream is empty or invalid for upload.")
            return None
        pdf_stream.seek(0)
        try:
            uploaded_file = genai.upload_file(
                path=pdf_stream,
                display_name=display_name,
                mime_type='application/pdf',
            )
            max_polls = 60
            poll_count = 0
            while uploaded_file.state == protos.File.State.PROCESSING and poll_count < max_polls:
                await asyncio.sleep(5)
                uploaded_file = genai.get_file(name=uploaded_file.name)
                poll_count += 1
            if uploaded_file.state == protos.File.State.FAILED:
                return None
            if uploaded_file.state == protos.File.State.PROCESSING:
                return None
            if not uploaded_file.state == protos.File.State.ACTIVE:
                return None
            return uploaded_file
        except Exception as e:
            print(f"Error during PDF upload to Google AI: {e}")
            return None

    async def upload_pdf_for_analysis_by_file_id(
        self, 
        file_id: str, 
        display_name: str, 
        storage_service: 'StorageService'
    ) -> Optional[types.File]:
        """
        Uploads a PDF to Google AI's temporary storage for analysis by file ID.
        Always downloads and uploads a new file - no duplicate checking.
        """
        pdf_stream = None
        try:
            pdf_stream = storage_service.download_file_content(file_id)
            if not pdf_stream or pdf_stream.getbuffer().nbytes == 0:
                return None
        except Exception as e:
            print(f"Error downloading PDF from storage service: {e}")
            return None
        try:
            pdf_stream.seek(0)
            uploaded_file = genai.upload_file(
                path=pdf_stream,
                display_name=display_name,
                mime_type='application/pdf',
            )
            max_polls = 60
            poll_count = 0
            while uploaded_file.state == protos.File.State.PROCESSING and poll_count < max_polls:
                await asyncio.sleep(5)
                uploaded_file = genai.get_file(name=uploaded_file.name)
                poll_count += 1
            if uploaded_file.state == protos.File.State.FAILED:
                return None
            if uploaded_file.state == protos.File.State.PROCESSING:
                return None
            if not uploaded_file.state == protos.File.State.ACTIVE:
                return None
            return uploaded_file
        except Exception as e:
            print(f"Error during PDF upload to Google AI: {e}")
            return None
        finally:
            if pdf_stream:
                try:
                    pdf_stream.close()
                    del pdf_stream
                except Exception as e:
                    print(f"Error closing PDF stream: {e}")

    async def analyze_pdf_content(
        self, 
        file: types.File, 
        analysis_prompt: str,
        max_retries: int = 3
    ) -> Tuple[str, Optional[str]]:
        """
        Analyzes PDF content using the Gemini model with the uploaded file.

        Args:
            file: The genai.types.File object referencing the uploaded PDF.
            analysis_prompt: The prompt to use for analysis.
            max_retries: Maximum number of retry attempts for the analysis.

        Returns:
            A tuple containing (analysis_result, error_message). If successful, error_message is None.
        """
        if not file or not file.name:
            return "ERROR_INPUT", "Invalid file object provided."

        if not analysis_prompt or not analysis_prompt.strip():
            return "ERROR_INPUT", "Empty or invalid analysis prompt provided."

        retry_count = 0
        last_error = None

        while retry_count < max_retries:
            try:
                print(f"Analyzing PDF content with model: {self.model_id}")
                print(f"File: {file.name}")
                print(f"Analysis prompt length: {len(analysis_prompt)} characters")
                print(f"Attempt {retry_count + 1}/{max_retries}")

                # Create the model with the file
                model_with_file = genai.GenerativeModel(self.model_id)
                
                # Generate content using the model with the file
                response = await asyncio.to_thread(
                    model_with_file.generate_content,
                    [analysis_prompt, file]
                )

                if response and response.text:
                    print(f"Successfully analyzed PDF content. Response length: {len(response.text)} characters")
                    return response.text, None
                else:
                    print("No analysis result generated in response")
                    return "ERROR_NO_RESPONSE", "No analysis result was generated in the response."

            except ResourceExhausted as e:
                last_error = f"Resource exhausted: {str(e)}"
                print(f"RESOURCE EXHAUSTED (attempt {retry_count + 1}): {e}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Retrying in {settings.retry_cooldown_seconds} seconds...")
                    await asyncio.sleep(settings.retry_cooldown_seconds)

            except GoogleAPIError as e:
                last_error = f"Google API error: {str(e)}"
                print(f"GOOGLE API ERROR (attempt {retry_count + 1}): {e}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Retrying in {settings.retry_cooldown_seconds} seconds...")
                    await asyncio.sleep(settings.retry_cooldown_seconds)

            except Exception as e:
                last_error = f"Unexpected error during API call: {str(e)}"
                print(f"UNEXPECTED ERROR (attempt {retry_count + 1}): Error during Gemini analysis: {e}")
                traceback.print_exc()
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Retrying in {settings.retry_cooldown_seconds} seconds...")
                    await asyncio.sleep(settings.retry_cooldown_seconds)

        # If we get here, all retries failed
        print(f"All {max_retries} attempts failed. Last error: {last_error}")
        return "ERROR_MAX_RETRIES", last_error

    async def delete_file(self, file: types.File) -> bool:
        """
        Deletes a file from Google AI storage.

        Args:
            file: The genai.types.File object to delete.

        Returns:
            True if deletion was successful, False otherwise.
        """
        if not file or not file.name:
            print("Invalid file object provided for deletion.")
            return False

        try:
            print(f"Deleting file: {file.name}")
            genai.delete_file(name=file.name)
            print(f"Successfully deleted file: {file.name}")
            return True

        except Exception as e:
            print(f"Error deleting file {file.name}: {e}")
            return False

    async def get_file_by_name(self, file_name: str) -> Optional[types.File]:
        """
        Get a file from Gemini AI storage by its actual file name.
        """
        try:
            file = genai.get_file(name=file_name)
            if file.state == protos.File.State.ACTIVE:
                return file
            return None
        except Exception as e:
            print(f"Error retrieving file by name: {e}")
            return None

    async def analyze_sections_multimodal(
        self, 
        file: types.File, 
        analysis_prompt: str,
        max_retries: int = 3
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Analyzes PDF content to identify sections using the Gemini model with the uploaded file.
        Returns structured data about sections with page information.

        Args:
            file: The genai.types.File object referencing the uploaded PDF.
            analysis_prompt: The prompt to use for section analysis.
            max_retries: Maximum number of retry attempts for the analysis.

        Returns:
            A list of dictionaries containing section information with page metadata, or None if analysis fails.
        """
        if not file or not file.name:
            print("Invalid file object provided for section analysis.")
            return None

        if not analysis_prompt or not analysis_prompt.strip():
            print("Empty or invalid analysis prompt provided.")
            return None

        retry_count = 0
        last_error = None

        while retry_count < max_retries:
            try:
                print(f"Analyzing PDF sections with model: {self.model_id}")
                print(f"File: {file.name}")
                print(f"Analysis prompt length: {len(analysis_prompt)} characters")
                print(f"Attempt {retry_count + 1}/{max_retries}")

                # Create the model with the file
                model_with_file = genai.GenerativeModel(self.model_id)
                
                # Generate content using the model with the file
                response = await model_with_file.generate_content_async(
                    contents=[analysis_prompt, file], 
                    generation_config={
                        "response_mime_type": "application/json", 
                        "response_schema": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sectionName": {"type": "string"},
                                    "pageRange": {"type": "string"}
                                },
                                "required": ["sectionName", "pageRange"]
                            }
                        }
                    }
                )

                if response and response.text:
                    print(f"Successfully analyzed PDF sections. Response length: {len(response.text)} characters")
                    
                    # Parse the response to extract section information
                    sections_info = self._parse_sections_response(response.text)
                    if sections_info:
                        print(f"Successfully parsed {len(sections_info)} sections from analysis")
                        return sections_info
                    else:
                        print("Failed to parse sections from analysis response")
                        return None
                else:
                    print("No analysis result generated in response")
                    return None

            except ResourceExhausted as e:
                last_error = f"Resource exhausted: {str(e)}"
                print(f"RESOURCE EXHAUSTED (attempt {retry_count + 1}): {e}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Retrying in {settings.retry_cooldown_seconds} seconds...")
                    await asyncio.sleep(settings.retry_cooldown_seconds)

            except GoogleAPIError as e:
                last_error = f"Google API error: {str(e)}"
                print(f"GOOGLE API ERROR (attempt {retry_count + 1}): {e}")
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Retrying in {settings.retry_cooldown_seconds} seconds...")
                    await asyncio.sleep(settings.retry_cooldown_seconds)

            except Exception as e:
                last_error = f"Unexpected error during API call: {str(e)}"
                print(f"UNEXPECTED ERROR (attempt {retry_count + 1}): Error during Gemini section analysis: {e}")
                traceback.print_exc()
                retry_count += 1
                if retry_count < max_retries:
                    print(f"Retrying in {settings.retry_cooldown_seconds} seconds...")
                    await asyncio.sleep(settings.retry_cooldown_seconds)

        # If we get here, all retries failed
        print(f"All {max_retries} attempts failed. Last error: {last_error}")
        return None

    def _parse_sections_response(self, response_text: str) -> Optional[List[Dict[str, Any]]]:
        """
        Parse the AI response to extract section information.
        
        Args:
            response_text: The raw text response from the AI model
            
        Returns:
            A list of dictionaries containing section information, or None if parsing fails
        """
        try:
            # Try to parse as JSON first
            import json
            response_data = json.loads(response_text)
            
            # Handle different possible JSON structures
            if isinstance(response_data, list):
                sections = response_data
            elif isinstance(response_data, dict) and 'sections' in response_data:
                sections = response_data['sections']
            elif isinstance(response_data, dict) and 'data' in response_data:
                sections = response_data['data']
            else:
                sections = [response_data]
            
            # Validate and normalize section structure
            normalized_sections = []
            for section in sections:
                if isinstance(section, dict):
                    normalized_section = {
                        'sectionName': section.get('sectionName', section.get('name', 'Unknown Section')),
                        'pageRange': section.get('pageRange', section.get('page_range', ''))
                    }
                    
                    normalized_sections.append(normalized_section)
            
            return normalized_sections if normalized_sections else None
            
        except json.JSONDecodeError:
            # If JSON parsing fails, try to extract information using regex patterns
            print("JSON parsing failed, attempting regex-based extraction")
            return self._extract_sections_with_regex(response_text)
        except Exception as e:
            print(f"Error parsing sections response: {e}")
            return None

    def _extract_sections_with_regex(self, response_text: str) -> Optional[List[Dict[str, Any]]]:
        """
        Fallback method to extract section information using regex patterns.
        
        Args:
            response_text: The raw text response from the AI model
            
        Returns:
            A list of dictionaries containing section information, or None if extraction fails
        """
        try:
            sections = []
            
            # Look for patterns like "Section: [name] (Pages: [range])"
            section_pattern = r'(?:Section|Chapter|Part)\s*[:\-]?\s*([^\(\)\n]+?)\s*(?:\(Pages?\s*[:\-]?\s*([^\)\n]+)\))?'
            matches = re.findall(section_pattern, response_text, re.IGNORECASE)
            
            for match in matches:
                section_name = match[0].strip()
                page_range = match[1].strip() if match[1] else ""
                
                sections.append({
                    'sectionName': section_name,
                    'pageRange': page_range
                })
            
            return sections if sections else None
            
        except Exception as e:
            print(f"Error in regex-based section extraction: {e}")
            return None

    async def get_file_matching_summary(self, genai_file_name: str = None) -> Dict[str, Any]:
        summary = {
            "matching_strategy": "gemini_file_name_only",
            "display_name_matching_disabled": True
        }
        if genai_file_name:
            summary["target_genai_file_name"] = genai_file_name
            try:
                file = await self.get_file_by_name(genai_file_name)
                summary["file_exists"] = file is not None
                summary["file_active"] = file.state == protos.File.State.ACTIVE if file else False
                summary["file_state"] = file.state.name if file and hasattr(file.state, 'name') else str(file.state) if file else None
            except Exception as e:
                summary["file_check_error"] = str(e)
        else:
            try:
                files = list(genai.list_files())
                summary["total_files_in_storage"] = len(files)
                summary["active_files_in_storage"] = [f.name for f in files if f.state == protos.File.State.ACTIVE]
            except Exception as e:
                summary["storage_check_error"] = str(e)
        return summary