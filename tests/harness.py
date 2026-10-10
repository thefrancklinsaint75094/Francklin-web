"""Faux serveur Telegram pour tester le bot de bout en bout, sans réseau.

Le bot réel (handlers, base, verrou) tourne ; seuls l'API Telegram, Anthropic,
BAN et Whisper sont simulés."""
from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults
from telegram.request import BaseRequest

from bot.config import PARIS
from bot.main import register_handlers

BOT_USER = {"id": 999, "is_bot": True, "first_name": "Dispatch Bot", "username": "dispatch_test_bot"}


@dataclass
class Msg:
    chat_id: int
    message_id: int
    text: str
    markup: dict | None = None
    history: list = field(default_factory=list)

    @property
    def buttons(self) -> list[tuple[str, str]]:
        if not self.markup:
            return []
        return [(b["text"], b.get("callback_data")) for row in self.markup.get("inline_keyboard", []) for b in row]

    def data(self, prefix: str) -> str:
        for _, d in self.buttons:
            if d and d.startswith(prefix):
                return d
        raise AssertionError(f"Pas de bouton {prefix!r} sur : {self.text!r} {self.buttons}")


class FakeTelegram(BaseRequest):
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.messages: dict[tuple[int, int], Msg] = {}
        self.blocked: set[int] = set()
        self.files: dict[str, bytes] = {}
        self._ids = itertools.count(10_000)

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def read_timeout(self):
        return 5

    # ------------------------------------------------------------ inspection
    def inbox(self, chat_id: int) -> list[Msg]:
        return sorted((m for m in self.messages.values() if m.chat_id == chat_id), key=lambda m: m.message_id)

    def last(self, chat_id: int) -> Msg:
        box = self.inbox(chat_id)
        assert box, f"Aucun message pour {chat_id}"
        return box[-1]

    def texts(self, chat_id: int) -> list[str]:
        return [m.text for m in self.inbox(chat_id)]

    def all_texts_ever(self, chat_id: int) -> list[str]:
        out = []
        for m in self.inbox(chat_id):
            out += m.history + [m.text]
        return out

    def find(self, chat_id: int, needle: str) -> Msg:
        for m in reversed(self.inbox(chat_id)):
            if needle in m.text:
                return m
        raise AssertionError(f"{needle!r} introuvable chez {chat_id} : {self.texts(chat_id)}")

    def answers(self) -> list[dict]:
        return [p for m, p in self.calls if m == "answerCallbackQuery"]

    def documents(self, chat_id: int) -> list[dict]:
        return [p for m, p in self.calls if m == "sendDocument" and int(p["chat_id"]) == chat_id]

    def clear(self):
        self.messages.clear()
        self.calls.clear()

    # ------------------------------------------------------------ API
    def _ok(self, result):
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def _err(self, code, desc):
        return code, json.dumps({"ok": False, "error_code": code, "description": desc}).encode()

    def _message_json(self, m: Msg):
        data = {"message_id": m.message_id, "date": int(time.time()),
                "chat": {"id": m.chat_id, "type": "private"}, "from": BOT_USER, "text": m.text}
        if m.markup and "inline_keyboard" in m.markup:   # comme Telegram : jamais le menu du bas (clavier)
            data["reply_markup"] = m.markup
        return data

    async def do_request(self, url, method, request_data=None, **kwargs):
        if "/file/bot" in url:
            return 200, self.files.get(url.rsplit("/", 1)[-1], b"")
        api = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        if isinstance(params.get("reply_markup"), str):
            params["reply_markup"] = json.loads(params["reply_markup"])
        self.calls.append((api, params))
        chat_id = int(params["chat_id"]) if "chat_id" in params else None
        if chat_id is not None and chat_id in self.blocked:
            return self._err(403, "Forbidden: bot was blocked by the user")

        if api == "getMe":
            return self._ok(BOT_USER)
        if api in ("setMyCommands", "deleteMyCommands", "answerCallbackQuery"):
            return self._ok(True)
        if api == "sendMessage":
            m = Msg(chat_id, next(self._ids), params["text"], params.get("reply_markup"))
            self.messages[(chat_id, m.message_id)] = m
            return self._ok(self._message_json(m))
        if api == "sendVenue":
            m = Msg(chat_id, next(self._ids), f"[carte] {params['title']} | {params['address']} "
                                              f"@ {params['latitude']},{params['longitude']}")
            self.messages[(chat_id, m.message_id)] = m
            data = self._message_json(m)
            data["venue"] = {"location": {"latitude": float(params["latitude"]), "longitude": float(params["longitude"])},
                             "title": params["title"], "address": params["address"]}
            return self._ok(data)
        if api == "sendDocument":
            m = Msg(chat_id, next(self._ids), "[document]")
            self.messages[(chat_id, m.message_id)] = m
            data = self._message_json(m)
            data["document"] = {"file_id": "doc", "file_unique_id": "doc", "file_name": "f.csv"}
            return self._ok(data)
        if api in ("editMessageText", "editMessageReplyMarkup"):
            key = (chat_id, int(params["message_id"]))
            m = self.messages.get(key)
            if m is None:
                return self._err(400, "Bad Request: message to edit not found")
            new_text = params.get("text", m.text)
            new_markup = params.get("reply_markup") if api == "editMessageText" or "reply_markup" in params else m.markup
            if api == "editMessageText" and "reply_markup" not in params:
                new_markup = None
            if new_text == m.text and new_markup == m.markup:
                return self._err(400, "Bad Request: message is not modified")
            if new_text != m.text:
                m.history.append(m.text)
            m.text, m.markup = new_text, new_markup
            return self._ok(self._message_json(m))
        if api in ("deleteMessage", "deleteMessages"):
            ids = params.get("message_ids", [params.get("message_id")])
            if isinstance(ids, str):
                ids = json.loads(ids)
            for message_id in ids:
                self.messages.pop((chat_id, int(message_id)), None)
            return self._ok(True)
        if api == "getFile":
            fid = params["file_id"]
            return self._ok({"file_id": fid, "file_unique_id": fid, "file_path": fid})
        return self._err(400, f"Méthode non simulée : {api}")


class Harness:
    """Application réelle + faux Telegram. Utilisation :

        h = await Harness.create()
        await h.text(franchise_tg, "12 rue de Rivoli …")
        await h.press(franchise_tg, h.tg.last(franchise_tg), "draft_confirm:")
    """

    def __init__(self, app: Application, tg: FakeTelegram):
        self.app = app
        self.tg = tg
        self._update_ids = itertools.count(1)
        self._user_msg_ids = itertools.count(1)
        self.usernames: dict[int, str | None] = {}

    @classmethod
    async def create(cls) -> "Harness":
        tg = FakeTelegram()
        app = (
            Application.builder()
            .token("123:test")
            .request(tg)
            .get_updates_request(FakeTelegram())
            .defaults(Defaults(parse_mode=ParseMode.HTML, tzinfo=PARIS))
            .build()
        )
        register_handlers(app)
        await app.initialize()
        return cls(app, tg)

    async def close(self):
        for job in self.app.job_queue.jobs():
            job.schedule_removal()
        await self.app.shutdown()

    @property
    def context(self):
        return SimpleNamespace(bot=self.app.bot, job_queue=self.app.job_queue, application=self.app)

    def _user(self, tg_id: int, username: str | None):
        # Comme un vrai compte : le @pseudo donné une fois est renvoyé à chaque update.
        if username is not None:
            self.usernames[tg_id] = username
        return {"id": tg_id, "is_bot": False, "first_name": f"User{tg_id}",
                "username": self.usernames.get(tg_id)}

    async def _feed(self, payload: dict):
        payload["update_id"] = next(self._update_ids)
        await self.app.process_update(Update.de_json(payload, self.app.bot))

    def _message(self, tg_id: int, username: str | None, **content) -> dict:
        return {"message_id": next(self._user_msg_ids), "date": int(time.time()),
                "chat": {"id": tg_id, "type": "private"}, "from": self._user(tg_id, username), **content}

    async def text(self, tg_id: int, text: str, username: str | None = None, reply_to: int | None = None):
        content = {"text": text}
        if text.startswith("/"):
            content["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if reply_to is not None:
            content["reply_to_message"] = {"message_id": reply_to, "date": int(time.time()),
                                           "chat": {"id": tg_id, "type": "private"}, "from": BOT_USER, "text": "…"}
        await self._feed({"message": self._message(tg_id, username, **content)})

    async def voice(self, tg_id: int, file_id: str = "voice1", username: str | None = None):
        await self._feed({"message": self._message(tg_id, username, voice={
            "file_id": file_id, "file_unique_id": file_id, "duration": 3})})

    async def photo(self, tg_id: int, file_id: str = "photo1", caption: str | None = None):
        content = {"photo": [{"file_id": file_id + "_s", "file_unique_id": "s", "width": 90, "height": 90},
                             {"file_id": file_id, "file_unique_id": "l", "width": 800, "height": 800}]}
        if caption:
            content["caption"] = caption
        await self._feed({"message": self._message(tg_id, None, **content)})

    async def sticker(self, tg_id: int):
        await self._feed({"message": self._message(tg_id, None, sticker={
            "file_id": "st", "file_unique_id": "st", "type": "regular", "width": 1, "height": 1,
            "is_animated": False, "is_video": False})})

    async def location(self, tg_id: int, lat: float, lon: float, live: bool = True, edited: bool = False,
                       message_id: int = 777):
        loc = {"latitude": lat, "longitude": lon}
        if live:
            loc["live_period"] = 28800
        msg = self._message(tg_id, None, location=loc)
        msg["message_id"] = message_id
        if edited:
            msg["edit_date"] = int(time.time())
            await self._feed({"edited_message": msg})
        else:
            await self._feed({"message": msg})

    async def press(self, tg_id: int, msg: Msg, data_prefix: str, username: str | None = None):
        data = msg.data(data_prefix)
        await self.press_data(tg_id, msg, data, username)

    async def press_data(self, tg_id: int, msg: Msg, data: str, username: str | None = None):
        await self._feed({"callback_query": {
            "id": str(next(self._update_ids)), "from": self._user(tg_id, username), "chat_instance": "ci",
            "data": data,
            "message": {"message_id": msg.message_id, "date": int(time.time()),
                        "chat": {"id": msg.chat_id, "type": "private"}, "from": BOT_USER, "text": msg.text},
        }})
