import hashlib
import io
import json
from unittest.mock import patch

import pytest

from traceweave_memory.evaluation.download import DATASET_ID, download_subset


def metadata(tmp_path, content=b"public fixture"):
    path = tmp_path / "metadata.json"
    digest = hashlib.sha256(content).hexdigest()
    path.write_text(
        json.dumps(
            {
                "id": DATASET_ID,
                "sha": "a" * 40,
                "cardData": {"license": "mit"},
                "siblings": [
                    {
                        "rfilename": "data/dialog/topic.json",
                        "size": len(content),
                        "lfs": {"sha256": digest},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_pinned_download_verifies_hash_and_reuses_valid_cache(tmp_path):
    source = metadata(tmp_path)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"public fixture")) as fetch:
        result = download_subset(source, tmp_path / "dataset", ["topic"])
    assert "resolve/" + "a" * 40 in fetch.call_args.args[0]
    assert result["files"][0]["bytes"] == 14
    with patch("urllib.request.urlopen", side_effect=AssertionError("cache should be used")):
        assert download_subset(source, tmp_path / "dataset", ["topic"]) == result


def test_download_limit_rejects_before_network(tmp_path):
    source = metadata(tmp_path)
    with (
        patch("urllib.request.urlopen", side_effect=AssertionError("no request")),
        pytest.raises(ValueError, match="byte budget"),
    ):
        download_subset(source, tmp_path / "dataset", ["topic"], max_bytes=1)


def test_bad_cached_file_cannot_become_new_manifest(tmp_path):
    source = metadata(tmp_path)
    output = tmp_path / "dataset"
    target = output / "data/dialog/topic.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"wrong fixture!")
    with pytest.raises(ValueError, match="hash"):
        download_subset(source, output, ["topic"])
    assert not (output / "manifest.json").exists()


def test_download_rejects_unknown_topic(tmp_path):
    with pytest.raises(ValueError, match="topics"):
        download_subset(metadata(tmp_path), tmp_path / "dataset", ["unknown"])
