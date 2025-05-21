import io
import fitz # PyMuPDF
from typing import List, Dict, Any, Optional, Tuple

# Define a structure for the section info we receive
class SectionInfo: # Using a simple class or Pydantic BaseModel is fine
    def __init__(self, sectionName: str, pageRange: str):
        self.sectionName = sectionName
        self.pageRange = pageRange

class PdfSplitterService:
    def split_pdf_by_sections(self, pdf_stream: io.BytesIO, sections: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Splits a PDF stream into separate PDFs based on provided section page ranges.

        Args:
            pdf_stream: An io.BytesIO stream containing the original PDF content.
            sections: A list of dictionaries, each with 'sectionName' and 'pageRange'.

        Returns:
            A list of dictionaries, each containing 'sectionName', 'fileName',
            and 'fileContent' (io.BytesIO stream) for each successfully split section.
        """
        split_files_info = []

        if not pdf_stream or pdf_stream.getbuffer().nbytes == 0:
            print("PDF stream is empty or invalid for splitting.")
            return split_files_info
        if not sections:
            print("No sections provided for splitting.")
            return split_files_info

        pdf_stream.seek(0) # Ensure stream is at the beginning

        try:
            original_doc = fitz.open(stream=pdf_stream, filetype="pdf")
            num_original_pages = original_doc.page_count
            print(f"PdfSplitter: Opened original PDF with {num_original_pages} pages.")

            for section in sections:
                section_name = section.get("sectionName", "UnknownSection").replace('/', '_').replace('\\', '_') # Sanitize name for filename
                page_range_str = section.get("pageRange")

                if not page_range_str:
                    print(f"PdfSplitter: Skipping section '{section_name}' due to missing pageRange.")
                    continue

                try:
                    # Parse the page range string
                    if '-' in page_range_str:
                        start_page_str, end_page_str = page_range_str.split('-')
                        start_page = int(start_page_str)
                        end_page = int(end_page_str)
                    else:
                        start_page = int(page_range_str)
                        end_page = start_page

                    # Convert 1-indexed pages to 0-indexed for PyMuPDF
                    start_page_0_indexed = start_page - 1
                    end_page_0_indexed = end_page - 1

                    # Validate page numbers against original document
                    if start_page_0_indexed < 0 or end_page_0_indexed >= num_original_pages or start_page_0_indexed > end_page_0_indexed:
                         print(f"PdfSplitter: Skipping section '{section_name}' due to invalid page range: {page_range_str} (Original PDF has {num_original_pages} pages).")
                         continue

                    print(f"PdfSplitter: Splitting section '{section_name}' pages {start_page}-{end_page} (0-indexed {start_page_0_indexed}-{end_page_0_indexed}).")

                    # Create a new PDF document for this section
                    section_doc = fitz.open()

                    # Copy pages from the original document to the new one
                    section_doc.insert_pdf(original_doc, from_page=start_page_0_indexed, to_page=end_page_0_indexed)

                    # Save the new document to an in-memory stream
                    section_stream = io.BytesIO()
                    # Use garbage collection and compression options
                    section_doc.save(section_stream, garbage=4, deflate=True, clean=True)
                    section_stream.seek(0) # Rewind the stream

                    section_doc.close() # Close the section document

                    # Determine the suggested filename (placeholder for original name part)
                    # The original file name will be added in the main endpoint
                    suggested_file_name = f"{section_name}.pdf" # Original name prepended later

                    split_files_info.append({
                        "sectionName": section_name, # Keep original section name for reference
                        "fileName": suggested_file_name,
                        "fileContent": section_stream
                    })
                    print(f"PdfSplitter: Successfully split and saved section '{section_name}'.")

                except ValueError:
                    print(f"PdfSplitter: Skipping section '{section_name}' due to unparseable pageRange: {page_range_str}.")
                except Exception as split_ex:
                    print(f"PdfSplitter: Error splitting section '{section_name}' with range {page_range_str}: {split_ex}")
                    # Continue with other sections

            original_doc.close() # Close the original document
            print("PdfSplitter: Finished splitting process.")
            return split_files_info

        except Exception as e:
            print(f"PdfSplitter: Critical error opening or processing original PDF: {e}")
            return [] # Return empty list on critical failure