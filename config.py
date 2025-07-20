# anx_device_plugin/config.py

from calibre.utils.config import JSONConfig
from PyQt5.Qt import QWidget, QLabel, QLineEdit, QGridLayout, QVBoxLayout

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

        self.path_edit = QLineEdit(self)
        self.path_edit.setText(prefs["device_path"]) # Load initial value
        self.layout.addWidget(self.path_edit)

        self.setLayout(self.layout) # Set the layout for the widget

    def save_settings(self):
        # Save the current value from the QLineEdit to preferences
        prefs["device_path"] = self.path_edit.text()