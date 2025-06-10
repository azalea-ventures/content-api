# helpers/enhance_helpers.py

import json
from enum import Enum
from typing import Dict, List, Optional, Tuple, Deque, Any, Union # Add Union

from config import settings
# Import new LessonSimple model
from models import PromptItem, Slide, LessonSimple, GeneratedContentItem
from services.generative_analysis_service import GenerativeAnalysisService

# Define a type alias for objects that helpers can process
ProcessableContentItem = Union[Slide, LessonSimple]

MAX_API_RETRIES_PER_TASK = settings.max_api_retries

class PromptConstructionStatus(Enum):
    SUCCESS = "SUCCESS"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"

def _construct_full_prompt(
    prompt_item: PromptItem,
    current_content_item_state: ProcessableContentItem, # Use the Union type
    all_request_prompts: List[PromptItem]
) -> Tuple[PromptConstructionStatus, Optional[str]]:
    full_prompt_parts = [prompt_item.prompt_template.strip()]
    all_dependencies_met = True
    known_prompt_names_in_request = {p.prompt_name for p in all_request_prompts}

    for prop_key_to_append in prompt_item.lesson_properties_to_append:
        value_to_append: Optional[str] = None
        property_display_name = prop_key_to_append.replace("_", " ").title()

        if prop_key_to_append == "content":
            value_to_append = current_content_item_state.content # Accesses .content attribute
            property_display_name = "Content" # Generic, can be slide or lesson content
        elif prop_key_to_append in known_prompt_names_in_request:
            dependency_found_in_outputs = False
            # Accesses .generated_outputs attribute
            for gen_output in current_content_item_state.generated_outputs:
                if gen_output.prompt_name == prop_key_to_append:
                    dependency_found_in_outputs = True
                    if gen_output.status == "SUCCESS" and gen_output.output is not None:
                        value_to_append = gen_output.output
                        property_display_name = f"Output from '{prop_key_to_append}'"
                    else:
                        all_dependencies_met = False
                    break
            if not dependency_found_in_outputs and all_dependencies_met:
                all_dependencies_met = False
        # Accesses .model_extra attribute
        elif current_content_item_state.model_extra and prop_key_to_append in current_content_item_state.model_extra:
            try:
                value_to_append = str(current_content_item_state.model_extra[prop_key_to_append])
            except Exception as e:
                item_name_for_log = getattr(current_content_item_state, 'name', 'Unnamed Item')
                print(f"Warning: Could not convert extra field '{prop_key_to_append}' for item '{item_name_for_log}' to string for prompt '{prompt_item.prompt_name}': {e}")
        else:
            item_name_for_log = getattr(current_content_item_state, 'name', 'Unnamed Item')
            print(f"Warning: Property '{prop_key_to_append}' for item '{item_name_for_log}' requested by prompt '{prompt_item.prompt_name}' is unresolvable. Not appending.")

        if not all_dependencies_met:
            item_name_for_log = getattr(current_content_item_state, 'name', 'Unnamed Item')
            print(f"Info: Dependency '{prop_key_to_append}' not met for prompt '{prompt_item.prompt_name}' on item '{item_name_for_log}'. Deferring.")
            break 

        if value_to_append is not None:
            full_prompt_parts.append(f"---\n{property_display_name}:\n{value_to_append.strip()}")
            
    if not all_dependencies_met:
        return PromptConstructionStatus.MISSING_DEPENDENCY, None
    
    return PromptConstructionStatus.SUCCESS, "\n".join(full_prompt_parts)

def _get_prompt_status(content_item: ProcessableContentItem, prompt_name: str) -> Optional[str]:
    for item in content_item.generated_outputs:
        if item.prompt_name == prompt_name:
            return item.status
    return None

async def _execute_api_call_for_prompt(
    gemini_service: GenerativeAnalysisService,
    # We now pass the actual item object to update, instead of indices
    item_to_process: ProcessableContentItem, # This will be a Slide or LessonSimple
    item_identifier_for_log: str, # A string for logging (e.g., "Slide 'X'" or "Lesson 'Y'")
    prompt_item: PromptItem,
    full_prompt_text: str,
    api_attempt_count: int,
    # Queue item will need to reflect what's needed to re-process this specific item
    # For /enhance/units: (lesson_idx, section_idx, slide_idx, prompt_item, full_prompt_text, api_attempt_count)
    # For /enhance/lessons: (lesson_simple_idx, prompt_item, full_prompt_text, api_attempt_count)
    # We'll make api_retry_queue store a more generic "task descriptor" or pass more args
    api_retry_queue: Deque,
    # Additional context for re-queuing if needed (e.g. indices)
    queue_context: Dict[str, Any] 
) -> bool:
    prompt_name = prompt_item.prompt_name
    print(f"API Call: Prompt '{prompt_name}', Item '{item_identifier_for_log}', API Attempt {api_attempt_count + 1}.")
    
    status, api_output_data = await gemini_service.generate_text(full_prompt_text) 
    
    # item_to_process is already the direct object to update
    output_item, _ = get_or_create_output_item(item_to_process, prompt_name)

    output_item.status = status 
    output_item.output = api_output_data

    if status == "SUCCESS":
        print(f"SUCCESS: Prompt '{prompt_name}', Item '{item_identifier_for_log}'.")
        return False
    elif status == "RATE_LIMIT" or status == "ERROR_API":
        print(f"{status}: Prompt '{prompt_name}', Item '{item_identifier_for_log}'. Error: {str(api_output_data)[:200]}")
        if api_attempt_count + 1 < settings.max_api_retries:
            print(f"Re-queuing for API retry: '{prompt_name}', Item '{item_identifier_for_log}' (API attempt {api_attempt_count + 2}).")
            # Re-queue with necessary context
            retry_task = {**queue_context, "prompt_item": prompt_item, "full_prompt_text": full_prompt_text, "api_attempt_count": api_attempt_count + 1}
            api_retry_queue.append(retry_task)
            output_item.status = "PENDING_API_RETRY"
        else:
            err_msg = f"Max API retries ({settings.max_api_retries}) for '{prompt_name}', Item '{item_identifier_for_log}'. Last: [{status}] {str(api_output_data)[:200]}"
            print(err_msg)
        return True
    else: 
        err_msg = f"Permanent Error for '{prompt_name}', Item '{item_identifier_for_log}': [{status}] {str(api_output_data)[:200]}"
        print(err_msg)
        return False

def get_or_create_output_item(content_item: ProcessableContentItem, prompt_name: str) -> Tuple[GeneratedContentItem, bool]:
    for item in content_item.generated_outputs:
        if item.prompt_name == prompt_name:
            return item, False
    new_item = GeneratedContentItem(prompt_name=prompt_name)
    content_item.generated_outputs.append(new_item)
    return new_item, True