# models.py

from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Union

# --- SHARED MODELS (Used by /enhance/units and /enhance/lessons) ---

class PromptItem(BaseModel):
    """Defines a single prompt to be processed."""
    prompt_name: str
    prompt_template: str
    lesson_properties_to_append: List[str] = Field(default_factory=list)
    # No 'order' or 'depends_on' for implicit, data-driven ordering

class GeneratedContentItem(BaseModel):
    """Represents a piece of content generated for a specific prompt."""
    prompt_name: str
    output: Optional[str] = None # The generated text/JSON string
    status: Optional[str] = None # e.g., "SUCCESS", "RATE_LIMIT", "DATA_DEPENDENCY_PENDING"

# --- HIERARCHICAL MODELS FOR /enhance/units (formerly /enhance) ---

class Slide(BaseModel):
    """Represents a single slide within a lesson section."""
    name: Optional[str] = None # Name or title of the slide
    content: str               # The primary instructional content of the slide
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list) # Outputs from prompts run on this slide
    
    model_config = {"extra": "allow"}

class Section(BaseModel):
    """Represents a section within a lesson, containing multiple slides."""
    name: Optional[str] = None # Name of the section
    slides: List[Slide]
    
    model_config = {"extra": "allow"}

class LessonUnit(BaseModel): # Renamed from Lesson to LessonUnit for clarity
    """
    Represents a complete lesson unit structure, containing sections and slides.
    This is the main data unit for the /enhance/units endpoint.
    """
    lesson_id: Optional[str] = None
    title: Optional[str] = None
    sections: List[Section]
    
    model_config = {"extra": "allow"}

class EnhanceUnitsRequest(BaseModel): # Renamed from EnhanceRequest
    prompts: Optional[List[PromptItem]] = None
    lessons: List[LessonUnit] # Uses LessonUnit

class EnhanceUnitsResponse(BaseModel): # Renamed from EnhanceResponse
    lessons: List[LessonUnit] # Uses LessonUnit


# --- "FLAT" LESSON MODEL for /enhance/lessons ---

class LessonSimple(BaseModel):
    """Represents a simpler, flat lesson structure for /enhance/lessons."""
    # Common fields that might be present in a lesson object
    timestamp: Optional[str] = None
    file_id: Optional[str] = None
    url: Optional[str] = None
    folder_path: Optional[str] = None
    file_name: Optional[str] = None
    lesson_date: Optional[str] = None # Consider date type if strict validation needed
    iclo_slide: Optional[float] = None
    strategy_application_slide: Optional[float] = None
    learning_objective: Optional[str] = None
    standards: Optional[str] = None
    
    # Example extra fields from user's sample
    clc_element: Optional[str] = None 
    strategy_application_element: Optional[str] = None
    
    content: str # The primary content for this lesson item
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)

    model_config = {"extra": "allow"} # Allows any other fields from input

# --- REQUEST/RESPONSE MODELS FOR /enhance/lessons ---

class EnhanceLessonsRequest(BaseModel):
    prompts: Optional[List[PromptItem]] = None
    lessons: List[LessonSimple]

class EnhanceLessonsResponse(BaseModel):
    lessons: List[LessonSimple]


# --- MODELS FOR /analyze ---

class SectionInfo(BaseModel):
    sectionName: str
    pageRange: str

class AnalyzeRequestItem(BaseModel):
    file_id: str

class AnalyzeResponseItemError(BaseModel):
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

class AnalyzeResponseItemSuccess(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    sections: List[SectionInfo] # Or List[Dict[str,str]]

class BatchAnalyzeItemResult(BaseModel):
    success: bool
    result: Optional[AnalyzeResponseItemSuccess] = None
    error_info: Optional[AnalyzeResponseItemError] = None

# --- MODELS FOR /split ---

class UploadedFileInfo(BaseModel):
    sectionName: str
    uploadedDriveFileId: Optional[str] = None
    uploadedDriveFileName: Optional[str] = None

class SplitResponseItemSuccess(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    uploadedSections: List[UploadedFileInfo]

class SplitResponseItemError(BaseModel):
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

class BatchSplitItemResult(BaseModel):
    success: bool
    result: Optional[SplitResponseItemSuccess] = None
    error_info: Optional[SplitResponseItemError] = None

class BatchSplitRequest(BaseModel): # Input for /split endpoint
    files_to_split: List[AnalyzeResponseItemSuccess] # Takes output from /analyze

# --- MODELS FOR /extract ---

class ExtractedSectionDataItem(BaseModel):
    page: int
    title: Optional[str] = None
    paragraph: str

ExtractedDataDict = Dict[str, List[ExtractedSectionDataItem]] # Type alias

class ExtractRequestItem(BaseModel):
    prompt_doc_id: str
    target_file_id: str

class ExtractResponseItemSuccess(BaseModel):
    promptDriveDocId: str
    targetDriveFileId: str
    targetDriveFileName: Optional[str] = None
    targetDriveParentFolderId: Optional[str] = None
    extractedData: ExtractedDataDict

class ExtractResponseItemError(BaseModel):
    promptDriveDocId: str
    targetDriveFileId: str
    error: str
    detail: Optional[str] = None

class BatchExtractItemResult(BaseModel):
    success: bool
    result: Optional[ExtractResponseItemSuccess] = None
    error_info: Optional[ExtractResponseItemError] = None