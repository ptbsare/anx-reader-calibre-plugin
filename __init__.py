# anx_device_plugin/__init__.py

import os, stat, re, hashlib, json, time, uuid
import sqlite3
from datetime import datetime
import shutil

from calibre.devices.usbms.driver import USBMS, BookList
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

from calibre.ebooks.metadata.book.base import Metadata

class AnxBookMetadata(Metadata): # Inherit from Metadata
    def __init__(self, title: str, authors: List[str], uuid: str, path: str, has_cover: bool, format_map: dict, device_id: str, size: int = 0, dt_obj: datetime = None, thumbnail: bytes = None, tags: List[str] = None, cover_path: str = None, file_md5: str = None):
        super().__init__(title, authors) # Call parent constructor with title and authors
        self.uuid = uuid
        self.path = path
        self.has_cover = has_cover
        self.format_map = format_map
        self.device_id = device_id
        self.size = size
        self.datetime = dt_obj if dt_obj is not None else datetime.utcnow() # Use dt_obj to avoid name collision
        self.thumbnail = thumbnail
        self.tags = tags if tags is not None else []
        self.cover_path = cover_path
        self.file_md5 = file_md5
        self.device_collections = [] # Add device_collections attribute
        # Remove get() and get_all_user_metadata() as they are provided by Metadata base class


class AnxDevicePlugin(USBMS): # Change base class to USBMS
    name                = 'ANX Virtual Device'
#    gui_name            = 'ANX Device'
    gui_name = _('ANX Device')
    icon = 'devices/tablet.png'
    description         = 'Connects to a custom folder structure with a database7.db file for managing ebooks.'
    author              = 'Gemini AI based on user script'
    version             = (1, 0, 0)
    supported_platforms = ['windows', 'osx', 'linux']
    capabilities        = frozenset(['send_books', 'delete_books', 'has_user_manual'])
    FORMATS             = ["epub", "mobi", "azw3", "fb2", "txt", "pdf"]
    MANAGES_DEVICE_PRESENCE = True # Set to True as per Remarkable plugin
    ASK_TO_ALLOW_CONNECT = True # Enable user approval for connection

    # Add dummy USB IDs to simulate a USB device
    VENDOR_ID = [0xAAAA] # Use a unique dummy Vendor ID
    PRODUCT_ID = [0xBBBB] # Use a unique dummy Product ID
    BCD = [0xCCCC] # Use a unique dummy BCD

    config_spec = JSONConfig('plugins/anx_device_plugin')
    config_spec.defaults['device_path'] = ''
    config_spec.defaults['blacklisted_devices'] = {} # Initialize blacklisted devices

    def __init__(self, plugin_path):
        # Initialize USBMS with a dummy path for now, actual path set in apply_settings
        super().__init__(plugin_path) # Call USBMS's __init__ or DevicePlugin's __init__
        self.gui = None
        self.prefs = prefs
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
        # USBMS specific properties, ensure they are initialized
        self._main_prefix = ''
        self._card_a_prefix = None  # Ensure card_a is not displayed
        self._card_b_prefix = None  # Ensure card_b is not displayed
        self.is_connected = False

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
                # Update USBMS internal state
                self._main_prefix = self.base_dir + os.sep if not self.base_dir.endswith(os.sep) else self.base_dir
                self.is_connected = True
            else:
                self.log.warning(f"ANX Device re-configured but not connected. Check path: {self.base_dir}")
                self.is_connected = False # Ensure USBMS state is updated
        else:
            self.log.info("ANX Device path not configured after saving. Please configure it in preferences.")
            self.is_connected = False # Ensure USBMS state is updated

    def get_gui_name(self):
        return self.gui_name
        
    def get_device_root(self):
        return prefs['device_path']

    def startup(self):
        self.apply_settings()

    def is_usb_connected(self, devices_on_system, debug=False,
            only_presence=False):
        # Override USBMS's is_usb_connected to report our connection status
        # This is crucial for Calibre GUI to detect the device
        self.log.info(f"ANX Device: is_usb_connected called. Returning {self.is_connected}, {self}")
        return self.is_connected, self

    def open(self, connected_device, library_uuid):
        self.log.info(f"ANX Device: open method called for {connected_device}")
        # Ensure base_dir is set if it wasn't already (e.g., from managed detection)
        if not self.base_dir and isinstance(connected_device, str) and connected_device.startswith(FAKE_DEVICE_SERIAL):
            self.base_dir = connected_device.replace(FAKE_DEVICE_SERIAL, '')
            self.db_path = os.path.join(self.base_dir, 'database7.db')
            self.file_dir = os.path.join(self.base_dir, 'data', 'file')
            self.cover_dir = os.path.join(self.base_dir, 'data', 'cover')
            # Also update USBMS internal path
            self._main_prefix = self.base_dir + os.sep if not self.base_dir.endswith(os.sep) else self.base_dir

        self.connected = True
        self.is_connected = True # Update USBMS internal state
        self.current_library_uuid = library_uuid # USBMS expects this
        self.load_books_from_device() # Load books when opened
        return True # Indicate successful open

        
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
                self.log.info(f"ANX Device: load_books_from_device - book_id: {book_id}, cover_path_rel from DB: {cover_path_rel}")
                
                full_file_path = os.path.join(self.base_dir, 'data', file_path_rel)
                full_cover_path = os.path.join(self.base_dir, 'data', cover_path_rel) if cover_path_rel else None

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
                    dt_obj=file_mtime.timetuple()[:6], # Convert datetime object to a tuple (year, month, day, hour, minute, second)
                    thumbnail=open(full_cover_path, 'rb').read() if full_cover_path and os.path.exists(full_cover_path) else None, # Fill thumbnail with cover image data
                    tags=[],
                    cover_path=full_cover_path,
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
        self.report_progress = report_progress # Assign to self.report_progress

    def get_device_information(self, end_session=True): # Add end_session parameter
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
                # Get full metadata including cover_data
                current_metadata = db.get_metadata(book_id, get_cover=True, get_user_manual=False)
                
                title = current_metadata.title if current_metadata.title else os.path.splitext(os.path.basename(src_path))[0]
                author = current_metadata.authors[0] if current_metadata.authors else "Unknown"
                
                sanitized_title = ascii_text(title)
                sanitized_author = ascii_text(author)
                
                fmt = os.path.splitext(src_path)[1].lstrip('.').lower()
                if not fmt:
                    fmt = 'epub' # Default format if not found

                filename = f"{sanitized_title} - {sanitized_author}.{fmt}"
                dest_file_path = os.path.join(self.file_dir, filename)

                os.makedirs(self.file_dir, exist_ok=True)
                os.makedirs(self.cover_dir, exist_ok=True)

                shutil.copyfile(src_path, dest_file_path)
                self.log.info(f"Copied ebook from {src_path} to {dest_file_path}")

                file_size = os.path.getsize(dest_file_path) # Get file size after copy
                file_md5 = hashlib.md5(open(dest_file_path, 'rb').read()).hexdigest()

                cover_path_rel = ""
                # Use cover_data from the Metadata object to get the format
                if current_metadata.cover_data and current_metadata.cover_data[1]:
                    cover_format = current_metadata.cover_data[0].lower() # e.g., 'jpeg', 'png'
                    cover_extension = '.jpg' # Default to jpg
                    if cover_format in ['jpeg', 'jpg']:
                        cover_extension = '.jpg'
                    elif cover_format == 'png':
                        cover_extension = '.png'
                    elif cover_format == 'gif':
                        cover_extension = '.gif'

                    cover_filename = f"{sanitized_title} - {sanitized_author}{cover_extension}"
                    dest_cover_path = os.path.join(self.cover_dir, cover_filename)
                    try:
                        with open(dest_cover_path, 'wb') as f:
                            f.write(current_metadata.cover_data[1])
                        cover_path_rel = os.path.relpath(dest_cover_path, self.base_dir)
                        self.log.info(f"Copied cover to {dest_cover_path} with format {cover_format}.")
                    except Exception as ce:
                        self.log.error(f"Error copying cover data to {dest_cover_path}: {ce}")
                        cover_path_rel = "" # Reset cover_path_rel if copy fails
                else:
                    self.log.warning(f"No cover data found for book {title}.")

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
                    format_map={fmt.upper(): file_size}, # Use the obtained file_size
                    device_id=self.uuid,
                    cover_path=cover_path_rel,
                    file_md5=file_md5,
                    size=file_size # Explicitly set size
                )
                self.books_in_device[anx_book_metadata.uuid] = anx_book_metadata
                self.booklist.add_book(anx_book_metadata, None)

                # Append (filepath, on_card) for USBMS.add_books_to_metadata
                locations.append((dest_file_path, on_card))

            except Exception as e:
                self.log.error(f"Error sending book {book_id}: {e}")
                import traceback
                self.log.error(traceback.format_exc())
                continue
        
        self.report_progress(1.0, 'Finished sending books.')
        return (locations, None, None)

    def delete_books(self, book_ids, callback=None, end_session=True):
        self.log.info(f"ANX Device: delete_books called with book_ids: {book_ids}")
        deleted_count = 0
        total_to_delete = len(book_ids)

        if not self.connected:
            self.log.error("ANX Device not connected. Cannot delete books.")
            return []

        # Create a mapping from file path to AnxBookMetadata for efficient lookup
        books_to_delete_map = {}
        for book_meta in self.booklist:
            # Normalize path for comparison (e.g., ensure consistent separators)
            normalized_path = os.path.normpath(book_meta.path)
            books_to_delete_map[normalized_path] = book_meta
            books_to_delete_map[book_meta.uuid] = book_meta # Also map by UUID

        books_to_remove_from_db = []

        self.log.info(f"ANX Device: Current books in device cache (paths): {[os.path.normpath(b.path) for b in self.booklist]}")
        self.log.info(f"ANX Device: Current books in device cache (UUIDs): {[b.uuid for b in self.booklist]}")

        for item_to_delete in book_ids:
            self.log.info(f"ANX Device: Attempting to delete item: {item_to_delete}")
            book_to_delete = None

            # Try to find by normalized path first
            normalized_item = os.path.normpath(item_to_delete)
            book_to_delete = books_to_delete_map.get(normalized_item)

            if not book_to_delete:
                # If not found by path, try to find by UUID
                book_to_delete = books_to_delete_map.get(item_to_delete)

            if book_to_delete:
                # Use the absolute paths directly from AnxBookMetadata
                book_path = book_to_delete.path
                cover_path = book_to_delete.cover_path
                self.log.info(f"ANX Device: Found book in cache. Path: {book_path}, Cover Path: {cover_path}")

                # Delete file
                if os.path.exists(book_path):
                    try:
                        os.remove(book_path)
                        self.log.info(f"ANX Device: Successfully deleted file: {book_path}")
                        deleted_count += 1
                    except Exception as e:
                        self.log.error(f"ANX Device: Error deleting file {book_path}: {e}", exc_info=True)
                else:
                    self.log.info(f"ANX Device: File not found on disk: {book_path}")

                # Delete cover file
                if cover_path and os.path.exists(cover_path):
                    try:
                        os.remove(cover_path)
                        self.log.info(f"ANX Device: Successfully deleted cover file: {cover_path}")
                    except Exception as e:
                        self.log.error(f"ANX Device: Error deleting cover file {cover_path}: {e}", exc_info=True)

                books_to_remove_from_db.append(book_to_delete)
            else:
                self.log.warning(f"ANX Device: Book or path '{item_to_delete}' not found in device cache. Skipping deletion.")

        # Remove from database
        if books_to_remove_from_db:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                for book in books_to_remove_from_db:
                    # Assuming UUID is stored as 'anx_book_ID'
                    anx_book_id = book.uuid.replace('anx_book_', '')
                    cursor.execute("DELETE FROM tb_books WHERE id = ?", (anx_book_id,))
                    self.log.info(f"ANX Device: Deleted book with ANX ID {anx_book_id} from database.")
                conn.commit()
            except Exception as e:
                self.log.error(f"ANX Device: Error deleting books from database: {e}", exc_info=True)
            finally:
                if conn:
                    conn.close()

        # Update internal booklist
        for book in books_to_remove_from_db:
            if book.uuid in self.books_in_device:
                del self.books_in_device[book.uuid]
            self.booklist.remove_book(book)
            self.log.info(f"ANX Device: Removed book {book.title} ({book.uuid}) from internal booklist.")

        self.report_progress(1.0, 'Finished deleting books.')
        return True # Return True for success, as per interface for USBMS.delete_books
    
    def card_prefix(self, end_session=True):
        return None, None

    def eject(self):
        self.is_connected = False


    def settings(self):
        return Opts(self.FORMATS)

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
            cover_path = book.cover_path # It's already an absolute path from load_books_from_device
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

                locations.append((dest_file_path, on_card)) # Return (filepath, on_card) for USBMS.add_books_to_metadata

            except Exception as e:
                self.log.error(f"Error sending book {os.path.basename(src_path)}: {e}") # Use src_path for logging
                import traceback
                self.log.error(traceback.format_exc())
                continue
        
        self.report_progress(1.0, 'Finished sending books.')
        return locations # Return only locations list

    def books(self, oncard=None, end_session=True): # Add end_session parameter
        return self.booklist

    def get_device_uid(self):
        # Return a unique ID for the device. For a virtual device, use its UUID.
        self.log.info(f"ANX Device: get_device_uid called, returning {self.uuid}")
        return self.uuid

    def ignore_connected_device(self, uid):
        # Add the device UID to the blacklist.
        self.log.info(f"ANX Device: ignore_connected_device called for UID: {uid}")
        blacklisted_devices = self.get_user_blacklisted_devices()
        if uid not in blacklisted_devices:
            blacklisted_devices[uid] = f"ANX Device ({uid})" # Store with a friendly name
            self.set_user_blacklisted_devices(blacklisted_devices)
            self.log.info(f"ANX Device: Added {uid} to blacklist.")
        
        # Reset plugin state as per interface documentation
        self.seen_device = False
        self.connected = False
        self.books_in_device = {}
        self.booklist.clear()

    def get_user_blacklisted_devices(self):
        # Return a dictionary of blacklisted devices (UID -> friendly name).
        return self.prefs.get('blacklisted_devices', {})

    def set_user_blacklisted_devices(self, devices):
        # Set the blacklisted devices.
        self.prefs['blacklisted_devices'] = devices
        self.prefs.commit() # Save changes to config file

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

class Opts:
    def __init__(self, format_map):
        self.format_map = format_map