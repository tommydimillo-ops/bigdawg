import base64
import time

from agent.chat import anthropic_client, openai_client
from agent.observability import log_event
from agent.request_context import get_current_request_id
from agent.usage import record_llm_usage
from config.settings import settings

VISION_PROMPT = "Describe what is on this screen."


def _analyze_with_openai(encoded_image):

    _call_started = time.time()
    response = openai_client.chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": VISION_PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded_image}"
                        }
                    }
                ]
            }
        ]
    )
    usage = getattr(response, "usage", None)
    if usage is not None:
        record_llm_usage(
            provider="openai", model=settings.vision_model, operation="vision",
            request_id=get_current_request_id(),
            input_tokens=getattr(usage, "prompt_tokens", 0), output_tokens=getattr(usage, "completion_tokens", 0),
            duration_seconds=time.time() - _call_started,
        )

    return response.choices[0].message.content


def _analyze_with_claude(encoded_image):

    _call_started = time.time()
    response = anthropic_client.messages.create(
        model=settings.vision_fallback_model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": encoded_image
                        }
                    },
                    {
                        "type": "text",
                        "text": VISION_PROMPT
                    }
                ]
            }
        ]
    )
    usage = getattr(response, "usage", None)
    if usage is not None:
        record_llm_usage(
            provider="anthropic", model=settings.vision_fallback_model, operation="vision",
            request_id=get_current_request_id(),
            input_tokens=getattr(usage, "input_tokens", 0), output_tokens=getattr(usage, "output_tokens", 0),
            duration_seconds=time.time() - _call_started,
        )

    return response.content[0].text


def analyze_image(image_path):

    with open(image_path, "rb") as image:
        encoded_image = base64.b64encode(image.read()).decode("utf-8")

    try:
        return _analyze_with_openai(encoded_image)
    except Exception as error:
        log_event(
            "vision_provider_failed", component="vision", level="warning",
            provider="openai", error_type=type(error).__name__,
        )
        return _analyze_with_claude(encoded_image)


if __name__ == "__main__":
    import glob
    result = analyze_image(sorted(glob.glob("screenshot_*.jpg"))[-1])
    print(result)