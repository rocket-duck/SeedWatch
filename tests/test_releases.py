import pytest

from app.releases import find_matching_release, parse_episodes, parse_season, release_identity


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Боевой континент 2 (1 сезон: 145-174 серии из 174)", list(range(145, 175))),
        ("Пожиратель звёзд (Поглощаю звезду) (5 сезон: 1-39 серии из 52 + Фильм) / Tunshi Xingkong (Swallowed Star) / 2026 / ЛМ / WEBRip (1080p)", list(range(1, 40))),
        ("Name S01E145-174", list(range(145, 175))),
        ("Name S05E01-E209", [1, 209]),
        ("Name S05E11-film", [11]),
        ("Name E01-E12", [1, 12]),
        ("Name E01-12", list(range(1, 13))),
        ("Name [AVC][1-12]", list(range(1, 13))),
        ("Name 2023-2026 WEB-DL 1080p", []),
    ],
)
def test_parse_episodes(title, expected):
    assert parse_episodes(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Cang Yuan Tu (The Demon Hunter) - S3E1-31 - 2026 MVO (HORIZON) 4K, SDR WEBRip 2160p - RUSSIAN", 3),
        ("Name S01E145-E174", 1),
        ("Боевой континент 2 (1 сезон: 145-174 серии из 174)", 1),
        ("Name Season 2 E01-E12", 2),
        ("Пожиратель звёзд (Поглощаю звезду) (5 сезон: 1-39 серии из 52 + Фильм) / Tunshi Xingkong (Swallowed Star) / 2026 / ЛМ / WEBRip (1080p)", 5),
        ("Name E01-E12", None),
        ("Name 2023-2026 WEB-DL 1080p", None),
        ("(S1,2,3,4) Name [1-12]", None),
        ("Name S2E05 / Season 2", 2),
    ],
)
def test_parse_season(title, expected):
    assert parse_season(title) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.kinozal.tv:443/details.php?id=456",
        "http://kinozal.tv/details.php?id=456",
    ],
)
def test_kinozal_identity_ignores_scheme_www_and_default_port(url):
    assert release_identity(url) == "kinozal.tv/details.php?id=456"


def test_generic_identity_ignores_trailing_slash():
    assert release_identity("https://aniliberty.top/anime/releases/release/show/") == (
        "aniliberty.top/anime/releases/release/show"
    )


def test_invalid_identity():
    assert release_identity("ftp://example.com/release") is None


def test_find_release_by_generic_url():
    expected = {"infoUrl": "https://www.aniliberty.top/anime/releases/release/show"}
    assert find_matching_release([expected], "aniliberty.top/anime/releases/release/show") == expected
