# helpers/enhance_helpers.py

from enum import Enum
from typing import Any, List, Optional, Tuple, Deque

# Import new models
from models import PromptItem, Slide, GeneratedContentItem # Slide is new here
from services.generative_analysis_service import GenerativeAnalysisService

MAX_API_RETRIES_PER_TASK = 3

class PromptConstructionStatus(Enum):
    SUCCESS = "SUCCESS"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"

def _construct_full_prompt(
    prompt_item: PromptItem,
    current_slide_state: Slide, # Changed from LessonDataEnhance
    all_request_prompts: List[PromptItem]
) -> Tuple[PromptConstructionStatus, Optional[str]]:
    full_prompt_parts = [prompt_item.prompt_template.strip()]
    all_dependencies_met = True
    known_prompt_names_in_request = {p.prompt_name for p in all_request_prompts}

    for prop_key_to_append in prompt_item.lesson_properties_to_append:
        value_to_append: Optional[str] = None
        property_display_name = prop_key_to_append.replace("_", " ").title()

        if prop_key_to_append == "content": # This now refers to Slide.content
            value_to_append = current_slide_state.content
            property_display_name = "Slide Content" # More specific display name
        elif prop_key_to_append in known_prompt_names_in_request:
            dependency_found_in_outputs = False
            # Check generated_outputs OF THE CURRENT SLIDE
            for gen_output in current_slide_state.generated_outputs:
                if gen_output.prompt_name == prop_key_to_append:
                    dependency_found_in_outputs = True
                    if gen_output.status == "SUCCESS" and gen_output.output is not None:
                        value_to_append = gen_output.output
                        property_display_name = f"Output from '{prop_key_to_append}'"
                    else:
                        print(f"Info: Prompt '{prompt_item.prompt_name}' needs '{prop_key_to_append}', but its status is '{gen_output.status}' or output is missing. Deferring.")
                        all_dependencies_met = False
                    break
            if not dependency_found_in_outputs and all_dependencies_met:
                print(f"Info: Prompt '{prompt_item.prompt_name}' needs '{prop_key_to_append}', which has not recorded output on this slide yet. Deferring.")
                all_dependencies_met = False
        # Check for 'extra' fields on the SLIDE object
        elif current_slide_state.model_extra and prop_key_to_append in current_slide_state.model_extra:
            try:
                value_to_append = str(current_slide_state.model_extra[prop_key_to_append])
            except Exception as e:
                print(f"Warning: Could not convert extra slide field '{prop_key_to_append}' to string for prompt '{prompt_item.prompt_name}': {e}")
        else:
            slide_name_for_log = current_slide_state.name or "Unnamed Slide"
            print(f"Warning: Property '{prop_key_to_append}' for slide '{slide_name_for_log}' requested by prompt '{prompt_item.prompt_name}' is unresolvable. Not appending.")

        if not all_dependencies_met:
            break 

        if value_to_append is not None:
            full_prompt_parts.append(f"---\n{property_display_name}:\n{value_to_append.strip()}")
            
    if not all_dependencies_met:
        return PromptConstructionStatus.MISSING_DEPENDENCY, None
    
    return PromptConstructionStatus.SUCCESS, "\n".join(full_prompt_parts)

def _get_prompt_status(slide: Slide, prompt_name: str) -> Optional[str]: # Takes Slide
    for item in slide.generated_outputs:
        if item.prompt_name == prompt_name:
            return item.status
    return None

async def _execute_api_call_for_prompt(
    gemini_service: GenerativeAnalysisService,
    # Indices to locate the specific slide in the main list of lessons
    lesson_idx: int,
    section_idx: int,
    slide_idx: int,
    prompt_item: PromptItem,
    full_prompt_text: str,
    api_attempt_count: int,
    # enhanced_lessons_output is List[Lesson], where each Lesson has sections and slides
    enhanced_lessons_output: List[Any], # Use Any for now, will be List[Lesson]
    api_retry_queue: Deque
) -> bool:
    prompt_name = prompt_item.prompt_name
    slide_name_for_log = enhanced_lessons_output[lesson_idx].sections[section_idx].slides[slide_idx].name or f"L{lesson_idx}S{section_idx}Sl{slide_idx}"
    print(f"API Call: Prompt '{prompt_name}', Slide '{slide_name_for_log}', API Attempt {api_attempt_count + 1}.")
    
    status, result_text_from_api = await gemini_service.generate_text(full_prompt_text)
    
    # Locate the specific slide to update
    slide_to_update = enhanced_lessons_output[lesson_idx].sections[section_idx].slides[slide_idx]
    output_item, _ = get_or_create_output_item(slide_to_update, prompt_name) # Pass slide object

    output_item.status = status

    if status == "SUCCESS":
        output_item.output = result_text_from_api
        print(f"SUCCESS: Prompt '{prompt_name}', Slide '{slide_name_for_log}'.")
        return False
    elif status == "RATE_LIMIT" or status == "ERROR_API":
        print(f"{status}: Prompt '{prompt_name}', Slide '{slide_name_for_log}'.")
        if api_attempt_count + 1 < MAX_API_RETRIES_PER_TASK:
            print(f"Re-queuing for API retry: '{prompt_name}', Slide '{slide_name_for_log}' (API attempt {api_attempt_count + 2}).")
            # API retry queue now needs all indices
            api_retry_queue.append((lesson_idx, section_idx, slide_idx, prompt_item, full_prompt_text, api_attempt_count + 1))
            output_item.output = f"Pending API retry ({status} on attempt {api_attempt_count + 1}): {result_text_from_api}"
            output_item.status = "PENDING_API_RETRY"
        else:
            err_msg = f"Max API retries ({MAX_API_RETRIES_PER_TASK}) for '{prompt_name}', Slide '{slide_name_for_log}'. Last: [{status}] {result_text_from_api}"
            print(err_msg)
            output_item.output = err_msg
        return True
    else: 
        err_msg = f"Permanent Error for '{prompt_name}', Slide '{slide_name_for_log}': [{status}] {result_text_from_api}"
        print(err_msg)
        output_item.output = err_msg
        return False

def get_or_create_output_item(slide: Slide, prompt_name: str) -> Tuple[GeneratedContentItem, bool]: # Takes Slide
    for item in slide.generated_outputs:
        if item.prompt_name == prompt_name:
            return item, False
    new_item = GeneratedContentItem(prompt_name=prompt_name)
    slide.generated_outputs.append(new_item)
    return new_item, True