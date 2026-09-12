# Gemini System Agent

A small, confirmation-first local agent that uses Gemini's Interactions API and function calling to propose PowerShell tasks. Every command is shown before execution, and declined commands are reported back to Gemini so it can adjust.

This is a local Windows application, not a hosted web service. GitHub can host the source, run the automated checks in `.github/workflows/ci.yml`, and distribute tagged source releases; the desktop UI and PowerShell tools run on the user's own Windows machine.

## Setup

1. Create the project environment and install dependencies from `cmd.exe` or PowerShell:

   ```cmd
   py -m venv .venv
   .venv\Scripts\python.exe -m pip install -e .
   ```

2. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`. The default model is `gemini-3.6-flash`, which uses Gemini's Interactions API.
   The key must be a valid Gemini API key created in Google AI Studio. If the agent reports `API_KEY_INVALID`, replace the value in `.env` with a newly created key.

Never commit `.env` or a real API key. For a GitHub repository, keep only the placeholder in `.env.example` and add deployment secrets through GitHub Actions or the host's secret manager.

## GitHub

### Publish the repository

Create an empty repository on GitHub, then run these commands from the project directory. Replace the URL with your repository's HTTPS or SSH URL:

```cmd
git init
git branch -M main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR-OWNER/YOUR-REPOSITORY.git
git push -u origin main
```

Before pushing, verify that `.env` is ignored and that `.env.example` contains only a placeholder API key:

```cmd
git status --short --ignored
```

### Continuous integration

The [Python CI workflow](.github/workflows/ci.yml) runs automatically for pushes to `main` and for pull requests. It tests Python 3.11, 3.12, and 3.13 by installing the package, compiling the Python sources, importing both modules, and checking the `gemini-agent` console command.

The CI workflow does not need `GEMINI_API_KEY` because it does not call the Gemini API. Do not add the API key to repository files. If a future workflow needs it, add `GEMINI_API_KEY` under **Repository Settings > Secrets and variables > Actions** and reference it as `${{ secrets.GEMINI_API_KEY }}`.

### Run after cloning

This project runs locally on Windows; GitHub does not host the Tkinter desktop UI or the PowerShell, microphone, and browser features. After cloning, use:

```cmd
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env
```

Set `GEMINI_API_KEY` in `.env`, then start the application with `run-agent.cmd` or `run-agent-ui.cmd`. GitHub-hosted runners are intended for validation, not for running the interactive desktop agent.


## Desktop UI

Start the graphical interface:

```cmd
run-agent-ui.cmd
```

The UI includes a task box, a dry-run toggle, and a live output panel. It uses the same agent logic as the terminal version, including confirmation dialogs before PowerShell or browser actions are executed.

## Terminal usage

Run a task from Command Prompt:

```cmd
run-agent.cmd "Show the top 10 processes by CPU usage"
```

Use `--dry-run` to let Gemini propose commands without executing them:

```cmd
run-agent.cmd --dry-run "Inspect available drives"
```

The wrapper uses the project's `.venv` automatically. You can also invoke the Python file directly with `py agent.py ...`.

## Commit changes to GitHub

Ask the agent explicitly to commit selected files, for example:

```cmd
run-agent.cmd "Commit agent.py and README.md to GitHub with message Add GitHub commit support and push the current branch"
```

The agent requires a repository-relative file list and a commit message. It shows the exact `git add`, `git commit`, and optional `git push` operation before asking for confirmation. Git uses the credentials and remote configuration already set up on the machine; do not enter credentials into the agent.

To keep the agent running and enter several tasks in one session, run it without a task:

```cmd
run-agent.cmd
```

Enter each task at the `Task>` prompt. Type `exit` or `quit` to stop, or press Ctrl+C.

To accept spoken tasks from the default microphone, run the interactive agent with voice input:

```cmd
run-agent.cmd --voice
```

Speak one task after each `Listening...` prompt. Say `exit` or `quit` to stop. Voice input uses SpeechRecognition and its Google speech-to-text service, so it requires a working microphone and an internet connection.

To close VS Code, ask explicitly, for example: `Close VS Code gracefully`. The agent will propose a non-forced close and wait for confirmation. Save any work first because VS Code may show an unsaved-changes prompt.

## Browser access

Ask the agent to open a public page in your Windows default browser:

```cmd
run-agent.cmd "Open https://github.com in my default browser"
```

This action launches the URL only. It does not read pages, enter credentials, or control the default browser. The existing browser actions use a separate visible Chromium profile when the agent needs page inspection or interaction.

Install the Chromium runtime once after installing the project:

```cmd
py -m playwright install chromium
```

Then ask the agent to browse normally, for example:

```cmd
run-agent.cmd "Open https://example.com and tell me the page title"
```

The agent opens a visible Chromium window and can navigate, read page text, click elements, fill non-sensitive fields, and press keys. Each browser action requires confirmation. Login state is stored locally in `.browser-profile` so sites can remain signed in between runs. Never enter passwords, API keys, or other secrets through the agent.

To upload repository files to GitHub, ask the agent to commit and push the selected files as described above. This uses Git's existing remote and credential configuration and does not require entering credentials in a browser.

## Safety boundary

The initial tool is intentionally limited to one local, non-interactive PowerShell command at a time. It has a 5-second timeout and truncates large output. Transient Gemini 503 responses are retried up to three times with a short backoff. The model is instructed to avoid destructive, credential-related, security-sensitive, and network-changing actions, but the confirmation prompt is the final authorization boundary. Review every proposed command before accepting it.

Browser access is deliberately limited to separate, confirmation-first actions rather than unrestricted JavaScript or shell access. Review every proposed browser action before accepting it.

Do not put a real API key in `.env.example`. Store it only in `.env`, which is excluded from source control. If a real key has been shared or committed, revoke it in Google AI Studio and create a replacement.
