# anx_device_plugin/anx_device_plugin.py

from calibre.customize import ZipImportMain

def class_factory(zip_path, class_name):
    return ZipImportMain(zip_path, __name__, class_name)
