"""tests/test_feed.py — Mixtape

Tests for the listening-now feed boundary behavior.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services import feed_service


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_listening_now_excludes_yesterday_events(app, monkeypatch):
    with app.app_context():
        fixed_now = datetime(2024, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(feed_service, "datetime", FixedDatetime)

        owner = User(username="owner", email="owner@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([owner, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=owner.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=owner.id))

        song = Song(title="Boundary Song", artist="Artist", shared_by=owner.id)
        db.session.add(song)
        db.session.flush()

        yesterday_event = ListeningEvent(
            user_id=friend.id,
            song_id=song.id,
            listened_at=fixed_now - timedelta(hours=12),
        )
        today_event = ListeningEvent(
            user_id=friend.id,
            song_id=song.id,
            listened_at=fixed_now - timedelta(hours=2),
        )
        db.session.add_all([yesterday_event, today_event])
        db.session.commit()

        feed = feed_service.get_friends_listening_now(owner.id)
        assert len(feed) == 1
        assert feed[0]["listened_at"] == today_event.listened_at.isoformat()
