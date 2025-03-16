import logging
from pathlib import Path
import pathspec
import click
import ollama
from tqdm import tqdm


model = "gemma3:27b"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def load_ignore_patterns(repo_path: Path, *ignore_files):
    patterns = []
    for ignore_file in ignore_files:
        path = repo_path / ignore_file
        if path.exists():
            patterns += path.read_text().splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def should_ignore(file: Path, ignore_spec, repo_path: Path):
    relative_path = str(file.relative_to(repo_path))
    return ignore_spec.match_file(relative_path)


def list_files(repo_path: Path, pattern: str):
    ignore_spec = load_ignore_patterns(repo_path, '.gitignore', '.botignore')

    return [
        f for f in repo_path.rglob(f"*{pattern}")
        if f.is_file()
        and ".git" not in f.parts
        and not should_ignore(f, ignore_spec, repo_path)
    ]


def analyze_file(file: Path, repo_path: Path):
    try:
        content = file.read_text(errors="ignore")
    except Exception as e:
        logging.error(f"Error reading {file}: {e}")
        return None

    prompt = f"""
<system>
You're a senior code reviewer. Carefully analyze the provided code and respond strictly according to the following format:

## Major Issues
List only critical issues affecting performance, security, correctness, maintainability, or readability. If none, explicitly state "None".

| Issue                             | Description                     | Recommendation               |
|-----------------------------------|---------------------------------|------------------------------|
| (e.g., Memory Leak in line X)     | (Brief description of the issue)| (How to fix/improve it)      |

## Suggestions for Refactoring
Suggest major structural improvements or refactorings that substantially enhance code clarity, efficiency, or maintainability. If none, explicitly state "None".

| Code Area                         | Suggested Refactoring           | Benefit                      |
|-----------------------------------|---------------------------------|------------------------------|
| (e.g., Function XYZ)              | (Description of refactoring)    | (How it improves the code)   |

## Out-of-tech
List any old or outdated tech, libraries, or practices that should be updated. If none, explicitly state "None".

@examples
- use functional components over the class components
- use tailwindcss over styled-components


## Documentation
If required, if this module needs explicit documentation, please mention it here. Not all modules needs documentation. If none, explicitly state "None".
If this contributes to a business logic, or core-foundation, it should be documented.

- check if the documentation is required
- check if the documentation is sufficient
- check if the current documentation holds any issues (wrong information, outdated, etc.)


## Notes
Avoid minor stylistic or trivial issues. If the code meets good standards and no major changes are needed, you can summarize briefly by stating "The code is fine. No major issues found."
</system>

<file>
{file.relative_to(repo_path)}
<file>

<user-code>
```
{content}
```
</user-code>
"""
    try:
        # Synchronous generation without streaming
        result = ollama.generate(model, prompt=prompt, stream=False)
        response_text = result.get("response", "")
    except Exception as e:
        logging.error(f"Error analyzing {file}: {e}")
        return None

    output_file = repo_path / "analysis" / file.relative_to(repo_path)
    output_file = output_file.with_suffix(output_file.suffix + ".md")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_file.write_text(response_text)
    except Exception as e:
        logging.error(f"Error writing analysis for {file}: {e}")
        return None

    tqdm.write(f"\nFinished analysis for {file}\n")
    return response_text


@click.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--pattern", default=".ts", help="File extension pattern to process (e.g., .ts)")
@click.option("--overwrite", is_flag=True, help="Overwrite existing analysis")
def main(directory, pattern, overwrite):
    repo_path = Path(directory)
    files = list_files(repo_path, pattern)
    logging.info(
        f"Found {len(files)} files with pattern '{pattern}' to process.")
    for file in tqdm(files, desc="Processing files"):
        output_file = repo_path / "analysis" / file.relative_to(repo_path)
        output_file = output_file.with_suffix(output_file.suffix + ".md")
        if output_file.exists() and not overwrite:
            tqdm.write(f"Skipping {file} (analysis already exists)")
            continue
        analyze_file(file, repo_path)


if __name__ == "__main__":
    main()
