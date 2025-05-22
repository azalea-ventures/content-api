# In models.py

from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

# --- SHARED MODELS (Used by /enhance and potentially others) ---

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

# --- NEW HIERARCHICAL MODELS FOR LESSON STRUCTURE (for /enhance) ---

class Slide(BaseModel):
    """Represents a single slide within a lesson section."""
    name: Optional[str] = None # Name or title of the slide
    content: str               # The primary instructional content of the slide
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list) # Outputs from prompts run on this slide
    
    # Allow other arbitrary fields to be passed through and returned
    model_config = {"extra": "allow"}

class Section(BaseModel):
    """Represents a section within a lesson, containing multiple slides."""
    name: Optional[str] = None # Name of the section
    slides: List[Slide]
    
    model_config = {"extra": "allow"}

class Lesson(BaseModel):
    """
    Represents a complete lesson structure, containing sections and slides.
    This is the main data unit for the /enhance endpoint's "lessons" array.
    """
    # Consider adding fields like lesson_id, title if they are part of your canonical lesson model
    lesson_id: Optional[str] = None
    title: Optional[str] = None
    sections: List[Section]
    
    model_config = {"extra": "allow"}

# --- REQUEST/RESPONSE MODELS FOR /enhance ---

class EnhanceRequest(BaseModel):
    prompts: Optional[List[PromptItem]] = None # Prompts are now optional
    lessons: List[Lesson] # Changed from lesson_data: List[LessonDataEnhance]

class EnhanceResponse(BaseModel):
    lessons: List[Lesson] # Echoes the input structure with generated_outputs populated

# --- Existing models for /analyze, /split, /extract (ensure they don't conflict) ---
# Your existing models like AnalyzeRequestItem, BatchSplitRequest, etc. remain here.
# Ensure LessonDataEnhance is fully removed or renamed if it was only for /enhance.

class SectionInfo(BaseModel): # For /analyze
    sectionName: str
    pageRange: str

class AnalyzeRequestItem(BaseModel): # For /analyze
    file_id: str

class AnalyzeResponseItemError(BaseModel): # For /analyze
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

class AnalyzeResponseItemSuccess(BaseModel): # For /analyze
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    sections: List[SectionInfo] # Or List[Dict[str,str]] if not strictly typed

class BatchAnalyzeItemResult(BaseModel): # For /analyze
    success: bool
    result: Optional[AnalyzeResponseItemSuccess] = None
    error_info: Optional[AnalyzeResponseItemError] = None

class UploadedFileInfo(BaseModel): # For /split
    sectionName: str
    uploadedDriveFileId: Optional[str] = None
    uploadedDriveFileName: Optional[str] = None

class SplitResponseItemSuccess(BaseModel): # For /split
    originalDriveFileId: str
    originalDriveFileName: Optional[str] = None
    originalDriveParentFolderId: Optional[str] = None
    uploadedSections: List[UploadedFileInfo]

class SplitResponseItemError(BaseModel): # For /split
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

class BatchSplitItemResult(BaseModel): # For /split
    success: bool
    result: Optional[SplitResponseItemSuccess] = None
    error_info: Optional[SplitResponseItemError] = None

class BatchSplitRequest(BaseModel): # For /split
    files_to_split: List[AnalyzeResponseItemSuccess]


class ExtractedSectionDataItem(BaseModel): # For /extract
    page: int
    title: Optional[str] = None
    paragraph: str

ExtractedDataDict = Dict[str, List[ExtractedSectionDataItem]] # Type alias for /extract

class ExtractRequestItem(BaseModel): # For /extract
    prompt_doc_id: str
    target_file_id: str

class ExtractResponseItemSuccess(BaseModel): # For /extract
    promptDriveDocId: str
    targetDriveFileId: str
    targetDriveFileName: Optional[str] = None
    targetDriveParentFolderId: Optional[str] = None
    extractedData: ExtractedDataDict

class ExtractResponseItemError(BaseModel): # For /extract
    promptDriveDocId: str
    targetDriveFileId: str
    error: str
    detail: Optional[str] = None

class BatchExtractItemResult(BaseModel): # For /extract
    success: bool
    result: Optional[ExtractResponseItemSuccess] = None
    error_info: Optional[ExtractResponseItemError] = None

