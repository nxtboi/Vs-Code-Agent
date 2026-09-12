"""Confirmation-first Gemini agent for local PowerShell tasks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import atexit
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from google import genai
from google.genai import types

MAX_OUTPUT = 12_000
COMMAND_TIMEOUT_SECONDS = 5
GIT_TIMEOUT_SECONDS = 30
GEMINI_RETRY_ATTEMPTS = 3
GEMINI_RETRY_DELAY_SECONDS = 1
VOICE_PHRASE_LIMIT_SECONDS = 15
VOICE_SAMPLE_RATE = 16_000
BROWSER_OUTPUT_LIMIT = 12_000
BROWSER_PROFILE_DIR = Path(__file__).with_name(".browser-profile")
BROWSER_RUNTIME: tuple[Any, Any] | None = None

SYSTEM_INSTRUCTION = """You are a careful local computer assistant.
Use run_powershell only when the user asks for a system action or when a command is needed to inspect the system.
Use browser_action when the user asks you to browse, inspect, or interact with a web page.
Keep commands focused and explain what they do in the function arguments.
Never request commands that delete data, change security settings, access credentials, or make network changes.
Use commit_github_changes only when the user explicitly asks to commit changes to GitHub. Require a specific repository path, commit message, and file list. Set push to true only when the user explicitly asks to push or publish the commit. Never access or request GitHub credentials.
Use open_default_browser only when the user asks to use their default browser. It can open public http or https URLs, but it cannot read pages or upload files. For GitHub uploads, use commit_github_changes when the user explicitly requests a commit or push.
Never ask the user to share passwords, API keys, or other secrets in chat. Do not submit sensitive information into web forms.
Prefer read-only inspection commands. Ask the user to perform sensitive actions manually instead.
When the user explicitly asks to close an application such as VS Code, you may use a graceful CloseMainWindow PowerShell command. Explain that unsaved changes may trigger an application prompt, and never use -Force unless the user explicitly requests a forced close.
"""

FUNCTION_DECLARATIONS = [
    {
        "type": "function",
        "name": "run_powershell",
        "description": (
            "Run one non-interactive PowerShell command locally after the user confirms it. "
            "Use for safe inspection or routine automation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The PowerShell command to run"},
                "purpose": {"type": "string", "description": "A short explanation of why it is needed"},
            },
            "required": ["command", "purpose"],
        },
    },
    {
        "type": "function",
        "name": "open_default_browser",
        "description": "Open a public HTTP or HTTPS URL in the user's operating system default browser after confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Public HTTP or HTTPS URL to open"},
                "purpose": {"type": "string", "description": "A short explanation of why it is needed"},
            },
            "required": ["url", "purpose"],
        },
    },
    {
        "type": "function",
        "name": "commit_github_changes",
        "description": (
            "Create a Git commit from explicitly listed files in a local repository and optionally push it to GitHub "
            "after the user confirms the complete operation. Use only for an explicit user request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository_path": {"type": "string", "description": "Local path to the Git repository"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit repository-relative file paths to stage",
                },
                "commit_message": {"type": "string", "description": "Commit message"},
                "push": {"type": "boolean", "description": "Push the new commit to the configured GitHub remote"},
                "branch": {"type": "string", "description": "Optional branch name to push"},
                "purpose": {"type": "string", "description": "A short explanation of why it is needed"},
            },
            "required": ["repository_path", "files", "commit_message", "push", "purpose"],
        },
    },
    {
        "type": "function",
        "name": "browser_action",
        "description": (
            "Control a visible local Chromium browser after the user confirms the action. "
            "The browser keeps its local login state between runs. Use read to inspect the current page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "read", "click", "fill", "press"],
                    "description": "The browser operation to perform",
                },
                "url": {"type": "string", "description": "URL for navigate"},
                "selector": {"type": "string", "description": "CSS selector for click, fill, or press"},
                "text": {"type": "string", "description": "Text for fill or key name for press"},
                "purpose": {"type": "string", "description": "A short explanation of why it is needed"},
            },
            "required": ["action", "purpose"],
        },
    },
]


def open_default_browser(arguments: dict[str, Any], dry_run: bool = False) -> str:
    """Open a validated public URL using the operating system's default browser."""
    url = str(arguments.get("url", "")).strip()
    purpose = str(arguments.get("purpose", "Open the requested web page"))
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return "Only complete http:// or https:// URLs can be opened in the default browser."
    if any(character in url for character in "\r\n"):
        return "The URL cannot contain line breaks."

    print(f"\nProposed default-browser action: {purpose}\nURL: {url}")
    if dry_run:
        return "Dry run: URL was displayed but not opened."
    answer = input("Open this URL in the default browser? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        return "The user declined to open the URL."
    try:
        opened = webbrowser.open(url, new=2)
    except OSError as error:
        return f"Could not open the default browser: {error}"
    return f"Default browser launch requested for {url}." if opened else f"Could not open {url} in the default browser."


def commit_github_changes(arguments: dict[str, Any], dry_run: bool = False) -> str:
    """Commit explicitly selected files and optionally push the current branch."""
    repository_text = str(arguments.get("repository_path", "")).strip()
    if not repository_text:
        return "A repository path is required."
    repository_path = Path(repository_text).expanduser().resolve()
    files = [str(file).strip() for file in arguments.get("files", []) if str(file).strip()]
    commit_message = str(arguments.get("commit_message", "")).strip()
    push = bool(arguments.get("push", False))
    branch = str(arguments.get("branch", "")).strip()
    purpose = str(arguments.get("purpose", "Commit requested changes"))

    if not repository_path.is_dir():
        return f"Repository path does not exist: {repository_path}"
    if not files:
        return "At least one explicit repository-relative file is required."
    if not commit_message:
        return "A non-empty commit message is required."
    if any(Path(file).is_absolute() or ".." in Path(file).parts for file in files):
        return "Files must be repository-relative paths without parent-directory segments."
    if branch and (branch.startswith("-") or any(character in branch for character in "\r\n")):
        return "Branch name cannot start with '-' or contain line breaks."

    commands = [["git", "add", "--", *files], ["git", "commit", "-m", commit_message]]
    if push:
        commands.append(["git", "push", "origin", branch] if branch else ["git", "push"])
    command_text = "\n".join(
        " ".join(f'\"{part}\"' if " " in part else part for part in command) for command in commands
    )
    print(f"\nProposed GitHub commit: {purpose}\nRepository: {repository_path}\nCommands:\n{command_text}")
    if dry_run:
        return "Dry run: GitHub commit was displayed but not executed."

    answer = input("Run this GitHub commit operation? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        return "The user declined to run this GitHub commit operation."

    results = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=repository_path,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Git command timed out after {GIT_TIMEOUT_SECONDS} seconds: {command[1]}"
        except OSError as error:
            return f"Could not start Git: {error}"

        output = (completed.stdout + completed.stderr).strip()
        results.append({"command": command[1], "exit_code": completed.returncode, "output": output})
        if completed.returncode != 0:
            return json.dumps({"repository": str(repository_path), "results": results})

    return json.dumps({"repository": str(repository_path), "results": results})


def run_powershell(command: str, purpose: str, dry_run: bool = False) -> str:
    """Execute a command only after interactive confirmation."""
    print(f"\nProposed action: {purpose}\nCommand: {command}")
    if dry_run:
        return "Dry run: command was displayed but not executed."

    answer = input("Run this command? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        return "The user declined to run this command."

    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {COMMAND_TIMEOUT_SECONDS} seconds."
    except OSError as error:
        return f"Could not start PowerShell: {error}"

    output = (completed.stdout + completed.stderr).strip()
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n[output truncated]"
    return json.dumps({"exit_code": completed.returncode, "output": output})


def run_browser_action(
    arguments: dict[str, Any], dry_run: bool = False, require_confirmation: bool = True
) -> str:
    """Perform one approved action in a persistent, visible browser session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Browser access requires Playwright. Run 'py -m pip install -e .' first.") from error

    action = str(arguments.get("action", ""))
    purpose = str(arguments.get("purpose", "No purpose provided"))
    url = str(arguments.get("url", ""))
    selector = str(arguments.get("selector", ""))
    text = str(arguments.get("text", ""))
    print(
        f"\nProposed browser action: {purpose}\n"
        f"Action: {action}; URL: {url or '(current page)'}; Selector: {selector or '(none)'}; Text: {text or '(none)'}"
    )
    if dry_run:
        return "Dry run: browser action was displayed but not executed."

    if require_confirmation:
        answer = input("Run this browser action? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return "The user declined to run this browser action."

    try:
        global BROWSER_RUNTIME
        if BROWSER_RUNTIME is None:
            playwright = sync_playwright().start()
            BROWSER_PROFILE_DIR.mkdir(exist_ok=True)
            browser = playwright.chromium.launch_persistent_context(
                str(BROWSER_PROFILE_DIR),
                headless=False,
            )
            BROWSER_RUNTIME = (playwright, browser)
        else:
            _, browser = BROWSER_RUNTIME
        page = browser.pages[0] if browser.pages else browser.new_page()
        if action == "navigate":
            if not url:
                return "A URL is required for navigate."
            page.goto(url, wait_until="domcontentloaded")
            result = f"Opened {page.url}\nTitle: {page.title()}"
        elif action == "read":
            result = f"URL: {page.url}\nTitle: {page.title()}\n\n{page.locator('body').inner_text()}"
        elif action == "click":
            page.locator(selector).first.click()
            result = f"Clicked {selector}. Current URL: {page.url}"
        elif action == "fill":
            page.locator(selector).first.fill(text)
            result = f"Filled {selector}."
        elif action == "press":
            page.locator(selector or "body").first.press(text)
            result = f"Pressed {text or '(empty key)'} on {selector or 'body'}."
        else:
            result = f"Unsupported browser action: {action}"
    except Exception as error:
        return f"Browser action failed: {error}"

    if len(result) > BROWSER_OUTPUT_LIMIT:
        result = result[:BROWSER_OUTPUT_LIMIT] + "\n[output truncated]"
    return result


def close_browser() -> None:
    """Close the persistent browser session when the agent exits."""
    global BROWSER_RUNTIME
    if BROWSER_RUNTIME is not None:
        playwright, browser = BROWSER_RUNTIME
        browser.close()
        playwright.stop()
        BROWSER_RUNTIME = None


atexit.register(close_browser)


def response_parts(response: Any) -> list[Any]:
    if not response.candidates:
        return []
    return response.candidates[0].content.parts or []


def describe_error(error: Exception) -> str:
    message = str(error)
    if "API_KEY_INVALID" in message or "API key not valid" in message:
        return (
            "Gemini rejected GEMINI_API_KEY. Create a new key in Google AI Studio, "
            "replace the value in .env, and run the command again."
        )
    if "503" in message or "UNAVAILABLE" in message or "unavailable" in message.lower():
        return (
            "Gemini is temporarily unavailable or the selected model is unavailable. "
            "The agent retried automatically; try again shortly or set GEMINI_MODEL to an available model."
        )
    return message


def generate_response(client: Any, model: str, **request: Any) -> Any:
    for attempt in range(GEMINI_RETRY_ATTEMPTS):
        try:
            return client.interactions.create(model=model, **request)
        except Exception as error:
            message = str(error)
            is_unavailable = "503" in message or "UNAVAILABLE" in message or "unavailable" in message.lower()
            if not is_unavailable or attempt == GEMINI_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(GEMINI_RETRY_DELAY_SECONDS * (attempt + 1))
    raise RuntimeError("Gemini response retry loop ended unexpectedly.")


def run_agent(prompt: str, dry_run: bool = False, confirm_browser: bool = True) -> None:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")

    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    request = {
        "input": prompt,
        "tools": FUNCTION_DECLARATIONS,
        "system_instruction": SYSTEM_INSTRUCTION,
        "generation_config": {"temperature": 0.2},
    }

    for _ in range(8):
        response = generate_response(client, model, **request)
        calls = [step for step in response.steps if step.type == "function_call"]
        if not calls:
            print(response.output_text or "The agent returned no text.")
            return

        results = []
        for call in calls:
            if call.name == "open_default_browser":
                result = open_default_browser(call.arguments or {}, dry_run=dry_run)
            elif call.name == "commit_github_changes":
                result = commit_github_changes(call.arguments or {}, dry_run=dry_run)
            elif call.name != "run_powershell":
                if call.name == "browser_action":
                    result = run_browser_action(
                        call.arguments or {},
                        dry_run=dry_run,
                        require_confirmation=confirm_browser,
                    )
                else:
                    result = "Unsupported tool requested."
            else:
                arguments = call.arguments or {}
                result = run_powershell(
                    str(arguments.get("command", "")),
                    str(arguments.get("purpose", "No purpose provided")),
                    dry_run=dry_run,
                )
            results.append(
                {
                    "type": "function_result",
                    "name": call.name,
                    "call_id": call.id,
                    "result": [{"type": "text", "text": result}],
                }
            )
        request = {
            "previous_interaction_id": response.id,
            "input": results,
            "tools": FUNCTION_DECLARATIONS,
            "system_instruction": SYSTEM_INSTRUCTION,
            "generation_config": {"temperature": 0.2},
        }
    print("The agent reached its step limit before finishing.")


def listen_for_prompt() -> str:
    """Capture one spoken task from the default microphone."""
    try:
        import speech_recognition as speech
        import sounddevice
        import numpy
    except ImportError as error:
        raise RuntimeError(
            "Voice input requires SpeechRecognition, sounddevice, and numpy. "
            "Run 'py -m pip install -e .' first."
        ) from error

    recognizer = speech.Recognizer()
    try:
        print(f"Listening for up to {VOICE_PHRASE_LIMIT_SECONDS} seconds...")
        recording = sounddevice.rec(
            int(VOICE_PHRASE_LIMIT_SECONDS * VOICE_SAMPLE_RATE),
            samplerate=VOICE_SAMPLE_RATE,
            channels=1,
            dtype="int16",
        )
        sounddevice.wait()
    except (OSError, sounddevice.PortAudioError) as error:
        raise RuntimeError(f"Could not access the default microphone: {error}") from error

    audio = speech.AudioData(numpy.asarray(recording).tobytes(), VOICE_SAMPLE_RATE, 2)
    try:
        prompt = recognizer.recognize_google(audio).strip()
    except speech.UnknownValueError:
        print("I could not understand that. Please try again.")
        return ""
    except speech.RequestError as error:
        raise RuntimeError(f"Speech recognition service unavailable: {error}") from error

    print(f"Heard: {prompt}")
    return prompt


def main() -> None:
    arguments = sys.argv[1:]
    dry_run = "--dry-run" in arguments
    voice = "--voice" in arguments
    prompt_parts = [argument for argument in arguments if argument not in {"--dry-run", "--voice"}]
    prompt = " ".join(prompt_parts).strip()
    if prompt:
        try:
            run_agent(prompt, dry_run=dry_run, confirm_browser=not voice)
        except Exception as error:
            print(f"Agent error: {describe_error(error)}", file=sys.stderr)
            raise SystemExit(1) from error
        return

    if voice:
        print("Gemini System Agent is running. Speak a task, or say 'exit' to stop.")
    else:
        print("Gemini System Agent is running. Type a task, or type 'exit' to stop.")
    while True:
        try:
            prompt = listen_for_prompt() if voice else input("\nTask> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAgent stopped.")
            return
        except RuntimeError as error:
            print(f"Agent error: {describe_error(error)}", file=sys.stderr)
            return
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            print("Agent stopped.")
            return
        try:
            run_agent(prompt, dry_run=dry_run, confirm_browser=not voice)
        except Exception as error:
            print(f"Agent error: {describe_error(error)}", file=sys.stderr)


if __name__ == "__main__":
    main()
