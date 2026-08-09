print("=" * 50)
print("🤖 CampusPilot AI")
print("=" * 50)

while True:
    command = input("\nWhat would you like me to do? ")

    if command.lower() in ["exit", "quit"]:
        print("Goodbye!")
        break

    print(f"\nI heard: {command}")
    print("Right now I'm a simple assistant, but we're going to make me much smarter!")

