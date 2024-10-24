import os
from configparser import ConfigParser

if os.path.exists("config.ini"):
    __config = ConfigParser()
    __config.read("config.ini")
else:
    raise FileExistsError("Отсутствует файл config.ini.\n"
                          "Если вы клонировали или запуллили git-репозиторий, "
                          "убедитесь, что вы скопировали example.config.ini, "
                          "переименовали его config.ini и исправили под себя.")

__data = __config["data"]
TOKEN = __data["token"]
OWNER_ID = int(__data["owner_id"])
OWNER_HANDLE = __data["owner_handle"]

__PREDEFINED_PATH = "predefined_files"
BUTTONS_IMG = os.path.join(__PREDEFINED_PATH, "buttons.png")
