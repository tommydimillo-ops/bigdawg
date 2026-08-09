from anthropic import Anthropic
from dotenv import load_dotenv
import os

load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

response = client.messages.create(
    model="claude-sonnet-4-0",
    max_tokens=200,
    messages=[
        {"role": "user", "content": "Say hello from CampusPilot"}
    ]
)

print(response.content[0].text)