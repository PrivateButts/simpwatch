import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from simpwatch.views import _get_app_access_token

USERS_URL = "https://api.twitch.tv/helix/users"
STREAMS_URL = "https://api.twitch.tv/helix/streams"


class Command(BaseCommand):
    help = "Fetch Twitch Helix user/stream info for a broadcaster login."

    def add_arguments(self, parser):
        parser.add_argument(
            "broadcaster",
            help="Twitch broadcaster login (for example: prvbutts)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print JSON output instead of a formatted summary.",
        )

    def handle(self, *args, **options):
        broadcaster = str(options["broadcaster"]).strip().lower()
        if not broadcaster:
            raise CommandError("broadcaster is required")

        client_id = str(getattr(settings, "TWITCH_CLIENT_ID", "")).strip()
        client_secret = str(getattr(settings, "TWITCH_CLIENT_SECRET", "")).strip()
        if not client_id or not client_secret:
            raise CommandError(
                "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be configured."
            )

        access_token = _get_app_access_token(client_id, client_secret)
        if not access_token:
            raise CommandError(
                "Failed to obtain Twitch app access token. Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET."
            )

        user_data = self._get_user_data(client_id, access_token, broadcaster)

        users = user_data.get("data", [])
        if not users:
            raise CommandError(f"No Twitch user found for login '{broadcaster}'.")

        user = users[0]
        user_id = str(user.get("id", "")).strip()
        if not user_id:
            raise CommandError("Twitch returned user data without an id.")

        stream_data = self._get_stream_data(client_id, access_token, user_id)
        streams = stream_data.get("data", [])
        stream = streams[0] if streams else {}

        payload = {
            "login": broadcaster,
            "user": {
                "id": user_id,
                "login": user.get("login", ""),
                "display_name": user.get("display_name", ""),
                "broadcaster_type": user.get("broadcaster_type", ""),
                "description": user.get("description", ""),
                "profile_image_url": user.get("profile_image_url", ""),
                "offline_image_url": user.get("offline_image_url", ""),
                "view_count": user.get("view_count", 0),
                "created_at": user.get("created_at", ""),
            },
            "stream": {
                "is_live": bool(stream),
                "id": stream.get("id", ""),
                "game_id": stream.get("game_id", ""),
                "game_name": stream.get("game_name", ""),
                "title": stream.get("title", ""),
                "viewer_count": stream.get("viewer_count", 0),
                "language": stream.get("language", ""),
                "started_at": stream.get("started_at", ""),
                "type": stream.get("type", ""),
                "thumbnail_url": stream.get("thumbnail_url", ""),
                "tags": stream.get("tags", []),
            },
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        self.stdout.write(self.style.SUCCESS(f"Twitch channel info for '{broadcaster}'"))
        self.stdout.write("")
        self.stdout.write("User")
        self.stdout.write(f"  id: {payload['user']['id']}")
        self.stdout.write(f"  login: {payload['user']['login']}")
        self.stdout.write(f"  display_name: {payload['user']['display_name']}")
        self.stdout.write(f"  broadcaster_type: {payload['user']['broadcaster_type'] or '(none)'}")
        self.stdout.write(f"  created_at: {payload['user']['created_at']}")
        self.stdout.write(f"  profile_image_url: {payload['user']['profile_image_url']}")
        self.stdout.write("")
        self.stdout.write("Stream")
        self.stdout.write(f"  is_live: {payload['stream']['is_live']}")
        self.stdout.write(f"  game_id: {payload['stream']['game_id'] or '(not live/unknown)'}")
        self.stdout.write(f"  game_name: {payload['stream']['game_name'] or '(not live/unknown)'}")
        self.stdout.write(f"  title: {payload['stream']['title'] or '(none)'}")
        self.stdout.write(f"  viewer_count: {payload['stream']['viewer_count']}")
        self.stdout.write(f"  language: {payload['stream']['language'] or '(unknown)'}")
        self.stdout.write(f"  started_at: {payload['stream']['started_at'] or '(not live)'}")


    def _get_user_data(self, client_id: str, access_token: str, broadcaster: str) -> dict:
        params = urllib.parse.urlencode({"login": broadcaster})
        url = f"{USERS_URL}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Client-Id": client_id,
                "Authorization": f"Bearer {access_token}",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            error_detail = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
                error_data = json.loads(error_body)
                error_detail = error_data.get("message") or error_data.get("error") or error_body
            except Exception:
                pass

            if error_detail:
                raise CommandError(
                    f"Failed to fetch Twitch user data for '{broadcaster}': HTTP {exc.code}\n{error_detail}"
                ) from exc
            else:
                raise CommandError(
                    f"Failed to fetch Twitch user data for '{broadcaster}': HTTP {exc.code}"
                ) from exc
        except Exception as exc:
            raise CommandError(
                f"Failed to fetch Twitch user data for '{broadcaster}': {exc}"
            ) from exc

    def _get_stream_data(self, client_id: str, access_token: str, user_id: str) -> dict:
        params = urllib.parse.urlencode({"user_id": user_id})
        url = f"{STREAMS_URL}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "Client-Id": client_id,
                "Authorization": f"Bearer {access_token}",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise CommandError(
                f"Failed to fetch Twitch stream data for user_id '{user_id}': HTTP {exc.code}"
            ) from exc
        except Exception as exc:
            raise CommandError(
                f"Failed to fetch Twitch stream data for user_id '{user_id}': {exc}"
            ) from exc
