from dotenv import load_dotenv
import os

load_dotenv()

def choose_ai(task):
    task = task.lower()

    if "pdf" in task or "document" in task or "summarize" in task:
        return "claude"

    if "code" in task or "program" in task or "build" in task:
        return "openai"

    return "openai"


if __name__ == "__main__":
    request = input("What do you need help with? ")

    ai = choose_ai(request)

    print("CampusPilot chose:", ai)
    