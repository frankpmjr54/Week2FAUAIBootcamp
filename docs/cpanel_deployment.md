# cPanel Deployment Checklist

## Python App Settings

Use these values in cPanel's Python Application screen:

```text
Application URL: mycomicgenius.com/comicbookapp
Application startup file: passenger_wsgi.py
Application entry point: application
```

## Files To Upload

Upload these to the cPanel application root:

```text
app.py
passenger_wsgi.py
requirements.txt
templates/
static/
docs/
.env
```

Create this folder if it does not exist:

```text
uploads/
```

Do not upload:

```text
.venv/
__pycache__/
.git/
server.log
server.err
```

## Environment Variables

If cPanel has environment variable fields, use those. Otherwise place `.env` in the application root.

Required values:

```env
OPENAI_API_KEY=your_real_key
FLASK_SECRET_KEY=a-long-random-secret
DATABASE_PATH=comic_app.db
UPLOAD_FOLDER=uploads
OPENAI_MODEL=gpt-4.1-mini
```

## Install Dependencies

In cPanel, enter the virtual environment for the Python app and run:

```bash
pip install -r requirements.txt
```

## Restart

After uploading files or changing settings, click Restart in the cPanel Python Application screen.

## If You See A 500 Error

Check these first:

1. `passenger_wsgi.py` exists in the application root.
2. The startup file is `passenger_wsgi.py`.
3. The entry point is `application`.
4. `templates/` and `static/` were uploaded.
5. Dependencies were installed with `pip install -r requirements.txt`.
6. `.env` exists or environment variables are configured in cPanel.
7. The application root is writable so SQLite can create `comic_app.db`.
8. The `uploads/` folder exists and is writable.

Then check the cPanel error log. Common places are:

```text
cPanel > Metrics > Errors
cPanel > Setup Python App > your app > logs
Application root / stderr.log
Application root / passenger.log
```
