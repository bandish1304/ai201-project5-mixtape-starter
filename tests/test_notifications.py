"""tests/test_notifications.py — Mixtape

Tests for notification creation when songs are rated.
"""

import pytest
from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_rating_song_creates_notification_for_sharer(app):
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Shared Song", artist="Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        before = db.session.query(Notification).filter_by(user_id=sharer.id).count()
        rate_song(rater.id, song.id, 4)
        after = db.session.query(Notification).filter_by(user_id=sharer.id).count()

        assert before == 0
        assert after == 1
        notification = db.session.query(Notification).filter_by(user_id=sharer.id).one()
        assert notification.notification_type == "song_rated"
        assert "rated your song" in notification.body
