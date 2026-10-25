import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from traceweave_memory.adapters.media import decode_image
from traceweave_memory.bootstrap import create_app
from traceweave_memory.config import Settings
from traceweave_memory.domain import MediaError


def image_uri(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def message(text="Alice bought a red umbrella.", image=None):
    content = text
    if image:
        content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image}},
        ]
    return {"role": "user", "content": content}


def payload(scope="alice", request="r1", messages=None):
    return {
        "user_id": scope,
        "request_id": request,
        "session_id": "s1",
        "messages": messages or [message()],
    }


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as test_client:
        yield test_client


def search(client, query="umbrella", scope="alice", **kwargs):
    return client.post("/search", json={"query": query, "user_id": scope, "top_k": 100, **kwargs})


def test_ordered_multimodal_roundtrip_and_restart(tmp_path):
    original = payload(messages=[message(image=image_uri())])
    original["messages"][0]["content"].append({"type": "text", "text": "  ticket 987  "})
    original["messages"][0]["timestamp"] = 0
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.post("/add", json=original).json() == {
            "success": True,
            "user_id": "alice",
            "request_id": "r1",
            "session_id": "s1",
        }
        result = search(client, "987").json()["data"][0]
        assert result["content"] == original["messages"][0]["content"]
        assert result["created_at"] == "1970-01-01T00:00:00Z"
    with TestClient(create_app(Settings(data_dir=tmp_path))) as restarted:
        assert search(restarted, "987").json()["data"][0] == result


def test_idempotency_and_changed_payload_conflict(client):
    original = payload()
    assert client.post("/add", json=original).status_code == 200
    before = search(client).json()
    assert client.post("/add", json=original).status_code == 200
    assert search(client).json() == before
    changed = payload(messages=[message("green umbrella")])
    assert client.post("/add", json=changed).status_code == 409
    assert search(client).json() == before


def test_scope_isolation_including_scores_and_media(client):
    client.post("/add", json=payload(messages=[message(image=image_uri())]))
    before = search(client).json()
    assert search(client, scope="alice ").json() == {"data": []}
    client.post("/add", json=payload("bob", messages=[message("umbrella " * 50)]))
    assert search(client).json() == before
    assert search(client, scope="bob").json()["data"][0]["id"] != before["data"][0]["id"]


@pytest.mark.parametrize(
    "mode,header",
    [
        ("bearer", {"Authorization": "Bearer example-secret"}),
        ("token", {"Authorization": "Token example-secret"}),
        ("x-api-key", {"X-Api-Key": "example-secret"}),
    ],
)
def test_authentication_and_public_health(tmp_path, mode, header):
    with TestClient(create_app(Settings(tmp_path, "example-secret", mode))) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/add", json=payload()).status_code == 401
        assert client.post("/add", json=payload(), headers=header).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"top_k": True},
        {"top_k": 101},
        {"top_k": 0},
        {"top_k": "10"},
        {"query": " "},
        {"query": []},
        {"query": [{"type": "video", "url": "x"}]},
        {"user_id": " "},
        {"options": "A"},
        {"filters": {}},
    ],
)
def test_search_contract_rejects_invalid_input(client, change):
    assert search(client, **change).status_code == 422


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com/image.png",
        "data:image/png;base64,!!!!",
        "data:image/png;base64,YWJj",
        image_uri().replace("image/png", "image/jpeg"),
    ],
)
def test_invalid_media_is_atomic_and_safe(client, uri):
    body = payload(messages=[message(), message(image=uri)])
    response = client.post("/add", json=body)
    assert response.status_code == 422
    assert search(client).json() == {"data": []}
    assert uri not in response.text


def test_schema_error_does_not_echo_body(client):
    response = client.post("/add", json={"messages": "sensitive-unexpected-content"})
    assert response.status_code == 422
    assert "sensitive-unexpected-content" not in response.text


def test_text_whitespace_chinese_and_options_not_answer(client):
    client.post("/add", json=payload(messages=[message("  小明买了红色雨伞，编号123。  ")]))
    assert search(client, "红色雨伞").json()["data"][0]["content"].startswith("  ")
    assert search(client, "unrelated", options=["123"]).json() == {"data": []}


def test_image_only_query_is_explicit_b0_empty(client):
    query = [{"type": "image_url", "image_url": {"url": image_uri()}}]
    assert search(client, query).json() == {"data": []}


def test_media_corruption_fails_instead_of_partial_success(client):
    client.post("/add", json=payload(messages=[message(image=image_uri())]))
    media = next(client.app.state.settings.data_dir.glob("media/*/*"))
    media.write_bytes(b"corrupt")
    response = search(client)
    assert response.status_code == 503


def test_sql_failure_rolls_back_evidence_and_fts(client):
    repository = client.app.state.service.repository
    with repository.connect() as db:
        db.executescript("""CREATE TRIGGER fail_second BEFORE INSERT ON evidence
            WHEN new.position=1 BEGIN SELECT RAISE(ABORT, 'injected'); END;""")
    with pytest.raises(Exception, match="injected"):
        client.post("/add", json=payload(messages=[message(), message("second umbrella")]))
    assert search(client).json() == {"data": []}
    with repository.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM lexical").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_second")
        db.commit()
    assert client.post("/add", json=payload()).status_code == 200


def test_concurrent_duplicate_writes(client):
    body = payload(messages=[message(image=image_uri())])
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda _: client.post("/add", json=body).status_code, range(8)))
    assert codes == [200] * 8
    assert len(search(client).json()["data"]) == 1


def test_local_body_limit_includes_chunked_requests(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, max_body_bytes=100))) as client:
        response = client.post(
            "/add",
            content=iter([b" " * 60, b" " * 60]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_media_limits_checked_before_writes_and_output_keeps_whole_records(client):
    body = payload(messages=[message(image=image_uri())])
    with patch("traceweave_memory.application.MAX_MEDIA_BYTES", 1):
        assert client.post("/add", json=body).status_code == 422
    assert search(client).json() == {"data": []}
    client.post("/add", json=body)
    with patch("traceweave_memory.application.MAX_MEDIA_BYTES", 1):
        assert search(client).json() == {"data": []}


def test_image_size_limit():
    with (
        patch("traceweave_memory.adapters.media.MAX_IMAGE_BYTES", 1),
        pytest.raises(MediaError, match="10 MiB"),
    ):
        decode_image(image_uri())


def test_fts_query_syntax_is_data_and_top_k_is_respected(client):
    client.post("/add", json=payload(messages=[message("umbrella"), message("umbrella red")]))
    assert search(client, '" OR umbrella NOT * --', top_k=1).status_code == 200
    assert len(search(client, "umbrella", top_k=1).json()["data"]) == 1


def test_unknown_database_version_refused(tmp_path):
    import sqlite3

    path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="schema"):
        create_app(Settings(data_dir=tmp_path))


def test_same_request_id_is_independent_across_scopes(client):
    for scope in ["alice", "bob"]:
        assert client.post("/add", json=payload(scope=scope)).status_code == 200
    assert len(search(client, scope="bob").json()["data"]) == 1


def test_timestamp_and_add_schema(client):
    body = payload()
    body["messages"][0]["timestamp"] = True
    assert client.post("/add", json=body).status_code == 422
    body["messages"][0]["timestamp"] = 10**25
    assert client.post("/add", json=body).status_code == 422
    assert search(client).json() == {"data": []}


def test_media_reference_is_not_exposed(client):
    client.post("/add", json=payload(messages=[message(image=image_uri())]))
    assert "media_ref" not in json.dumps(search(client).json())
