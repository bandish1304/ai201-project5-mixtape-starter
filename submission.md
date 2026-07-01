# Mixtape Codebase Map

This project is organized like a small Flask app with a very clear split between routing, data models, and business logic. The routes mostly stay thin and pass work into service functions. That makes the app easier to follow once you understand the call chain.

## Main Files

### app.py
This is the Flask entry point. It creates the app, sets up the database connection, registers the blueprints, and creates the tables on startup. It also contains a simple home route for the root URL.

### models.py
This file defines all of the database tables and relationships.

The main models are:
- User, which stores usernames, emails, streaks, and timestamps
- Song, which stores shared songs and links each song back to the user who shared it
- ListeningEvent, which records when a user listened to a song
- Rating, which stores a user’s score for a song
- Playlist, which stores playlist metadata
- Notification, which stores in-app notifications
- Tag, which supports song tagging

It also defines the join tables for friendships, song tags, and playlist entries. The playlist join table is important because it stores extra information like position, who added the song, and when it was added.

### routes/
The route files are the HTTP layer. They parse requests, validate the obvious missing fields, call the relevant service, and turn the result into JSON.

- routes/songs.py handles song search, song details, rating, and listen events
- routes/playlists.py handles creating playlists, reading playlist data, reading playlist songs, and adding a song to a playlist
- routes/users.py handles user lookup, streak lookups, and notifications
- routes/feed.py handles the friends listening now feed and the activity feed

### services/
This is where the actual app logic lives.

- services/streak_service.py updates a user’s listening streak when they listen to a song and also returns the current streak
- services/feed_service.py builds the friends listening now feed and the broader activity feed
- services/search_service.py searches songs by title or artist and returns song details
- services/notification_service.py creates notifications, records ratings, adds songs to playlists, and returns notifications
- services/playlist_service.py creates playlists and reads playlist metadata and songs

## How The Pieces Fit Together

The app follows the same pattern almost everywhere:

1. A route receives the request and pulls values out of the URL, query string, or JSON body.
2. The route does light validation for missing required inputs.
3. The route calls one service function.
4. The service talks to the database through SQLAlchemy.
5. The route formats the returned object or list into JSON.

That separation is the main design pattern in this codebase. The routes do not contain much business logic, and the services do not worry about HTTP response formatting.

## Example Data Flow: Adding A Song To A Playlist

One clear feature flow is the playlist add-song path:

1. The client sends a POST request to /playlists/<playlist_id>/songs.
2. routes/playlists.py reads song_id and added_by from the request body.
3. routes/playlists.py calls services.notification_service.add_to_playlist().
4. add_to_playlist() looks up the song, the user who added it, and the playlist.
5. If the song is not already in the playlist, the function appends it and commits the change.
6. If the person who added the song is not the original sharer, the function creates a Notification for the original sharer.
7. The route returns a simple success message as JSON.

So this one endpoint actually touches two pieces of behavior: playlist mutation and notification creation. The route stays simple, but the service coordinates the database work.

## Another Example: Listening And Streaks

The listening flow is similar:

1. The client posts to /songs/<song_id>/listen.
2. routes/songs.py reads user_id from the body.
3. routes/songs.py calls services.streak_service.record_listening_event().
4. That service creates a ListeningEvent row, updates the user’s listening streak, and commits the transaction.
5. The route returns the new listening event.

That flow shows the same shape as the playlist example: route first, service second, database last.

## Patterns I Noticed

- The app is built around thin routes and heavier services.
- Most objects have to_dict methods, so JSON responses are usually built by converting model objects at the edge.
- The services usually fetch records with db.session.get() and raise ValueError when something is missing, which the routes turn into HTTP errors.
- Relationships are modeled directly in SQLAlchemy rather than through a lot of separate helper layers.
- The codebase is small enough that the easiest way to understand a feature is to trace it from the route to the service and then to the model it touches.

## One Small Note From Reading The Code

The playlist song lookup in services/playlist_service.py is supposed to return songs in playlist order. That file is worth extra attention because it is one of the places where the ordering logic really matters.

## Root Cause Analysis

### Issue 5: The last song in a playlist never shows up

1. How I reproduced it

I started the app with the seed data and requested the playlist songs endpoint for a playlist that already had multiple songs in it. The response consistently returned every song except the last one in that playlist. I confirmed the bug by comparing the playlist length in the database with the JSON response from /playlists/<playlist_id>/songs.

2. How I found the root cause

I traced the feature from routes/playlists.py to services/playlist_service.py because the route for GET /playlists/<playlist_id>/songs calls get_playlist_songs(). I also looked at models.py to understand how playlist_entries stores ordering information. The moment that made the cause obvious was in get_playlist_songs(): the function queried all songs in order, then returned songs[:-1], which removes the last item from every non-empty playlist.

3. The root cause

The bug was an off-by-one slice in services/playlist_service.py. The query was already returning the full ordered song list, but the final return statement cut off the last element by slicing with songs[:-1]. That meant the endpoint always dropped the final playlist song, even though the database query found it correctly.

4. My fix and side-effect check

I changed the return value to use the full list of songs instead of slicing off the last item. After that, I reran tests/test_playlists.py and confirmed the playlist-related tests still passed. I also checked the neighboring playlist route handlers in routes/playlists.py to make sure the fix did not affect playlist creation, metadata lookup, or song addition behavior.

### Issue 1: My listening streak keeps resetting

1. How I reproduced it

I reproduced the bug with the streak tests by simulating a listen on Saturday and then another listen on Sunday for the same user. The streak should have moved from 1 to 2, but it stayed at 1. That showed the bug only happened when the second listen crossed the Saturday-to-Sunday boundary.

2. How I found the root cause

I started in routes/songs.py because the listen endpoint is what triggers streak updates. That led directly to services/streak_service.py and the update_listening_streak() function. The important line was the conditional that handled a one-day gap: it incremented only when today.weekday() was not 6. Once I compared that with Python's weekday numbering, it was clear that Sunday was being treated as a reset case instead of a consecutive day.

3. The root cause

The streak logic had an extra weekday check on top of the one-day-gap check. Python's weekday() returns 6 for Sunday, so the code was explicitly excluding Sunday from the increment path. That meant Saturday -> Sunday always reset the streak instead of extending it.

4. My fix and side-effect check

I removed the weekday check and left the logic as a plain one-day increment. That keeps same-day listens from double counting, still resets after skipped days, and correctly increments across Sunday. I verified the fix with tests/test_streaks.py, including the Sunday case and the neighboring same-day and skipped-day cases.

### Issue 2: Friends Listening Now shows people from yesterday

1. How I reproduced it

I reproduced this by fixing the current time in the feed service, creating one listening event that was still on the current calendar day and another event from the previous day, then calling the listening-now feed. Before the fix, the code path would include anything within the last 24 hours, which allowed yesterday's listen to appear if it was still recent enough.

2. How I found the root cause

I traced the feed route from routes/feed.py into services/feed_service.py and focused on get_friends_listening_now(). The suspicious line was the cutoff calculation: it used datetime.now(timezone.utc) minus 24 hours. That is a rolling time window, not a calendar-day boundary. Once I compared that with the bug report wording, the mismatch was obvious: the code was treating "recent" as 24 hours, but the bug was about yesterday's activity leaking into today's feed.

3. The root cause

The feed logic used a 24-hour sliding window instead of a start-of-day boundary. That means a listen from yesterday evening could still appear if the current time was less than 24 hours later, even though it belonged to the previous day.

4. My fix and side-effect check

I changed the cutoff to the start of the current UTC day so only listens from today are included in Friends Listening Now. I then checked both sides of the boundary with a fixed-time regression test in tests/test_feed.py: a today event stayed in the feed, and a yesterday event was excluded. I also confirmed the rest of the feed behavior still worked by running the existing playlist, search, and streak tests alongside it.

### Issue 4: I got notified when a friend added my song to a playlist but not when they rated it

1. How I reproduced it

Using the seeded data, I rated one of another user's shared songs and checked that user's notification count before and after the rating. The count did not change, which confirmed that the rating path was not creating a notification.

2. How I found the root cause

I followed the route from routes/songs.py into services/notification_service.py, because the rate endpoint calls rate_song(). Then I compared that function with add_to_playlist() in the same file. add_to_playlist() already showed the intended notification pattern: after the database change, it calls create_notification() for the original sharer. rate_song() updated or created the Rating, but it never did that final notification step.

3. The root cause

The rating service saved the rating record and committed it, but it stopped there. The notification logic existed for playlist adds, but the rating branch never called create_notification(), so the song's original sharer never heard about ratings.

4. My fix and side-effect check

I added a notification step after the rating commit that sends the original sharer a song_rated notification when someone else rates their song. I verified the fix with a regression test in tests/test_notifications.py and also checked the same rating flow against the seeded database to confirm the notification count increased.

### Issue 3: The same song keeps showing up twice in search

1. How I reproduced it

I traced the search path with seeded songs that have multiple tags, because that is the data shape called out in the project notes for this issue. The search path goes through routes/songs.py into services/search_service.py. The query shape there uses an outer join against song_tags, which is the part that can multiply rows for songs with more than one tag.

2. How I found the root cause

I looked at routes/songs.py first, then services/search_service.py, and then checked models.py to see how song_tags is structured. The key moment was seeing that search_songs() joined song_tags but did not remove duplicate song rows afterward. That is the classic pattern that can return the same ORM entity multiple times when a song has multiple related tag rows.

3. The root cause

The search query was built on top of a join to the song_tags association table, but it did not de-duplicate the song rows after the join. For songs with more than one tag row, that join can produce repeated rows for the same Song.

4. My fix and side-effect check

I added DISTINCT to the Song query so each matching song only appears once in the result list. After that, I reran the search tests and confirmed the search results still returned the expected songs without duplicates, including the multi-tag song case used in the regression tests.
