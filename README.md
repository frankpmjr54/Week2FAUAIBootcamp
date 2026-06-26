# Comic Book Value App

A Flask web app where users can create an account, log in, upload a comic book photo, and receive:

- an AI-generated comic description from the OpenAI API
- an estimated current value from a local SQLite valuation table
- a saved scan history tied to their user account

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and add your `OPENAI_API_KEY`.

## Run

```powershell
python run.py
```

Open `http://127.0.0.1:5000`.

## Notes

The valuation database starts with a small seed table in `app.py`. Replace it with a real price dataset or marketplace API when you are ready.
