# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Intent router: incoming WhatsApp text -> one narrowly-scoped handler.

The first word of the message picks the command and the rest is its argument
("stock XYZ-123" -> command "stock", args "XYZ-123"). Only commands registered
in COMMANDS can run - the message text is never used to look up a module,
method, DocType or SQL. Adding a business query later means writing one handler
and adding one line to COMMANDS, e.g.:

	"stock": get_stock,          # stock XYZ-123
	"order": get_order_status,   # order SO-00045

Handlers receive (args, sender) and return the reply text. Any handler that
reads ERPNext data must require sender.user and apply that user's permissions.
"""

from dataclasses import dataclass

HELLO_REPLY = "Hello from Frontec ERP 👋"
PING_REPLY = "pong"
UNKNOWN_REPLY = "Frontec ERP is connected, but this command is not implemented yet."
UNKNOWN_INTENT = "unknown"

# Trailing punctuation ignored on the command word, so "Hello!" still matches.
_COMMAND_PUNCTUATION = ".,!?;:"


@dataclass(frozen=True)
class RouteResult:
	intent: str
	reply: str


def hello(args, sender):
	return HELLO_REPLY


def ping(args, sender):
	return PING_REPLY


COMMANDS = {
	"hello": hello,
	"ping": ping,
}


def parse_command(message):
	"""Split a message into (command, args); command is lower-cased, args keep their case."""
	text = (message or "").strip()
	command, _, args = text.partition(" ")
	return command.rstrip(_COMMAND_PUNCTUATION).casefold(), args.strip()


def route(message, sender):
	command, args = parse_command(message)
	handler = COMMANDS.get(command)
	if handler is None:
		return RouteResult(intent=UNKNOWN_INTENT, reply=UNKNOWN_REPLY)
	return RouteResult(intent=command, reply=handler(args, sender))
