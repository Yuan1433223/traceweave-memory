import base64
import copy
import io
import json

import pytest
from PIL import Image

from traceweave_memory.evaluation.mem_gallery import image_content, prepare_memory, prepare_query
from traceweave_memory.evaluation.runner import retrieval_metrics


def make_source(tmp_path):
    image = tmp_path / "data/image/topic/image.png"
    image.parent.mkdir(parents=True)
    stream = io.BytesIO()
    Image.new("RGB", (3, 3)).save(stream, "PNG")
    image.write_bytes(stream.getvalue())
    turn = {
        "round": "D1:1",
        "user": "user original",
        "assistant": "assistant original",
        "input_image": ["../image/topic/image.png"],
        "image_id": ["D1:IMG_1"],
        "image_caption": ["original caption"],
    }
    return {
        "multi_session_dialogues": [
            {"session_id": "D1", "date": "2024-06-17", "dialogues": [turn]}
        ],
        "human-annotated QAs": [
            {"question": "question", "answer": "secret-gold", "clue": ["D1:1"], "point": "FR"}
        ],
    }


def test_dataset_separates_roles_gold_and_source_mapping(tmp_path):
    source = make_source(tmp_path)
    before = copy.deepcopy(source)
    memory = prepare_memory(source, tmp_path, "scope")
    assert source == before
    write = memory.writes[0]
    assert [item["role"] for item in write["messages"]] == ["user", "assistant"]
    assert "secret-gold" not in json.dumps(memory.writes)
    assert "clue" not in json.dumps(memory.writes)
    labels = list(memory.source_ids_by_evidence.values())
    assert {"D1:1", "D1:IMG_1"} in labels
    query = prepare_query(source["human-annotated QAs"][0], tmp_path, "scope", 10)
    assert query == {"query": "question", "user_id": "scope", "top_k": 10}


def test_adapter_preserves_all_images_and_rejects_missing_media(tmp_path):
    source = make_source(tmp_path)
    turn = source["multi_session_dialogues"][0]["dialogues"][0]
    turn["input_image"] *= 2
    turn["image_caption"] *= 2
    turn["image_id"].append("D1:IMG_2")
    memory = prepare_memory(source, tmp_path, "scope")
    content = memory.writes[0]["messages"][0]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 2
    turn["input_image"][0] = "../image/topic/missing.png"
    with pytest.raises(FileNotFoundError):
        prepare_memory(source, tmp_path, "scope")


def test_metrics_no_gold_is_not_success_and_duplicate_views_not_double_counted():
    assert retrieval_metrics([], set(), 10)["complete"] is None
    labels = [{"a"}, {"a"}, {"b"}]
    assert retrieval_metrics(labels, {"a", "b"}, 2)["recall"] == 0.5
    assert retrieval_metrics(labels, {"a", "b"}, 3)["complete"] == 1


def test_adapter_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        image_content(tmp_path, "../image/../../outside.png")


def test_query_image_is_original_and_answer_not_attached(tmp_path):
    make_source(tmp_path)
    question = {
        "question": "Which image?",
        "question_image": "../image/topic/image.png",
        "answer": "not-query",
    }
    query = prepare_query(question, tmp_path, "scope", 20)
    uri = query["query"][1]["image_url"]["url"]
    assert base64.b64decode(uri.split(",")[1]).startswith(b"\x89PNG")
    assert "not-query" not in json.dumps(query)


def test_dataset_filename_mime_mismatch_is_serialized_with_actual_type(tmp_path):
    make_source(tmp_path)
    original = tmp_path / "data/image/topic/image.png"
    mislabeled = original.with_suffix(".jpg")
    mislabeled.write_bytes(original.read_bytes())
    content = image_content(tmp_path, "../image/topic/image.jpg")
    assert content["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(content["image_url"]["url"].split(",")[1]) == original.read_bytes()
