import os

from agent.secret_paths import refuse_secret_read
from documents.pdf_reader import read_pdf

MAX_DOCUMENT_TEXT = 6000

TEXT_EXTENSIONS = {".txt", ".md"}


def read_document(file_path):
    file_path = os.path.expanduser(file_path.strip())

    # First, before even the existence check: this is the one function both
    # the registered read_document tool and ResearchAgent's own copy of it
    # call, so guarding here covers both -- and refusing before checking
    # existence means a refusal cannot be used to probe whether a secret
    # file exists. Until plan-b8 the only thing keeping `.env` out of here
    # was the extension whitelist below, by accident: `credentials.txt`,
    # `github_token.md` and `.env.txt` were all readable.
    refusal = refuse_secret_read(file_path, reader="read_document")
    if refusal:
        return refusal

    if not os.path.exists(file_path):
        return f"Could not find a file at {file_path}"

    extension = os.path.splitext(file_path)[1].lower()

    try:
        if extension == ".pdf":
            text = read_pdf(file_path)
        elif extension in TEXT_EXTENSIONS:
            with open(file_path, "r", errors="ignore") as file:
                text = file.read()
        else:
            return (
                f"Unsupported file type: {extension or 'unknown'}. "
                "Only PDF, .txt, and .md files are supported."
            )
    except Exception as error:
        return f"Could not read {file_path}: {error}"

    if not text.strip():
        return f"{file_path} appears to be empty or contains no extractable text."

    return text[:MAX_DOCUMENT_TEXT]


if __name__ == "__main__":
    import sys

    print(read_document(sys.argv[1]))
