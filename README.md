# StudyAI

A lightweight, beginner-friendly study app with student accounts, an AI tutor, three-question practice quizzes, mistake explanations, AI-generated lesson summaries and notes, and per-account progress tracking. Registration saves a student's class and board so the tutor can adapt its explanations, examples, and practice difficulty. This is prompt-based personalization, not model training or fine-tuning.

## Requirements

- Python 3.9 or newer
- For key-free local AI: Ollama and the `qwen2.5:3b` model

## Setup

Install Ollama from <https://ollama.com/download>, open the Ollama app, and download the model in Terminal:

```bash
ollama pull qwen2.5:3b
```

To solve a question from an uploaded image, download the local vision model too:

```bash
ollama pull llava:7b
```

Then, from the project folder, create and activate a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create your settings file from the example. It selects local Ollama and does not require a key:

```bash
cp .env.example .env
```

Start the app:

```bash
python app.py
```

Open <http://127.0.0.1:5000> in your browser. Your study progress is stored locally in `studyai.db`. Keep `.env` private; it is excluded from version control.

In the tutor panel, upload a PNG, JPG, or WebP image up to 5 MB and optionally add what you want explained. Each successful image solution uses one daily tutor question. The default local image model is `llava:7b`; set `OLLAMA_VISION_MODEL` in `.env` to use another Ollama vision model. For cloud providers, set `GROQ_VISION_MODEL` or `OPENAI_VISION_MODEL` to a vision-capable model. Images are sent to the configured AI provider for analysis.

Ollama must be running while you study. If StudyAI cannot connect, open the Ollama app or run `ollama serve`. Progress is stored locally in `studyai.db`. To use a cloud provider instead, set `STUDYAI_PROVIDER=groq` or `STUDYAI_PROVIDER=openai` and its corresponding API key in `.env`. Model names and local/cloud endpoints can be customized there.

Create an account from the registration page. Use at least 12 characters for the password and select your class and school board. The account and its personal profile are created immediately and stored in SQLite; you are signed in automatically. Study progress and saved notes are stored separately for each account, and passwords are stored as secure hashes. Sign-in attempts are throttled by client address and account/address pair. A private Flask session secret is generated automatically for local development and saved in the ignored `.session_secret` file; set `SESSION_SECRET` to a long, random value when deploying the app. Use HTTPS and enable `COOKIE_SECURE=1` when serving over HTTPS. Deploy behind a production WSGI server and configure HTTPS at the reverse proxy; Flask's built-in server is for local development only.

## Study plans

Every new account starts on FREE: 15 tutor questions and 5 answered practice questions per day. GO is planned at ₹100/month (50 tutor questions and 25 practice answers per day), and PRO at ₹150/month (unlimited daily questions). Daily allowances reset at midnight India time. Practice answers count when a student submits a test; a test contains up to three questions and may be shorter when that is all that remains for the day. Notes and progress are available on every plan.

Paid checkout is not connected yet. GO and PRO are displayed for preview only and cannot be activated or charged from the app.
