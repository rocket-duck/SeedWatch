import pytest

from app.qbit import QbitClient


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("[Anistar.org] Shrouding the Heavens [TV-1] - 181 [1080p].mp4", {181}),
        ("[Anistar.org] Shrouding the Heavens [TV-1] - 001 [1080p].mkv", {1}),
        ("Shrouding the Heavens (156) фильм Битва.mp4", {156}),
        ("Zhe Tian (33) серия.mkv", {33}),
        ("Tunshi.Xingkong.Ona.S05e33-E241 - 1080.mp4", {33, 241}),
        ("Demon.Hunter.S03E15.(2026).WEBRip.2160p.mp4", {15}),
        ("Show - 12 [720p].mkv", {12}),
        ("01.ass", {1}),
        ("poster.jpg", set()),
    ],
)
def test_file_episodes(filename, expected):
    assert QbitClient._file_episodes(filename) == expected
