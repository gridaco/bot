import logging
from pathlib import Path
import pathspec
import click
import ollama
from rich.console import Console
from rich.live import Live
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn, MofNCompleteColumn
)
from rich.logging import RichHandler
from rich.layout import Layout
from rich.panel import Panel
from collections import deque

model = "gemma3:27b"

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)

logger = logging.getLogger("rich")


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


def mkprompt(root: Path, file: Path, content: str):
    return f"""
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
{file.relative_to(root)}
<file>

<user-code>
```
{content}
```
</user-code>
"""


def analyze_file(file: Path, repo_path: Path):
    try:
        content = file.read_text(errors="ignore")
    except Exception as e:
        logger.error(f"Error reading {file}: {e}")
        return

    prompt = mkprompt(repo_path, file, content)

    output_file = repo_path / "analysis" / file.relative_to(repo_path)
    output_file = output_file.with_suffix(output_file.suffix + ".md")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    analysis = ""

    try:
        stream = ollama.generate(model=model, prompt=prompt, stream=True)
        for chunk in stream:
            part = chunk.get('response', '')
            analysis += part
            yield part
    except Exception as e:
        logger.error(f"Error analyzing {file}: {e}")
        return

    try:
        output_file.write_text(analysis)
    except Exception as e:
        logger.error(f"Error writing analysis to {output_file}: {e}")
        return


def process_analysis(file: Path, repo_path: Path, stream_layout):
    buffer = [""]
    for token in analyze_file(file, repo_path):
        if '\n' in token:
            lines = token.split('\n')
            buffer[-1] += lines[0]
            buffer.extend(lines[1:])
        else:
            buffer[-1] += token

        # update immediately, let Rich panel handle overflow visually
        stream_layout.update(
            Panel('\n'.join(buffer[-2:]), title=f"AI Streaming: {file.name}")
        )


@click.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--pattern", default=".ts", help="File extension pattern to process (e.g., .ts)")
@click.option("--overwrite", is_flag=True, help="Overwrite existing analysis")
def main(directory, pattern, overwrite):
    repo_path = Path(directory)
    files = list_files(repo_path, pattern)

    console = Console()
    log_buffer = deque(maxlen=100)

    class LayoutLogHandler(logging.Handler):
        def __init__(self, layout):
            super().__init__()
            self.layout = layout
            self.setFormatter(logging.Formatter("%(message)s"))

        def emit(self, record):
            msg = self.format(record)
            log_buffer.append(msg)
            self.layout["log"].update(
                Panel("\n".join(log_buffer), title="Logs"))

    layout = Layout()
    layout.split(
        Layout(name="progress", size=3),
        Layout(name="log", ratio=1),
        Layout(name="stream", size=10)
    )

    progress = Progress(
        SpinnerColumn(),
        MofNCompleteColumn(),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    task = progress.add_task("[cyan]Analyzing files...", total=len(files))

    layout["progress"].update(progress)
    layout["log"].update(Panel("", title="Logs"))
    layout["stream"].update(Panel("", title="AI Streaming Output"))

    # Pass layout explicitly here:
    logger.handlers = [LayoutLogHandler(layout)]
    logger.setLevel(logging.INFO)

    with Live(layout, refresh_per_second=10, console=console):
        logger.info(
            f"Found {len(files)} files with pattern '{pattern}' to process.")
        for file in files:
            output_file = repo_path / "analysis" / file.relative_to(repo_path)
            output_file = output_file.with_suffix(output_file.suffix + ".md")
            if output_file.exists() and not overwrite:
                logger.info(
                    f"⏭️ Skipping {file.relative_to(repo_path)} (analysis exists)")
                progress.advance(task)
                continue

            process_analysis(file, repo_path, layout["stream"])
            logger.info(
                f"✅ Completed analysis for {file.relative_to(repo_path)}")
            progress.advance(task)

    logger.info("[green bold]All files processed successfully![/green bold]")


if __name__ == "__main__":
    main()
