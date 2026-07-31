#!/usr/bin/env python3
# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Finished Kit Label Print Agent
==============================

Runs continuously on the Windows PC connected to the TSC TTP-244 Pro. It:

  1. Polls ERPNext (Frappe Cloud) over the REST API for "Label Print Request"
     rows with Status = "Pending".
  2. Renders TSPL for the requested template (KIT_LABEL) from the row's data.
  3. Sends the raw TSPL to the printer via Windows RAW printing (win32print) -
     no driver rendering, no BarTender.
  4. Writes the result back: Status = "Printed" (+ printed_on) or "Failed"
     (+ error_log).

It is deliberately defensive: one bad job is logged and marked Failed, and the
agent keeps running. Configuration lives in config.json next to this file (copy
config.example.json). See README.md for setup, including the scoped API user.

Dependencies:  pip install pywin32 requests
Run:           python label_print_agent.py
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime

import requests

try:
	import win32print
except ImportError:  # pragma: no cover - only importable on Windows with pywin32
	win32print = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DOCTYPE = "Label Print Request"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config():
	if not os.path.exists(CONFIG_PATH):
		sys.exit(
			f"Missing {CONFIG_PATH}. Copy config.example.json to config.json and fill it in."
		)
	with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
		cfg = json.load(fh)

	required = ["site_url", "api_key", "api_secret", "printer_name"]
	missing = [k for k in required if not cfg.get(k)]
	if missing:
		sys.exit(f"config.json is missing required keys: {', '.join(missing)}")

	cfg.setdefault("poll_interval_seconds", 12)
	cfg.setdefault("label_width_mm", 60)
	cfg.setdefault("label_height_mm", 40)
	cfg.setdefault("gap_mm", 2)
	cfg.setdefault("print_density", 8)
	cfg.setdefault("print_speed", 4)
	cfg.setdefault("dry_run", False)
	cfg["site_url"] = cfg["site_url"].rstrip("/")
	return cfg


def log(msg):
	print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# ERPNext REST API
# --------------------------------------------------------------------------- #
class ERPNextClient:
	def __init__(self, cfg):
		self.base = cfg["site_url"]
		self.session = requests.Session()
		self.session.headers.update({
			"Authorization": f"token {cfg['api_key']}:{cfg['api_secret']}",
			"Accept": "application/json",
			"Content-Type": "application/json",
		})
		self.timeout = 30

	def get_pending(self, limit=20):
		"""Fetch pending requests, oldest first, with the fields the templates need."""
		fields = [
			"name", "work_order", "production_item", "item_code", "item_name",
			"company", "batch_no", "number_of_labels", "label_template",
		]
		params = {
			"filters": json.dumps([["status", "=", "Pending"]]),
			"fields": json.dumps(fields),
			"order_by": "creation asc",
			"limit_page_length": limit,
		}
		url = f"{self.base}/api/resource/{DOCTYPE.replace(' ', '%20')}"
		resp = self.session.get(url, params=params, timeout=self.timeout)
		resp.raise_for_status()
		return resp.json().get("data", [])

	def update_status(self, name, status, printed_on=None, error_log=None):
		body = {"status": status}
		if printed_on is not None:
			body["printed_on"] = printed_on
		if error_log is not None:
			body["error_log"] = error_log[:2000]
		url = f"{self.base}/api/resource/{DOCTYPE.replace(' ', '%20')}/{name}"
		resp = self.session.put(url, data=json.dumps(body), timeout=self.timeout)
		resp.raise_for_status()
		return resp.json().get("data")


# --------------------------------------------------------------------------- #
# TSPL template rendering
# --------------------------------------------------------------------------- #
def tspl_escape(text):
	"""Make a value safe to embed inside a TSPL double-quoted string."""
	return str(text or "").replace("\\", "\\\\").replace('"', '\\"')


def render_kit_label(req, cfg):
	"""Build the KIT_LABEL TSPL for one Label Print Request.

	Layout (60x40 mm @ 203 dpi, ~8 dots/mm):
	  Company name  -> top line
	  Product name  -> below, larger
	  Work Order no -> below
	  Batch (if any)-> below
	  QR code       -> right side, encodes the Work Order number

	Kept intentionally small and declarative so new templates are easy to add:
	return a bytes payload of TSPL and RAW-print it. To tweak positions/fonts, edit
	the TEXT/QRCODE coordinates below.
	"""
	company = tspl_escape(req.get("company"))
	product = tspl_escape(req.get("item_name") or req.get("item_code"))
	work_order = tspl_escape(req.get("work_order"))
	batch = tspl_escape(req.get("batch_no"))
	copies = max(1, int(req.get("number_of_labels") or 1))

	lines = [
		f"SIZE {cfg['label_width_mm']} mm, {cfg['label_height_mm']} mm",
		f"GAP {cfg['gap_mm']} mm, 0 mm",
		f"DENSITY {cfg['print_density']}",
		f"SPEED {cfg['print_speed']}",
		"DIRECTION 1",
		"CLS",
		f'TEXT 16,16,"3",0,1,1,"{company}"',
		f'TEXT 16,60,"4",0,1,1,"{product}"',
		f'TEXT 16,120,"3",0,1,1,"WO: {work_order}"',
	]
	if batch:
		lines.append(f'TEXT 16,160,"2",0,1,1,"Batch: {batch}"')
	# QR of the Work Order number, top-right.
	lines.append(f'QRCODE 360,16,H,5,A,0,"{work_order}"')
	# PRINT sets,copies -> 1 set, N identical copies.
	lines.append(f"PRINT 1,{copies}")

	payload = "\r\n".join(lines) + "\r\n"
	return payload.encode("utf-8", errors="replace")


TEMPLATES = {
	"KIT_LABEL": render_kit_label,
}


def render(req, cfg):
	template_name = (req.get("label_template") or "KIT_LABEL").strip().upper()
	renderer = TEMPLATES.get(template_name)
	if renderer is None:
		raise ValueError(
			f"Unknown label template '{template_name}'. Known: {', '.join(sorted(TEMPLATES))}."
		)
	return renderer(req, cfg)


# --------------------------------------------------------------------------- #
# RAW printing (win32print)
# --------------------------------------------------------------------------- #
def send_to_printer(printer_name, raw_bytes, job_name="KIT_LABEL"):
	if win32print is None:
		raise RuntimeError(
			"win32print is not available. Install it with: pip install pywin32 "
			"(this agent must run on the Windows PC connected to the printer)."
		)
	handle = win32print.OpenPrinter(printer_name)
	try:
		# RAW datatype -> the printer receives the TSPL verbatim, no driver rendering.
		win32print.StartDocPrinter(handle, 1, (job_name, None, "RAW"))
		try:
			win32print.StartPagePrinter(handle)
			win32print.WritePrinter(handle, raw_bytes)
			win32print.EndPagePrinter(handle)
		finally:
			win32print.EndDocPrinter(handle)
	finally:
		win32print.ClosePrinter(handle)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def process_one(client, cfg, req):
	name = req["name"]
	try:
		raw = render(req, cfg)
		if cfg.get("dry_run"):
			log(f"DRY RUN {name}: would print {req.get('number_of_labels')} label(s):")
			print(raw.decode("utf-8", errors="replace"))
		else:
			send_to_printer(cfg["printer_name"], raw, job_name=name)
		client.update_status(
			name, "Printed",
			printed_on=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
			error_log="",
		)
		log(f"Printed {name} ({req.get('number_of_labels')} label(s) for {req.get('work_order')}).")
	except Exception:
		err = traceback.format_exc()
		log(f"FAILED {name}: {err.strip().splitlines()[-1]}")
		try:
			client.update_status(name, "Failed", error_log=err)
		except Exception:
			log(f"Could not write Failed status back for {name}:\n{traceback.format_exc()}")


def main():
	cfg = load_config()
	client = ERPNextClient(cfg)
	interval = cfg["poll_interval_seconds"]
	log(f"Label print agent started. Site={cfg['site_url']} Printer={cfg['printer_name']} "
		f"Poll={interval}s DryRun={cfg.get('dry_run')}")

	while True:
		try:
			pending = client.get_pending()
			if pending:
				log(f"{len(pending)} pending request(s).")
			for req in pending:
				process_one(client, cfg, req)
		except requests.HTTPError as e:
			log(f"API error: {e} - {getattr(e.response, 'text', '')[:300]}")
		except Exception:
			log(f"Poll error:\n{traceback.format_exc()}")
		time.sleep(interval)


if __name__ == "__main__":
	try:
		main()
	except KeyboardInterrupt:
		log("Stopped.")
