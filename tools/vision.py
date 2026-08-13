import base64

from agent.chat import anthropic_client, openai_client
from agent.observability import log_event
from config.settings import settings

VISION_PROMPT = "Describe what is on this screen."


def _analyze_with_openai(encoded_image):

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

    return response.choices[0].message.content


def _analyze_with_claude(encoded_image):

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