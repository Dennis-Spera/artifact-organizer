from __future__ import annotations

import os
import socket
import json
import tarfile
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from time import time
from typing import Any, Dict, List, Optional

from nicegui import app, ui
from tinydb import Query, TinyDB

DB_FILE = 'data.json'
DEFAULT_CATEGORY = 'Uncategorized'
APP_DIR = Path(__file__).resolve().parent
IMAGES_DIR = APP_DIR / 'images'
BACKUP_SETTINGS_FILE = APP_DIR / 'backup_settings.json'
DEFAULT_IMAGE_SOURCE = (
    'data:image/svg+xml;utf8,'
    '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">'
    '<rect width="300" height="300" rx="24" fill="%23e5e7eb"/>'
    '<rect x="40" y="40" width="220" height="220" rx="18" fill="%23ffffff" stroke="%23cbd5e1" stroke-width="6"/>'
    '<path d="M85 205l42-52 30 34 31-41 27 34v25H85z" fill="%2394a3b8"/>'
    '<circle cx="120" cy="112" r="18" fill="%2394a3b8"/>'
    '<text x="150" y="266" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" fill="%2364748b">Click to upload</text>'
    '</svg>'
)


db = TinyDB(DB_FILE)
links_table = db.table('links')
categories_table = db.table('categories')

IMAGES_DIR.mkdir(exist_ok=True)
app.add_static_files('/images', str(IMAGES_DIR))


class LinkStore:
    def __init__(self) -> None:
        self._ensure_default_category()

    def _ensure_default_category(self) -> None:
        if not categories_table.contains(Query().name == DEFAULT_CATEGORY):
            categories_table.insert({'name': DEFAULT_CATEGORY, 'created_at': self._now()})

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    @staticmethod
    def _normalize_category(name: str) -> str:
        return name.strip() or DEFAULT_CATEGORY

    def get_categories(self) -> List[str]:
        names = {row.get('name', DEFAULT_CATEGORY).strip() for row in categories_table.all() if row.get('name')}
        other_categories = sorted((name for name in names if name != DEFAULT_CATEGORY), key=str.casefold)
        return [DEFAULT_CATEGORY, *other_categories]

    def add_category(self, name: str) -> str:
        clean = self._normalize_category(name)
        if categories_table.contains(Query().name == clean):
            raise ValueError(f'Category "{clean}" already exists.')
        categories_table.insert({'name': clean, 'created_at': self._now()})
        return clean

    def rename_category(self, old_name: str, new_name: str) -> str:
        old_clean = self._normalize_category(old_name)
        new_clean = self._normalize_category(new_name)
        if old_clean == DEFAULT_CATEGORY:
            raise ValueError('Cannot rename the default category.')
        if not categories_table.contains(Query().name == old_clean):
            raise ValueError(f'Category "{old_clean}" not found.')
        if old_clean != new_clean and categories_table.contains(Query().name == new_clean):
            raise ValueError(f'Category "{new_clean}" already exists.')

        categories_table.update({'name': new_clean}, Query().name == old_clean)
        links_table.update({'category': new_clean}, Query().category == old_clean)
        return new_clean

    def delete_category(self, name: str) -> None:
        clean = self._normalize_category(name)
        if clean == DEFAULT_CATEGORY:
            raise ValueError('Cannot delete the default category.')

        categories_table.remove(Query().name == clean)
        links_table.update({'category': DEFAULT_CATEGORY}, Query().category == clean)

    def list_links(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        if category and category != 'All':
            rows = links_table.search(Query().category == category)
        else:
            rows = links_table.all()
        return sorted(rows, key=lambda item: item.get('title', '').lower())

    def add_link(
        self,
        title: str,
        url: str,
        category: str,
        description: str,
        image_source: str,
        entry_type: str = 'link',
        text_content: str = '',
    ) -> int:
        clean_category = self._normalize_category(category)
        if not categories_table.contains(Query().name == clean_category):
            categories_table.insert({'name': clean_category, 'created_at': self._now()})

        return links_table.insert(
            {
                'title': title.strip(),
                'url': url.strip(),
                'category': clean_category,
                'description': description.strip(),
                'image_source': image_source or DEFAULT_IMAGE_SOURCE,
                'entry_type': entry_type,
                'text_content': text_content.strip(),
                'updated_at': self._now(),
            }
        )

    def update_link(
        self,
        doc_id: int,
        title: str,
        url: str,
        category: str,
        description: str,
        image_source: str,
        entry_type: str = 'link',
        text_content: str = '',
    ) -> None:
        clean_category = self._normalize_category(category)
        if not categories_table.contains(Query().name == clean_category):
            categories_table.insert({'name': clean_category, 'created_at': self._now()})

        links_table.update(
            {
                'title': title.strip(),
                'url': url.strip(),
                'category': clean_category,
                'description': description.strip(),
                'image_source': image_source or DEFAULT_IMAGE_SOURCE,
                'entry_type': entry_type,
                'text_content': text_content.strip(),
                'updated_at': self._now(),
            },
            doc_ids=[doc_id],
        )

    def delete_link(self, doc_id: int) -> None:
        links_table.remove(doc_ids=[doc_id])


store = LinkStore()

selected_category = 'All'
editing_doc_id: Optional[int] = None
current_image_source = DEFAULT_IMAGE_SOURCE
form_visible = False


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def load_backup_settings() -> Dict[str, str]:
    defaults = {'backup_dir': '', 'last_backup_at': '', 'restore_dir': ''}
    if not BACKUP_SETTINGS_FILE.exists():
        return defaults

    try:
        payload = json.loads(BACKUP_SETTINGS_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return defaults

    return {
        'backup_dir': str(payload.get('backup_dir', '')).strip(),
        'last_backup_at': str(payload.get('last_backup_at', '')).strip(),
        'restore_dir': str(payload.get('restore_dir', '')).strip(),
    }


def save_backup_settings() -> None:
    BACKUP_SETTINGS_FILE.write_text(json.dumps(backup_settings, indent=2), encoding='utf-8')


def days_since_last_backup() -> Optional[int]:
    last_backup_at = backup_settings.get('last_backup_at', '').strip()
    if not last_backup_at:
        return None

    try:
        last_dt = datetime.fromisoformat(last_backup_at.replace('Z', '+00:00'))
    except ValueError:
        return None

    return (datetime.now(timezone.utc).date() - last_dt.date()).days


def backup_status_text() -> str:
    days = days_since_last_backup()
    if days is None:
        return 'Days since last backup: never'
    if days == 0:
        return 'Days since last backup: 0 (today)'
    if days == 1:
        return 'Days since last backup: 1 day'
    return f'Days since last backup: {days} days'


def backup_location_text() -> str:
    configured = backup_settings.get('backup_dir', '').strip()
    return f'Backup location: {configured or "Not set"}'


def restore_location_text() -> str:
    configured = backup_settings.get('restore_dir', '').strip()
    return f'Restore location: {configured or "Not set"}'


def list_backup_archives() -> List[Path]:
    configured = backup_settings.get('backup_dir', '').strip()
    if not configured:
        return []

    backup_dir = Path(configured).expanduser()
    if not backup_dir.exists() or not backup_dir.is_dir():
        return []

    return sorted(
        [p for p in backup_dir.glob('*.tar.gz') if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def restore_backup_archive(archive_path: Path, restore_root: Path) -> Path:
    if not archive_path.exists() or not archive_path.is_file():
        raise ValueError(f'Backup archive not found: {archive_path}')

    restore_root = restore_root.expanduser().resolve()
    restore_root.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, mode='r:gz') as tar:
        members = tar.getmembers()
        if not members:
            raise ValueError('Backup archive is empty.')

        for member in members:
            target_path = (restore_root / member.name).resolve()
            if target_path != restore_root and restore_root not in target_path.parents:
                raise ValueError('Unsafe archive contents detected; restore aborted.')

        tar.extractall(path=restore_root)

    return restore_root


def open_restore_dialog() -> None:
    archives = list_backup_archives()
    if not archives:
        safe_notify('No backup archives found in the configured backup location', color='warning')
        return

    default_restore_dir = backup_settings.get('restore_dir', '').strip() or str(APP_DIR.parent)
    archive_options = {str(path): path.name for path in archives}
    default_archive = str(archives[0])

    with ui.dialog() as dialog, ui.card().classes('min-w-[560px]'):
        ui.label('Restore from backup').classes('text-subtitle1')
        archive_select = ui.select(
            archive_options,
            label='Backup archive',
            value=default_archive,
        ).props('outlined')
        restore_input = ui.input('Restore destination directory', value=default_restore_dir).props('outlined')
        ui.label('Restore will extract files into the selected destination directory.').classes('text-caption text-grey-7')

        with ui.row().classes('justify-end q-gutter-sm'):
            ui.button('Cancel', on_click=dialog.close).props('flat')

            def submit_restore() -> None:
                archive_value = (archive_select.value or '').strip()
                destination_value = (restore_input.value or '').strip()
                if not archive_value:
                    safe_notify('Select a backup archive', color='warning')
                    return
                if not destination_value:
                    safe_notify('Restore destination is required', color='warning')
                    return

                archive_path = Path(archive_value)
                restore_destination = Path(destination_value).expanduser()
                try:
                    final_restore_path = restore_backup_archive(archive_path, restore_destination)
                except (OSError, tarfile.TarError, ValueError) as exc:
                    safe_notify(f'Restore failed: {exc}', color='negative')
                    return

                backup_settings['restore_dir'] = str(restore_destination)
                save_backup_settings()
                dialog.close()
                safe_notify(f'Restore completed to {final_restore_path}', color='positive')
                render_categories()

            ui.button('Restore now', on_click=submit_restore, color='primary')

    dialog.open()


def run_backup_now() -> None:
    configured = backup_settings.get('backup_dir', '').strip()
    if not configured:
        safe_notify('Set a backup location first', color='warning')
        return

    backup_dir = Path(configured).expanduser()
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        safe_notify(f'Cannot create backup directory: {exc}', color='negative')
        return

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    archive_path = backup_dir / f'{APP_DIR.name}_{stamp}.tar.gz'

    backup_dir_in_project: Optional[Path] = None
    try:
        backup_dir_in_project = backup_dir.resolve().relative_to(APP_DIR.resolve())
    except ValueError:
        backup_dir_in_project = None

    try:
        with tarfile.open(archive_path, mode='w:gz') as tar:
            for item in APP_DIR.rglob('*'):
                if not item.is_file():
                    continue
                if item.resolve() == archive_path.resolve():
                    continue

                relative_item = item.relative_to(APP_DIR)
                if backup_dir_in_project and (
                    relative_item == backup_dir_in_project
                    or backup_dir_in_project in relative_item.parents
                ):
                    continue

                tar.add(item, arcname=Path(APP_DIR.name) / relative_item)
    except OSError as exc:
        safe_notify(f'Backup failed: {exc}', color='negative')
        return

    backup_settings['last_backup_at'] = _now_iso_utc()
    save_backup_settings()
    safe_notify(f'Backup saved: {archive_path}', color='positive')
    render_categories()


def open_backup_location_dialog() -> None:
    with ui.dialog() as dialog, ui.card().classes('min-w-[480px]'):
        ui.label('Set backup location').classes('text-subtitle1')
        location_input = ui.input('Directory path', value=backup_settings.get('backup_dir', '')).props('outlined')
        ui.label('Example: /mnt/data/Dropbox/backups').classes('text-caption text-grey-7')

        with ui.row().classes('justify-end q-gutter-sm'):
            ui.button('Cancel', on_click=dialog.close).props('flat')

            def submit_backup_location() -> None:
                location = (location_input.value or '').strip()
                if not location:
                    safe_notify('Backup location is required', color='warning')
                    return

                backup_settings['backup_dir'] = str(Path(location).expanduser())
                save_backup_settings()
                dialog.close()
                safe_notify('Backup location updated', color='positive')
                render_categories()

            ui.button('Save', on_click=submit_backup_location, color='primary')

    dialog.open()


backup_settings = load_backup_settings()


def choose_port(start_port: int, attempts: int = 20) -> int:
    for port in range(start_port, start_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.settimeout(0.2)
            if candidate.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError(f'No free port found in range {start_port}-{start_port + attempts - 1}')


def find_matching_image(content: bytes) -> Optional[Path]:
    content_size = len(content)
    for existing_path in IMAGES_DIR.iterdir():
        if not existing_path.is_file():
            continue
        if existing_path.stat().st_size != content_size:
            continue
        if existing_path.read_bytes() == content:
            return existing_path
    return None


def save_upload_to_images(file_name: str, content: bytes) -> str:
    existing_path = find_matching_image(content)
    if existing_path is not None:
        return f'/{existing_path.relative_to(APP_DIR).as_posix()}'

    original_name = Path(file_name).name
    suffix = Path(original_name).suffix.lower()
    stem = Path(original_name).stem[:80]
    safe_stem = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '_' for ch in stem).strip('_') or 'upload'
    unique_name = f'{safe_stem}_{uuid4().hex[:12]}{suffix}'
    target_path = IMAGES_DIR / unique_name
    target_path.write_bytes(content)
    return f'/{target_path.relative_to(APP_DIR).as_posix()}'


def normalize_image_source(source: str) -> str:
    if not source:
        return DEFAULT_IMAGE_SOURCE
    if source.startswith(('data:', 'http://', 'https://')):
        return source

    normalized = source if source.startswith('/') else f'/{source}'
    local_path = APP_DIR / normalized.lstrip('/')
    if local_path.exists():
        return normalized
    return DEFAULT_IMAGE_SOURCE


def preview_image_source(source: str) -> str:
    normalized = normalize_image_source(source)
    if normalized.startswith(('data:', 'http://', 'https://')):
        return normalized
    return f'{normalized}?v={int(time() * 1000)}'


def resolve_managed_image_path(source: str) -> Optional[Path]:
    normalized = source if source.startswith('/') else f'/{source}'
    if not normalized.startswith('/images/'):
        return None

    candidate = (APP_DIR / normalized.lstrip('/')).resolve()
    images_root = IMAGES_DIR.resolve()
    if images_root not in candidate.parents:
        return None
    return candidate


def remove_orphaned_image(source: str, excluding_doc_id: Optional[int] = None) -> None:
    if not source or source == DEFAULT_IMAGE_SOURCE:
        return
    if source.startswith(('http://', 'https://', 'data:')):
        return

    image_path = resolve_managed_image_path(source)
    if image_path is None:
        return

    for row in links_table.all():
        if excluding_doc_id is not None and row.doc_id == excluding_doc_id:
            continue
        if row.get('image_source') == source:
            return

    if image_path.exists():
        image_path.unlink()


def safe_notify(message: str, color: str = 'primary') -> None:
    try:
        ui.notify(message, color=color)
    except Exception:
        # This can happen if the callback outlives a destroyed slot/context.
        print(f'notify[{color}]: {message}')


async def handle_image_upload(event: Any) -> None:
    global current_image_source
    try:
        file_bytes = await event.file.read()
        if not file_bytes:
            raise ValueError('Uploaded file is empty.')

        file_name = event.file.name or 'upload'
        current_image_source = save_upload_to_images(file_name, file_bytes)
        link_image.set_source(preview_image_source(current_image_source))
        link_image.update()
        upload_status_label.text = f'Selected: {file_name} ({len(file_bytes)} bytes)'
        safe_notify(f'Image selected: {file_name}', color='positive')
    except Exception as exc:
        upload_status_label.text = f'Upload failed: {exc}'
        safe_notify(f'Upload failed: {exc}', color='negative')


def update_entry_mode() -> None:
    is_text_only = bool(text_only_toggle.value)
    form_mode_label.text = 'Add or Update Text' if is_text_only else 'Add or Update Link'
    url_input.set_visibility(not is_text_only)
    description_input.set_visibility(not is_text_only)
    text_content_input.set_visibility(is_text_only)
    if editing_doc_id is None:
        save_button.text = 'Add Text Note' if is_text_only else 'Add Link'
    else:
        save_button.text = 'Save Text Note' if is_text_only else 'Save Link'
    if is_text_only:
        text_content_input.style('width: 67%; min-height: 420px;')
        text_content_input.props('rows=14')
    else:
        text_content_input.style('width: 100%; min-height: 120px;')
        text_content_input.props('rows=3')


def update_form_visibility() -> None:
    visible = form_visible
    form_mode_label.set_visibility(visible)
    form_card.set_visibility(visible)
    link_container.set_visibility(not visible)


def open_add_form() -> None:
    global form_visible
    form_visible = True
    reset_form()
    update_form_visibility()


def set_selected_category(name: str) -> None:
    global selected_category, form_visible
    selected_category = name
    form_visible = False
    update_form_visibility()
    render_categories()
    render_links()


def render_categories() -> None:
    category_list.clear()
    with category_list:
        ui.label('Categories').classes('text-subtitle1 q-mb-sm')

        with ui.row().classes('items-center q-gutter-sm q-mb-sm'):
            category_input = ui.input('Add category').props('dense outlined')

            def submit_add_category() -> None:
                value = (category_input.value or '').strip()
                if not value:
                    safe_notify('Category name is required', color='warning')
                    return
                try:
                    new_name = store.add_category(value)
                    category_input.value = ''
                    set_selected_category(new_name)
                    safe_notify(f'Category "{new_name}" added', color='positive')
                except ValueError as exc:
                    safe_notify(str(exc), color='negative')

            ui.button('Add', on_click=submit_add_category).props('dense').classes('w-20')

        ui.separator().classes('q-mb-sm q-mt-sm')

        ui.label('Add link/text').classes('text-subtitle1 q-mb-xs')
        ui.button('Add', on_click=open_add_form, color='primary').props('dense').classes('w-20 q-mb-sm')

        ui.separator().classes('q-mb-sm')

        ui.label('Backup').classes('text-subtitle1 q-mb-xs')
        ui.button('Set backup location', on_click=open_backup_location_dialog).props('dense').classes('w-full justify-start q-mb-xs')
        ui.button('Backup now', on_click=run_backup_now, color='primary').props('dense').classes('w-full justify-start q-mb-xs')
        ui.button('Restore from backup', on_click=open_restore_dialog).props('dense').classes('w-full justify-start q-mb-xs')
        ui.label(backup_location_text()).classes('text-caption text-grey-7 q-mb-xs')
        ui.label(restore_location_text()).classes('text-caption text-grey-7 q-mb-xs')
        ui.label(backup_status_text()).classes('text-caption text-grey-7 q-mb-sm')

        ui.separator().classes('q-mb-sm')

        all_color = 'primary' if selected_category == 'All' else 'grey-8'
        uncategorized_color = 'primary' if selected_category == DEFAULT_CATEGORY else 'grey-8'
        ui.button('All', on_click=lambda: set_selected_category('All'), color=all_color).classes('w-full justify-start q-mb-xs')
        ui.button(
            DEFAULT_CATEGORY,
            on_click=lambda: set_selected_category(DEFAULT_CATEGORY),
            color=uncategorized_color,
        ).classes('w-full justify-start q-mb-sm')

        ui.separator().classes('q-mb-sm')

        for name in store.get_categories():
            if name == DEFAULT_CATEGORY:
                continue
            color = 'primary' if selected_category == name else 'grey-8'
            with ui.row().classes('w-full items-center q-gutter-xs'):
                ui.button(name, on_click=lambda n=name: set_selected_category(n), color=color).classes('col')
                if name != DEFAULT_CATEGORY:
                    ui.button(icon='edit', on_click=lambda n=name: open_rename_category_dialog(n)).props('flat round dense')
                    ui.button(icon='delete', on_click=lambda n=name: delete_category(n)).props('flat round dense color=negative')


def delete_category(name: str) -> None:
    global selected_category
    try:
        store.delete_category(name)
        if selected_category == name:
            selected_category = 'All'
        update_form_visibility()
        render_categories()
        render_links()
        safe_notify(f'Category "{name}" deleted. Links moved to "{DEFAULT_CATEGORY}".', color='positive')
    except ValueError as exc:
        safe_notify(str(exc), color='negative')


def open_rename_category_dialog(old_name: str) -> None:
    with ui.dialog() as dialog, ui.card().classes('min-w-[320px]'):
        ui.label(f'Rename category: {old_name}').classes('text-subtitle1')
        new_name_input = ui.input('New name', value=old_name).props('outlined')

        with ui.row().classes('justify-end q-gutter-sm'):
            ui.button('Cancel', on_click=dialog.close).props('flat')

            def submit_rename() -> None:
                global selected_category
                try:
                    updated = store.rename_category(old_name, (new_name_input.value or '').strip())
                    if selected_category == old_name:
                        selected_category = updated
                    dialog.close()
                    render_categories()
                    render_links()
                    safe_notify('Category updated', color='positive')
                except ValueError as exc:
                    safe_notify(str(exc), color='negative')

            ui.button('Save', on_click=submit_rename, color='primary')

    dialog.open()


def reset_form() -> None:
    global editing_doc_id, current_image_source
    editing_doc_id = None
    current_image_source = DEFAULT_IMAGE_SOURCE
    title_input.value = ''
    text_only_toggle.value = False
    url_input.value = ''
    description_input.value = ''
    text_content_input.value = ''
    categories = ['All'] + store.get_categories()
    category_select.options = categories
    category_select.value = selected_category if selected_category != 'All' else DEFAULT_CATEGORY
    link_image.set_source(preview_image_source(current_image_source))
    upload_status_label.text = 'No image selected'
    update_entry_mode()


def save_link() -> None:
    global editing_doc_id

    title = (title_input.value or '').strip()
    url = (url_input.value or '').strip()
    category = (category_select.value or DEFAULT_CATEGORY).strip()
    description = (description_input.value or '').strip()
    text_content = (text_content_input.value or '').strip()
    is_text_only = bool(text_only_toggle.value)

    if is_text_only:
        if not text_content:
            safe_notify('Text is required for text-only entries', color='warning')
            return
        if not title:
            title = 'Text Note'
        url = ''
        description = ''
    else:
        if not title or not url:
            safe_notify('Title and URL are required', color='warning')
            return
        text_content = ''

    if category == 'All':
        category = DEFAULT_CATEGORY

    if editing_doc_id is None:
        store.add_link(
            title=title,
            url=url,
            category=category,
            description=description,
            image_source=current_image_source,
            entry_type='text' if is_text_only else 'link',
            text_content=text_content,
        )
        safe_notify('Link added', color='positive')
    else:
        previous = links_table.get(doc_id=editing_doc_id)
        previous_image_source = previous.get('image_source', '') if previous else ''
        store.update_link(
            doc_id=editing_doc_id,
            title=title,
            url=url,
            category=category,
            description=description,
            image_source=current_image_source,
            entry_type='text' if is_text_only else 'link',
            text_content=text_content,
        )
        if previous_image_source and previous_image_source != current_image_source:
            remove_orphaned_image(previous_image_source, excluding_doc_id=editing_doc_id)
        safe_notify('Link updated', color='positive')

    reset_form()
    render_categories()
    render_links()


def edit_link(doc_id: int) -> None:
    global editing_doc_id, current_image_source, form_visible
    record = links_table.get(doc_id=doc_id)
    if not record:
        safe_notify('Link not found', color='negative')
        return

    editing_doc_id = doc_id
    is_text_only = record.get('entry_type', 'link') == 'text'
    title_input.value = record.get('title', '')
    url_input.value = record.get('url', '')
    description_input.value = record.get('description', '')
    text_content_input.value = record.get('text_content', '')
    text_only_toggle.value = is_text_only
    current_image_source = record.get('image_source', DEFAULT_IMAGE_SOURCE)
    link_image.set_source(preview_image_source(current_image_source))

    categories = ['All'] + store.get_categories()
    category_select.options = categories
    category_select.value = record.get('category', DEFAULT_CATEGORY)
    form_visible = True
    update_form_visibility()
    update_entry_mode()


def remove_link(doc_id: int) -> None:
    record = links_table.get(doc_id=doc_id)
    image_source = record.get('image_source', '') if record else ''
    store.delete_link(doc_id)
    if image_source:
        remove_orphaned_image(image_source)
    safe_notify('Link deleted', color='positive')
    if editing_doc_id == doc_id:
        reset_form()
    render_links()


def render_links() -> None:
    link_container.clear()
    rows = store.list_links(selected_category)
    total_entries = len(rows)

    with link_container:
        ui.label(f'Links ({selected_category}) - Total links/text: {total_entries}').classes('text-h6 q-mb-sm')
        if not rows:
            ui.label('No links yet. Add one using the form above.').classes('text-grey-7')
            return

        for row in rows:
            with ui.card().classes('w-full q-mb-sm'):
                with ui.row().classes('w-full items-start q-gutter-md no-wrap'):
                    ui.image(normalize_image_source(row.get('image_source', DEFAULT_IMAGE_SOURCE))).classes('w-24 h-24 rounded-lg object-cover')
                    with ui.column().classes('col'):
                        ui.markdown(row.get('title', 'Untitled')).classes('text-subtitle1')
                        entry_type = row.get('entry_type', 'link')
                        if entry_type == 'text':
                            ui.badge('Text').props('color=teal')
                        else:
                            ui.badge('Link').props('color=indigo')
                        if entry_type == 'link':
                            ui.link(row.get('url', ''), row.get('url', '')).props('target=_blank')
                        else:
                            ui.label('Text-only entry').classes('text-grey-7')
                        ui.badge(row.get('category', DEFAULT_CATEGORY)).props('color=primary outline')
                        if entry_type == 'text' and row.get('text_content'):
                            ui.markdown(row['text_content']).classes('q-mt-sm')
                        elif row.get('description'):
                            ui.markdown(row['description']).classes('q-mt-sm')
                    with ui.column().classes('items-end q-gutter-xs'):
                        ui.button('Edit', on_click=lambda d=row.doc_id: edit_link(d)).props('dense')
                        ui.button('Delete', on_click=lambda d=row.doc_id: remove_link(d)).props('dense color=negative')


ui.colors(primary='#2f7d6d')
ui.page_title('Artifact Organizer')

with ui.header().classes('items-center justify-between'):
    ui.label('Artifact Organizer').classes('text-h6')

with ui.left_drawer(value=True, top_corner=True, bottom_corner=True).classes('bg-grey-2'):
    category_list = ui.column().classes('w-64 q-pa-md')

with ui.column().classes('w-full q-pa-md'):
    form_mode_label = ui.label('Add or Update Link').classes('text-h5 q-mb-sm')
    with ui.card().classes('w-full q-pa-md q-mb-md') as form_card:
        with ui.row().classes('w-full items-start q-col-gutter-md'):
            with ui.column().classes('col-12 col-md-9 q-gutter-md'):
                text_only_toggle = ui.switch('Text only (no URL)', value=False, on_change=lambda _: update_entry_mode())
                with ui.row().classes('w-full q-col-gutter-md'):
                    title_input = ui.input('Title').props('outlined').classes('col-12 col-md-4 q-ml-sm')
                    url_input = ui.input('URL').props('outlined').classes('col-12 col-md-4')
                    category_select = ui.select(['All'] + store.get_categories(), label='Category', value=DEFAULT_CATEGORY).props('outlined').classes('col-12 col-md-4 q-ml-sm')
                description_input = ui.textarea('Description').props('outlined autogrow').classes('w-full q-ml-sm')
                text_content_input = ui.textarea('Text content').props('outlined').classes('w-full q-ml-sm')
                with ui.row().classes('q-gutter-sm'):
                    save_button = ui.button('Add Link', on_click=save_link, color='primary')
                    ui.button('Clear', on_click=reset_form).props('flat')

            with ui.column().classes('col-12 col-md-3 items-center'):
                ui.label('Preview image').classes('text-subtitle2 q-mb-xs')
                with ui.element('div').style(
                    'width: 150px; height: 150px; position: relative; overflow: hidden; border-radius: 12px; border: 2px dashed #cbd5e1;'
                ):
                    link_image = ui.image(preview_image_source(current_image_source)).style('width: 150px; height: 150px; object-fit: cover;')
                    ui.label('Preview').style(
                        'position: absolute; inset: auto 0 0 0; text-align: center; background: rgba(15, 23, 42, 0.55); color: white; font-size: 12px; padding: 4px 0;'
                    )

                image_upload = ui.upload(
                    on_upload=handle_image_upload,
                    auto_upload=True,
                    max_files=1,
                ).props('accept=.png,.jpg,.jpeg,.gif,.webp,.svg,.heic,.heif label=Choose image').classes('w-full q-mt-sm')
                upload_status_label = ui.label('No image selected').classes('text-caption text-grey-7 q-mt-xs')

    link_container = ui.column().classes('w-full')

render_categories()
update_form_visibility()
reset_form()
render_links()

preferred_port = int(os.getenv('PORT', '8081'))
ui.run(title='Artifact Organizer', reload=False, show_welcome_message=False, port=choose_port(preferred_port))
