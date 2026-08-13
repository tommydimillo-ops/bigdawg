import base64
import logging

from agent.chat import anthropic_client, openai_client

logger = logging.getLogger(__name__)

VISION_PROMPT = "Describe what is on this screen."


def _analyze_with_openai(encoded_image):

    response = openai_client.chat.completions.create(
        model="gpt-5",
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
        model="claude-haiku-4-5-20251001",
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
    except Exception:
        logger.warning("OpenAI vision request failed, falling back to Claude", exc_info=True)
        return _analyze_with_claude(encoded_image)


if __name__ == "__main__":
    import glob
    result = analyze_image(sorted(glob.glob("screenshot_*.jpg"))[-1])
    print(result)