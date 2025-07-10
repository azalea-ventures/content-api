# models.py

from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Union
import uuid # For default task_id

# --- SHARED MODELS (Used by /enhance/* and /extract) ---

class PromptItem(BaseModel):
    """Defines a single prompt to be processed."""
    prompt_name: str
    prompt_template: str
    # For /extract, this refers to outputs of other prompts in the same extraction task
    # For /enhance, this refers to 'content' or outputs of other prompts on the same Slide/LessonSimple
    lesson_properties_to_append: List[str] = Field(default_factory=list)
    
    # If different prompts expect different JSON structures (especially for /extract):
    output_json_format_example_str: Optional[str] = None # String representation of the JSON example
                                                        # If None, implies general text or JSON structure defined only in prompt.
    # No 'order' or 'depends_on' for implicit, data-driven ordering

class GeneratedContentItem(BaseModel):
    """Represents a piece of content generated for a specific prompt."""
    prompt_name: str
    output: Optional[str] = None # The raw generated string (JSON string or text)
    status: Optional[str] = None # e.g., "SUCCESS", "RATE_LIMIT", "DATA_DEPENDENCY_PENDING"

# --- Models for /enhance/units ---
class Slide(BaseModel):
    name: Optional[str] = None
    content: str
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)
    model_config = {"extra": "allow"}

class Section(BaseModel):
    name: Optional[str] = None
    slides: List[Slide]
    model_config = {"extra": "allow"}

class LessonUnit(BaseModel):
    lesson_id: Optional[str] = None
    title: Optional[str] = None
    sections: List[Section]
    model_config = {"extra": "allow"}

class EnhanceUnitsRequest(BaseModel):
    prompts: Optional[List[PromptItem]] = None
    lessons: List[LessonUnit]

class EnhanceUnitsResponse(BaseModel):
    lessons: List[LessonUnit]

# --- Models for /enhance/lessons ---
class LessonSimple(BaseModel):
    timestamp: Optional[str] = None
    file_id: Optional[str] = None # Often used as an identifier
    url: Optional[str] = None
    folder_path: Optional[str] = None
    file_name: Optional[str] = None
    lesson_date: Optional[str] = None
    iclo_slide: Optional[float] = None
    strategy_application_slide: Optional[float] = None
    learning_objective: Optional[str] = None
    standards: Optional[str] = None
    clc_element: Optional[str] = None
    strategy_application_element: Optional[str] = None
    content: str
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)
    model_config = {"extra": "allow"}

class EnhanceLessonsRequest(BaseModel):
    prompts: Optional[List[PromptItem]] = None
    lessons: List[LessonSimple]

class EnhanceLessonsResponse(BaseModel):
    lessons: List[LessonSimple]


# --- SHARED DATA STRUCTURES ---

# This might be specific to a type of extraction, can be kept if useful
# or if the output of certain extraction prompts should conform to this.
class ExtractedSectionDataItem(BaseModel):
    page: int
    title: Optional[str] = None
    paragraph: str
ExtractedDataDict = Dict[str, List[ExtractedSectionDataItem]]

# --- SHARED MODELS (Used by multiple endpoints) ---
class SectionInfo(BaseModel):
    section_name: str
    page_range: str

# --- SIMPLIFIED SECTION MODELS (Removed individual page objects) ---
class SectionWithPages(BaseModel):
    """Represents a section with its page range (simplified without individual page objects)"""
    page_range: str
    section_name: str
    result: Optional[str] = None  # Just store the result, not the entire prompt
    genai_file_name: Optional[str] = None  # NEW: Added for split/extract workflow

# --- NEW REFACTORED EXTRACT MODELS ---

class SectionExtractPrompt(BaseModel):
    """A prompt to be executed for a specific section"""
    id: str
    user_id: str
    prompt_name: str
    prompt_text: str
    result: Optional[str] = None  # Store the result directly on the prompt

class SectionWithPrompts(BaseModel):
    """A section with prompts for extraction (simplified without individual page objects)"""
    prompts: Optional[List[SectionExtractPrompt]] = Field(default_factory=list)
    page_range: str
    section_name: str
    genai_file_name: Optional[str] = None  # NEW: Added for split/extract workflow

class AnalyzeResultWithPrompts(BaseModel):
    """The result structure from analyze with prompts added (simplified without individual page objects)"""
    storage_file_id: str
    file_name: str
    storage_parent_folder_id: str
    sections: List[SectionWithPrompts]

class ExtractRequest(BaseModel):
    storage_file_id: str
    file_name: Optional[str] = None
    storage_parent_folder_id: Optional[str] = None
    sections: List[SectionWithPages]  # Multiple sections
    genai_file_name: Optional[str] = None
    prompt: SectionExtractPrompt  # Single prompt to apply to all sections

class RefactoredExtractResponse(BaseModel):
    """Response from the refactored extract endpoint (matches the request format)"""
    success: bool
    result: Optional[AnalyzeResultWithPrompts] = None
    error: Optional[str] = None
    genai_file_name: Optional[str] = None

class ExtractResponse(BaseModel):
    """Response from the updated extract endpoint with prompt as sibling of sections"""
    success: bool
    storage_file_id: str
    file_name: Optional[str] = None
    storage_parent_folder_id: Optional[str] = None
    sections: List[SectionWithPages]  # Multiple processed sections with just results
    prompt: SectionExtractPrompt  # Single prompt with results
    error: Optional[str] = None
    genai_file_name: Optional[str] = None

# --- Other existing models for /analyze, /split (UPDATED) ---
class AnalyzeRequestItem(BaseModel):
    file_id: str
    prompt_text: str
    genai_file_name: Optional[str] = None

class SplitRequest(BaseModel):
    storage_file_id: str
    file_name: Optional[str] = None
    storage_parent_folder_id: Optional[str] = None
    sections: List[SectionInfo]

class AnalyzeResponseItemError(BaseModel):
    storage_file_id: str
    error: str
    detail: Optional[str] = None

class AnalyzeResponseItemSuccess(BaseModel):
    storage_file_id: str
    file_name: Optional[str] = None
    storage_parent_folder_id: Optional[str] = None
    sections: List[SectionWithPages]  # Updated to use simplified section model
    genai_file_name: Optional[str] = None

class BatchAnalyzeItemResult(BaseModel):
    success: bool
    result: Optional[AnalyzeResponseItemSuccess] = None
    error_info: Optional[AnalyzeResponseItemError] = None

class UploadedFileInfo(BaseModel):
    section_name: str
    page_range: str
    genai_file_name: Optional[str] = None  # NEW: Added for split/extract workflow

class SplitResponseItemSuccess(BaseModel):
    storage_file_id: str
    file_name: Optional[str] = None
    storage_parent_folder_id: Optional[str] = None
    sections: List[UploadedFileInfo]

class SplitResponseItemError(BaseModel):
    storage_file_id: str
    error: str
    detail: Optional[str] = None

class BatchSplitItemResult(BaseModel):
    success: bool
    result: Optional[SplitResponseItemSuccess] = None
    error_info: Optional[SplitResponseItemError] = None



