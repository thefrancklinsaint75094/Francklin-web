"""Erreurs de polling : pas d'alerte pour un conflit passager, une alerte s'il dure."""
from types import SimpleNamespace

import pytest
from telegram.error import Conflict, TimedOut

from bot import main, messaging


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_notify(bot, text, markup=None, **kw):
        out.append(text)

    async def fake_log(*a, **k):
        out.append("event")

    monkeypatch.setattr(messaging, "notify_dispatch", fake_notify)
    monkeypatch.setattr(main.db, "log_event", fake_log)
    main._conflict.update(first=None, last=None, notified=False)
    return out


def ctx(err):
    return SimpleNamespace(error=err, bot=None)


async def test_transient_conflict_is_silent(sent):
    for _ in range(4):
        await main.error_handler(None, ctx(Conflict("terminated by other getUpdates request")))
    assert sent == []


async def test_network_error_is_silent(sent):
    await main.error_handler(None, ctx(TimedOut()))
    assert sent == []


async def test_persistent_conflict_alerts_once(sent, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    for _ in range(20):  # un conflit toutes les 10 s pendant 200 s
        await main.error_handler(None, ctx(Conflict("x")))
        clock[0] += 10
    assert sent.count(main.texts.D_TWO_INSTANCES) == 1


async def test_new_streak_after_silence(sent, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    await main.error_handler(None, ctx(Conflict("x")))
    clock[0] += 100
    await main.error_handler(None, ctx(Conflict("x")))  # 100 s de silence : nouvel épisode
    assert sent == []
