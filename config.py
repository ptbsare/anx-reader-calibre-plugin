# anx_device_plugin/config.py

from calibre.utils.config import JSONConfig
from PyQt5.Qt import QWidget, QLabel, QLineEdit, QGridLayout, QVBoxLayout, QPushButton, QHBoxLayout, QFileDialog
import os

# This will create a config file for the plugin named 'anx_device_plugin.json'
# in Calibre's configuration directory (plugins/anx_device_plugin.json)
prefs = JSONConfig("plugins/anx_device_plugin")

# Set defaults for the settings
prefs.defaults["device_path"] = ""

class ConfigWidget(QWidget):
    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.layout = QVBoxLayout(self) # Use QVBoxLayout for simplicity

        self.path_label = QLabel(_('Path to ANX device folder (e.g., /mnt/user/volume1/电子书库/webdav/anx):'))
        self.layout.addWidget(self.path_label)

        # Create a horizontal layout for the QLineEdit and QPushButton
        path_selection_layout = QHBoxLayout()
        self.path_edit = QLineEdit(self)
        self.path_edit.setText(prefs["device_path"]) # Load initial value
        path_selection_layout.addWidget(self.path_edit)

        self.browse_button = QPushButton(_('Browse'))
        self.browse_button.clicked.connect(self.browse_folder)
        path_selection_layout.addWidget(self.browse_button)
        
        self.layout.addLayout(path_selection_layout) # Add the horizontal layout to the main vertical layout

        self.setLayout(self.layout) # Set the layout for the widget

    def browse_folder(self):
        # Open a directory selection dialog
        current_path = self.path_edit.text()
        if not current_path or not os.path.isdir(current_path):
            current_path = os.path.expanduser("~") # Default to home directory if current path is invalid

        folder_path = QFileDialog.getExistingDirectory(self, _("Select ANX Device Folder"), current_path)
        if folder_path:
            self.path_edit.setText(folder_path)

    def save_settings(self):
        # Save the current value from the QLineEdit to preferences
        prefs["device_path"] = self.path_edit.text()

    def validate(self):
        # Calibre expects a validate method in ConfigWidget for device plugins
        # It should return True if settings are valid, False otherwise.
        # For now, we'll assume the path is valid if not empty.
        path = self.path_edit.text()
        if not path:
            # Optionally show a warning to the user if path is empty
            # from PyQt5.QtWidgets import QMessageBox
            # QMessageBox.warning(self, _("Invalid Path"), _("Device path cannot be empty."))
            return False
        return True