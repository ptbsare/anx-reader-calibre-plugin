# anx_device_plugin/__init__.py

import os, stat, re, hashlib, json, time, uuid
import sqlite3
from datetime import datetime
import shutil

from calibre.devices.usbms.driver import USBMS
from calibre.utils.config import JSONConfig
from calibre.utils.logging import default_log
from PyQt5.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget
from calibre.devices.usbms.books import Book as USBMSBook, CollectionsBookList # Import Book as USBMSBook and CollectionsBookList
from calibre.library import db # Import calibre.library.db

# Import the custom ConfigWidget and preferences object
from .config import ConfigWidget, prefs

# Define FAKE_DEVICE_SERIAL globally for consistent use
FAKE_DEVICE_SERIAL = 'ANX_VIRTUAL_DEVICE_PATH:'

class AnxDevicePlugin(USBMS): # Change base class to USBMS
    name                = 'ANX Virtual Device'
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
    CAN_SET_METADATA = ['title', 'authors']
    # Add dummy USB IDs to simulate a USB device
    VENDOR_ID = [0xAAAA] # Use a unique dummy Vendor ID
    PRODUCT_ID = [0xBBBB] # Use a unique dummy Product ID
    BCD = [0xCCCC] # Use a unique dummy BCD

    config_spec = JSONConfig('plugins/anx_device_plugin')
    config_spec.defaults['device_path'] = ''
    config_spec.defaults['blacklisted_devices'] = {} # Initialize blacklisted devices

    def __init__(self, plugin_path):
        super().__init__(plugin_path) # Call USBMS's __init__ or DevicePlugin's __init__
        self.gui = None
        self.prefs = prefs
        self.log = default_log
        if not hasattr(self, 'uuid') or not self.uuid:
            self.uuid = str(uuid.uuid4())
            self.log.debug(f"ANX Device: Forced initialization of self.uuid to {self.uuid}")
        self.db_path = None
        self.file_dir = None
        self.cover_dir = None
        self.base_dir = None
        self.connected = False
        self.seen_device = False # Added for managed device presence
        self.books_in_device = {} # Manually initialize books_in_device
        # Use CollectionsBookList as it handles collections and is preferred
        self._main_prefix = prefs['device_path'] + os.sep if prefs['device_path'] else None
        self._card_a_prefix = None
        self._card_b_prefix = None
        self.booklist = CollectionsBookList(prefix=self._main_prefix, settings=None, oncard=None)
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
        # Reset connection status initially
        self.connected = False
        self.is_connected = False
        self.db_path = None
        self.file_dir = None
        self.cover_dir = None

        if not self.base_dir:
            self.log.debug("ANX Device path not configured after saving. Please configure it in preferences.")
            return # Exit early if base_dir is not configured

        self.db_path = os.path.join(self.base_dir, 'database7.db')
        self.file_dir = os.path.join(self.base_dir, 'data', 'file')
        self.cover_dir = os.path.join(self.base_dir, 'data', 'cover')

        # Validate paths immediately after setting them
        if not os.path.isdir(self.base_dir):
            self.log.warning(f"ANX Device: Base directory does not exist or is not a directory: {self.base_dir}")
            return # Exit early if base_dir is invalid

        if not os.path.isfile(self.db_path):
            self.log.warning(f"ANX Device: Database file not found: {self.db_path}")
            return # Exit early if db_path is invalid

        if not os.path.isdir(self.file_dir):
            self.log.warning(f"ANX Device: File directory does not exist or is not a directory: {self.file_dir}")
            return # Exit early if file_dir is invalid

        if not os.path.isdir(self.cover_dir):
            self.log.warning(f"ANX Device: Cover directory does not exist or is not a directory: {self.cover_dir}")
            return # Exit early if cover_dir is invalid

        # If all paths are valid, proceed with connection check
        self.connected = self.is_connect_to_this_device()
        if self.connected:
            self.log.debug(f"ANX Device re-configured and connected to: {self.base_dir}")
            self.load_books_from_device()
            # Update USBMS internal state
            self._main_prefix = self.base_dir + os.sep if not self.base_dir.endswith(os.sep) else self.base_dir
            self.booklist.prefix = self._main_prefix # Update booklist's prefix
            self.is_connected = True
        else:
            self.log.warning(f"ANX Device re-configured but not connected. Check path and database: {self.base_dir}")
            # is_connected is already False

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
        # Ensure apply_settings has been called to set self.is_connected correctly
        self.apply_settings() # Re-apply settings to ensure paths are set and checked

        # If base_dir is not valid, ensure the device is reported as not connected
        if not self.base_dir or not os.path.isdir(self.base_dir) or \
           not self.db_path or not os.path.isfile(self.db_path) or \
           not self.file_dir or not os.path.isdir(self.file_dir) or \
           not self.cover_dir or not os.path.isdir(self.cover_dir):
            self.connected = False
            self.is_connected = False
            self.log.debug(f"ANX Device: is_usb_connected - Invalid paths detected. Reporting not connected.")
            return False, self # Explicitly return False if paths are invalid

        self.log.debug(f"ANX Device: is_usb_connected called. Returning {self.is_connected}, {self}")
        return self.is_connected, self

    def open(self, connected_device, library_uuid):
        self.log.debug(f"ANX Device: open method called for {connected_device}")
        # Ensure base_dir is set if it wasn't already (e.g., from managed detection)
        # Also ensure paths are validated
        if isinstance(connected_device, str) and connected_device.startswith(FAKE_DEVICE_SERIAL):
            self.base_dir = connected_device.replace(FAKE_DEVICE_SERIAL, '')
            self.db_path = os.path.join(self.base_dir, 'database7.db')
            self.file_dir = os.path.join(self.base_dir, 'data', 'file')
            self.cover_dir = os.path.join(self.base_dir, 'data', 'cover')
            # Also update USBMS internal path
            self._main_prefix = self.base_dir + os.sep if not self.base_dir.endswith(os.sep) else self.base_dir
        
        # Validate paths after setting base_dir
        # If any path is invalid, set connected status to False and return False
        if not self.base_dir or not os.path.isdir(self.base_dir):
            self.log.error(f"ANX Device: Invalid base directory during open: {self.base_dir}")
            self.connected = False
            self.is_connected = False
            return False
        
        if not self.db_path or not os.path.isfile(self.db_path):
            self.log.error(f"ANX Device: Database file not found during open: {self.db_path}")
            self.connected = False
            self.is_connected = False
            return False
        
        if not self.file_dir or not os.path.isdir(self.file_dir):
            self.log.error(f"ANX Device: File directory not found during open: {self.file_dir}")
            self.connected = False
            self.is_connected = False
            return False
        
        if not self.cover_dir or not os.path.isdir(self.cover_dir):
            self.log.error(f"ANX Device: Cover directory not found during open: {self.cover_dir}")
            self.connected = False
            self.is_connected = False
            return False

        self.connected = True
        self.is_connected = True # Update USBMS internal state
        self.current_library_uuid = library_uuid # USBMS expects this
        self.load_books_from_device() # Load books when opened
        return True # Indicate successful open

        
    def is_connect_to_this_device(self, opts=None):
        # Ensure paths are valid before attempting DB connection
        if not self.base_dir or not os.path.isdir(self.base_dir):
            self.log.debug(f"ANX Device: Connection check failed. Base directory invalid: {self.base_dir}")
            return False
        
        if not self.db_path or not os.path.isfile(self.db_path):
            self.log.debug(f"ANX Device: Connection check failed. Database file invalid: {self.db_path}")
            return False
        
        if not self.file_dir or not os.path.isdir(self.file_dir):
            self.log.debug(f"ANX Device: Connection check failed. File directory invalid: {self.file_dir}")
            return False
        
        if not self.cover_dir or not os.path.isdir(self.cover_dir):
            self.log.debug(f"ANX Device: Connection check failed. Cover directory invalid: {self.cover_dir}")
            return False
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tb_books';")
            table_exists = cursor.fetchone() is not None
            conn.close()
            if not table_exists:
                self.log.warning(f"ANX Device: 'tb_books' table not found in database: {self.db_path}")
            return table_exists
        except Exception as e:
            self.log.error(f"ANX Device: Error checking database {self.db_path}: {e}", exc_info=True)
            return False

    def load_books_from_device(self, detected_mime=None):
        # Clear USBMS's internal booklist and books_in_device before reloading
        # These are properties of the USBMS base class
        self.books_in_device.clear()
        self.booklist.clear()
        
        # Ensure paths are valid before attempting DB connection
        if not self.base_dir or not os.path.isdir(self.base_dir) or \
           not self.db_path or not os.path.isfile(self.db_path) or \
           not self.file_dir or not os.path.isdir(self.file_dir) or \
           not self.cover_dir or not os.path.isdir(self.cover_dir):
            self.log.error(f"ANX Device: Cannot load books. Invalid device paths detected. Base: {self.base_dir}, DB: {self.db_path}, File: {self.file_dir}, Cover: {self.cover_dir}")
            return # Exit early if paths are invalid
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # Select all columns from tb_books to store in user_metadata
            cursor.execute("""
                SELECT id, title, author, file_path, cover_path, file_md5,
                       create_time, update_time, last_read_position,
                       reading_percentage, is_deleted, rating, group_id, description
                FROM tb_books WHERE is_deleted != 1;
            """)
            
            for row in cursor.fetchall():
                (book_id, title, author, file_path_rel, cover_path_rel, file_md5,
                 create_time, update_time, last_read_position,
                 reading_percentage, is_deleted, rating, group_id, description) = row
                
                self.log.debug(f"ANX Device: load_books_from_device - book_id: {book_id}, cover_path_rel from DB: {cover_path_rel}")
                
                # Normalize paths from DB to current OS path style before joining
                normalized_file_path_rel = os.path.normpath(file_path_rel)
                normalized_cover_path_rel = os.path.normpath(cover_path_rel) if cover_path_rel else None

                full_file_path = os.path.join(self.base_dir, 'data', normalized_file_path_rel)
                full_cover_path = os.path.join(self.base_dir, 'data', normalized_cover_path_rel) if normalized_cover_path_rel else None

                file_size = os.path.getsize(full_file_path) if os.path.exists(full_file_path) else 0
                file_mtime = datetime.fromtimestamp(os.path.getmtime(full_file_path)) if os.path.exists(full_file_path) else datetime.utcnow()

                # Create a USBMS.Book object directly
                # USBMS.Book constructor: __init__(self, prefix, lpath, size, mtime=None, is_dir=False, is_readonly=False, extra_metadata={})
                # We need to provide a relative path (lpath) to the book within the device prefix.
                lpath = os.path.relpath(full_file_path, self.base_dir)
                
                book = USBMSBook( # Use USBMSBook
                    prefix=self.base_dir,
                    lpath=lpath,
                    size=file_size,
                )
                book.uuid = str(uuid.uuid4()) # Manually generate UUID
                book.datetime = file_mtime.timetuple() # Set datetime attribute as a full time tuple
                book.is_dir = False # Set is_dir attribute after creation
                book.is_readonly = True # Set is_readonly attribute after creation

                # Store ANX specific metadata as user_metadata, including all extended attributes
                book.set_user_metadata('#anx_db_id', {'datatype': 'int', 'is_multiple': False, '#value#': book_id})
                book.set_user_metadata('#anx_file_path', {'datatype': 'text', 'is_multiple': False, '#value#': file_path_rel or ''})
                book.set_user_metadata('#anx_cover_path', {'datatype': 'text', 'is_multiple': False, '#value#': cover_path_rel or ''})
                book.set_user_metadata('#anx_file_md5', {'datatype': 'text', 'is_multiple': False, '#value#': file_md5 or ''})
                book.set_user_metadata('#anx_create_time', {'datatype': 'datetime', 'is_multiple': False, '#value#': create_time or ''})
                book.set_user_metadata('#anx_update_time', {'datatype': 'datetime', 'is_multiple': False, '#value#': update_time or ''})
                book.set_user_metadata('#anx_last_read_position', {'datatype': 'text', 'is_multiple': False, '#value#': last_read_position or ''})
                book.set_user_metadata('#anx_reading_percentage', {'datatype': 'float', 'is_multiple': False, '#value#': reading_percentage or 0.0})
                book.set_user_metadata('#anx_is_deleted', {'datatype': 'int', 'is_multiple': False, '#value#': is_deleted or 1})
                book.set_user_metadata('#anx_rating', {'datatype': 'float', 'is_multiple': False, '#value#': rating or 0.0})
                book.set_user_metadata('#anx_group_id', {'datatype': 'int', 'is_multiple': False, '#value#': group_id or 0})
                book.set_user_metadata('#anx_description', {'datatype': 'text', 'is_multiple': False, '#value#': description or ''})

                # Populate standard Book attributes from DB
                book.title = title
                book.authors = [author] if author else [_('Unknown')]
                # Calibre will assign a UUID. We will use user_metadata for our internal ID.
                # book.uuid = f"anx_book_{book_id}" # Removed manual UUID setting
                # default_log.info(f"ANX Device: load_books_from_device - Set book.uuid to: {book.uuid}") # Removed log
                book.has_cover = True if full_cover_path and os.path.exists(full_cover_path) else False
                book.format_map = {os.path.splitext(full_file_path)[1].lstrip('.').upper(): file_size}
                book.device_id = self.uuid
                book.in_library = False # Device books are not in library by default
                book.device_collections = [] # Initialize as empty list

                # If cover exists, load thumbnail
                if book.has_cover:
                    try:
                        with open(full_cover_path, 'rb') as f:
                            book.thumbnail = f.read()
                    except Exception as ce:
                        self.log.error(f"Error loading thumbnail for {title}: {ce}")
                        book.thumbnail = None
                else:
                    book.thumbnail = None

                self.books_in_device[book.uuid] = book
                self.booklist.add_book(book, None) # Use USBMS's BookList.add_book method (which handles duplicates)
                
            conn.close()
            self.log.debug(f"Loaded {len(self.books_in_device)} books from ANX device.")
        except Exception as e:
            import traceback
            self.log.error(f"Error loading books from device: {e}")
            self.log.error(traceback.format_exc())

    def detect_managed_devices(self, devices_on_system, force_refresh=False):
        # This method is called when MANAGES_DEVICE_PRESENCE is True
        # It should return True only if the device is actually present and ready for connection.
        
        device_path = prefs['device_path']
        self.log.debug(f"ANX Device: detect_managed_devices - configured device_path: {device_path}")
        
        # Immediate check for valid device path
        if not device_path or not os.path.isdir(device_path):
            self.log.debug(f"ANX Device: No valid device path configured or path does not exist: {device_path}. Not detecting device.")
            self.seen_device = False
            self.connected = False
            self.is_connected = False
            return False # Return False if path is invalid or not configured
        
        # Set base_dir and sub-paths
        self.base_dir = device_path
        self.db_path = os.path.join(self.base_dir, 'database7.db')
        self.file_dir = os.path.join(self.base_dir, 'data', 'file')
        self.cover_dir = os.path.join(self.base_dir, 'data', 'cover')

        # Perform comprehensive path validation before proceeding to DB check
        if not os.path.isfile(self.db_path) or \
           not os.path.isdir(self.file_dir) or \
           not os.path.isdir(self.cover_dir):
            self.log.debug(f"ANX Device: Sub-paths invalid for {self.base_dir}. Not detecting device.")
            self.seen_device = False
            self.connected = False
            self.is_connected = False
            return False # Return False if any sub-path is invalid

        # If all paths are valid, then perform the full connection check using is_connect_to_this_device
        is_connected = self.is_connect_to_this_device()
        self.log.debug(f"ANX Device: detect_managed_devices.is_connect_to_this_device() returned: {is_connected}")
        
        if is_connected:
            self.log.debug(f"ANX Device detected at: {device_path}")
            self.seen_device = True
            self.connected = True
            self.is_connected = True
            return True # Return True if device is fully connected
        else:
            self.log.warning(f"ANX Device path is valid, but connection check failed for: {device_path}")
            self.seen_device = False
            self.connected = False
            self.is_connected = False
            return False

    def debug_managed_device_detection(self, devices_on_system, output):
        self.log.debug("ANX Device: debug_managed_device_detection called.")
        output.write("ANX Device Plugin: Debugging managed device detection.\n")
        output.write(f"Configured device path: {prefs['device_path']}\n")
        return False # Return False as no device was successfully opened by this debug method

    def get_plugged_devices(self, all_devices):
        self.log.debug("ANX Device: get_plugged_devices called (should not be called if MANAGES_DEVICE_PRESENCE is True).")
        return []

    def set_progress_reporter(self, report_progress):
        self.report_progress = report_progress # Assign to self.report_progress

    def get_device_information(self, end_session=True):
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


    def delete_books(self, book_ids, callback=None, end_session=True):
        self.log.debug(f"ANX Device: delete_books called with book_ids: {book_ids}")
        deleted_count = 0
        total_to_delete = len(book_ids)



        books_to_remove_from_db = []
        books_to_remove_from_cache = []

        self.log.debug(f"ANX Device: Current books in device cache (paths): {[os.path.normpath(b.path) for b in self.booklist]}")
        self.log.debug(f"ANX Device: Current books in device cache (UUIDs): {[b.uuid for b in self.booklist]}")

        # Build a temporary map for efficient lookup based on UUID or normalized path
        temp_book_map = {}
        for book_obj in self.books_in_device.values(): # Iterate over values (USBMSBook objects)
            temp_book_map[book_obj.uuid] = book_obj # Map Calibre's UUID to the book object
            temp_book_map[os.path.normpath(book_obj.path)] = book_obj # Map normalized path to the book object

        for item_to_delete in book_ids:
            self.log.debug(f"ANX Device: Attempting to delete item: {item_to_delete}")
            book_to_delete = None

            # Try to find by UUID first, then by normalized path
            book_to_delete = temp_book_map.get(item_to_delete)
            if not book_to_delete:
                book_to_delete = temp_book_map.get(os.path.normpath(item_to_delete))

            if book_to_delete:
                # Use the absolute paths directly from USBMSBook's attributes
                book_path = book_to_delete.path
                cover_path_rel = book_to_delete.get('#anx_cover_path') # Use .get() method
                
                # Construct the full absolute path for the cover file
                cover_path = os.path.join(self.base_dir, 'data', os.path.normpath(cover_path_rel)) if cover_path_rel else None
                self.log.debug(f"ANX Device: Found book in cache. {book_to_delete.get_all_user_metadata(make_copy=False)} Path: {book_path}, Cover Path (absolute): {cover_path}")

                # Delete file
                if os.path.exists(book_path):
                    try:
                        os.remove(book_path)
                        self.log.debug(f"ANX Device: Successfully deleted file: {book_path}")
                        deleted_count += 1
                    except Exception as e:
                        self.log.error(f"ANX Device: Error deleting file {book_path}: {e}", exc_info=True)
                else:
                    self.log.debug(f"ANX Device: File not found on disk: {book_path}")

                # Delete cover file
                if cover_path and os.path.exists(cover_path):
                    try:
                        os.remove(cover_path)
                        self.log.debug(f"ANX Device: Successfully deleted cover file: {cover_path}")
                    except Exception as e:
                        self.log.error(f"ANX Device: Error deleting cover file {cover_path}: {e}", exc_info=True)

                books_to_remove_from_db.append(book_to_delete)
                books_to_remove_from_cache.append(book_to_delete) # Mark for removal from cache
            else:
                self.log.warning(f"ANX Device: Book or path '{item_to_delete}' not found in device cache. Skipping deletion.")

        # Remove from database
        if books_to_remove_from_db:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                for book in books_to_remove_from_db:
                    # Retrieve ANX DB ID
                    anx_db_id = book.get('#anx_db_id') # Use .get() method

                    if anx_db_id is not None:
                        current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        cursor.execute("UPDATE tb_books SET is_deleted = 1, update_time = ? WHERE id = ?", (current_time, anx_db_id))
                        self.log.debug(f"ANX Device: Updata entries for book with ANX DB ID {anx_db_id} from tb_books.")
                        #cursor.execute("DELETE FROM tb_reading_time WHERE book_id = ?", (anx_db_id,))
                        #self.log.debug(f"ANX Device: Deleted entries for book with ANX DB ID {anx_db_id} from tb_reading_time.")
                        #cursor.execute("DELETE FROM tb_notes WHERE book_id = ?", (anx_db_id,))
                        #self.log.debug(f"ANX Device: Deleted entries for book with ANX DB ID {anx_db_id} from tb_notes.")
                        # Remove from in-memory cache and booklist directly
                        if book.uuid in self.books_in_device:
                            del self.books_in_device[book.uuid]
                            self.log.debug(f"ANX Device: Removed book {book.uuid} from self.books_in_device cache.")
                        
                        # Remove from booklist (which is CollectionsBookList)
                        # CollectionsBookList has a remove_book method
                        try:
                            self.booklist.remove_book(book)
                            self.log.debug(f"ANX Device: Removed book {book.uuid} from self.booklist.")
                        except Exception as list_e:
                            self.log.error(f"ANX Device: Error removing book {book.uuid} from booklist: {list_e}")
                    else:
                        self.log.warning(f"ANX Device: Could not find #anx_db_id in user_metadata for book {book.uuid}. Skipping DB deletion and in-memory removal.")
                conn.commit()
            except Exception as e:
                self.log.error(f"ANX Device: Error deleting books from database: {e}", exc_info=True)
            finally:
                if conn:
                    conn.close()


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
        book = self.books_in_device.get(book_id) # This is now a Book object
        if book:
            return book # Book object already contains all necessary metadata and inherits from Metadata
        return None

    def get_file(self, path, outfile, end_session=True):
        self.log.debug(f"ANX Device: get_file called for path: {path}")
        # As per user's feedback, 'path' is the absolute path to the file.
        # Directly use this path to open the file.
        
        actual_file_path = path

        if os.path.exists(actual_file_path):
            file_size = os.path.getsize(actual_file_path)
            self.log.debug(f"ANX Device: get_file - File exists at {actual_file_path}, size: {file_size} bytes.")
            if file_size == 0:
                self.log.error(f"ANX Device: get_file - File at {actual_file_path} has zero size!")
            
            try:
                with open(actual_file_path, 'rb') as f:
                    shutil.copyfileobj(f, outfile)
                self.log.debug(f"ANX Device: get_file - Successfully copied file content from {actual_file_path} to outfile.")
                return True # Indicate success
            except Exception as e:
                self.log.error(f"ANX Device: Error copying file {actual_file_path} to outfile: {e}", exc_info=True)
                return False
        else:
            self.log.error(f"ANX Device: get_file - File does not exist at path: {actual_file_path}")
            self.log.error(f"ANX Device: get_file - File not found on disk at {actual_file_path}. This path was provided directly by Calibre.")
        return False # Indicate failure

    def get_cover(self, book_id, as_file=False):
        book = self.books_in_device.get(book_id)
        if book and book.has_cover:
            cover_path = book.get('#anx_cover_path')
            if cover_path and os.path.exists(cover_path):
                if as_file:
                    return open(cover_path, 'rb')
                with open(cover_path, 'rb') as f:
                    return f.read()
        return None

    def get_icon(self):
        return None
    def free_space(self, end_session=True):
        if not self.base_dir or not os.path.isdir(self.base_dir):
            self.log.debug(f"ANX Device: free_space - Invalid base directory: {self.base_dir}. Returning (0,0,0).")
            return (0, 0, 0)
        
        try:
            total, used, free = shutil.disk_usage(self.base_dir)
            return (free, total, 0)
        except Exception as e:
            self.log.error(f"ANX Device: Error getting free space for {self.base_dir}: {e}", exc_info=True)
            return (0, 0, 0)

    def total_space(self, end_session=True):
        if not self.base_dir or not os.path.isdir(self.base_dir):
            self.log.debug(f"ANX Device: total_space - Invalid base directory: {self.base_dir}. Returning (0,0,0).")
            return (0, 0, 0)
        
        try:
            total, used, free = shutil.disk_usage(self.base_dir)
            return (total, total, 0)
        except Exception as e:
            self.log.error(f"ANX Device: Error getting total space for {self.base_dir}: {e}", exc_info=True)
            return (0, 0, 0)

    def sync_booklists(self, booklists, end_session=True):
        self.log.debug("ANX Device: sync_booklists called.")
        
        main_booklist = booklists[0] # The main booklist from Calibre's USBMS driver
        
        # Iterate through the books in the main_booklist
        for book_obj in main_booklist:
            # Get ANX DB ID
            anx_db_id = book_obj.get('#anx_db_id') # Use .get() method

            if anx_db_id is None:
                self.log.warning(f"ANX Device: sync_booklists - Could not find #anx_db_id in user_metadata for book {book_obj.uuid}. Skipping metadata update for this book.")
                continue

            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                
                # Retrieve current metadata from DB to check for changes for all relevant fields
                cursor.execute("""
                    SELECT title, author, cover_path, file_path, file_md5,
                           create_time, update_time, last_read_position,
                           reading_percentage, is_deleted, rating, group_id, description
                    FROM tb_books WHERE id = ?;
                """, (anx_db_id,))
                
                db_data = cursor.fetchone()
                if not db_data:
                    self.log.warning(f"ANX Device: sync_booklists - Book with ANX DB ID {anx_db_id} not found in database. Skipping metadata update.")
                    continue

                (db_title, db_author, db_cover_path, db_file_path, db_file_md5,
                 db_create_time, db_update_time, db_last_read_position,
                 db_reading_percentage, db_is_deleted, db_rating, db_group_id, db_description) = db_data
                
                update_fields = []
                update_values = []
                
                # Compare and update title
                if book_obj.title != db_title:
                    update_fields.append("title = ?")
                    update_values.append(book_obj.title)
                    self.log.debug(f"ANX Device: sync_booklists - Title changed for book ID {anx_db_id}: '{db_title}' -> '{book_obj.title}'")
                
                # Compare and update author
                current_author_in_book = book_obj.authors[0] if book_obj.authors else ''
                if current_author_in_book != db_author:
                    update_fields.append("author = ?")
                    update_values.append(current_author_in_book)
                    self.log.debug(f"ANX Device: sync_booklists - Author changed for book ID {anx_db_id}: '{db_author}' -> '{current_author_in_book}'")

                # Compare and update other extended attributes from user_metadata
                fields_to_check = {
                    '#anx_cover_path': ('cover_path', db_cover_path, 'text'),
                    '#anx_file_path': ('file_path', db_file_path, 'text'), # file_path is not usually editable by user directly, but for completeness
                    '#anx_file_md5': ('file_md5', db_file_md5, 'text'), # file_md5 is not editable
                    '#anx_create_time': ('create_time', db_create_time, 'datetime'),
                    '#anx_last_read_position': ('last_read_position', db_last_read_position, 'text'),
                    '#anx_reading_percentage': ('reading_percentage', db_reading_percentage, 'float'),
                    '#anx_rating': ('rating', db_rating, 'float'),
                    '#anx_group_id': ('group_id', db_group_id, 'int'),
                    '#anx_description': ('description', db_description, 'text'),
                }

                for user_meta_key, (db_field_name, db_current_value, data_type) in fields_to_check.items():
                    user_meta_val = book_obj.get(user_meta_key) # Use .get() method
                    
                    # Type conversion for comparison
                    if data_type == 'float' and user_meta_val is not None:
                        try:
                            user_meta_val = float(user_meta_val)
                        except (ValueError, TypeError):
                            user_meta_val = 0.0 # Default if conversion fails
                    elif data_type == 'int' and user_meta_val is not None:
                        try:
                            user_meta_val = int(user_meta_val)
                        except (ValueError, TypeError):
                            user_meta_val = 0 # Default if conversion fails
                    
                    if user_meta_val != db_current_value:
                        update_fields.append(f"{db_field_name} = ?")
                        update_values.append(user_meta_val)
                        self.log.debug(f"ANX Device: sync_booklists - {db_field_name} changed for book ID {anx_db_id}: '{db_current_value}' -> '{user_meta_val}'")

                if update_fields:
                    update_fields.append("update_time = ?")
                    update_values.append(current_time) # Update update_time on any change
                    
                    sql_update = f"UPDATE tb_books SET {', '.join(update_fields)} WHERE id = ?;"
                    update_values.append(anx_db_id)
                    
                    self.log.debug(f"ANX Device: sync_booklists - SQL Update: {sql_update}")
                    self.log.debug(f"ANX Device: sync_booklists - Update Values: {update_values}")
                    
                    cursor.execute(sql_update, tuple(update_values))
                    conn.commit()
                    self.log.debug(f"ANX Device: Successfully updated metadata for book with ANX DB ID {anx_db_id} in database.")
                else:
                    self.log.debug(f"ANX Device: No metadata changes detected for book ID {anx_db_id}.")
                
            except Exception as e:
                self.log.error(f"ANX Device: Error updating metadata for book {book_obj.uuid} (ANX DB ID: {anx_db_id}) in database during sync_booklists: {e}", exc_info=True)
            finally:
                if conn:
                    conn.close()
        self.log.debug("ANX Device: sync_booklists finished.")
        return True # Indicate success

    def upload_books(self, files, names, on_card=None, end_session=True, metadata=None):
        sent_count = 0
        total_books = len(files)



        locations = []
        for i, src_path in enumerate(files):
            try:
                self.report_progress(float(i) / total_books, f'Sending book {i+1} of {total_books}')
                
                book_data = metadata[i]
                self.log.debug(f"ANX Device: upload_books - book_data.cover_data: {book_data.cover_data}")
                
                title = book_data.title if book_data.title else os.path.splitext(os.path.basename(src_path))[0]
                author = book_data.authors[0] if book_data.authors else "Unknown"
                
                
                fmt = os.path.splitext(src_path)[1].lstrip('.').lower()
                if not fmt:
                    fmt = 'epub'
                # Ensure the filename is based on safe title and author, preserving UTF-8, and handle length
                filename = self._get_safe_filename(title, author, fmt)
                dest_file_path = os.path.join(self.file_dir, filename)
                
                os.makedirs(self.file_dir, exist_ok=True)
                os.makedirs(self.cover_dir, exist_ok=True)
                
                shutil.copyfile(src_path, dest_file_path)
                self.log.debug(f"Copied ebook from {src_path} to {dest_file_path}")
                
                file_md5 = hashlib.md5(open(dest_file_path, 'rb').read()).hexdigest()
                
                cover_path_rel = ""
                dest_cover_path = "" # Initialize dest_cover_path
                
                cover_data_to_write = None
                cover_extension = '.jpg' # Default extension

                # 1. Preferred cover extraction: Use book_data.cover (path to cover file)
                if book_data and hasattr(book_data, 'cover') and book_data.cover:
                    calibre_cover_path = book_data.cover
                    self.log.debug(f"ANX Device: upload_books - Attempting to use book_data.cover path: {calibre_cover_path}")
                    if os.path.exists(calibre_cover_path):
                        try:
                            with open(calibre_cover_path, 'rb') as f:
                                cover_data_to_write = f.read()
                            cover_extension = os.path.splitext(calibre_cover_path)[1].lower()
                            self.log.debug(f"ANX Device: upload_books - Successfully read cover from book_data.cover path: {calibre_cover_path}.")
                        except Exception as e:
                            self.log.error(f"ANX Device: Error reading cover from book_data.cover path {calibre_cover_path}: {e}")
                            cover_data_to_write = None
                    else:
                        self.log.warning(f"ANX Device: book_data.cover path does not exist: {calibre_cover_path}")

                # 2. Fallback to Calibre DB if book_data.cover is not available or failed
                if not cover_data_to_write:
                    self.log.debug(f"ANX Device: upload_books - book_data.cover not available or failed, falling back to Calibre DB.")
                    try:
                        calibre_db = db().new_api # Use db().new_api to access the Calibre database API directly
                        self.log.debug(f"ANX Device: upload_books - book_data.id: {book_data.id}, calibre_db: {calibre_db}")
                        
                        # Get full metadata including cover_data
                        current_metadata = calibre_db.get_metadata(book_data.id, get_cover=True)
                        cover_rel_path = current_metadata.get('cover')
                        
                        if cover_rel_path:
                            book_library_path = calibre_db.field_for('path', book_data.id)
                            calibre_cover_path = os.path.join(book_library_path, cover_rel_path)
                            self.log.debug(f"ANX Device: upload_books - calibre_cover_path from DB metadata: {calibre_cover_path}")

                            if os.path.exists(calibre_cover_path):
                                try:
                                    with open(calibre_cover_path, 'rb') as f:
                                        cover_data_to_write = f.read()
                                    cover_extension = os.path.splitext(calibre_cover_path)[1].lower()
                                    self.log.debug(f"ANX Device: upload_books - Successfully read cover from Calibre DB path: {calibre_cover_path}.")
                                except Exception as e:
                                    self.log.error(f"ANX Device: Error reading cover from Calibre DB path {calibre_cover_path}: {e}")
                                    cover_data_to_write = None
                            else:
                                self.log.warning(f"ANX Device: No valid cover file found at {calibre_cover_path} for book {title} in Calibre DB.")
                        else:
                            self.log.warning(f"ANX Device: No cover path found in metadata for book {title} in Calibre DB.")
                    except sqlite3.OperationalError as db_e:
                        self.log.warning(f"ANX Device: Could not access Calibre DB for cover: {db_e}. This might be due to a 'database is locked' error. Skipping cover extraction from DB.")
                        cover_data_to_write = None # Ensure cover_data_to_write is None on DB error
                    except Exception as e:
                        self.log.error(f"ANX Device: Unexpected error accessing Calibre DB for cover: {e}", exc_info=True)
                        cover_data_to_write = None # Ensure cover_data_to_write is None on unexpected error

                # 3. Fallback to book_data.cover_data (format, data) tuple
                if not cover_data_to_write and book_data and hasattr(book_data, 'cover_data') and book_data.cover_data and len(book_data.cover_data) == 2 and book_data.cover_data[1]:
                    cover_data_to_write = book_data.cover_data[1]
                    cover_format = book_data.cover_data[0].lower() if book_data.cover_data[0] else 'jpeg'
                    if cover_format == 'png':
                        cover_extension = '.png'
                    elif cover_format == 'gif':
                        cover_extension = '.gif'
                    self.log.debug(f"ANX Device: upload_books - Using cover data from book_data.cover_data as a fallback.")

                # 4. Fallback to book_data.thumbnail (width, height, cover_data as jpeg)
                if not cover_data_to_write and book_data and hasattr(book_data, 'thumbnail') and book_data.thumbnail and len(book_data.thumbnail) == 3:
                    cover_data_to_write = book_data.thumbnail[2] # Get the actual image data
                    cover_extension = '.jpg' # Assuming thumbnail is always JPEG
                    self.log.debug(f"ANX Device: upload_books - Using cover data from book_data.thumbnail as a last resort.")

                dest_cover_path = "" # Initialize dest_cover_path
                if cover_data_to_write:
                    # Use _get_safe_filename for cover filename as well
                    # For cover, we pass the extension as fmt to _get_safe_filename
                    cover_filename = self._get_safe_filename(title, author, cover_extension.lstrip('.'))
                    dest_cover_path = os.path.join(self.cover_dir, cover_filename)
                    
                    try:
                        with open(dest_cover_path, 'wb') as f:
                            f.write(cover_data_to_write)
                        cover_path_rel = os.path.relpath(dest_cover_path, os.path.join(self.base_dir, 'data')).replace(os.sep, '/')
                        self.log.debug(f"Copied cover to {dest_cover_path}")
                    except Exception as ce:
                        self.log.error(f"Error copying cover data to {dest_cover_path}: {ce}")
                        cover_path_rel = "" # Reset cover_path_rel if copy fails
                        dest_cover_path = "" # Reset dest_cover_path if copy fails
                else:
                    self.log.warning(f"No cover data available to write for book {title}.")
                    cover_path_rel = ""
                    dest_cover_path = ""
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')

                cursor.execute("SELECT id, is_deleted, file_path FROM tb_books WHERE file_md5 = ?;", (file_md5,))
                existing_book = cursor.fetchone()

                if existing_book:
                    existing_id, is_deleted, file_path_rel_from_db = existing_book
                    # Case 1: MD5 exists and is_deleted is 1 (book was soft-deleted)
                    if is_deleted == 1:
                        self.log.debug(f"Book '{title}' with MD5 '{file_md5}' exists but is marked as deleted. Reactivating and updating.")
                        # File has already been copied, so we just update the database record
                        file_relative_path = os.path.relpath(dest_file_path, os.path.join(self.base_dir, 'data')).replace(os.sep, '/')
                        
                        current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                        cursor.execute("""
                            UPDATE tb_books 
                            SET is_deleted = 0, update_time = ?, file_path = ?, cover_path = ?
                            WHERE id = ?;
                        """, (current_time, file_relative_path, cover_path_rel, existing_id))
                        conn.commit()
                        self.log.debug(f"Reactivated book with ID {existing_id}.")
                        # Continue to the next book
                        conn.close()
                        continue
                    
                    # Case 2: MD5 exists and is_deleted is 0 (book is active)
                    else:
                        full_file_path_on_device = os.path.join(self.base_dir, 'data', os.path.normpath(file_path_rel_from_db))
                        # Case 2a: File does not exist on disk
                        if not os.path.exists(full_file_path_on_device):
                            self.log.debug(f"Book '{title}' with MD5 '{file_md5}' exists, but file is missing. Replacing file.")
                            # The file has already been copied to dest_file_path by this point.
                            # We just need to ensure the DB path is correct if it changed.
                            file_relative_path = os.path.relpath(dest_file_path, os.path.join(self.base_dir, 'data')).replace(os.sep, '/')
                            if file_relative_path != file_path_rel_from_db:
                                current_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                cursor.execute("UPDATE tb_books SET file_path = ?, update_time = ? WHERE id = ?", (file_relative_path, current_time, existing_id))
                                conn.commit()
                            # We don't need to do anything else, the file is now where it should be.
                        # Case 2b: File exists on disk
                        else:
                            self.log.warning(f"Book '{title}' with MD5 '{file_md5}' already exists and file is present. Skipping as duplicate.")
                        
                        conn.close()
                        continue
                
                file_relative_path = os.path.relpath(dest_file_path, os.path.join(self.base_dir, 'data')).replace(os.sep, '/')
                
                # Extract extended attributes from book_data
                # Provide default values if attributes are not present in book_data
                create_time = book_data.get('create_time', current_time)
                update_time = book_data.get('update_time', current_time)
                last_read_position = book_data.get('last_read_position', '')
                reading_percentage = book_data.get('reading_percentage', 0.0)
                is_deleted = book_data.get('is_deleted', 0)
                rating = book_data.get('rating', 0.0)
                group_id = book_data.get('group_id', 0)
                description = book_data.get('description', '')

                sql_insert = """
                INSERT INTO tb_books (title, cover_path, file_path, author, create_time, update_time, file_md5, last_read_position, reading_percentage, is_deleted, rating, group_id, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                cursor.execute(sql_insert, (
                    title,
                    cover_path_rel,
                    file_relative_path,
                    author,
                    create_time,
                    update_time,
                    file_md5,
                    last_read_position,
                    reading_percentage,
                    is_deleted,
                    rating,
                    group_id,
                    description
                ))
                conn.commit()
                book_id_from_db = cursor.lastrowid
                # After inserting, select the full book data to construct the USBMSBook object
                cursor.execute("""
                    SELECT id, title, author, file_path, cover_path, file_md5,
                           create_time, update_time, last_read_position,
                           reading_percentage, is_deleted, rating, group_id, description
                    FROM tb_books WHERE id = ?;
                """, (book_id_from_db,))
                
                row = cursor.fetchone()
                conn.close() # Close connection after query
                
                if not row:
                    self.log.error(f"ANX Device: Failed to retrieve book with ID {book_id_from_db} after insertion. Skipping location return.")
                    continue # Skip to next book if data retrieval fails

                (book_id, title, author, file_path_rel, cover_path_rel, file_md5,
                 create_time, update_time, last_read_position,
                 reading_percentage, is_deleted, rating, group_id, description) = row

                self.log.debug(f"Book '{title}' successfully added to ANX device database with ID: {book_id_from_db}.")
                sent_count += 1

                # Normalize paths from DB to current OS path style before joining
                normalized_file_path_rel = os.path.normpath(file_path_rel)
                normalized_cover_path_rel = os.path.normpath(cover_path_rel) if cover_path_rel else None

                full_file_path = os.path.join(self.base_dir, 'data', normalized_file_path_rel)
                full_cover_path = os.path.join(self.base_dir, 'data', normalized_cover_path_rel) if normalized_cover_path_rel else None

                file_size = os.path.getsize(full_file_path) if os.path.exists(full_file_path) else 0
                file_mtime = datetime.fromtimestamp(os.path.getmtime(full_file_path)) if os.path.exists(full_file_path) else datetime.utcnow()

                # Prepare a dictionary with all necessary info for add_books_to_metadata
                book_info = {
                    'book_id': book_id,
                    'title': title,
                    'author': author,
                    'file_path_rel': file_path_rel,
                    'cover_path_rel': cover_path_rel,
                    'file_md5': file_md5,
                    'create_time': create_time,
                    'update_time': update_time,
                    'last_read_position': last_read_position,
                    'reading_percentage': reading_percentage,
                    'is_deleted': is_deleted,
                    'rating': rating,
                    'group_id': group_id,
                    'description': description,
                    'full_file_path': full_file_path,
                    'full_cover_path': full_cover_path,
                    'file_size': file_size,
                    'file_mtime': file_mtime,
                    'fmt': fmt # Original format
                }
                
                locations.append((full_file_path, None, book_info)) # Pass book_info as the third element in the tuple

            except Exception as e:
                self.log.error(f"Error sending book {os.path.basename(src_path)}: {e}") # Use src_path for logging
                import traceback
                self.log.error(traceback.format_exc())
                continue
        
        self.report_progress(1.0, 'Finished sending books.')
        return locations # Return only locations list

    def books(self, oncard=None, end_session=True):
        # Return USBMS's internal booklist directly
        return self.booklist

    def get_device_uid(self):
        # Return a unique ID for the device. For a virtual device, use its UUID.
        self.log.debug(f"ANX Device: get_device_uid called, returning {self.uuid}")
        return self.uuid

    def ignore_connected_device(self, uid):
        # Add the device UID to the blacklist.
        self.log.debug(f"ANX Device: ignore_connected_device called for UID: {uid}")
        blacklisted_devices = self.get_user_blacklisted_devices()
        if uid not in blacklisted_devices:
            blacklisted_devices[uid] = f"ANX Device ({uid})" # Store with a friendly name
            self.set_user_blacklisted_devices(blacklisted_devices)
            self.log.debug(f"ANX Device: Added {uid} to blacklist.")
        
        # Reset plugin state as per interface documentation
        # Reset plugin state as per interface documentation
        self.seen_device = False
        self.connected = False
        # USBMS base class handles clearing its internal books_in_device and booklist on disconnect/ignore
        # self.books_in_device.clear() # No need to clear here, USBMS handles it
        # self.booklist.clear() # No need to clear here, USBMS handles it

    def get_user_blacklisted_devices(self):
        # Return a dictionary of blacklisted devices (UID -> friendly name).
        return self.prefs.get('blacklisted_devices', {})

    def set_user_blacklisted_devices(self, devices):
        # Set the blacklisted devices.
        self.prefs['blacklisted_devices'] = devices
        self.prefs.commit() # Save changes to config file

    def do_user_manual(self, gui):
        self.gui.job_manager.show_message('ANX Device Plugin: Manage ebooks in your custom ANX folder structure. Configure the device path in Calibre Preferences -> Plugins -> Device Plugins -> ANX Virtual Device -> Customize plugin.')

    def add_books_to_metadata(self, locations, metadata, booklists):
        default_log.debug(f"ANX Device: add_books_to_metadata called with {len(locations)} locations.")
        usbms_booklist = booklists[0] # The main booklist from Calibre's USBMS driver

        for i, (full_file_path, on_card_name, book_info) in enumerate(locations):
            try:
                # Retrieve all necessary info from book_info dictionary
                book_id = book_info['book_id']
                title = book_info['title']
                author = book_info['author']
                file_path_rel = book_info['file_path_rel']
                cover_path_rel = book_info['cover_path_rel']
                file_md5 = book_info['file_md5']
                create_time = book_info['create_time']
                update_time = book_info['update_time']
                last_read_position = book_info['last_read_position']
                reading_percentage = book_info['reading_percentage']
                is_deleted = book_info['is_deleted']
                rating = book_info['rating']
                group_id = book_info['group_id']
                description = book_info['description']
                file_size = book_info['file_size']
                file_mtime = book_info['file_mtime']
                fmt = book_info['fmt']
                full_cover_path = book_info['full_cover_path']

                # We need the base_dir to construct lpath and check paths
                # Since this is a classmethod, we cannot access self.base_dir directly.
                # We need to get it from prefs or ensure it's passed somehow.
                # For now, assume prefs is accessible (it's a global JSONConfig object).
                base_dir = prefs['device_path']
                if not base_dir:
                    default_log.error("ANX Device: add_books_to_metadata - device_path is not configured. Cannot add book to metadata.")
                    continue

                lpath = os.path.relpath(full_file_path, base_dir)

                book = USBMSBook(
                    prefix=base_dir, # Use base_dir here
                    lpath=lpath,
                    size=file_size,
                )
                book.uuid = str(uuid.uuid4())
                book.datetime = file_mtime.timetuple()
                book.is_dir = False
                book.is_readonly = True

                # Store ANX specific metadata as user_metadata
                book.set_user_metadata('#anx_db_id', {'datatype': 'int', 'is_multiple': False, '#value#': book_id})
                book.set_user_metadata('#anx_file_path', {'datatype': 'text', 'is_multiple': False, '#value#': file_path_rel or ''})
                book.set_user_metadata('#anx_cover_path', {'datatype': 'text', 'is_multiple': False, '#value#': cover_path_rel or ''})
                book.set_user_metadata('#anx_file_md5', {'datatype': 'text', 'is_multiple': False, '#value#': file_md5 or ''})
                book.set_user_metadata('#anx_create_time', {'datatype': 'datetime', 'is_multiple': False, '#value#': create_time or ''})
                book.set_user_metadata('#anx_update_time', {'datatype': 'datetime', 'is_multiple': False, '#value#': update_time or ''})
                book.set_user_metadata('#anx_last_read_position', {'datatype': 'text', 'is_multiple': False, '#value#': last_read_position or ''})
                book.set_user_metadata('#anx_reading_percentage', {'datatype': 'float', 'is_multiple': False, '#value#': reading_percentage or 0.0})
                book.set_user_metadata('#anx_is_deleted', {'datatype': 'int', 'is_multiple': False, '#value#': is_deleted or 0})
                book.set_user_metadata('#anx_rating', {'datatype': 'float', 'is_multiple': False, '#value#': rating or 0.0})
                book.set_user_metadata('#anx_group_id', {'datatype': 'int', 'is_multiple': False, '#value#': group_id or 0})
                book.set_user_metadata('#anx_description', {'datatype': 'text', 'is_multiple': False, '#value#': description or ''})

                # Populate standard Book attributes
                book.title = title
                book.authors = [author] if author else [_('Unknown')]
                book.has_cover = True if full_cover_path and os.path.exists(full_cover_path) else False
                book.format_map = {fmt.upper(): file_size}
                book.device_id = self.uuid 
                book.in_library = False
                book.device_collections = []

                if book.has_cover and os.path.exists(full_cover_path):
                    try:
                        with open(full_cover_path, 'rb') as f:
                            book.thumbnail = f.read()
                    except Exception as ce:
                        default_log.error(f"Error loading thumbnail for {title} in add_books_to_metadata: {ce}")
                        book.thumbnail = None
                else:
                    book.thumbnail = None
                
                usbms_booklist.add_book(book, on_card_name) # Add to the booklist
                # Ensure books_in_device is updated for newly added books
                # This is important for methods like delete_books to find the book
                # and for load_books_from_device not to re-add it on subsequent calls.
                self.books_in_device[book.uuid] = book
                default_log.debug(f"ANX Device: Added book {title} to device metadata and updated books_in_device.")

            except Exception as e:
                default_log.error(f"ANX Device: Error in add_books_to_metadata for location {full_file_path}: {e}", exc_info=True)

    def _get_safe_filename(self, title, author, fmt, max_len=90):
        # Generate a base filename from title and author
        base_filename = f"{title} - {author}"
        
        # Get the extension with a leading dot
        ext = f".{fmt}" if fmt else ""
        
        # Calculate available length for the base name
        available_len = max_len - len(ext)
        
        if available_len < 1: # Ensure there's at least some space for the base name
            available_len = 1
        
        # Truncate the base filename if it's too long
        if len(base_filename) > available_len:
            # For simplicity, we'll just truncate from the end
            base_filename = base_filename[:available_len]
            
        # Combine truncated base filename with extension
        full_filename = f"{base_filename}{ext}"
        
        # Replace any characters that are not allowed in filenames
        # This is a basic sanitization. Calibre's internal safe_filename might be more robust.
        # For simplicity, we'll replace common problematic characters with underscores.
        full_filename = re.sub(r'[<>:"/\\|?*]', '_', full_filename)
        
        return full_filename

class Opts:
    def __init__(self, format_map):
        self.format_map = format_map