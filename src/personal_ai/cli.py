from personal_ai.ollama_client import ChatMessage, OllamaClient

MODEL = "qwen2.5-coder-32k"


def main() -> None:
    with OllamaClient(model=MODEL) as client:
        response = client.chat(
            [
                ChatMessage(
                    role="user",
                    content="Reply with exactly: My personal AI is connected.",
                )
            ]
        )

    print(response.content)


if __name__ == "__main__":
    main()
