# helpers/enhance_helpers.py

from enum import Enum
from typing import List, Optional, Tuple, Deque # Deque for type hint

from models import PromptItem, LessonDataEnhance, GeneratedContentItem
# Import service class types for type hinting
from services.generative_analysis_service import GenerativeAnalysisService

# --- CONSTANTS specific to enhance helpers ---
MAX_API_RETRIES_PER_TASK = 3 # Used by _execute_api_call_for_prompt

class PromptConstructionStatus(Enum):
    SUCCESS = "SUCCESS"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"

def _construct_full_prompt(
    prompt_item: PromptItem,
    current_lesson_state: LessonDataEnhance,
    all_request_prompts: List[PromptItem]
) -> Tuple[PromptConstructionStatus, Optional[str]]:
    full_prompt_parts = [prompt_item.prompt_template.strip()]
    all_dependencies_met = True
    known_prompt_names_in_request = {p.prompt_name for p in all_request_prompts}

    for prop_key_to_append in prompt_item.lesson_properties_to_append:
        value_to_append: Optional[str] = None
        property_display_name = prop_key_to_append.replace("_", " ").title()

        if prop_key_to_append == "content":
            value_to_append = current_lesson_state.content
            property_display_name = "Content"
        elif prop_key_to_append in known_prompt_names_in_request:
            dependency_found_in_outputs = False
            for gen_output in current_lesson_state.generated_outputs:
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
                print(f"Info: Prompt '{prompt_item.prompt_name}' needs '{prop_key_to_append}', which has not recorded output yet. Deferring.")
                all_dependencies_met = False
        elif current_lesson_state.model_extra and prop_key_to_append in current_lesson_state.model_extra:
            try:
                value_to_append = str(current_lesson_state.model_extra[prop_key_to_append])
            except Exception as e:
                print(f"Warning: Could not convert extra field '{prop_key_to_append}' to string for prompt '{prompt_item.prompt_name}': {e}")
        else:
            print(f"Warning: Property '{prop_key_to_append}' requested by prompt '{prompt_item.prompt_name}' is unresolvable. Not appending.")

        if not all_dependencies_met:
            break 

        if value_to_append is not None:
            full_prompt_parts.append(f"---\n{property_display_name}:\n{value_to_append.strip()}")
            
    if not all_dependencies_met:
        return PromptConstructionStatus.MISSING_DEPENDENCY, None
    
    return PromptConstructionStatus.SUCCESS, "\n".join(full_prompt_parts)

def _get_prompt_status(lesson: LessonDataEnhance, prompt_name: str) -> Optional[str]:
    for item in lesson.generated_outputs:
        if item.prompt_name == prompt_name:
            return item.status
    return None

async def _execute_api_call_for_prompt(
    gemini_service: GenerativeAnalysisService,
    lesson_idx: int,
    prompt_item: PromptItem,
    full_prompt_text: str,
    api_attempt_count: int,
    enhanced_lessons_output: List[LessonDataEnhance],
    api_retry_queue: Deque # Use Deque from typing for the type hint
) -> bool:
    prompt_name = prompt_item.prompt_name
    print(f"API Call: Prompt '{prompt_name}', Lesson {lesson_idx}, API Attempt {api_attempt_count + 1}.")
    
    status, result_text_from_api = await gemini_service.generate_text(full_prompt_text)
    lesson_to_update = enhanced_lessons_output[lesson_idx]
    output_item, _ = get_or_create_output_item(lesson_to_update, prompt_name)

    output_item.status = status

    if status == "SUCCESS":
        output_item.output = result_text_from_api
        print(f"SUCCESS: Prompt '{prompt_name}', Lesson {lesson_idx}.")
        return False
    elif status == "RATE_LIMIT" or status == "ERROR_API": # Includes retriable API errors
        print(f"{status}: Prompt '{prompt_name}', Lesson {lesson_idx}.")
        if api_attempt_count + 1 < MAX_API_RETRIES_PER_TASK: # Use constant defined in this module
            print(f"Re-queuing for API retry: '{prompt_name}', Lesson {lesson_idx} (API attempt {api_attempt_count + 2}).")
            api_retry_queue.append((lesson_idx, prompt_item, full_prompt_text, api_attempt_count + 1))
            output_item.output = f"Pending API retry ({status} on attempt {api_attempt_count + 1}): {result_text_from_api}"
            output_item.status = "PENDING_API_RETRY"
        else:
            err_msg = f"Max API retries ({MAX_API_RETRIES_PER_TASK}) for '{prompt_name}', Lesson {lesson_idx}. Last: [{status}] {result_text_from_api}"
            print(err_msg)
            output_item.output = err_msg
        return True
    else: 
        # Permanent errors like ERROR_BLOCKED, ERROR_EMPTY, ERROR_INVALID_JSON, ERROR_API_CANT_GENERATE_JSON
        err_msg = f"Permanent Error for '{prompt_name}', Lesson {lesson_idx}: [{status}] {result_text_from_api}"
        print(err_msg)
        output_item.output = err_msg
        return False

def get_or_create_output_item(lesson: LessonDataEnhance, prompt_name: str) -> Tuple[GeneratedContentItem, bool]:
    for item in lesson.generated_outputs:
        if item.prompt_name == prompt_name:
            return item, False
    new_item = GeneratedContentItem(prompt_name=prompt_name)
    lesson.generated_outputs.append(new_item)
    return new_item, True