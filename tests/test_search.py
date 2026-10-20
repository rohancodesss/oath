from oath.android.reader import load_android
from oath.search import build_search_db, list_chats, search


def test_build_and_search(tmp_path, android_root, key_hex):
    result = load_android(android_root, key_hex)
    db_path = tmp_path / "search.db"

    n = build_search_db(result, db_path)
    assert n == result.message_count

    hits = search(db_path, "Saturday")
    assert len(hits) == 1
    assert "Saturday" in hits[0].text
    assert hits[0].from_me is False

    assert search(db_path, "no-such-word-anywhere") == []


def test_search_scoped_to_chat(tmp_path, android_root, key_hex):
    result = load_android(android_root, key_hex)
    db_path = tmp_path / "search.db"
    build_search_db(result, db_path)

    chats = list_chats(db_path)
    assert sum(n for _, n in chats) == result.message_count

    some_chat, _ = chats[0]
    assert all(h.chat == some_chat for h in search(db_path, "a", chat=some_chat, limit=1000))


def test_rebuild_overwrites(tmp_path, android_root, key_hex):
    result = load_android(android_root, key_hex)
    db_path = tmp_path / "search.db"
    build_search_db(result, db_path)
    n2 = build_search_db(result, db_path)
    assert n2 == result.message_count
