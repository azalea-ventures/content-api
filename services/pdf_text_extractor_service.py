# services/pdf_text_extractor_service.py

import io
import fitz # PyMuPDF
from typing import List, Dict, Any, Optional

class PdfTextExtractorService:
    def extract_text_from_pdf_per_page(self, pdf_stream: io.BytesIO) -> List[Dict[str, Any]]:
        """
        Extracts text page by page from a PDF stream using PyMuPDF (fitz).

        Args:
            pdf_stream: An io.BytesIO stream containing the PDF content.

        Returns:
            A list of dictionaries, each with 'page_number' and 'text'.
            Returns an empty list if stream is invalid or extraction fails.
        """
        page_content = []
        if not pdf_stream or pdf_stream.getbuffer().nbytes == 0:
            print("PdfTextExtractor: PDF stream is empty or invalid.")
            return page_content

        pdf_stream.seek(0) # Ensure stream is at the beginning

        try:
            doc = fitz.open(stream=pdf_stream, filetype="pdf")
            num_pages = doc.page_count
            print(f"PdfTextExtractor: Found {num_pages} pages.")

            for page_num in range(num_pages):
                try:
                    page = doc.load_page(page_num) # page_num is 0-indexed in fitz
                    text = page.get_text("text") # Extract text with "text" format

                    page_content.append({
                        "page_number": page_num + 1, # Store as 1-indexed
                        "text": text.strip() if text else ""
                    })

                except Exception as page_ex:
                     print(f"PdfTextExtractor: Error extracting text from page {page_num + 1}: {page_ex}")
                     page_content.append({
                        "page_number": page_num + 1,
                        "text": ""
                    })

            doc.close()
            print(f"PdfTextExtractor: Finished text extraction. Total pages: {len(page_content)}")
            return page_content

        except Exception as e:
            print(f"PdfTextExtractor: Critical error initializing or reading PDF: {e}")
            return []

    def extract_full_text_from_pdf(self, pdf_stream: io.BytesIO) -> str:
        """
        Extracts and concatenates all text from all pages of a PDF stream.

        Args:
            pdf_stream: An io.BytesIO stream containing the PDF content.

        Returns:
            A single string containing all extracted text, or empty string on failure.
        """
        page_content_list = self.extract_text_from_pdf_per_page(pdf_stream)
        # Concatenate text from all pages, add page break markers
        full_text = ""
        for page_info in page_content_list:
             full_text += f"--- Page {page_info['page_number']} ---\n" # Optional: keep page markers
             full_text += page_info['text'] + "\n\n"

        return full_text.strip() # Return combined text