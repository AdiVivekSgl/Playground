# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Intent router: incoming WhatsApp text -> one narrowly-scoped handler.

The first word of the message picks the command and the rest is its argument
("stock XYZ-123" -> command "stock", args "XYZ-123"). Only commands registered
in COMMANDS can run - the message text is never used to look up a module,
method, DocType or SQL. Adding a business query means writing one handler and
adding one line to COMMANDS, e.g.:

	"order": get_order_status,   # order SO-00045

A few fixed natural-language phrasings (_NATURAL_LANGUAGE) are rewritten to a
registered command first; they can only ever produce a command in COMMANDS.

Handlers receive (args, sender) and return the reply text. Any handler that
reads ERPNext data must require sender.user and apply that user's permissions
(see stock.py and user_context.as_user).
"""

import re
from dataclasses import dataclass

from playground.playground.whatsapp.stock import get_stock

HELLO_REPLY = "Hello from Frontec ERP 👋"
PING_REPLY = "pong"
UNKNOWN_REPLY = "Frontec ERP is connected, but this command is not implemented yet."
UNKNOWN_INTENT = "unknown"

# Trailing punctuation ignored on the command word, so "Hello!" still matches.
_COMMAND_PUNCTUATION = ".,!?;:"

# Narrow natural-language forms, each rewritten to (command, item code). No LLM,
# no guessing: the whole message must match one pattern, otherwise it is routed
# by its first word as usual. The item code is a single token.
_ITEM_CODE = r"(?P<code>[A-Za-z0-9][A-Za-z0-9._/-]*)"
_NATURAL_LANGUAGE = (
	# "How many pcs of XYZ-123 are available?", "how much stock of item XYZ-123 do we have"
	(
		"stock",
		re.compile(
			r"how\s+(?:many|much)\s+(?:pcs|pieces|units|nos|qty|quantity|stock)\s+(?:of\s+)?(?:item\s+)?"
			+ _ITEM_CODE
			+ r"(?:\s+(?:is|are|do\s+we\s+have|we\s+have))?(?:\s+(?:available|in\s+stock|left|there))?",
			re.IGNORECASE,
		),
	),
	# "stock of XYZ-123", "What is the stock for XYZ-123?"
	(
		"stock",
		re.compile(r"(?:what\s+is\s+(?:the\s+)?)?stock\s+(?:of|for)\s+(?:item\s+)?" + _ITEM_CODE, re.IGNORECASE),
	),
)


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
	"stock": get_stock,  # stock XYZ-123
}


def parse_command(message):
	"""Split a message into (command, args); command is lower-cased, args keep their case."""
	text = (message or "").strip()
	command, _, args = text.partition(" ")
	return command.rstrip(_COMMAND_PUNCTUATION).casefold(), args.strip()


def parse_natural_language(message):
	"""Return (command, args) when the whole message is a known phrasing, else None."""
	text = " ".join((message or "").split()).rstrip(_COMMAND_PUNCTUATION + " ")
	for command, pattern in _NATURAL_LANGUAGE:
		match = pattern.fullmatch(text)
		if match:
			return command, match.group("code")
	return None


def route(message, sender):
	command, args = parse_natural_language(message) or parse_command(message)
	handler = COMMANDS.get(command)
	if handler is None:
		return RouteResult(intent=UNKNOWN_INTENT, reply=UNKNOWN_REPLY)
	return RouteResult(intent=command, reply=handler(args, sender))
