from ai.router import choose_ai
from database.memory import save_memory, get_memory


print("CampusPilot starting...")

memory = get_memory()

if "student_name" not in memory:
    name = input("What's your name? ")
    save_memory("student_name", name)
else:
    name = memory["student_name"]

print(f"Welcome back, {name}!")

question = input("What do you need help with? ")

chosen_ai = choose_ai(question)

if chosen_ai == "claude":
    from ai.claude_agent import ask_claude
    answer = ask_claude(question)

else:
    from ai.openai_agent import ask_openai
    answer = ask_openai(question)

print("\nCampusPilot:")
print(answer)