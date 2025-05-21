# models.py

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Union

# --- Pydantic Models ---

# Shared model for section info (used in /analyze output)
class SectionInfo(BaseModel):
    sectionName: str
    pageRange: str

# For /analyze batch input
class AnalyzeRequestItem(BaseModel):
    file_id: str

# For /analyze batch response item - success case
class AnalyzeResponseItemSuccess(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: str
    originalDriveParentFolderId: str
    sections: List[Dict[str, str]] # List of section dictionaries

# For /analyze batch response item - failure case
class AnalyzeResponseItemError(BaseModel):
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

# For /analyze batch response (each item is either success or error)
class BatchAnalyzeItemResult(BaseModel):
     success: bool
     result: Optional[AnalyzeResponseItemSuccess] = None
     error_info: Optional[AnalyzeResponseItemError] = None

# Shared model for info about uploaded split files
class UploadedFileInfo(BaseModel):
    sectionName: str
    uploadedDriveFileId: Optional[str]
    uploadedDriveFileName: str

# For /split batch input
class BatchSplitRequest(BaseModel):
    files_to_split: List[AnalyzeResponseItemSuccess] # List of successful analysis results

# For /split batch response item - success case
class SplitResponseItemSuccess(BaseModel):
    originalDriveFileId: str
    originalDriveFileName: str
    originalDriveParentFolderId: str
    uploadedSections: List[UploadedFileInfo]

# For /split batch response item - failure case
class SplitResponseItemError(BaseModel):
    originalDriveFileId: str
    error: str
    detail: Optional[str] = None

# For /split batch response (each item is either success or error)
class BatchSplitItemResult(BaseModel):
     success: bool
     result: Optional[SplitResponseItemSuccess] = None
     error_info: Optional[SplitResponseItemError] = None

# --- NEW Models for /extract endpoint ---

# Model for a single item in the /extract batch request
class ExtractRequestItem(BaseModel):
    prompt_doc_id: str # Google Drive File ID of the Doc containing the prompt
    target_file_id: str # Google Drive File ID of the PDF to extract data FROM

# Model for the structure of extracted data per section
# e.g., {"page": 9, "title": "...", "paragraph": "..."}
class ExtractedSectionDataItem(BaseModel):
    page: int
    title: Optional[str] = None # Titles might not always be present for every paragraph
    paragraph: str

# Model for the structure of extracted data for a whole file
# e.g., {"Section Name 1": [ExtractedSectionDataItem, ...], "Section Name 2": [...]}
# This is a dictionary where keys are section names (str) and values are lists of data items
ExtractedDataDict = Dict[str, List[ExtractedSectionDataItem]]


# For /extract batch response item - success case
class ExtractResponseItemSuccess(BaseModel):
    promptDriveDocId: str # Echo back the prompt doc ID
    targetDriveFileId: str # Echo back the target file ID
    targetDriveFileName: str # Include target file name
    targetDriveParentFolderId: Optional[str] # Include target parent folder ID
    extractedData: ExtractedDataDict # The structured data extracted by Gemini


# For /extract batch response item - failure case
class ExtractResponseItemError(BaseModel):
    promptDriveDocId: str
    targetDriveFileId: str
    error: str
    detail: Optional[str] = None


# For /extract batch response (each item is either success or error)
class BatchExtractItemResult(BaseModel):
     success: bool
     result: Optional[ExtractResponseItemSuccess] = None
     error_info: Optional[ExtractResponseItemError] = None

class PromptItem(BaseModel):
    prompt_name: str
    prompt_template: str
    lesson_properties_to_append: List[str] = Field(default_factory=list)

class GeneratedContentItem(BaseModel):
    """Represents a piece of content generated for a lesson based on a specific prompt."""
    prompt_name: str
    output: Optional[str] = None  # Renamed from generated_text
    status: Optional[str] = None

class LessonDataEnhance(BaseModel):
    content: str
    generated_outputs: List[GeneratedContentItem] = Field(default_factory=list)
    model_config = {"extra": "allow"}

class EnhanceRequest(BaseModel):
    prompts: List[PromptItem]
    lesson_data: List[LessonDataEnhance]

class EnhanceResponse(BaseModel):
    lesson_data: List[LessonDataEnhance]
