# anx_device_plugin/__init__.py

import os, stat, re, hashlib, json, time, uuid
import sqlite3
from datetime import datetime
import shutil

from calibre.devices.interface import DevicePlugin, BookList
from calibre.utils.filenames import ascii_text
from calibre.utils.config import JSONConfig
from calibre.utils.logging import default_log
from PyQt5.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget
from dataclasses import dataclass, field
from typing import List

# Import the custom ConfigWidget and preferences object
from .config import ConfigWidget, prefs
class AnxFile:
    def __init__(self, name, path, is_dir=False, size=0, ctime=0, wtime=0):
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.size = size
        self.ctime = ctime
        self.wtime = wtime
        self.is_readonly = True # For simplicity, assume all files are read-only
# Define the custom BookList for ANX device
class AnxBookList(BookList):
    def __init__(self, oncard=None, prefix=None, settings=None):
        super().__init__(oncard, prefix, settings)
        self.books_by_uuid = {}
        self.uuids_in_list = []

    def add_book(self, book, replace_metadata=None):
        if book.uuid not in self.books_by_uuid:
            self.books_by_uuid[book.uuid] = book
            self.uuids_in_list.append(book.uuid)
        self.append(book) # Directly add to the list, as super().add_book is NotImplementedError

    def remove_book(self, book):
        if book.uuid in self.books_by_uuid:
            del self.books_by_uuid[book.uuid]
            if book.uuid in self.uuids_in_list:
                self.uuids_in_list.remove(book.uuid)
        super().remove_book(book)

    def __iter__(self):
        for uuid in self.uuids_in_list:
            yield self.books_by_uuid[uuid]

    def __len__(self):
        return len(self.uuids_in_list)
    
    def clear(self):
        self.books_by_uuid = {}
        self.uuids_in_list = []
        self[:] = []

    def __repr__(self):
        return f"AnxBookList(len={len(self)})"

class AnxBookMetadata:
    def __init__(self, title: str, authors: List[str], uuid: str, path: str, has_cover: bool, format_map: dict, device_id: str, size: int = 0, datetime: datetime = None, thumbnail: bytes = None, tags: List[str] = None, cover_path: str = None, file_md5: str = None):
        self.title = title
        self.authors = authors
        self.uuid = uuid
        self.path = path
        self.has_cover = has_cover
        self.format_map = format_map
        self.device_id = device_id
        self.size = size
        self.datetime = datetime if datetime is not None else datetime.utcnow()
        self.thumbnail = thumbnail
        self.tags = tags if tags is not None else []
        self.cover_path = cover_path
        self.file_md5 = file_md5

    def __repr__(self):
        return f"AnxBookMetadata(title='{self.title}', uuid='{self.uuid}')"


class AnxDevicePlugin(DevicePlugin):
    name                = 'ANX Virtual Device'
#    gui_name            = 'ANX Device'
    gui_name = _('ANX Device')
    icon = 'devices/tablet.png'
    description         = 'Connects to a custom folder structure with a database7.db file for managing ebooks.'
    author              = 'Gemini AI based on user script'
    version             = (1, 0, 0)
    supported_platforms = ['windows', 'osx', 'linux']
    capabilities        = frozenset(['send_books', 'delete_books', 'card_a_from_b', 'has_user_manual'])
    MANAGES_DEVICE_PRESENCE = True # Set to True as per Remarkable plugin
    
    config_spec = JSONConfig('plugins/anx_device_plugin')
    config_spec.defaults['device_path'] = ''

    def __init__(self, plugin_path):
        DevicePlugin.__init__(self, plugin_path)
        self.gui = None
        self.settings = prefs
        self.log = default_log
        if not hasattr(self, 'uuid') or not self.uuid:
            self.uuid = str(uuid.uuid4())
            self.log.info(f"ANX Device: Forced initialization of self.uuid to {self.uuid}")
        self.db_path = None
        self.file_dir = None
        self.cover_dir = None
        self.base_dir = None
        self.connected = False
        self.seen_device = False # Added for managed device presence
        self.books_in_device = {}
        self.booklist = AnxBookList()

    def load_actual_plugin(self, gui):
        self.gui = gui
        return self

    def is_customizable(self):
        return True

    @classmethod
    def config_widget(cls):
        return ConfigWidget()

    @classmethod
    def save_settings(cls, config_widget):
        config_widget.save_settings()

    def apply_settings(self):
        self.base_dir = prefs['device_path']
        if self.base_dir:
            self.db_path = os.path.join(self.base_dir, 'database7.db')
            self.file_dir = os.path.join(self.base_dir, 'data', 'file')
            self.cover_dir = os.path.join(self.base_dir, 'data', 'cover')
            self.connected = self.is_connect_to_this_device()
            if self.connected:
                self.log.info(f"ANX Device re-configured and connected to: {self.base_dir}")
                self.load_books_from_device()
            else:
                self.log.warning(f"ANX Device re-configured but not connected. Check path: {self.base_dir}")
        else:
            self.log.info("ANX Device path not configured after saving. Please configure it in preferences.")

    def get_gui_name(self):
        return self.gui_name
        
    def get_device_root(self):
        return prefs['device_path']

    def startup(self):
        self.apply_settings()

    def open(self, connected_device, library_uuid):
        self.log.info(f"ANX Device: open method called for {connected_device}")
        self.connected = True

    def is_usb_connected(self, devices_on_system, debug=False,
            only_presence=False):
        # We manage device presence ourselves, so this method should always
        # return False
        return True
        
    def is_connect_to_this_device(self, opts=None):
        if not self.base_dir:
            return False
        
        db_exists = os.path.exists(self.db_path)
        file_dir_exists = os.path.isdir(self.file_dir)
        cover_dir_exists = os.path.isdir(self.cover_dir)
        
        if db_exists and file_dir_exists and cover_dir_exists:
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tb_books';")
                table_exists = cursor.fetchone() is not None
                conn.close()
                return table_exists
            except Exception as e:
                self.log.error(f"Error checking database: {e}")
                return False
        return False

    def load_books_from_device(self, detected_mime=None):
        self.books_in_device = {}
        self.booklist.clear()
        if not self.connected:
            return
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, author, file_path, cover_path, file_md5 FROM tb_books WHERE is_deleted = 0;")
            
            for row in cursor.fetchall():
                book_id, title, author, file_path_rel, cover_path_rel, file_md5 = row
                
                full_file_path = os.path.join(self.base_dir, 'data', file_path_rel)
                full_cover_path = os.path.join(self.base_dir, 'cover', cover_path_rel) if cover_path_rel else None

                file_size = os.path.getsize(full_file_path) if os.path.exists(full_file_path) else 0
                file_mtime = datetime.fromtimestamp(os.path.getmtime(full_file_path)) if os.path.exists(full_file_path) else datetime.utcnow()

                anx_book_metadata = AnxBookMetadata(
                    title=title,
                    authors=[author],
                    uuid=f"anx_book_{book_id}",
                    path=full_file_path,
                    has_cover=True if full_cover_path and os.path.exists(full_cover_path) else False,
                    format_map={os.path.splitext(full_file_path)[1].lstrip('.').upper(): file_size},
                    device_id=self.uuid,
                    size=file_size,
                    datetime=file_mtime,
                    thumbnail=open(full_cover_path, 'rb').read() if full_cover_path and os.path.exists(full_cover_path) else None, # Fill thumbnail with cover image data
                    tags=[],
                    cover_path=cover_path_rel,
                    file_md5=file_md5
                )

                self.books_in_device[anx_book_metadata.uuid] = anx_book_metadata
                self.booklist.add_book(anx_book_metadata, None)
                
            conn.close()
            self.log.info(f"Loaded {len(self.books_in_device)} books from ANX device.")
        except Exception as e:
            import traceback
            self.log.error(f"Error loading books from device: {e}")
            self.log.error(traceback.format_exc())

    def detect_managed_devices(self, devices_on_system, force_refresh=False):
        # This method is called when MANAGES_DEVICE_PRESENCE is True
        if self.seen_device and not force_refresh:
            return True # Device already seen and no refresh forced
        
        device_path = prefs['device_path']
        self.log.info(f"ANX Device: detect_managed_devices - configured device_path: {device_path}")
        
        if device_path and os.path.isdir(device_path):
            self.base_dir = device_path
            self.db_path = os.path.join(self.base_dir, 'database7.db')
            self.file_dir = os.path.join(self.base_dir, 'data', 'file')
            self.cover_dir = os.path.join(self.base_dir, 'data', 'cover')
            
            is_connected = self.is_connect_to_this_device() # Corrected spelling
            self.log.info(f"ANX Device: detect_managed_devices.is_connect_to_this_device() returned: {is_connected}")
            
            if is_connected:
                self.log.info(f"ANX Device detected at: {device_path}")
                self.seen_device = True
                return True # Return a truthy value to indicate device found
            else:
                self.log.warning(f"ANX Device path is valid, but connection check failed for: {device_path}")
        else:
            self.log.info(f"ANX Device: No valid device path configured or path does not exist: {device_path}")
        self.seen_device = False
        return False

    def debug_managed_device_detection(self, devices_on_system, output):
        self.log.info("ANX Device: debug_managed_device_detection called.")
        output.write("ANX Device Plugin: Debugging managed device detection.\n")
        output.write(f"Configured device path: {prefs['device_path']}\n")
        return False # Return False as no device was successfully opened by this debug method

    def get_plugged_devices(self, all_devices):
        # This method is not used when MANAGES_DEVICE_PRESENCE is True,
        # but is kept as a fallback or for compatibility.
        # Remarkable plugin does not implement this.
        self.log.info("ANX Device: get_plugged_devices called (should not be called if MANAGES_DEVICE_PRESENCE is True).")
        return []

    def set_progress_reporter(self, report_progress):
        self.report_progress_callback = report_progress

    def get_device_information(self):
        # Returns (device name, device version, software version on device, MIME type, drive information dictionary (optional))
        # For a folder based device, device version can be a placeholder.
        # Software version can also be a placeholder.
        # MIME type is typically 'application/x-kindle-ebook' or similar, but for a generic folder, it's not strictly defined.
        return self.gui_name, 'ANX', '1.0.0', 'application/octet-stream', {'path': self.base_dir}

    def get_book_formats(self, book_id):
        book = self.books_in_device.get(book_id)
        if book and book.path:
            ext = os.path.splitext(book.path)[1].lstrip('.').upper()
            return {ext: book.path}
        return {}

    def get_can_send_to(self, fmt, mi, plugin_data):
        allowed_extensions = ["EPUB", "MOBI", "AZW3", "FB2", "TXT", "PDF"]
        return fmt.upper() in allowed_extensions

    def send_books(self, book_list, callback=None):
        sent_count = 0
        total_books = len(book_list)

        if not self.connected:
            self.log.error("ANX Device not connected. Cannot send books.")
            return []

        locations = []
        for i, src_path in enumerate(book_list):
            try:
                # Unpack the tuple for send_books
                book_id, fmt, src_path = src_path
                self.report_progress(float(i) / total_books, f'Sending book {i+1} of {total_books}')
                
                # Get Calibre's Metadata object for the current book
                db = self.gui.current_db
                book_data = db.get_metadata(book_id, get_cover=False, get_user_manual=False)
                
                title = book_data.title if book_data.title else os.path.splitext(os.path.basename(src_path))[0]
                author = book_data.authors[0] if book_data.authors else "Unknown"
                
                sanitized_title = ascii_text(title)
                sanitized_author = ascii_text(author)
                
                filename = f"{sanitized_title} - {sanitized_author}.{fmt.lower()}"
                dest_file_path = os.path.join(self.file_dir, filename)

                os.makedirs(self.file_dir, exist_ok=True)
                os.makedirs(self.cover_dir, exist_ok=True)

                shutil.copyfile(src_path, dest_file_path)
                self.log.info(f"Copied ebook from {src_path} to {dest_file_path}")

                file_md5 = hashlib.md5(open(dest_file_path, 'rb').read()).hexdigest()

                cover_path_rel = ""
                cover_filename = f"{sanitized_title} - {sanitized_author}.jpg"
                dest_cover_path = os.path.join(self.cover_dir, cover_filename)
                
                cover_data = db.get_cover(book_id, as_path=True)
                if cover_data:
                    shutil.copyfile(cover_data, dest_cover_path)
                    cover_path_rel = os.path.relpath(dest_cover_path, self.base_dir)
                    self.log.info(f"Copied cover to {dest_cover_path}")
                else:
                    self.log.warning(f"No cover found for book {title}")

                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')

                cursor.execute("SELECT id FROM tb_books WHERE file_md5 = ?;", (file_md5,))
                existing_book = cursor.fetchone()

                if existing_book:
                    self.log.info(f"Book '{title}' with MD5 '{file_md5}' already exists in device DB. Skipping insert.")
                    conn.close()
                    continue
                
                file_relative_path = os.path.relpath(dest_file_path, self.base_dir)

                sql_insert = """
                INSERT INTO tb_books (title, cover_path, file_path, author, create_time, update_time, file_md5, last_read_position, reading_percentage, is_deleted, rating, group_id, description) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                cursor.execute(sql_insert, (
                    title, 
                    cover_path_rel, 
                    file_relative_path, 
                    author, 
                    current_time, 
                    current_time, 
                    file_md5, 
                    '', 0.0, 0, 0.0, 0, ''
                ))
                conn.commit()
                book_id_from_db = cursor.lastrowid
                conn.close()
                self.log.info(f"Book '{title}' successfully added to ANX device database with ID: {book_id_from_db}.")
                sent_count += 1

                anx_book_metadata = AnxBookMetadata(
                    title=title,
                    authors=[author],
                    uuid=f"anx_book_{book_id_from_db}",
                    path=dest_file_path,
                    has_cover=True if cover_path_rel else False,
                    format_map={fmt.upper(): os.path.getsize(dest_file_path)},
                    device_id=self.uuid,
                    cover_path=cover_path_rel,
                    file_md5=file_md5
                )
                self.books_in_device[anx_book_metadata.uuid] = anx_book_metadata
                self.booklist.add_book(anx_book_metadata, None)

                locations.append(anx_book_metadata.uuid)

            except Exception as e:
                self.log.error(f"Error sending book {book_id}: {e}")
                import traceback
                self.log.error(traceback.format_exc())
                continue
        
        self.report_progress(1.0, 'Finished sending books.')
        return (locations, None, None)

    def delete_books(self, book_ids, callback=None):
        deleted_count = 0
        total_books = len(book_ids)

        if not self.connected:
            self.log.error("ANX Device not connected. Cannot delete books.")
            return []

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for i, book_uuid in enumerate(book_ids):
                self.report_progress(float(i) / total_books, f'Deleting book {i+1} of {total_books}')
                
                anx_book_metadata = self.books_in_device.get(book_uuid)
                if not anx_book_metadata:
                    self.log.warning(f"Book with UUID {book_uuid} not found in device cache. Skipping deletion.")
                    continue

                anx_book_id = book_uuid.replace('anx_book_', '')
                
                cursor.execute("SELECT file_path, cover_path FROM tb_books WHERE id = ?;", (anx_book_id,))
                result = cursor.fetchone()
                
                if result:
                    file_path_rel, cover_path_rel = result
                    full_file_path = os.path.join(self.base_dir, file_path_rel)
                    full_cover_path = os.path.join(self.base_dir, cover_path_rel) if cover_path_rel else None

                    if os.path.exists(full_file_path):
                        os.remove(full_file_path)
                        self.log.info(f"Deleted ebook file: {full_file_path}")
                    else:
                        self.log.warning(f"Ebook file not found for deletion: {full_file_path}")

                    if full_cover_path and os.path.exists(full_cover_path):
                        os.remove(full_cover_path)
                        self.log.info(f"Deleted cover file: {full_cover_path}")
                    else:
                        self.log.warning(f"Cover file not found for deletion: {full_cover_path}")
                    
                    cursor.execute("DELETE FROM tb_books WHERE id = ?;", (anx_book_id,))
                    conn.commit()
                    self.log.info(f"Book with ANX ID {anx_book_id} deleted from database.")
                    deleted_count += 1

                    if book_uuid in self.books_in_device:
                        del self.books_in_device[book_uuid]
                    self.booklist.remove_book(anx_book_metadata)

                else:
                    self.log.warning(f"Book with ANX ID {anx_book_id} not found in device database. Skipping deletion.")

        except Exception as e:
            self.log.error(f"Error deleting books: {e}")
            import traceback
            self.log.error(traceback.format_exc())
        finally:
            if conn:
                conn.close()
        
        self.report_progress(1.0, 'Finished deleting books.')
        return []

    def get_library_uuid(self, detected_device_id):
        return None

    def get_and_set_config(self, opts):
        pass

    def get_sync_key(self, book_id):
        return None

    def post_build_sync_tree(self, book_id, book_format):
        return None

    def get_book_type(self, book_id):
        return 'EBOOK'

    def get_metadata(self, book_id, allow_cache=True):
        anx_book = self.books_in_device.get(book_id)
        if anx_book:
            mi = Metadata()
            mi.title = anx_book.title
            mi.authors = anx_book.authors
            mi.uuid = anx_book.uuid
            mi.has_cover = anx_book.has_cover
            mi.path = anx_book.path
            mi.device_id = anx_book.device_id
            mi.format_map = anx_book.format_map
            return mi
        return None

    def get_file(self, book_id, fmt, allow_cache=True):
        book = self.books_in_device.get(book_id)
        if book and book.path:
            return open(book.path, 'rb')
        return None

    def get_cover(self, book_id, as_file=False):
        book = self.books_in_device.get(book_id)
        if book and book.has_cover and book.cover_path:
            cover_path = os.path.join(self.base_dir, book.cover_path)
            if os.path.exists(cover_path):
                if as_file:
                    return open(cover_path, 'rb')
                with open(cover_path, 'rb') as f:
                    return f.read()
        return None

    def get_icon(self):
        return None
    
    def free_space(self, end_session=True):
        if not self.connected:
            return (0, 0, 0)
        
        try:
            total, used, free = shutil.disk_usage(self.base_dir)
            return (free, total, 0)
        except Exception as e:
            self.log.error(f"Error getting free space: {e}")
            return (0, 0, 0)

    def total_space(self, end_session=True):
        if not self.connected:
            return (0, 0, 0)
        
        try:
            total, used, free = shutil.disk_usage(self.base_dir)
            return (total, total, 0)
        except Exception as e:
            self.log.error(f"Error getting total space: {e}")
            return (0, 0, 0)

    @classmethod
    def remove_books_from_metadata(cls, paths, booklists):
        anx_booklist = booklists[0]
        
        to_remove_uuids = []
        for p in paths:
            if p.startswith('anx_book_'):
                to_remove_uuids.append(p)
            elif ':' in p:
                parts = p.split(':')
                if len(parts) > 1 and parts[1].startswith('anx_book_'):
                    to_remove_uuids.append(parts[1])
            else:
                for book_uuid in list(anx_booklist.uuids_in_list):
                    anx_book = anx_booklist.books_by_uuid.get(book_uuid)
                    if anx_book and anx_book.path == p:
                        to_remove_uuids.append(anx_book.uuid)
                        break

        updated_books_in_list = []
        for book_uuid in list(anx_booklist.uuids_in_list):
            if book_uuid not in to_remove_uuids:
                updated_books_in_list.append(anx_booklist.books_by_uuid[book_uuid])
        
        anx_booklist.clear()
        for book in updated_books_in_list:
            anx_booklist.add_book(book, None)

    def upload_books(self, files, names, on_card=None, end_session=True, metadata=None):
        sent_count = 0
        total_books = len(files)

        if not self.connected:
            self.log.error("ANX Device not connected. Cannot send books.")
            return []

        locations = []
        for i, src_path in enumerate(files):
            try:
                self.report_progress(float(i) / total_books, f'Sending book {i+1} of {total_books}')
                
                book_data = metadata[i]
                
                title = book_data.title if book_data.title else os.path.splitext(os.path.basename(src_path))[0]
                author = book_data.authors[0] if book_data.authors else "Unknown"
                
                sanitized_title = ascii_text(title)
                sanitized_author = ascii_text(author)
                
                fmt = os.path.splitext(src_path)[1].lstrip('.').lower()
                if not fmt:
                    fmt = 'epub'

                filename = f"{sanitized_title} - {sanitized_author}.{fmt}"
                dest_file_path = os.path.join(self.file_dir, filename)

                os.makedirs(self.file_dir, exist_ok=True)
                os.makedirs(self.cover_dir, exist_ok=True)

                shutil.copyfile(src_path, dest_file_path)
                self.log.info(f"Copied ebook from {src_path} to {dest_file_path}")

                file_md5 = hashlib.md5(open(dest_file_path, 'rb').read()).hexdigest()

                cover_path_rel = ""
                cover_filename = f"{sanitized_title} - {sanitized_author}.jpg"
                dest_cover_path = os.path.join(self.cover_dir, cover_filename)
                
                cover_data = book_data.cover_data[0] if book_data.cover_data else None
                if cover_data:
                    with open(dest_cover_path, 'wb') as f:
                        f.write(cover_data[1])
                    cover_path_rel = os.path.relpath(dest_cover_path, self.base_dir)
                    self.log.info(f"Copied cover to {dest_cover_path}")
                else:
                    self.log.warning(f"No cover found for book {title}")

                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')

                cursor.execute("SELECT id FROM tb_books WHERE file_md5 = ?;", (file_md5,))
                existing_book = cursor.fetchone()

                if existing_book:
                    self.log.info(f"Book '{title}' with MD5 '{file_md5}' already exists in device DB. Skipping insert.")
                    conn.close()
                    continue
                
                file_relative_path = os.path.relpath(dest_file_path, self.base_dir)

                sql_insert = """
                INSERT INTO tb_books (title, cover_path, file_path, author, create_time, update_time, file_md5, last_read_position, reading_percentage, is_deleted, rating, group_id, description) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                cursor.execute(sql_insert, (
                    title, 
                    cover_path_rel, 
                    file_relative_path, 
                    author, 
                    current_time, 
                    current_time, 
                    file_md5, 
                    '', 0.0, 0, 0.0, 0, ''
                ))
                conn.commit()
                book_id_from_db = cursor.lastrowid
                conn.close()
                self.log.info(f"Book '{title}' successfully added to ANX device database with ID: {book_id_from_db}.")
                sent_count += 1

                anx_book_metadata = AnxBookMetadata(
                    title=title,
                    authors=[author],
                    uuid=f"anx_book_{book_id_from_db}",
                    path=dest_file_path,
                    has_cover=True if cover_path_rel else False,
                    format_map={fmt.upper(): os.path.getsize(dest_file_path)},
                    device_id=self.uuid,
                    cover_path=cover_path_rel,
                    file_md5=file_md5
                )
                self.books_in_device[anx_book_metadata.uuid] = anx_book_metadata
                self.booklist.add_book(anx_book_metadata, None)

                locations.append(anx_book_metadata.uuid)

            except Exception as e:
                self.log.error(f"Error sending book {book_id}: {e}")
                import traceback
                self.log.error(traceback.format_exc())
                continue
        
        self.report_progress(1.0, 'Finished sending books.')
        return (locations, None, None)

    def books(self, oncard=None):
        return self.booklist

    def list(self, path, recurse=False):
        # This method is called by calibre/devices/cli.py for 'ls' command
        # It should return a list of tuples: (directory_path, [list of AnxFile objects])
        self.log.info(f"ANX Device: list method called for path: {path}, recurse: {recurse}")
        
        results = []
        if path == '/' or path == 'card:/':
            files_in_root = []
            # Add a dummy 'books' directory
            books_dir_path = os.path.join(path, 'books')
            files_in_root.append(AnxFile('books', books_dir_path, is_dir=True))

            # Add all books as files under the 'books' directory if recurse is True
            if recurse:
                for book_uuid, book_meta in self.books_in_device.items():
                    file_name = os.path.basename(book_meta.path)
                    file_path_on_device = os.path.join(books_dir_path, file_name)
                    files_in_root.append(AnxFile(
                        file_name,
                        file_path_on_device,
                        is_dir=False,
                        size=book_meta.size,
                        ctime=book_meta.datetime.timestamp(),
                        wtime=book_meta.datetime.timestamp()
                    ))
            results.append((path, files_in_root))
        
        # If a specific directory like '/books' is requested and not recursing
        elif path.endswith('/books') or path.endswith('/books/'):
            files_in_books = []
            for book_uuid, book_meta in self.books_in_device.items():
                file_name = os.path.basename(book_meta.path)
                file_path_on_device = os.path.join(path, file_name)
                files_in_books.append(AnxFile(
                    file_name,
                    file_path_on_device,
                    is_dir=False,
                    size=book_meta.size,
                    ctime=book_meta.datetime.timestamp(),
                    wtime=book_meta.datetime.timestamp()
                ))
            results.append((path, files_in_books))
        
        return results

    def do_user_manual(self, gui):
        self.gui.job_manager.show_message('ANX Device Plugin: Manage ebooks in your custom ANX folder structure. Configure the device path in Calibre Preferences -> Plugins -> Device Plugins -> ANX Virtual Device -> Customize plugin.')
