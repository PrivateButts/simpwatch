from __future__ import annotations


def parse_bot_mention_command(
    content: str, bot_username: str
) -> tuple[str, list[str]] | None:
    """Parse a message directed at the bot as ``@bot_username command [args…]``.

    Returns ``(command, args)`` where *command* is lower-cased and *args* is a
    list of the remaining tokens.  Returns ``None`` when the message does not
    start with a mention of *bot_username* or contains no command word.
    """
    parts = content.strip().split()
    if not parts:
        return None
    first = parts[0].lstrip("@").lower()
    if first != bot_username.lstrip("@").lower():
        return None
    if len(parts) < 2:
        return None
    command = parts[1].lower()
    args = parts[2:]
    return command, args


def parse_bot_simp_args(args: list[str]) -> tuple[str, str] | None:
    """Parse bot-mention simp arguments.

    Accepts ``@target`` with optional ``reason|because <text>`` tail and
    returns ``(target_username, reason)``. Returns ``None`` for invalid forms.
    """
    if not args:
        return None

    target_token = args[0]
    if not target_token.startswith("@"):
        return None

    target_username = target_token.lstrip("@").strip().lower()
    if not target_username:
        return None

    if len(args) < 2:
        return target_username, ""

    keyword = args[1].lower()
    if keyword not in {"reason", "because"}:
        return target_username, ""

    if len(args) < 3:
        return target_username, ""

    return target_username, " ".join(args[2:]).strip()


def parse_bot_ban_args(args: list[str]) -> tuple[str, str] | None:
    """Parse bot-mention ban arguments.

    Accepts ``@target`` with optional ``reason|because <text>`` tail and
    returns ``(target_username, reason)``. Returns ``None`` for invalid forms.
    """
    return parse_bot_simp_args(args)


def parse_twitch_target(content: str) -> str | None:
    parts = content.strip().split()
    if len(parts) < 2:
        return None
    candidate = parts[1]
    if not candidate.startswith("@"):
        return None
    username = candidate.lstrip("@").strip().lower()
    return username or None


def parse_twitch_reason(content: str) -> str:
    parts = content.strip().split()
    if len(parts) < 3:
        return ""

    start_index = 1
    if parts[1].startswith("@"):
        start_index = 2

    if len(parts) <= start_index:
        return ""

    keyword = parts[start_index].lower()
    if keyword not in {"reason", "because"}:
        return ""

    if len(parts) <= start_index + 1:
        return ""

    return " ".join(parts[start_index + 1 :]).strip()


def parse_twitch_bamder_reason(content: str) -> str:
    parts = content.strip().split()
    if len(parts) < 2:
        return ""

    if parts[1].lower() == "reason":
        if len(parts) < 3:
            return ""
        return " ".join(parts[2:]).strip()

    return " ".join(parts[1:]).strip()


def parse_twitch_ban_args(content: str) -> tuple[str, str] | None:
    parts = content.strip().split()
    if len(parts) < 2:
        return None

    target_token = parts[1]
    if not target_token.startswith("@"):
        return None

    target_username = target_token.lstrip("@").strip().lower()
    if not target_username:
        return None

    if len(parts) < 3:
        return target_username, ""

    keyword = parts[2].lower()
    if keyword not in {"reason", "because"}:
        return target_username, ""

    if len(parts) < 4:
        return target_username, ""

    return target_username, " ".join(parts[3:]).strip()
