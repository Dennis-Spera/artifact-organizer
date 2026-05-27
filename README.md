# Artifact Organizer

Artifact Organizer is a local NiceGUI + TinyDB app for collecting and organizing links and text notes by category.

It supports:

- Category management (add, rename, delete)
- Link entries (title + URL + description)
- Text-only entries (notes)
- Optional image per entry (stored in local images folder)
- Markdown rendering for displayed content

## Tech Stack

- Python 3.12+
- NiceGUI
- TinyDB

## Repository Structure

- app.py: Main NiceGUI application
- pyproject.toml: Project metadata and dependencies
- data.json: TinyDB database (created/updated at runtime)
- images/: Uploaded image files for entries
- activate.fish: Fish shell helper to activate local virtual environment

## Quick Start

### Option 1: Standard venv + pip

1. Create virtual environment:

```bash
python3 -m venv .venv
```

2. Activate it (fish):

```bash
source activate.fish
```

3. Install dependencies:

```bash
pip install nicegui tinydb
```

4. Run the app:

```bash
python app.py
```

Open the URL printed in terminal (usually http://localhost:8080).

### Option 2: UV workflow

If you use uv:

```bash
uv sync
uv run python app.py
```

## How To Use

1. In the left sidebar, add categories.
2. Click Add under Add link/text to open the add/edit form.
3. Choose mode:
	- Link mode: add title + URL (+ optional description/image)
	- Text mode: add text note content (+ optional title/image)
4. Select a category in the sidebar to filter entries.
5. Edit or delete entries from the list view.

## Data Storage

- Entry and category data is stored in data.json via TinyDB.
- Uploaded images are stored in images/.
- If an uploaded filename already exists in images/, it is reused.

## Notes

- Markdown is rendered when entries are displayed.
- The app is intended for local use and currently has no authentication.

