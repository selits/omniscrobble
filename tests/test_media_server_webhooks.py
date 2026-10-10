"""Focused parser and webhook integration tests for Jellyfin and Emby."""

from tests.helpers import *  # noqa: F403
from tests.helpers import _ensure_servers_enabled_for_tests  # noqa: F401

pytestmark = pytest.mark.usefixtures("_ensure_servers_enabled_for_tests")

@pytest.mark.asyncio(loop_scope="module")
async def test_jellyfin_parser_and_webhook():
    from app.jellyfin_parser import parse_jellyfin_webhook
    client = make_async_test_client(app)

    # Info GET endpoint
    info_res = await client.get("/webhook/jellyfin")
    assert info_res.status_code == 200
    assert "Jellyfin" in info_res.json()["instructions"]

    # 1. Movie playback start
    jf_movie_payload = {
        "NotificationType": "PlaybackStart",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Oppenheimer",
        "Year": 2023,
        "RunTimeTicks": 108000000000,  # 180 min
        "PlaybackPositionTicks": 10800000000,  # 10%
        "Provider_Ids": {"Imdb": "tt15398776", "Tmdb": "872585"},
        "DeviceName": "Living Room Shield",
        "Client": "Jellyfin AndroidTV",
    }

    parsed = parse_jellyfin_webhook(jf_movie_payload)
    assert parsed is not None
    assert parsed.event == "media.play"
    assert parsed.server_type == "jellyfin"
    assert parsed.media_type == "movie"
    assert parsed.title == "Oppenheimer"
    assert parsed.year == 2023
    assert parsed.ids["imdb"] == "tt15398776"
    assert parsed.ids["tmdb"] == 872585
    assert abs(parsed.progress - 10.0) < 0.1

    # Post to /webhook/jellyfin
    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {"action": "start"}
        res = await client.post("/webhook/jellyfin", json=jf_movie_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_start"

    # 2. Episode playback stop at 85% (scrobble_stop)
    jf_ep_payload = {
        "NotificationType": "PlaybackStop",
        "NotificationUsername": "selits",
        "ItemType": "Episode",
        "SeriesName": "Succession",
        "Name": "Connor's Wedding",
        "SeasonNumber": 4,
        "EpisodeNumber": 3,
        "Year": 2023,
        "RunTimeTicks": 36000000000,
        "PlaybackPositionTicks": 30600000000,  # 85%
        "Provider_Ids": {"Imdb": "tt22216852", "Tvdb": "80349"},
        "DeviceName": "Bedroom Roku",
    }

    parsed_ep = parse_jellyfin_webhook(jf_ep_payload)
    assert parsed_ep is not None
    assert parsed_ep.event == "media.stop"
    assert parsed_ep.server_type == "jellyfin"
    assert parsed_ep.media_type == "episode"
    assert parsed_ep.show_title == "Succession"
    assert parsed_ep.season == 4
    assert parsed_ep.episode == 3
    assert abs(parsed_ep.progress - 85.0) < 0.1

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        res = await client.post("/webhook/jellyfin", json=jf_ep_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"

    # 3. UserDataSaved rating event
    jf_rate_payload = {
        "NotificationType": "UserDataSaved",
        "NotificationUsername": "selits",
        "ItemType": "Movie",
        "Name": "Inception",
        "Year": 2010,
        "UserData": {"IsFavorite": True},
        "Provider_Ids": {"Imdb": "tt1375666"},
    }
    parsed_rate = parse_jellyfin_webhook(jf_rate_payload)
    assert parsed_rate is not None
    assert parsed_rate.event == "media.rate"
    assert parsed_rate.rating == 10

    # 4. User filtering
    parsed_ignored = parse_jellyfin_webhook(jf_ep_payload, allowed_users=["other_user"])
    assert parsed_ignored is None


@pytest.mark.asyncio(loop_scope="module")
async def test_emby_parser_and_webhook():
    from app.emby_parser import parse_emby_webhook
    client = make_async_test_client(app)

    # Info GET endpoint
    info_res = await client.get("/webhook/emby")
    assert info_res.status_code == 200
    assert "Emby" in info_res.json()["instructions"]

    # 1. Movie playback pause
    emby_payload = {
        "Event": "playback.pause",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Movie",
            "Name": "Interstellar",
            "ProductionYear": 2014,
            "RunTimeTicks": 101400000000,
            "ProviderIds": {"Imdb": "tt0816692", "Tmdb": "157336"},
        },
        "PlaybackInfo": {
            "PositionTicks": 20280000000,  # 20%
            "DeviceName": "Living Room TV",
        },
    }

    parsed = parse_emby_webhook(emby_payload)
    assert parsed is not None
    assert parsed.event == "media.pause"
    assert parsed.server_type == "emby"
    assert parsed.media_type == "movie"
    assert parsed.title == "Interstellar"
    assert parsed.year == 2014
    assert parsed.ids["imdb"] == "tt0816692"
    assert parsed.ids["tmdb"] == 157336
    assert abs(parsed.progress - 20.0) < 0.1

    # Null PremiereDate is valid when ProductionYear is absent.
    null_premiere = {
        "Event": "playback.start",
        "Item": {"Type": "Movie", "Name": "Unknown Year", "ProductionYear": None, "PremiereDate": None},
    }
    assert parse_emby_webhook(null_premiere).year is None

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_pause", new_callable=AsyncMock) as mock_pause:
        mock_pause.return_value = {"action": "pause"}
        res = await client.post("/webhook/emby", json=emby_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_pause"

    # 2. Episode playback stop at 95%
    emby_ep_payload = {
        "Event": "playback.stop",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Episode",
            "Name": "Pilot",
            "SeriesName": "Breaking Bad",
            "ParentIndexNumber": 1,
            "IndexNumber": 1,
            "ProductionYear": 2008,
            "RunTimeTicks": 34800000000,
            "ProviderIds": {"Tvdb": "81189"},
        },
        "PlaybackInfo": {
            "PositionTicks": 33060000000,  # 95%
            "DeviceName": "iPad",
        },
    }

    parsed_ep = parse_emby_webhook(emby_ep_payload)
    assert parsed_ep is not None
    assert parsed_ep.event == "media.stop"
    assert parsed_ep.server_type == "emby"
    assert parsed_ep.season == 1
    assert parsed_ep.episode == 1
    assert parsed_ep.show_title == "Breaking Bad"

    with patch.object(trakt, "is_authenticated", return_value=True), \
         patch.object(trakt, "scrobble_stop", new_callable=AsyncMock) as mock_stop, \
         patch.object(notifier, "dispatch", new_callable=AsyncMock):
        mock_stop.return_value = {"action": "scrobble"}
        res = await client.post("/webhook/emby", json=emby_ep_payload)
        assert res.status_code == 200
        assert res.json()["action"] == "scrobble_stop"

    # 3. Rating event: item.rate
    emby_rate_payload = {
        "Event": "item.rate",
        "User": {"Name": "selits"},
        "Item": {
            "Type": "Movie",
            "Name": "Arrival",
            "ProductionYear": 2016,
            "ProviderIds": {"Imdb": "tt2543164"},
        },
        "UserRating": 9,
    }
    parsed_rate = parse_emby_webhook(emby_rate_payload)
    assert parsed_rate is not None
    assert parsed_rate.event == "media.rate"
    assert parsed_rate.rating == 9


