import io
import json
import re
import os
import time
import traceback
from typing import List, Dict, Any, Optional, Union, Tuple

import google.generativeai as genai
# Import types for type hinting File object
from google.generativeai import types
# Import protos to access the File.State enum
from google.generativeai import protos # <-- ADD THIS IMPORT
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError # For specific error catching

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
        Generates text content from a given prompt using the configured Gemini model.
        FORCES "application/json" as the response_mime_type.
        Returns a tuple: (status: str, content: Optional[str])
        Status can be: "SUCCESS", "RATE_LIMIT", "ERROR_BLOCKED", "ERROR_API", 
                       "ERROR_EMPTY", "ERROR_NO_MODEL", "ERROR_INVALID_JSON"
        """
        if not self.model:
            print("Error: Gemini model not initialized in GenerativeAnalysisService.")
            return "ERROR_NO_MODEL", "Gemini model not initialized."

        # Always configure for JSON output for this method
        generation_config = types.GenerationConfig(
            response_mime_type="application/json"
            # You can add other default generation configs here if needed for this method:
            # temperature=0.5,
            # max_output_tokens=1024, # Ensure this is large enough for your JSONs
        )
        print(f"GenerativeAnalysisService: Sending prompt for JSON output (len {len(prompt_text)})...")

        try:
            response = await self.model.generate_content_async(
                prompt_text,
                generation_config=generation_config # Pass the config here
            )

            if response and response.candidates:
                candidate = response.candidates[0]
                finish_reason_obj = getattr(candidate, 'finish_reason', None)
                finish_reason_name = finish_reason_obj.name if finish_reason_obj else 'UNKNOWN'

                if finish_reason_name == "SAFETY":
                    safety_ratings_str = str(getattr(candidate, 'safety_ratings', 'No safety ratings available'))
                    print(f"Warning: Content generation stopped by API due to safety ratings: {safety_ratings_str}")
                    return "ERROR_BLOCKED", f"Content generation blocked by API due to safety ({safety_ratings_str})."

                if candidate.content and candidate.content.parts:
                    generated_text = "".join(part.text for part in candidate.content.parts if hasattr(part, 'text'))
                    if generated_text.strip():
                        # Since we requested JSON, attempt to parse it to validate.
                        try:
                            json.loads(generated_text) # Validate JSON structure
                            # If parsing is successful, it's valid JSON.
                            print(f"SUCCESS (JSON validated): Prompt processed.")
                            return "SUCCESS", generated_text
                        except json.JSONDecodeError as json_e:
                            error_message = f"API returned invalid JSON despite mime_type request. Error: {json_e}. Raw text: '{generated_text[:200]}...'"
                            print(f"Warning: {error_message}")
                            return "ERROR_INVALID_JSON", error_message
                    else: # Empty text from API
                        print(f"Warning: Gemini API returned empty text (expected JSON). Finish reason: {finish_reason_name}")
                        return "ERROR_EMPTY", f"Gemini API returned empty text (expected JSON, finish reason: {finish_reason_name})."
                else: # No parts or empty parts
                    print(f"Warning: Gemini API returned no content parts (expected JSON). Finish reason: {finish_reason_name}")
                    return "ERROR_EMPTY", f"Gemini API returned no content parts (expected JSON, finish reason: {finish_reason_name})."
            
            elif response and response.prompt_feedback and response.prompt_feedback.block_reason:
                # ... (same prompt feedback block reason handling as before) ...
                block_reason_obj = response.prompt_feedback.block_reason
                block_reason_name = block_reason_obj.name if block_reason_obj else "UNKNOWN"
                block_reason_message = response.prompt_feedback.block_reason_message or block_reason_name
                print(f"Warning: Prompt blocked by Gemini. Reason: {block_reason_name} - {block_reason_message}")
                return "ERROR_BLOCKED", f"Prompt blocked by API (Reason: {block_reason_message})."
            else: # No candidates and no clear block reason
                print("Warning: Gemini API returned no candidates (expected JSON).")
                return "ERROR_EMPTY", "Gemini API returned no candidates (expected JSON)."

        except ResourceExhausted as r_exc:
            print(f"RATE LIMIT: Gemini API rate limit exceeded. {r_exc}")
            return "RATE_LIMIT", f"Gemini API rate limit exceeded: {str(r_exc)}"
        except GoogleAPIError as api_exc:
            # This can also catch errors if the model can't generate JSON for the prompt
            # e.g. google.api_core.exceptions.InvalidArgument: 400 Response must be valid JSON.
            print(f"API ERROR: Error during Gemini text generation: {api_exc}")
            traceback.print_exc()
            # Check if it's specifically a JSON generation failure from the API
            if "Response must be valid JSON" in str(api_exc) or "Could not decode response" in str(api_exc) :
                 return "ERROR_API_CANT_GENERATE_JSON", f"Gemini API could not generate valid JSON for the prompt: {str(api_exc)}"
            return "ERROR_API", f"Gemini API error: {str(api_exc)}"
        except Exception as e:
            print(f"UNEXPECTED ERROR: Error during Gemini text generation: {e}")
            traceback.print_exc()
            return "ERROR_API", f"Unexpected error during API call: {str(e)}"

    def upload_pdf_for_analysis(self, pdf_stream: io.BytesIO, display_name: str) -> Optional[types.File]:
        """
        Uploads a PDF stream to Google AI's temporary storage for analysis.
        Uses the API key configured via genai.configure().

        Args:
            pdf_stream: An io.BytesIO stream containing the PDF content.
            display_name: A name for the file in Google AI storage (e.g., the original filename).

        Returns:
            A genai.types.File object referencing the uploaded file, or None on failure.
        """
        if not pdf_stream or pdf_stream.getbuffer().nbytes == 0:
            print("PDF stream is empty or invalid for upload.")
            return None

        pdf_stream.seek(0)

        try:
            print(f"Uploading PDF '{display_name}' to Google AI storage using configured API key...")
            uploaded_file = genai.upload_file(
                path=pdf_stream,
                display_name=display_name,
                mime_type='application/pdf',
            )
            print(f"Uploaded file '{display_name}' with URI: {uploaded_file.uri}")

            print("Polling file state for processing...")
            max_polls = 60 # Max 60 polls = 5 minutes (5 seconds per poll)
            poll_count = 0
            # *** CORRECTED ENUM REFERENCE ***
            while uploaded_file.state == protos.File.State.PROCESSING and poll_count < max_polls:
                time.sleep(5)
                uploaded_file = genai.get_file(file_name=uploaded_file.name)
                print(f"File state: {uploaded_file.state}")
                poll_count += 1

            # After polling loop, check final state
            # *** CORRECTED ENUM REFERENCE ***
            if uploaded_file.state == protos.File.State.FAILED:
                print(f"File upload and processing failed. Final state: {uploaded_file.state}, Cause: {uploaded_file.state.cause}")
                return None
            # *** CORRECTED ENUM REFERENCE ***
            if uploaded_file.state == protos.File.State.PROCESSING: # Check if loop exited due to max_polls
                 print(f"File processing timed out after {max_polls * 5} seconds. Final state: {uploaded_file.state}")
                 return None
            # *** CORRECTED ENUM REFERENCE ***
            if not uploaded_file.state == protos.File.State.ACTIVE: # ACTIVE
                 print(f"File did not reach ACTIVE state (expected {protos.File.State.ACTIVE}). Final state: {uploaded_file.state}")
                 return None

            print("File is active and ready for analysis.")
            return uploaded_file

        except Exception as e:
            print(f"Error during PDF upload to Google AI: {e}")
            return None
        # finally block is in the caller endpoint


    def analyze_sections_multimodal(self, uploaded_file: types.File, prompt_text: str) -> Optional[List[Dict[str, str]]]:
        """
        Analyzes a PDF file reference for section headings and their page ranges using a user-supplied prompt.
        """
        if not uploaded_file or not uploaded_file.state == protos.File.State.ACTIVE:
            print("analyze_sections_multimodal received invalid uploaded_file object (not active).")
            print(f"Uploaded file state: {uploaded_file.state}")
            return None

        if not prompt_text:
            print("analyze_sections_multimodal received empty prompt_text.")
            return None

        prompt_parts = [uploaded_file, prompt_text]

        sections_info: Optional[List[Dict[str, str]]] = None
        gemini_output: Optional[str] = None

        try:
            print(f"Sending multimodal prompt to Gemini model '{self.model_id}'...")
            response = self.model.generate_content(
                prompt_parts,
                request_options={'timeout': 300}
            )
            print("Received response from Gemini.")

            try: gemini_output = response.text
            except ValueError as ve:
                 print(f"Gemini response potentially blocked or empty: {ve}")
                 return None

            if not gemini_output:
                print("Gemini response text is empty.")
                return None

            print(f"Raw Gemini Output (first 500 chars): {gemini_output[:500]}...")

            # Find the index of the first '[' and the last ']'
            json_start_array = gemini_output.find('[')
            json_end_array = gemini_output.rfind(']') + 1

            json_array_string = None
            if json_start_array != -1 and json_end_array != 0 and json_end_array > json_start_array:
                 json_array_string = gemini_output[json_start_array:json_end_array]
                 print(f"Detected JSON array pattern.")


            if json_array_string is None:
                 print("Could not find a valid JSON array pattern in Gemini response text.")
                 print(f"Full Gemini Output:\n{gemini_output}")
                 return None

            json_string_cleaned = json_array_string.replace("```json", "").replace("```", "").strip()
            if not json_string_cleaned:
                 print("Extracted JSON string became empty after cleaning.")
                 print(f"Attempted JSON string before cleaning:\n{json_array_string}")
                 return None

            sections_info = json.loads(json_string_cleaned)

            if not isinstance(sections_info, list) or not all(isinstance(item, dict) and 'sectionName' in item and 'pageRange' in item and
                                                             isinstance(item['sectionName'], str) and isinstance(item['pageRange'], str)
                                                             for item in sections_info):
                 print("Parsed JSON does not match expected structure.")
                 print(f"Parsed Data: {sections_info}")
                 return None


            print("Successfully parsed Gemini response.")
            return sections_info

        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from Gemini response: {e}")
            print(f"Attempted JSON string before cleaning:\n{json_array_string if 'json_array_string' in locals() else 'N/A - no JSON string found'}")
            print(f"Cleaned JSON string:\n{json_string_cleaned if 'json_string_cleaned' in locals() else 'N/A - no cleaned string'}")
            print(f"Full Gemini Output:\n{gemini_output if gemini_output else 'N/A - empty output'}")
            return None
        except Exception as e:
            print(f"An error occurred during Gemini analysis: {e}")
            return None
        # Cleanup handled in the caller

    # --- NEW METHOD for /extract ---
    def extract_structured_data_multimodal(
        self,
        uploaded_target_file: types.File, # The PDF to extract data FROM
        prompt_text: str, # The text from the prompt Doc
        output_json_format_example: Dict[str, Any]
    ) -> Optional[Dict[str, List[Dict[str, Union[int, str, None]]]]]:
        """
        Sends the target PDF file reference and prompt text to Gemini for
        structured data extraction.

        Args:
            uploaded_target_file: A genai.types.File object referencing the uploaded target PDF.
            prompt_text: The text content from the prompt Google Doc.
            output_json_format_example: An example of the desired output JSON structure.

        Returns:
            A dictionary matching the output_json_format_example structure, or None on failure.
        """
        # *** CORRECTED ENUM REFERENCE ***
        if not uploaded_target_file or not uploaded_target_file.state == protos.File.State.ACTIVE:
            print("extract_structured_data_multimodal received invalid uploaded_target_file object (not active).")
            print(f"Uploaded file state: {uploaded_target_file.state}")
            return None
        if not prompt_text:
            print("extract_structured_data_multimodal received empty prompt text.")
            return None
        if not output_json_format_example:
             print("extract_structured_data_multimodal received empty output JSON format example.")
             return None


        # Construct the multimodal prompt
        prompt_text_instructions = (
            "Analyze the provided document (a multi-page PDF). "
            "Use the following text from another document as instructions:\n"
            f"--- PROMPT INSTRUCTIONS START ---\n{prompt_text.strip()}\n--- PROMPT INSTRUCTIONS END ---\n\n"
            "Based on the instructions, extract structured data from the provided PDF. "
            "Ensure the output follows this exact JSON format example:\n"
            f"--- JSON FORMAT EXAMPLE START ---\n{json.dumps(output_json_format_example, indent=2)}\n--- JSON FORMAT EXAMPLE END ---\n\n"
            "Specifically, output a JSON object where the keys are section names identified in the prompt instructions. "
            "Each value should be a list of objects, with each object containing the keys 'page', 'title' (if available for the specific paragraph), and 'paragraph' text extracted from the PDF.\n"
            "Report page numbers as 1-indexed integers.\n"
            "Do not include any other text or markdown outside the final JSON object.\n"
            "Ensure the JSON is valid and complete." # Added explicit instruction
        )

        prompt_parts = [
            uploaded_target_file, # Include the target PDF file reference
            prompt_text_instructions # Include text instructions
        ]

        extracted_data: Optional[Dict[str, List[Dict[str, Union[int, str, None]]]]] = None
        gemini_output: Optional[str] = None

        try:
            print(f"Sending multimodal extraction prompt to Gemini model '{self.model_id}' for file {uploaded_target_file.uri}...")
            response = self.model.generate_content(
                prompt_parts,
                request_options={'timeout': 300} # May need a long timeout
            )
            print("Received response from Gemini.")

            try: gemini_output = response.text
            except ValueError as ve:
                 print(f"Gemini response potentially blocked or empty: {ve}")
                 return None

            if not gemini_output:
                print("Gemini response text is empty.")
                return None

            print(f"Raw Gemini Extraction Output (first 500 chars): {gemini_output[:500]}...")

            # *** CORRECTED JSON EXTRACTION LOGIC ***
            # Find the index of the first '{' and the last '}'
            json_start_obj = gemini_output.find('{')
            json_end_obj = gemini_output.rfind('}') + 1 # +1 to include the closing brace

            if json_start_obj == -1 or json_end_obj == 0 or json_end_obj <= json_start_obj:
                print("Could not find a valid JSON object pattern {} in Gemini extraction response text.")
                print(f"Full Gemini Output:\n{gemini_output}")
                return None

            json_object_string = gemini_output[json_start_obj:json_end_obj]

            # Clean up common markdown wrappers if present *around the extracted string*
            json_string_cleaned = json_object_string.replace("```json", "").replace("```", "").strip()

             # Add a check if cleaning resulted in an empty string
            if not json_string_cleaned:
                 print("Extracted JSON object string became empty after cleaning.")
                 print(f"Attempted JSON string before cleaning:\n{json_object_string}")
                 return None


            # Attempt to parse the cleaned JSON string
            # This method expects a Dict[str, List[Dict[str, Union[int, str, None]]]]
            extracted_data = json.loads(json_string_cleaned)

            # Optional: Add basic validation to check if the parsed data matches the expected high-level structure
            # This assumes the structure is always a Dict where keys are strings and values are Lists
            # and list items are Dicts with 'page' (int), 'paragraph' (str), and optional 'title' (str/None)
            if not isinstance(extracted_data, dict) or not all(
                 isinstance(key, str) and
                 isinstance(value, list) and
                 all(
                      isinstance(item, dict) and
                      isinstance(item.get('page'), int) and
                      isinstance(item.get('paragraph'), str) and
                      ('title' not in item or isinstance(item.get('title'), (str, type(None))))
                      for item in value
                 )
                 for key, value in extracted_data.items()
            ):
                 print("Parsed JSON does not match expected extraction structure.")
                 print(f"Parsed Data: {extracted_data}")
                 # Log the specific type/key mismatch if possible for debugging
                 return None


            print("Successfully parsed Gemini extraction response.")
            return extracted_data

        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from Gemini extraction response: {e}")
            print(f"Attempted JSON string before cleaning:\n{json_object_string if 'json_object_string' in locals() else 'N/A - no JSON string found'}")
            print(f"Cleaned JSON string:\n{json_string_cleaned if 'json_string_cleaned' in locals() else 'N/A - no cleaned string'}")
            print(f"Full Gemini Output:\n{gemini_output if gemini_output else 'N/A - empty output'}")
            return None
        except Exception as e:
            print(f"An error occurred during Gemini extraction analysis: {e}")
            return None
        # Cleanup handled in caller


    def delete_uploaded_file(self, file_name: str):
        """Deletes an uploaded file from Google AI storage. Uses configured API key."""
        try:
            # Correct parameter name as per source code
            genai.delete_file(name=file_name) # <-- CORRECTED: Use 'name' parameter
            print(f"Successfully deleted uploaded file: {file_name}")
        except Exception as e:
            print(f"Error deleting uploaded file {file_name}: {e}")