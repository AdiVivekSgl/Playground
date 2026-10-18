# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Remote attendance from the FTPL-Attendance page.

The page (github.com/AdiVivekSgl/FTPL-Attendance) reads the phone's GPS and
opens WhatsApp with a prefilled message to the Frontec number:

	📍 FRONTEC ATTENDANCE
	Location: https://www.google.com/maps?q=17.385044,78.486671
	Accuracy: 12m

When the employee taps Send it arrives here like any other text. A message
carrying the FRONTEC ATTENDANCE marker becomes one **Remote Attendance** row
for the sender's mapped ERPNext user. There is no IN/OUT: each message is one
location record.

The coordinates are whatever the message says - the employee can edit the
text before sending - so this is a self-reported location, not proof.
"""

import json
import re

import frappe
from frappe.utils import now_datetime

from playground.playground.whatsapp.user_context import as_user

DOCTYPE = "Remote Attendance"
MARKER = "FRONTEC ATTENDANCE"
# Same threshold as the page's "accuracy is low" warning.
LOW_ACCURACY_METERS = 100
SOURCE_MESSAGE_LIMIT = 1000

_MARKER_RE = re.compile(re.escape(MARKER), re.IGNORECASE)
# "maps?q=17.385044,78.486671" (also &q=, or a space after the comma).
_COORDS_RE = re.compile(r"[?&]q=\s*(?P<lat>[-+]?\d{1,2}(?:\.\d+)?)\s*,\s*(?P<lon>[-+]?\d{1,3}(?:\.\d+)?)")
_ACCURACY_RE = re.compile(r"accuracy\s*:\s*(?P<m>\d{1,7})\s*m\b", re.IGNORECASE)

NOT_LINKED_REPLY = (
	"Sorry, your WhatsApp number is not linked to an ERPNext user, so your attendance was not recorded. "
	"Please ask your ERPNext administrator to add it under *WhatsApp User*."
)
NO_LOCATION_REPLY = (
	"Sorry, I couldn't read a location in that attendance message, so nothing was recorded. "
	"Please open the Frontec Attendance page again and send the message without editing it."
)
RECORDED_REPLY = "✅ Attendance recorded for *{name}*\n🕒 {time}\n📍 {lat}, {lon}{accuracy}"
LOW_ACCURACY_NOTE = "\n⚠️ Location accuracy is low - next time, try again outdoors or with GPS on."


def is_attendance_message(message):
	return bool(_MARKER_RE.search(message or ""))


def parse_location(message):
	"""Return (latitude, longitude, accuracy_m or None), or None when there are no valid coordinates."""
	text = message or ""
	match = _COORDS_RE.search(text)
	if not match:
		return None
	lat, lon = float(match.group("lat")), float(match.group("lon"))
	if not (-90 <= lat <= 90 and -180 <= lon <= 180):
		return None
	accuracy = _ACCURACY_RE.search(text)
	return lat, lon, int(accuracy.group("m")) if accuracy else None


def record_attendance(message, sender):
	if not sender or not sender.user:
		return NOT_LINKED_REPLY
	location = parse_location(message)
	if location is None:
		return NO_LOCATION_REPLY
	lat, lon, accuracy = location

	# Created as the mapped user so they are its owner (the Employee role may
	# read its own rows). ignore_permissions because nobody may create Remote
	# Attendance from the desk - this handler is the only way in, and it only
	# ever writes a row for the sender's own mapped user.
	with as_user(sender.user) as user:
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"timestamp": now_datetime(),
				"user": user,
				"employee": _employee_for(user),
				"phone": sender.phone,
				"latitude": lat,
				"longitude": lon,
				"accuracy": accuracy,
				"low_accuracy": int(accuracy is None or accuracy > LOW_ACCURACY_METERS),
				"map_link": f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}",
				"location": _geojson_point(lat, lon),
				"source_message": message[:SOURCE_MESSAGE_LIMIT],
			}
		)
		doc.insert(ignore_permissions=True)

	return format_reply(doc)


def format_reply(doc):
	reply = RECORDED_REPLY.format(
		name=doc.get("employee_name") or doc.get("full_name") or doc.user,
		time=doc.timestamp.strftime("%d-%m-%Y %H:%M"),
		lat=f"{doc.latitude:.6f}",
		lon=f"{doc.longitude:.6f}",
		accuracy=f" (±{doc.accuracy} m)" if doc.accuracy is not None else "",
	)
	if doc.low_accuracy:
		reply += LOW_ACCURACY_NOTE
	return reply


def _employee_for(user):
	# Server-side lookup of the sender's own Employee record (not returned to them).
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")


def _geojson_point(lat, lon):
	return json.dumps(
		{
			"type": "FeatureCollection",
			"features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [lon, lat]}}],
		}
	)
