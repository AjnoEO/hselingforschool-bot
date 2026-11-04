from enums import Platform
from messaging import MessageRef

class FakeMessenger:
    def __init__(self, platform=Platform.TELEGRAM):
        self.platform = platform
        self.calls = []

    def ref_for(self, message):
        return MessageRef(message.chat.id, message.message_id)

    def send(self, peer_id, text, *, media=None, keyboard=None, reply_to=None, show_caption_above_media=False):
        msg_type = "text" if not media else "photo" if media.is_img else "document"
        self.calls.append((msg_type, peer_id, text, media, keyboard))
        return MessageRef(peer_id, 1)

    def edit_text(self, ref, text, *, keyboard=None):
        self.calls.append(("edit_text", ref, text, keyboard))

    def edit_keyboard(self, ref, keyboard):
        self.calls.append(("edit_keyboard", ref, keyboard))

    def delete(self, ref):
        self.calls.append(("delete", ref))

    def answer_callback(self, callback_id, text="", show_alert=False, *, user_id=None, peer_id=None):
        self.calls.append(("answer", callback_id, text, show_alert))

    def copy_message(self, peer_id, ref):
        self.calls.append(("copy", peer_id, ref))
        return MessageRef(peer_id, 1)

    def download_document(self, document):
        self.calls.append(("download", document))
        return b"data"
