import os
from configparser import ConfigParser
from enums import Platform

# Путь к конфигу можно переопределить переменной окружения (используется в тестах)
CONFIG_FILE = os.environ.get("OLYMP_CONFIG", "config.ini")
if os.path.exists(CONFIG_FILE):
    __config = ConfigParser()
    __config.read(CONFIG_FILE, encoding="utf8")
else:
    raise FileExistsError(f"Отсутствует файл {CONFIG_FILE}.\n"
                          "Если вы клонировали или запуллили git-репозиторий, "
                          "убедитесь, что вы скопировали example.config.ini, "
                          "переименовали его config.ini и исправили под себя.")


def __option(section: str, option: str, fallback: str = "") -> str:
    if not __config.has_section(section):
        return fallback
    return __config.get(section, option, fallback=fallback).strip()


__data = __config["data"]
OWNER_ID = int(__data["owner_id"])
__owner_platform = __data.get("owner_platform", "TELEGRAM").split("#", 1)[0].strip().upper()
OWNER_PLATFORM = Platform[__owner_platform] if __owner_platform in Platform.__members__ else Platform(int(__owner_platform))

TELEGRAM_TOKEN = __config.get("telegram", "token", fallback=__data.get("token", ""))
VK_TOKEN = __option("vk", "token")
VK_GROUP_ID = int(__option("vk", "group_id", "0").split("#", 1)[0] or 0)
# TODO: решить, что делать при отсутствии VK-токена/group_id и хэндлов для платформы
VK_ENABLED = bool(VK_TOKEN and VK_GROUP_ID)

OWNER_HANDLES = {
    p: __option(p.code, "owner_handle", __data.get("owner_handle", "")) for p in Platform
}
ASK_PEOPLE_HANDLES = {
    p: [
        handle.strip()
        for handle in __option(p.code, "ask_people_handles", __data.get("ask_people_handles", "")).split(",")
        if handle.strip()
    ] for p in Platform
}

__bot = __config["bot"]
PROMOTE_COMMANDS = __bot.getboolean("promote_commands")
NO_EXAMINER_COMPLAINTS = __bot.getboolean("no_examiner_complaints")
MEMBER_PAGE_SIZE = __bot.getint("member_page_size")

PREDEFINED_PATH = "predefined_files"
BUTTONS_IMG = os.path.join(PREDEFINED_PATH, "buttons.png")
