import os

from documents.pdf_reader import read_pdf

MAX_DOCUMENT_TEXT = 6000

TEXT_EXTENSIONS = {".txt", ".md"}


def read_document(file_path):
    file_path = os.path.expanduser(file_path.strip())

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
